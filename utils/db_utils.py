# In utils/db_utils.py

import logging
from .supabase_client import get_supabase_admin_client
from datetime import datetime
from cachetools import TTLCache

# --- PERFORMANCE: In-memory TTL cache to reduce DB hits on every page load ---
_creator_channels_cache = TTLCache(maxsize=500, ttl=60)
_dashboard_stats_cache = TTLCache(maxsize=200, ttl=60)

# It's good practice to use the shared admin client for these utility functions
# as they are often called from background tasks or trusted server-side routes.
supabase = get_supabase_admin_client()
log = logging.getLogger(__name__)

def get_profile(user_id: str):
    """Fetches a user's complete profile data."""
    try:
        response = supabase.table('profiles').select('*').eq('id', user_id).maybe_single().execute()
        # Add a check to ensure response is not None before accessing .data
        return response.data if response and response.data else {}
    except Exception as e:
        log.error(f"Error getting profile for user {user_id}: {e}")
        return {}

def get_usage_stats(user_id: str):
    """Fetches a user's usage statistics from the usage_stats table."""
    try:
        response = supabase.table('usage_stats').select('*').eq('user_id', user_id).maybe_single().execute()
        return response.data if response and response.data else {}
    except Exception as e:
        log.error(f"Error getting usage stats for user {user_id}: {e}")
        return {} # Return an empty dict on error to prevent crashes downstream

def link_user_to_community(user_id: str, community_id: str):
    """Creates a link in the user_communities join table."""
    try:
        supabase.table('user_communities').upsert(
            {'user_id': user_id, 'community_id': community_id},
            ignore_duplicates=True
        ).execute()
        return True
    except Exception as e:
        log.error(f"Error linking user {user_id} to community {community_id}: {e}")
        return False

def find_channel_by_url(channel_url: str):
    """Checks if a channel already exists in the master channels table."""
    try:
        response = supabase.table('channels').select('id, status').eq('channel_url', channel_url).maybe_single().execute()
        return response.data if response else None
    except Exception as e:
        log.error(f"Error finding channel by URL {channel_url}: {e}")
        return None

def link_user_to_channel(user_id: str, channel_id: int):
    """Creates a link in the user_channels join table."""
    try:
        # Upsert with ignore_duplicates=True is a safe way to ensure the link exists
        # without causing an error if it's already there.
        response = supabase.table('user_channels').upsert(
            {'user_id': user_id, 'channel_id': channel_id},
            ignore_duplicates=True
        ).execute()
        return response.data
    except Exception as e:
        log.error(f"Error linking user {user_id} to channel {channel_id}: {e}")
        return None

def create_channel(channel_url: str, user_id: str, is_shared: bool = False, community_id: str = None):
    """Adds a new channel to the master list with a 'pending' status."""
    try:
        channel_payload = {
            'channel_url': channel_url,
            'user_id': user_id, # Store who originally added it
            'status': 'pending',
            'is_shared': is_shared,
            'community_id': community_id
        }
        response = supabase.table('channels').insert(channel_payload).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        log.error(f"Error creating channel for URL {channel_url}: {e}")
        return None

def add_community(community_data: dict):
    """Adds a new community with default plan values."""
    try:
        # Set default values for a new community based on the 'basic_community' plan
        defaults = {
            'plan_id': 'basic_community',
            'query_limit': 0, # Will be set by Whop webhook based on member count
            'queries_used': 0,
            'shared_channel_limit': 1,
            'trial_queries_used': 0
        }
        # Merge provided data with defaults, letting provided data take precedence
        final_data = {**defaults, **community_data}

        response = supabase.table('communities').upsert(
            final_data, on_conflict='whop_community_id'
        ).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        log.error(f"Error adding community {community_data.get('whop_community_id')}: {e}")
        return None

def count_channels_for_user(user_id: str) -> int:
    """Counts the total number of channels (personal or shared) created by a specific user."""
    try:
        response = supabase.table('channels').select('id', count='exact').eq('creator_id', user_id).execute()
        return response.count or 0
    except Exception as e:
        log.error(f"Error counting channels for user {user_id}: {e}")
        return 0

def count_shared_channels(community_id: str) -> int:
    """Counts the number of shared channels for a given community."""
    try:
        response = supabase.table('channels').select('id', count='exact').eq('community_id', community_id).eq('is_shared', True).execute()
        return response.count or 0
    except Exception as e:
        log.error(f"Error counting shared channels for community {community_id}: {e}")
        return 0

def increment_community_query_usage(community_id: str, is_trial: bool):
    """
    Increments the query counter for a specific community.
    Handles both the owner's trial and the shared community pool.
    """
    try:
        # Calls the updated RPC function that takes community_id directly.
        params = {'p_community_id': community_id, 'p_is_trial': is_trial}
        supabase.rpc('increment_query_usage', params).execute()
    except Exception as e:
        log.error(f"Error incrementing query usage for community {community_id}: {e}")

def increment_personal_query_usage(user_id: str):
    """
    Increments the query counter for a specific user.
    """
    try:
        params = {'p_user_id': user_id}
        # --- THIS IS THE FIX ---
        # The RPC function name has been corrected to match the database schema.
        supabase.rpc('increment_personal_query_usage', params).execute()
        # --- END FIX ---
    except Exception as e:
        log.error(f"Error incrementing personal query usage for user {user_id}: {e}")

def create_notification(user_id: str, message: str, type: str = 'info'):
    """Inserts a new notification for a specific user."""
    try:
        supabase.table('notifications').insert({
            'user_id': user_id,
            'message': message,
            'type': type,
            'is_read': False
        }).execute()
        return True
    except Exception as e:
        log.error(f"Error creating notification for user {user_id}: {e}")
        return False

