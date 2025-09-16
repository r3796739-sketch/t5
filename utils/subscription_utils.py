# yoppychat2/utils/subscription_utils.py

import os
from functools import wraps
from flask import session, jsonify
import redis
import json
from .supabase_client import get_supabase_admin_client
from .db_utils import get_profile, get_usage_stats
# The direct dependency on whop_api for role checking is now removed.
# from . import whop_api 

# --- Redis Caching Setup (Unchanged) ---
try:
    redis_client = redis.from_url(os.environ.get("REDIS_URL"))
    CACHE_DURATION_SECONDS = 300 # Cache for 5 minutes
    print("Successfully connected to Redis for caching.")
except Exception as e:
    redis_client = None
    print(f"Could not connect to Redis for caching: {e}. Caching will be disabled.")

# --- Plan Definitions (Unchanged) ---
COMMUNITY_PLANS = {
    'basic_community': {
        'name': 'Basic Community',
        'shared_channels_allowed': 1,
        'queries_per_month': 200
    },
    'pro_community': {
        'name': 'Pro Community',
        'shared_channels_allowed': 2,
        'queries_per_month': 500
    },
    'rich_community': {
        'name': 'Rich Community',
        'shared_channels_allowed': 5,
        'queries_per_month': 1500
    }
}

PLANS = {
    'free': { 
        'name': 'Free', 
        'max_channels': 2, 
        'max_queries_per_month': 50, 
        'price_usd': 0, 
        'commission_rate': 0 
    },
    # This is the old 'creator' plan, now for regular users
    os.environ.get('RAZORPAY_PLAN_ID_PERSONAL', 'personal'): { 
        'name': 'Personal', 
        'max_channels': 10, 
        'max_queries_per_month': 2500, 
        'price_usd': 3.60,  # Corrected from 9 (approx. ₹299)
        'commission_rate': 0.70 
    },
    # This is the old 'pro' plan, now repurposed for creators
    os.environ.get('RAZORPAY_PLAN_ID_CREATOR', 'creator'): { 
        'name': 'Creator', 
        'max_channels': float('inf'), 
        'max_queries_per_month': 10000, 
        'price_usd': 18.00, # Corrected from 9.99 (approx. ₹1,499)
        'commission_rate': 0.75 
    },
    'admin_testing': { 'name': 'free', 'max_channels': 1, 'max_queries_per_month': 10 },
    'community_member': { 'name': 'Community Member', 'max_channels': 0, 'max_queries_per_month': 50 },
    'whop_basic_member': { 'name': 'Basic Member', 'max_channels': 2, 'max_queries_per_month': 50 },
    'whop_pro_member': { 'name': 'Pro Member', 'max_channels': 5, 'max_queries_per_month': 100 },
    'whop_rich_member': { 'name': 'Rich Member', 'max_channels': 10, 'max_queries_per_month': 300 }
}


# --- get_community_status (Unchanged) ---
def get_community_status(community_id: str) -> dict:
    """
    Fetches a community's plan, limits, and current usage.
    """
    cache_key = f"community_status:{community_id}"
    if redis_client:
        try:
            cached_status = redis_client.get(cache_key)
            if cached_status:
                return json.loads(cached_status)
        except redis.RedisError as e:
            print(f"Redis GET error for community {community_id}: {e}. Fetching from DB.")

    supabase_admin = get_supabase_admin_client()
    response = supabase_admin.table('communities').select('*').eq('id', community_id).single().execute()
    if not response.data:
        return None

    community_data = response.data
    plan_id = community_data.get('plan_id', 'basic_community')
    plan_details = COMMUNITY_PLANS.get(plan_id, COMMUNITY_PLANS['basic_community'])

    status = {
        'community_id': community_id,
        'plan_id': plan_id,
        'plan_name': plan_details['name'],
        'limits': {
            'shared_channel_limit': plan_details['shared_channels_allowed'],
            'queries_per_month': plan_details.get('queries_per_month', 50),
            'query_limit': community_data.get('query_limit', 0),
            'owner_trial_limit': 10
        },
        'usage': {
            'queries_used': community_data.get('queries_used', 0),
            'trial_queries_used': community_data.get('trial_queries_used', 0)
        }
    }

    if redis_client:
        redis_client.setex(cache_key, CACHE_DURATION_SECONDS, json.dumps(status))

    return status