def increment_channels_processed(user_id: str):
    """
    Increments the channels_processed counter for a specific user.
    This should be called only when a new, unique channel is added to a user's list.
    """
    try:
        params = {'p_user_id': user_id}
        # Use the existing RPC function suggested by the database error hint.
        supabase.rpc('increment_channel_count', params).execute()
    except Exception as e:
        log.error(f"Error incrementing channels processed for user {user_id}: {e}")

def decrement_channels_processed(user_id: str):
    """
    Decrements the channels_processed counter for a specific user.
    Called when a chatbot is deleted to free up the user's quota.
    """
    try:
        params = {'p_user_id': user_id}
        supabase.rpc('decrement_channel_count', params).execute()
    except Exception as e:
        log.error(f"Error decrementing channels processed for user {user_id}: {e}")

def create_initial_usage_stats(user_id: str):
    """Creates the initial usage_stats row for a new user."""
    try:
        # Using upsert is safe and prevents errors if the row somehow already exists.
        supabase.table('usage_stats').upsert({'user_id': user_id}).execute()
        return True
    except Exception as e:
        log.error(f"Error creating initial usage stats for user {user_id}: {e}")
        return False

def create_or_update_profile(profile_data: dict):
    """Creates or updates a user profile. Used for both direct and Whop users."""
    try:
        # Using upsert is efficient. It will update if 'id' exists, or insert if it doesn't.
        response = supabase.table('profiles').upsert(profile_data).execute()

        # Check if the upsert was successful
        if response.data:
            print(f"Successfully upserted profile for user ID: {profile_data.get('id')}")
            return response.data[0]

        # If upsert fails or returns no data, attempt a direct select
        user_id = profile_data.get('id')
        if user_id:
            return get_profile(user_id)

        return None
    except Exception as e:
        log.error(f"Error upserting profile: {e}")
        return None

def get_discord_server_link(server_id: int):
    """Gets the channel link for a given Discord server ID."""
    try:
        response = supabase.table('discord_servers').select('*').eq('server_id', server_id).single().execute()
        return response.data
    except Exception as e:
        log.error(f"Error getting discord server link for server {server_id}: {e}")
        return None
    
def get_channel_by_id(channel_id: int):
    """Gets a channel by its ID."""
    try:
        response = supabase.table('channels').select('*').eq('id', channel_id).single().execute()
        return response.data
    except Exception as e:
        log.error(f"Error getting channel by id {channel_id}: {e}")
        return None

def get_discord_bot(bot_token: str):
    """Gets a Discord bot's data from the database."""
    try:
        response = supabase.table('discord_bots').select('*').eq('bot_token', bot_token).single().execute()
        return response.data
    except Exception as e:
        log.error(f"Error getting discord bot: {e}")
        return None

def activate_discord_bot(bot_token: str, server_id: int):
    """Activates a Discord bot for a specific server."""
    try:
        response = supabase.table('discord_bots').update({'discord_server_id': server_id, 'is_active': True}).eq('bot_token', bot_token).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        log.error(f"Error activating discord bot: {e}")
        return None

def create_discord_bot(bot_data: dict):
    """Creates a new Discord bot entry in the database."""
    try:
        response = supabase.table('discord_bots').insert(bot_data).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        log.error(f"Error creating discord bot: {e}")
        return None

def find_app_user_by_discord_id(discord_id: int):
    """Finds a YoppyChat user profile by their linked Discord user ID."""
    try:
        response = supabase.table('profiles').select('id').eq('discord_user_id', discord_id).single().execute()
        return response.data
    except Exception as e:
        log.error(f"Error finding app user by discord_id {discord_id}: {e}")
        return None
    
def get_user_channels_for_discord(user_id: str):
    """Fetches all channels linked to a user for Discord autocomplete."""
    try:
        # This query joins the user_channels and channels tables to get
        # the channel ID and name for a specific user.
        response = supabase.table('user_channels').select('channels(id, channel_name)').eq('user_id', user_id).execute()
        if response.data:
            # The result is a list of {'channels': {'id': 1, 'channel_name': 'Name'}}
            # We flatten it to a cleaner list of dictionaries.
            return [item['channels'] for item in response.data if item.get('channels')]
        return []
    except Exception as e:
        log.error(f"Error fetching user channels for discord for user {user_id}: {e}")
        return []
    
def get_user_channels_by_discord_id(discord_id: int):
    """
    Fetches a user's channels directly using their Discord ID via an RPC call.
    This is much faster as it's a single database operation.
    """
    try:
        # The function we created in Supabase is called an RPC (Remote Procedure Call).
        # We now pass the discord_id directly as a number, without str().
        params = {'p_discord_id': discord_id} # <--- THIS IS THE ONLY CHANGE
        response = supabase.rpc('get_channels_by_discord_id', params).execute()
        return response.data if response.data else []
    except Exception as e:
        log.error(f"Error fetching channels via RPC for discord_id {discord_id}: {e}")
        return []
    
def link_discord_server_to_channel(server_id: int, channel_id: int, user_id: str):
    """Creates or updates the link between a server and a channel."""
    try:
        # Upsert ensures that if a server re-links, it just updates the record
        response = supabase.table('discord_servers').upsert({
            'server_id': server_id,
            'linked_channel_id': channel_id,
            'owner_user_id': user_id
        }, on_conflict='server_id').execute()
        return response.data
    except Exception as e:
        log.error(f"Error linking server {server_id} to channel {channel_id}: {e}")
        return None
    

def delete_discord_bot_for_user(bot_id: int, user_id: str):
    """Deletes a discord bot record, ensuring it belongs to the specified user."""
    try:
        response = supabase.table('discord_bots').delete().eq('id', bot_id).eq('user_id', user_id).execute()
        # If the response contains data, it means a record was successfully found and deleted.
        if response.data:
            log.info(f"User {user_id} successfully deleted bot {bot_id}")
            return True
        else:
            log.warning(f"User {user_id} attempted to delete bot {bot_id}, but no matching record was found.")
            return False
    except Exception as e:
        log.error(f"Error deleting discord bot {bot_id} for user {user_id}: {e}")
        return False
    
def check_bot_query_allowed(user_id: str, channel_data: dict = None, active_community_id: str = None, google_review_settings_id: str = None):
    """
    Checks if a bot/integration query is allowed based on the user's plan limits.
    This mirrors the limit_enforcer decorator logic but works outside Flask request context.
    
    Returns (allowed: bool, error_message: str or None, resolved_community_id: str or None, seller_id_to_charge: str or False)
    
    If seller_id_to_charge is string, it's a marketplace query and we must deduct from the seller's personal pool.
    If it's False, it's a standard query or fallback, and we must deduct from the buyer's personal/community pool.
    """
    from .subscription_utils import get_user_status, get_community_status

    try:
        # Auto-detect community context from channel data if not provided
        if not active_community_id and channel_data:
            active_community_id = channel_data.get('community_id')

        # --- Check marketplace transfer FIRST ---
        transfer = None
        try:
            if channel_data and channel_data.get('id'):
                transfer_res = supabase.table('chatbot_transfers').select(
                    'id, query_limit_monthly, queries_used_this_month, creator_id'
                ).eq('chatbot_id', channel_data['id']).eq('buyer_id', user_id).eq('status', 'active').maybe_single().execute()
                if transfer_res and transfer_res.data:
                    transfer = transfer_res.data
            elif google_review_settings_id:
                transfer_res = supabase.table('chatbot_transfers').select(
                    'id, query_limit_monthly, queries_used_this_month, creator_id'
                ).eq('google_review_id', google_review_settings_id).eq('buyer_id', user_id).eq('status', 'active').maybe_single().execute()
                if transfer_res and transfer_res.data:
                    transfer = transfer_res.data
            
            if transfer:
                if transfer['queries_used_this_month'] < transfer['query_limit_monthly']:
                    # Phase 1: Credits are ALREADY deducted from the seller's total pool upfront.
                    # As long as the transfer has capacity, allow it.
                    supabase.rpc('increment_marketplace_query', {'p_transfer_id': transfer['id']}).execute()
                    # Return True indicating success, and 'skip_charge' as 4th arg so no global usage is incremented for anyone
                    return True, None, active_community_id, 'skip_charge'
                # Phase 2: If we reach here, allocation exhausted. Fallback to buyer!
        except Exception as e:
            log.warning(f"Could not check marketplace limits: {e}")

        # --- Phase 2 / Standard check — check buyer's personal/community limits ---
        user_status = get_user_status(user_id, active_community_id)
        if not user_status:
            return True, None, active_community_id, False  # Fail open if status unavailable

        # Check community owner trial first
        if user_status.get('is_active_community_owner') and active_community_id:
            community_status = get_community_status(active_community_id)
            if community_status:
                trial_limit = community_status['limits'].get('owner_trial_limit', 0)
                trial_used = community_status['usage'].get('trial_queries_used', 0)
                if trial_used < trial_limit:
                    return True, None, active_community_id, False

        # Check personal plan
        if user_status.get('has_personal_plan'):
            max_queries = user_status['limits'].get('max_queries_per_month', 0)
            queries_used = user_status['usage'].get('queries_this_month', 0)
            if max_queries != float('inf') and queries_used >= max_queries:
                return False, f"You've reached your monthly credit limit of {int(max_queries)}.", active_community_id, False
        elif active_community_id:
            # Community member — check shared pool
            community_status = get_community_status(active_community_id)
            if community_status:
                max_queries = community_status['limits'].get('query_limit', 0)
                queries_used = community_status['usage'].get('queries_used', 0)
                if max_queries != float('inf') and queries_used >= max_queries:
                    return False, "The community's shared credit limit has been reached.", active_community_id, False
        else:
            # Free user / no community
            max_queries = user_status['limits'].get('max_queries_per_month', 0)
            queries_used = user_status['usage'].get('queries_this_month', 0)
            if max_queries != float('inf') and queries_used >= max_queries:
                return False, f"You've reached your monthly credit limit of {int(max_queries)}.", active_community_id, False

        return True, None, active_community_id, False

    except Exception as e:
        log.error(f"Error checking bot query limits for user {user_id}: {e}")
        return True, None, active_community_id, False  # Fail open on error


def record_bot_query_usage(user_id: str, active_community_id: str = None):
    """
    Records a query for a bot interaction, deducting from the appropriate pool
    (personal + community if applicable) and invalidating the necessary cache.
    """
    try:
        log.info(f"Recording bot query for user {user_id} (community: {active_community_id or 'none'}).")
        # 1. Always deduct from personal pool
        increment_personal_query_usage(user_id)

        # 2. Also deduct from community pool if applicable
        if active_community_id:
            increment_community_query_usage(active_community_id, is_trial=False)

        # 3. Invalidate the user's status cache with correct community context
        from .subscription_utils import redis_client # Local import to avoid circular dependency
        if redis_client:
            user_cache_key = f"user_status:{user_id}:community:{active_community_id or 'none'}"
            redis_client.delete(user_cache_key)
            log.info(f"Invalidated cache key via bot usage: {user_cache_key}")
            # Also invalidate the community cache if applicable
            if active_community_id:
                community_cache_key = f"community_status:{active_community_id}"
                redis_client.delete(community_cache_key)
    except Exception as e:
        log.error(f"Failed to record bot query usage for user {user_id}: {e}")

def create_channel(channel_url: str, user_id: str, is_shared: bool = False, community_id: str = None):
    """Adds a new channel to the master list with a 'pending' status."""
    try:
        channel_payload = {
            'channel_url': channel_url,
            'creator_id': user_id, # Changed from 'user_id' to 'creator_id'
            'status': 'pending',
            'is_shared': is_shared,
            'community_id': community_id,
            'has_youtube': True  # Required by constraint
        }
        response = supabase.table('channels').insert(channel_payload).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        log.error(f"Error creating channel for URL {channel_url}: {e}")
        return None
    