# --- REFACTORED get_user_status ---
def get_user_status(user_id: str, active_community_id: str = None) -> dict:
    """
    Fetches a user's status by relying on the database and session,
    removing the need for external API calls to whop_api.py.
    """
    cache_key = f"user_status:{user_id}:community:{active_community_id or 'none'}"
    if redis_client:
        try:
            cached_status = redis_client.get(cache_key)
            if cached_status:
                cached_data = json.loads(cached_status)
                # Handle 'inf' deserialization
                if cached_data.get('limits', {}).get('max_channels') == 'inf':
                    cached_data['limits']['max_channels'] = float('inf')
                if cached_data.get('limits', {}).get('max_queries_per_month') == 'inf':
                    cached_data['limits']['max_queries_per_month'] = float('inf')
                return cached_data
        except redis.RedisError as e:
            print(f"Redis GET error for user {user_id}: {e}. Fetching from DB.")

    profile = get_profile(user_id)
    if not profile:
        return None

    is_whop_user = bool(profile.get('whop_user_id'))
    personal_plan_id = profile.get('personal_plan_id') or profile.get('direct_subscription_plan')
    
    # NEW: Determine ownership and role from the database
    is_active_community_owner = False
    community_role = 'member' # Default role for Whop users
    if is_whop_user and active_community_id:
        supabase_admin = get_supabase_admin_client()
        community_res = supabase_admin.table('communities').select('owner_user_id').eq('id', active_community_id).single().execute()
        if community_res.data and str(community_res.data['owner_user_id']) == str(user_id):
            is_active_community_owner = True
            community_role = 'admin'

    # Determine the final plan ID
    raw_plan_id = 'free'
    if is_whop_user:
        raw_plan_id = 'community_member'
        if is_active_community_owner:
            community_status = get_community_status(active_community_id)
            if community_status:
                trial_used = community_status.get('usage', {}).get('trial_queries_used', 0)
                trial_limit = community_status.get('limits', {}).get('owner_trial_limit', 10)
                if trial_used < trial_limit:
                    raw_plan_id = 'admin_testing'

    if personal_plan_id and personal_plan_id in PLANS:
        is_valid_plan = (is_whop_user and personal_plan_id.startswith('whop_')) or \
                        (not is_whop_user and personal_plan_id in ['creator', 'pro', 'free'])
        if is_valid_plan:
            raw_plan_id = personal_plan_id

    plan_details = PLANS.get(raw_plan_id, PLANS['free'])
    usage_stats = get_usage_stats(user_id)

    status = {
        'user_id': user_id,
        'plan_id': raw_plan_id,
        'plan_name': plan_details.get('name', 'Unknown Plan'),
        'has_personal_plan': bool(personal_plan_id),
        'is_whop_user': is_whop_user,
        'active_community_id': active_community_id,
        'is_active_community_owner': is_active_community_owner,
        'community_role': community_role if is_whop_user else None,
        'limits': plan_details.copy(),
        'usage': {
            'queries_this_month': usage_stats.get('queries_this_month', 0),
            'channels_processed': usage_stats.get('channels_processed', 0)
        }
    }

    if redis_client:
        serializable_status = json.loads(json.dumps(status, default=lambda o: 'inf' if o == float('inf') else o))
        redis_client.setex(cache_key, CACHE_DURATION_SECONDS, json.dumps(serializable_status))

    return status