def get_channels_created_by_user(user_id: str):
    """Fetches all channels where the given user is the creator."""
    # --- PERFORMANCE: Return from memory cache if available ---
    cache_key = f"creator_channels:{user_id}"
    if cache_key in _creator_channels_cache:
        return _creator_channels_cache[cache_key]

    try:
        # This query specifically selects channels where the creator_id matches the user's ID
        response = supabase.table('channels').select('*').eq('creator_id', user_id).execute()
        result = {ch['channel_name']: ch for ch in response.data if ch.get('channel_name')} if response.data else {}
        # Store in cache for 60 seconds
        _creator_channels_cache[cache_key] = result
        return result
    except Exception as e:
        log.error(f"Error getting creator channels for user {user_id}: {e}")
        return {}
    
def get_creator_dashboard_stats(creator_user_id: str):
    """
    Gathers key statistics for a creator's channels, including total referrals,
    paid referrals, MRR, and current user adds.
    """
    cache_key = f"dashboard_stats:{creator_user_id}"
    if cache_key in _dashboard_stats_cache:
        return _dashboard_stats_cache[cache_key]

    supabase = get_supabase_admin_client()
    stats = {}
    
    from .subscription_utils import PLANS

    try:
        # 1. Get all channels created by this user
        creator_channels_res = supabase.table('channels').select('id').eq('creator_id', creator_user_id).execute()
        if not (creator_channels_res.data):
            return {}

        channel_ids = [c['id'] for c in creator_channels_res.data]
        for cid in channel_ids:
            stats[cid] = {
                'referrals': 0, 
                'paid_referrals': 0, # <-- Initialize new stat
                'creator_mrr': 0.0, 
                'current_adds': 0, 
                'referred_user_plans': []
            }

        # 2. Get the "Current Adds" for each channel
        if channel_ids:
            params = {'p_channel_ids': channel_ids}
            current_adds_res = supabase.rpc('get_channel_add_counts', params).execute()
            if current_adds_res.data:
                for row in current_adds_res.data:
                    if row['channel_id'] in stats:
                        stats[row['channel_id']]['current_adds'] = row['add_count']

        # 3. Get all referred users and their plans
        referred_users_res = supabase.table('profiles').select('referred_by_channel_id, direct_subscription_plan').in_('referred_by_channel_id', channel_ids).execute()
        if referred_users_res.data:
            for user in referred_users_res.data:
                channel_id = user['referred_by_channel_id']
                if channel_id in stats:
                    stats[channel_id]['referrals'] += 1
                    
                    # --- START: THE FIX ---
                    # Check if the user has a subscription plan that is not 'free'
                    plan = user.get('direct_subscription_plan')
                    if plan and plan != 'free':
                        stats[channel_id]['paid_referrals'] += 1
                        stats[channel_id]['referred_user_plans'].append(plan)
                    # --- END: THE FIX ---
        
        # 4. Calculate MRR
        for channel_id, channel_stats in stats.items():
            mrr = 0
            for plan_id in channel_stats['referred_user_plans']:
                plan_details = PLANS.get(plan_id, {})
                price = plan_details.get('price_usd', 0)
                commission_rate = plan_details.get('commission_rate', 0)
                mrr += price * commission_rate
            channel_stats['creator_mrr'] = round(mrr, 2)
            del channel_stats['referred_user_plans']

        _dashboard_stats_cache[cache_key] = stats
        return stats

    except Exception as e:
        log.error(f"Error getting creator dashboard stats for user {creator_user_id}: {e}")
        return {}
def record_creator_earning(referred_user_id: str, plan_id: str):
    """
    Records a commission earning for a creator when their referred user subscribes.
    """
    from .subscription_utils import PLANS
    supabase = get_supabase_admin_client()
    try:
        # Find who referred this user
        profile_res = supabase.table('profiles').select('referred_by_channel_id').eq('id', referred_user_id).single().execute()
        if not (profile_res.data and profile_res.data.get('referred_by_channel_id')):
            log.info(f"User {referred_user_id} was not referred. No commission recorded.")
            return

        channel_id = profile_res.data['referred_by_channel_id']

        # Find the creator of that channel
        channel_res = supabase.table('channels').select('creator_id').eq('id', channel_id).single().execute()
        if not (channel_res.data and channel_res.data.get('creator_id')):
            return

        creator_id = channel_res.data['creator_id']

        # Calculate the commission
        plan_details = PLANS.get(plan_id, {})
        price = plan_details.get('price_usd', 0)
        commission_rate = plan_details.get('commission_rate', 0)
        earning_amount = round(price * commission_rate, 2)

        if earning_amount > 0:
            supabase.table('creator_earnings').insert({
                'creator_id': creator_id,
                'referred_user_id': referred_user_id,
                'channel_id': channel_id,
                'amount_usd': earning_amount,
                'plan_id': plan_id
            }).execute()
            log.info(f"Recorded ${earning_amount} earning for creator {creator_id} from referred user {referred_user_id}.")

    except Exception as e:
        log.error(f"Error recording creator earning for referred user {referred_user_id}: {e}")

def get_creator_balance_and_history(creator_id: str):
    """
    Refactored to correctly calculate balances based on earnings and payout statuses.
    """
    supabase = get_supabase_admin_client()
    try:
        # 1. Calculate the total amount the creator has ever earned.
        earnings_res = supabase.table('creator_earnings').select('amount_usd').eq('creator_id', creator_id).execute()
        total_earned = sum(item['amount_usd'] for item in earnings_res.data) if earnings_res.data else 0.0

        # 2. Get all payouts and categorize them by status.
        history_res = supabase.table('creator_payouts').select('*').eq('creator_id', creator_id).order('requested_at', desc=True).execute()
        history = history_res.data or []
        
        pending_payouts = sum(p['amount_usd'] for p in history if p['status'] in ['pending', 'processing'])
        total_paid = sum(p['amount_usd'] for p in history if p['status'] == 'paid')

        # 3. The withdrawable balance is what's left over.
        withdrawable_balance = total_earned - pending_payouts - total_paid
        
        return {
            'withdrawable_balance': round(withdrawable_balance, 2),
            'pending_payouts': round(pending_payouts, 2),
            'total_earned': round(total_earned, 2),
            'history': history
        }

    except Exception as e:
        log.error(f"Error getting creator balance for {creator_id}: {e}")
        return {'withdrawable_balance': 0.0, 'pending_payouts': 0.0, 'total_earned': 0.0, 'history': []}

def get_monthly_revenue_history(creator_id: str, months_back: int = 6):
    """
    Fetches the monthly revenue for the last N months, split by affiliate and marketplace.
    Returns data formatted for easy use in Chart.js.
    """
    cache_key = f"revenue_history:{creator_id}:{months_back}"
    if cache_key in _dashboard_stats_cache:
        return _dashboard_stats_cache[cache_key]

    supabase = get_supabase_admin_client()
    from dateutil.relativedelta import relativedelta
    
    try:
        # Initialize the last N months
        today = datetime.now()
        months = []
        
        # Build the zeroed-out data structure mapping month string (e.g. "Jan") to revenue
        for i in range(months_back - 1, -1, -1):
            dt = today - relativedelta(months=i)
            # Use 'YYYY-MM' for exact matching, then we will reformat labels for the chart
            month_key = dt.strftime('%Y-%m')
            label = dt.strftime('%b') # e.g., 'Jan', 'Feb'
            months.append({
                'key': month_key,
                'label': label,
                'affiliate': 0.0,
                'chatbot': 0.0
            })

        # --- Affiliate Earnings ---
        # Fetching all earnings without a strict date filter to make the logic robust, 
        # or we could filter by date >= N months ago. For simplicity, filtering in python.
        six_months_ago = today - relativedelta(months=months_back - 1)
        start_date_iso = six_months_ago.replace(day=1, hour=0, minute=0, second=0).isoformat()
        
        affiliate_res = supabase.table('creator_earnings') \
            .select('amount_usd, created_at') \
            .eq('creator_id', creator_id) \
            .gte('created_at', start_date_iso) \
            .execute()
            
        if affiliate_res.data:
            for earning in affiliate_res.data:
                # 'created_at' e.g. "2024-03-21T10:20:30"
                date_str = earning.get('created_at')
                if date_str:
                    e_month_key = date_str[:7] # Extract 'YYYY-MM'
                    for m in months:
                        if m['key'] == e_month_key:
                            m['affiliate'] += earning.get('amount_usd', 0.0)
                            break
                            
        # --- Marketplace Earnings ---
        marketplace_res = supabase.table('creator_marketplace_earnings') \
            .select('creator_amount, payment_date') \
            .eq('creator_id', creator_id) \
            .gte('payment_date', start_date_iso) \
            .execute()
            
        if marketplace_res.data:
            for earning in marketplace_res.data:
                date_str = earning.get('payment_date')
                if date_str:
                    e_month_key = date_str[:7]
                    for m in months:
                        if m['key'] == e_month_key:
                            # Marketplace amounts are in paise, convert to standard currency
                            m['chatbot'] += (earning.get('creator_amount', 0) / 100.0)
                            break

        # Format the output for Chart.js
        labels = [m['label'] for m in months]
        affiliate_data = [round(m['affiliate'], 2) for m in months]
        chatbot_data = [round(m['chatbot'], 2) for m in months]

        result = {
            'labels': labels,
            'datasets': {
                'affiliate': affiliate_data,
                'chatbot': chatbot_data
            }
        }
        _dashboard_stats_cache[cache_key] = result
        return result

    except Exception as e:
        log.error(f"Error getting monthly revenue history for {creator_id}: {e}")
        # Return empty structure if it fails
        return {'labels': [], 'datasets': {'affiliate': [], 'chatbot': []}}


def create_payout_request(creator_id: str, amount: float, payout_details: dict):
    """
    Creates a new payout request and stores a snapshot of the payout details.
    """
    supabase = get_supabase_admin_client()
    try:
        current_balances = get_creator_balance_and_history(creator_id)
        withdrawable_balance = current_balances.get('withdrawable_balance', 0.0)

        if amount > withdrawable_balance:
            return None, "Withdrawal amount cannot exceed your available balance."

        # --- START OF THE FIX ---
        # Save the provided payout_details into the new column for this specific request
        new_payout = supabase.table('creator_payouts').insert({
            'creator_id': creator_id,
            'amount_usd': amount,
            'status': 'pending',
            'payout_destination_details': payout_details 
        }).execute().data[0]
        # --- END OF THE FIX ---

        return new_payout, "Payout requested successfully. It will be reviewed by an admin."
    except Exception as e:
        log.error(f"Error creating payout request for creator {creator_id}: {e}")
        return None, "An internal error occurred."
    
def get_user_by_razorpay_customer_id(customer_id: str):
    """
    Finds a user by their Razorpay customer ID.
    """
    try:
        response = supabase.table('profiles').select('id').eq('razorpay_customer_id', customer_id).single().execute()
        return response.data['id'] if response.data else None
    except Exception as e:
        log.error(f"Error getting user by Razorpay customer ID: {e}")
        return None