# --- Decorators (Unchanged) ---
def limit_enforcer(check_type: str):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user' not in session:
                return jsonify({'status': 'error', 'message': 'Authentication required.'}), 401
            user_id = session['user']['id']
            active_community_id = session.get('active_community_id')
            user_status = get_user_status(user_id, active_community_id)
            if not user_status:
                 return jsonify({'status': 'error', 'message': 'Could not verify user.'}), 500
            if check_type == 'query':
                if user_status.get('is_active_community_owner'):
                    community_status = get_community_status(active_community_id)
                    if community_status:
                        trial_limit = community_status['limits'].get('owner_trial_limit', 0)
                        trial_used = community_status['usage'].get('trial_queries_used', 0)
                        if trial_used < trial_limit:
                            return f(*args, **kwargs)
                if user_status.get('has_personal_plan'):
                    max_queries = user_status['limits'].get('max_queries_per_month', 0)
                    queries_used = user_status['usage'].get('queries_this_month', 0)
                    if max_queries != float('inf') and queries_used >= max_queries:
                        return jsonify({'status': 'limit_reached', 'message': f"You've reached your monthly query limit of {int(max_queries)}."}), 403
                elif active_community_id:
                    community_status = get_community_status(active_community_id)
                    if not community_status:
                        return jsonify({'status': 'error', 'message': 'Could not verify community status.'}), 500
                    max_queries = community_status['limits'].get('query_limit', 0)
                    queries_used = community_status['usage'].get('queries_used', 0)
                    if max_queries != float('inf') and queries_used >= max_queries:
                        return jsonify({'status': 'limit_reached', 'message': "The community's shared query limit has been reached."}), 403
                else:
                    max_queries = user_status['limits'].get('max_queries_per_month', 0)
                    queries_used = user_status['usage'].get('queries_this_month', 0)
                    if max_queries != float('inf') and queries_used >= max_queries:
                        return jsonify({'status': 'limit_reached', 'message': f"You've reached your monthly query limit of {int(max_queries)}."}), 403
            elif check_type == 'channel':
                max_channels = user_status['limits'].get('max_channels', 0)
                current_channels = user_status['usage'].get('channels_processed', 0)
                if max_channels != float('inf') and current_channels >= max_channels:
                    message = f"You have reached the maximum of {max_channels} personal channels for your plan."
                    if user_status.get('is_whop_user'):
                        return jsonify({'status': 'limit_reached', 'message': message, 'action': 'show_upgrade_popup'}), 403
                    else:
                        return jsonify({'status': 'limit_reached', 'message': message}), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def community_channel_limit_enforcer(_func=None, *, check_on_increase_only=False):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user' not in session:
                return jsonify({'status': 'error', 'message': 'Authentication required.'}), 401
            user_id = session['user']['id']
            active_community_id = session.get('active_community_id')
            if not active_community_id:
                return jsonify({'status': 'error', 'message': 'No active community context found.'}), 400
            if check_on_increase_only:
                channel_id = kwargs.get('channel_id')
                if not channel_id:
                    return jsonify({'status': 'error', 'message': 'Channel ID is required for this check.'}), 500
                supabase_admin = get_supabase_admin_client()
                channel_res = supabase_admin.table('channels').select('is_shared').eq('id', channel_id).single().execute()
                if not channel_res.data:
                    return jsonify({'status': 'error', 'message': 'Channel not found.'}), 404
                if channel_res.data['is_shared']:
                    return f(*args, **kwargs)
            user_status = get_user_status(user_id, active_community_id)
            if not user_status.get('is_active_community_owner'):
                return jsonify({'status': 'error', 'message': 'Only community owners can perform this action.'}), 403
            community_status = get_community_status(active_community_id)
            if not community_status:
                return jsonify({'status': 'error', 'message': 'Could not verify community status.'}), 500
            from . import db_utils
            current_shared_channels = db_utils.count_shared_channels(active_community_id)
            max_shared_channels = community_status['limits'].get('shared_channel_limit', 0)
            if current_shared_channels >= max_shared_channels:
                return jsonify({'status': 'limit_reached', 'message': f"You have reached the maximum of {max_shared_channels} shared channels for your community's plan."}), 403
            return f(*args, **kwargs)
        return decorated_function
    if _func:
        return decorator(_func)
    return decorator

def admin_channel_limit_enforcer(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return jsonify({'status': 'error', 'message': 'Authentication required.'}), 401
        user_id = session['user']['id']
        active_community_id = session.get('active_community_id')
        if not active_community_id:
            return jsonify({'status': 'error', 'message': 'No active community context found.'}), 400
        user_status = get_user_status(user_id, active_community_id)
        if not user_status.get('is_active_community_owner'):
            return jsonify({'status': 'error', 'message': 'Only community owners can perform this action.'}), 403
        community_status = get_community_status(active_community_id)
        if not community_status:
            return jsonify({'status': 'error', 'message': 'Could not verify community status.'}), 500
        from . import db_utils
        current_total_channels = db_utils.count_channels_for_user(user_id)
        max_total_channels = community_status['limits'].get('shared_channel_limit', 0)
        if current_total_channels >= max_total_channels:
            return jsonify({'status': 'limit_reached', 'message': f"As a community admin, your total channel limit is {max_total_channels}. You have reached this limit."}), 403
        return f(*args, **kwargs)
    return decorated_function