def update_razorpay_subscription(user_id: str, subscription_data: dict):
    """
    Updates the user's subscription details in the database.
    """
    try:
        # --- START OF FIX ---
        # Get the timestamp values from the subscription data
        start_timestamp = subscription_data.get('current_start')
        end_timestamp = subscription_data.get('current_end')
        # Safely convert timestamps to ISO format only if they exist
        start_iso = datetime.fromtimestamp(start_timestamp).isoformat() if start_timestamp is not None else None
        end_iso = datetime.fromtimestamp(end_timestamp).isoformat() if end_timestamp is not None else None     
        # Prepare the data for upserting into the database
        upsert_payload = {
            'id': subscription_data.get('id'),
            'user_id': user_id,
            'plan_id': subscription_data.get('plan_id'),
            'status': subscription_data.get('status'),
            'current_start': start_iso,
            'current_end': end_iso,
        }
        # Upsert the data into the razorpay_subscriptions table
        supabase.table('razorpay_subscriptions').upsert(upsert_payload, on_conflict='id').execute()
        # --- END OF FIX ---  
    except Exception as e:
        log.error(f"Error updating Razorpay subscription for user {user_id}: {e}")

def update_payout_status(payout_id: str, status: str):
    """
    Updates the status of a payout request.
    """
    try:
        supabase.table('creator_payouts').update({'status': status}).eq('id', payout_id).execute()
    except Exception as e:

        log.error(f"Error updating payout status for payout {payout_id}: {e}")

def get_platform_cashflow_stats():
    """
    Gathers comprehensive cashflow statistics across the entire platform
    for the admin dashboard, supporting both USD and INR.
    """
    supabase = get_supabase_admin_client()
    exchange_rate = 83.5 # Fixed conversion rate for representation
    
    stats_usd = {
        'total_affiliate_generated': 0.0,
        'marketplace_gross': 0.0,
        'marketplace_platform_fees': 0.0,
        'pending_payouts': 0.0,
        'total_paid_out': 0.0,
        'subscription_mrr': 0.0,
        'platform_net_profit': 0.0
    }
    
    stats_inr = {
        'total_affiliate_generated': 0.0,
        'marketplace_gross': 0.0,
        'marketplace_platform_fees': 0.0,
        'pending_payouts': 0.0,
        'total_paid_out': 0.0,
        'subscription_mrr': 0.0,
        'platform_net_profit': 0.0
    }
    
    try:
        # 1. Total Affiliate Earnings Generated (Stored in USD)
        ce_res = supabase.table('creator_earnings').select('amount_usd').execute()
        if ce_res.data:
            usd_amount = sum(item.get('amount_usd', 0) for item in ce_res.data)
            stats_usd['total_affiliate_generated'] = usd_amount
            stats_inr['total_affiliate_generated'] = usd_amount * exchange_rate

        # 2. Marketplace Earnings (Stored in Paise - INR)
        me_res = supabase.table('creator_marketplace_earnings').select('gross_amount, platform_fee, creator_amount').execute()
        if me_res.data:
            inr_gross = sum(item.get('gross_amount', 0) for item in me_res.data) / 100.0
            inr_fees = sum(item.get('platform_fee', 0) for item in me_res.data) / 100.0
            
            stats_inr['marketplace_gross'] = inr_gross
            stats_inr['marketplace_platform_fees'] = inr_fees
            stats_usd['marketplace_gross'] = inr_gross / exchange_rate
            stats_usd['marketplace_platform_fees'] = inr_fees / exchange_rate

        # 3. Payouts (Stored in USD)
        payouts_res = supabase.table('creator_payouts').select('amount_usd, status').execute()
        if payouts_res.data:
            pending_usd = sum(item.get('amount_usd', 0) for item in payouts_res.data if item.get('status') in ['pending', 'processing'])
            paid_usd = sum(item.get('amount_usd', 0) for item in payouts_res.data if item.get('status') == 'paid')
            
            stats_usd['pending_payouts'] = pending_usd
            stats_usd['total_paid_out'] = paid_usd
            stats_inr['pending_payouts'] = pending_usd * exchange_rate
            stats_inr['total_paid_out'] = paid_usd * exchange_rate

        # 4. Direct Subscription Net MRR (Aligned directly with the application's actual Profile Access Grants)
        from .subscription_utils import PLANS
        mrr_usd = 0.0
        gross_usd = 0.0
        commission_usd = 0.0
        
        # Scour all user profiles for active platform allocations
        profiles_res = supabase.table('profiles').select('id, direct_subscription_plan, personal_plan_id, referred_by_channel_id').execute()
        if profiles_res.data:
            for p in profiles_res.data:
                # Resolve the active effective plan exactly as `get_user_status` does
                active_plan_id = p.get('personal_plan_id') or p.get('direct_subscription_plan')
                
                # Check for active paying plans mapped into our architecture
                if active_plan_id and active_plan_id != 'free':
                    plan = PLANS.get(active_plan_id)
                    if plan:
                        base_price = plan.get('price_usd', 0)
                        gross_usd += base_price
                        
                        # Process platform referrals securely
                        if p.get('referred_by_channel_id'):
                            commission_rate = plan.get('commission_rate', 0)
                            comm_amt = base_price * commission_rate
                            base_price -= comm_amt
                            commission_usd += comm_amt
                            
                        mrr_usd += base_price
            
            stats_usd['subscription_mrr'] = mrr_usd
            stats_inr['subscription_mrr'] = mrr_usd * exchange_rate
            
            stats_usd['subscription_gross'] = gross_usd
            stats_inr['subscription_gross'] = gross_usd * exchange_rate
            
            stats_usd['subscription_commission'] = commission_usd
            stats_inr['subscription_commission'] = commission_usd * exchange_rate

        # 5. Total Platform Profit Estimation
        stats_usd['platform_net_profit'] = stats_usd['subscription_mrr'] + stats_usd['marketplace_platform_fees'] - stats_usd['total_affiliate_generated'] - stats_usd['total_paid_out']
        stats_inr['platform_net_profit'] = stats_inr['subscription_mrr'] + stats_inr['marketplace_platform_fees'] - stats_inr['total_affiliate_generated'] - stats_inr['total_paid_out']

        # Format everything to 2 decimal places
        for key in stats_usd:
            stats_usd[key] = round(stats_usd[key], 2)
            stats_inr[key] = round(stats_inr[key], 2)
            
        return {'usd': stats_usd, 'inr': stats_inr}
    except Exception as e:
        log.error(f"Error getting platform cashflow stats: {e}")
        return {'usd': stats_usd, 'inr': stats_inr}


def get_admin_activity_feed(limit=20):
    """
    Fetches recent platform activity events for the admin dashboard.
    Pulls from profiles (signups), channels (chatbot creations), and
    chatbot_transfers (marketplace transfers).
    """
    supabase = get_supabase_admin_client()
    events = []

    try:
        # 1. Recent Signups
        signups_res = supabase.table('profiles').select(
            'email, full_name, created_at, whop_user_id'
        ).order('created_at', desc=True).limit(limit).execute()
        if signups_res.data:
            for u in signups_res.data:
                user_type = 'Whop' if u.get('whop_user_id') else 'Direct'
                events.append({
                    'type': 'signup',
                    'icon': '👤',
                    'title': f"New {user_type} user signed up",
                    'description': u.get('email', 'Unknown'),
                    'timestamp': u.get('created_at', '')
                })

        # 2. Recent Chatbot Creations
        chatbots_res = supabase.table('channels').select(
            'channel_name, created_at, creator_id, creator:creator_id(full_name, email)'
        ).order('created_at', desc=True).limit(limit).execute()
        if chatbots_res.data:
            for ch in chatbots_res.data:
                creator_info = ch.get('creator', {}) or {}
                creator_name = creator_info.get('full_name') or creator_info.get('email') or 'Unknown'
                events.append({
                    'type': 'chatbot',
                    'icon': '🤖',
                    'title': f"Chatbot created: '{ch.get('channel_name', 'Unnamed')}'",
                    'description': f"by {creator_name}",
                    'timestamp': ch.get('created_at', '')
                })

        # 3. Recent Marketplace Transfers (Actual Transactions)
        try:
            # Get real transactions from creator_marketplace_earnings with buyer info
            earnings_res = supabase.table('creator_marketplace_earnings').select(
                'id, transfer_id, payment_date, gross_amount, creator_amount, transfer:transfer_id(buyer:buyer_id(email), creator:creator_id(full_name, email))'
            ).order('payment_date', desc=True).limit(limit).execute()
            if earnings_res.data:
                for e in earnings_res.data:
                    transfer_info = (e.get('transfer') or {}) if isinstance(e.get('transfer'), dict) else {}
                    buyer_info = (transfer_info.get('buyer') or {}) if isinstance(transfer_info.get('buyer'), dict) else {}
                    creator_info = (transfer_info.get('creator') or {}) if isinstance(transfer_info.get('creator'), dict) else {}
                    
                    buyer_email = buyer_info.get('email', 'Unknown')
                    creator_name = creator_info.get('full_name') or creator_info.get('email') or 'Unknown'
                    
                    # Amounts are in paise, convert to INR
                    buyer_paid = (e.get('gross_amount') or 0) / 100.0
                    creator_earned = (e.get('creator_amount') or 0) / 100.0

                    events.append({
                        'type': 'transfer',
                        'icon': '💰',
                        'title': f"Marketplace purchase: {creator_name}'s chatbot",
                        'description': f"{buyer_email} paid ₹{buyer_paid:.2f} (creator earned ₹{creator_earned:.2f})",
                        'timestamp': e.get('payment_date', '')
                    })
        except Exception as te:
            log.warning(f"Could not fetch transfers for activity feed: {te}")

        # Sort all events by timestamp descending and return top N
        events.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        return events[:limit]

    except Exception as e:
        log.error(f"Error getting admin activity feed: {e}")
        return []


def get_admin_trend_data(days_back=30):
    """
    Fetches admin trend data for the past N days for Chart.js:
    - Revenue Over Time (daily subscription + marketplace)
    - User Growth (Whop vs Direct new signups per day)
    - Credit Consumption (total daily queries across platform)
    """
    from datetime import timedelta, timezone as tz
    supabase = get_supabase_admin_client()

    today = datetime.now(tz.utc).date()
    start_date = today - timedelta(days=days_back)
    start_iso = datetime.combine(start_date, datetime.min.time()).isoformat()

    # Pre-fill all dates
    exchange_rate = 83.5  # USD to INR conversion rate
    date_labels = []
    date_keys = {}
    for i in range(days_back + 1):
        d = start_date + timedelta(days=i)
        key = d.isoformat()  # 'YYYY-MM-DD'
        label = d.strftime('%b %d')  # 'Apr 01'
        date_labels.append(label)
        date_keys[key] = {
            'revenue_subscription_usd': 0.0,
            'revenue_subscription_inr': 0.0,
            'revenue_platform_sub_inr': 0.0, # Added for true subscriptions
            'revenue_platform_sub_usd': 0.0, # Added for true subscriptions
            'revenue_marketplace_inr': 0.0,
            'revenue_marketplace_usd': 0.0,
            'users_referral': 0,
            'users_direct': 0,
            'users_paid': 0,
            'users_free': 0,
            'marketplace_buyers': set(),
            'marketplace_sellers': set(),
            'credits_used': 0
        }

    try:
        # --- 1. Revenue: Affiliate/Subscription earnings by day (in USD) ---
        earnings_res = supabase.table('creator_earnings').select(
            'amount_usd, created_at'
        ).gte('created_at', start_iso).execute()
        if earnings_res.data:
            for e in earnings_res.data:
                day_key = (e.get('created_at') or '')[:10]
                if day_key in date_keys:
                    usd_amt = e.get('amount_usd', 0)
                    date_keys[day_key]['revenue_subscription_usd'] += usd_amt
                    date_keys[day_key]['revenue_subscription_inr'] += usd_amt * exchange_rate

        # --- 2. Revenue: Marketplace platform fees by day (platform keeps as commission) ---
        try:
            mkt_res = supabase.table('creator_marketplace_earnings').select(
                'platform_fee, payment_date'
            ).gte('payment_date', start_iso).execute()
            if mkt_res.data:
                for m in mkt_res.data:
                    day_key = (m.get('payment_date') or '')[:10]
                    if day_key in date_keys:
                        inr_amt = m.get('platform_fee', 0) / 100.0  # Convert paise to INR
                        date_keys[day_key]['revenue_marketplace_inr'] += inr_amt
                        date_keys[day_key]['revenue_marketplace_usd'] += inr_amt / exchange_rate
        except Exception:
            pass

        # --- 3. User Growth: New signups per day (Referral, Direct, Paid, Free) ---
        from .subscription_utils import PLANS
        profiles_res = supabase.table('profiles').select(
            'created_at, referred_by_channel_id, direct_subscription_plan, whop_user_id'
        ).gte('created_at', start_iso).execute()
        if profiles_res.data:
            for p in profiles_res.data:
                day_key = (p.get('created_at') or '')[:10]
                if day_key in date_keys:
                    # Referral vs Direct
                    if p.get('referred_by_channel_id'):
                        date_keys[day_key]['users_referral'] += 1
                    else:
                        date_keys[day_key]['users_direct'] += 1
                    
                    # Paid vs Free
                    plan = p.get('direct_subscription_plan')
                    is_whop = bool(p.get('whop_user_id'))
                    if plan and plan.lower() != 'free' or is_whop:
                        date_keys[day_key]['users_paid'] += 1
                        
                        # Add Subscription exact price to the day's revenue
                        if plan and plan != 'free':
                            plan_config = PLANS.get(plan)
                            if plan_config:
                                price_this_sub_usd = plan_config.get('price_usd', 0)
                                date_keys[day_key]['revenue_platform_sub_usd'] += price_this_sub_usd
                                date_keys[day_key]['revenue_platform_sub_inr'] += price_this_sub_usd * exchange_rate
                    else:
                        date_keys[day_key]['users_free'] += 1

        # --- 4. Credit Consumption & Marketplace Users ---
        try:
            # Get channels created per day as a proxy for platform activity/credit usage
            channels_res = supabase.table('channels').select(
                'created_at'
            ).gte('created_at', start_iso).execute()
            if channels_res.data:
                for ch in channels_res.data:
                    day_key = (ch.get('created_at') or '')[:10]
                    if day_key in date_keys:
                        # Each chatbot creation ~ average 15-25 credits for processing
                        date_keys[day_key]['credits_used'] += 20

            # Also count transfers as credit activity and track buyers/sellers
            try:
                transfers_res = supabase.table('chatbot_transfers').select(
                    'created_at, buyer_system_id, buyer_email, seller_id'
                ).gte('created_at', start_iso).execute()
                if transfers_res.data:
                    for t in transfers_res.data:
                        day_key = (t.get('created_at') or '')[:10]
                        if day_key in date_keys:
                            date_keys[day_key]['credits_used'] += 5
                            buyer_id = t.get('buyer_system_id') or t.get('buyer_email')
                            seller_id = t.get('seller_id')
                            if buyer_id:
                                date_keys[day_key]['marketplace_buyers'].add(buyer_id)
                            if seller_id:
                                date_keys[day_key]['marketplace_sellers'].add(seller_id)
            except Exception:
                pass
        except Exception:
            pass

        # Build the output arrays
        ordered_keys = sorted(date_keys.keys())
        revenue_subscription_usd = [round(date_keys[k]['revenue_subscription_usd'], 2) for k in ordered_keys]
        revenue_subscription_inr = [round(date_keys[k]['revenue_subscription_inr'], 2) for k in ordered_keys]
        revenue_platform_sub_usd = [round(date_keys[k]['revenue_platform_sub_usd'], 2) for k in ordered_keys]
        revenue_platform_sub_inr = [round(date_keys[k]['revenue_platform_sub_inr'], 2) for k in ordered_keys]
        revenue_marketplace_usd = [round(date_keys[k]['revenue_marketplace_usd'], 2) for k in ordered_keys]
        revenue_marketplace_inr = [round(date_keys[k]['revenue_marketplace_inr'], 2) for k in ordered_keys]
        users_referral = [date_keys[k]['users_referral'] for k in ordered_keys]
        users_direct = [date_keys[k]['users_direct'] for k in ordered_keys]
        users_paid = [date_keys[k]['users_paid'] for k in ordered_keys]
        users_free = [date_keys[k]['users_free'] for k in ordered_keys]
        marketplace_buyers = [len(date_keys[k]['marketplace_buyers']) for k in ordered_keys]
        marketplace_sellers = [len(date_keys[k]['marketplace_sellers']) for k in ordered_keys]
        credits_used = [date_keys[k]['credits_used'] for k in ordered_keys]

        return {
            'labels': date_labels,
            'revenue': {
                'subscription_usd': revenue_subscription_usd,
                'subscription_inr': revenue_subscription_inr,
                'platform_sub_usd': revenue_platform_sub_usd,
                'platform_sub_inr': revenue_platform_sub_inr,
                'marketplace_usd': revenue_marketplace_usd,
                'marketplace_inr': revenue_marketplace_inr
            },
            'users': {
                'referral': users_referral,
                'direct': users_direct,
                'paid': users_paid,
                'free': users_free,
                'buyers': marketplace_buyers,
                'sellers': marketplace_sellers
            },
            'credits': credits_used
        }

    except Exception as e:
        log.error(f"Error getting admin trend data: {e}")
        return {
            'labels': date_labels,
            'revenue': {'subscription_usd': [], 'subscription_inr': [], 'marketplace_usd': [], 'marketplace_inr': []},
            'users': {'referral': [], 'direct': [], 'paid': [], 'free': [], 'buyers': [], 'sellers': []},
            'credits': []
        }

