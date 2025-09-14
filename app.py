import logging
from functools import wraps
from utils.youtube_utils import is_youtube_video_url, clean_youtube_url
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, Response
import os
import json
import secrets
from datetime import datetime, timezone
from tasks import huey, process_channel_task, sync_channel_task, process_telegram_update_task, delete_channel_task
from utils.qa_utils import answer_question_stream
from utils.supabase_client import get_supabase_client, get_supabase_admin_client, refresh_supabase_session
from utils.history_utils import get_chat_history
from utils.telegram_utils import set_webhook, get_bot_token_and_url
from utils.config_utils import load_config
from utils.subscription_utils import get_user_status, limit_enforcer, community_channel_limit_enforcer, get_community_status, admin_channel_limit_enforcer
from utils import db_utils
import time
import requests
import redis
from postgrest.exceptions import APIError
from markupsafe import Markup
import markdown
from huey.exceptions import TaskException
from dotenv import load_dotenv
from flask_compress import Compress
import jwt
import asyncio
from utils.discord_utils import update_bot_profile
from utils import whop_api
import hmac
import hashlib
import base64
from utils.subscription_utils import PLANS, COMMUNITY_PLANS
from datetime import datetime, timezone, timedelta
from dateutil.parser import isoparse
import uuid
from supabase_auth.errors import AuthApiError
logger = logging.getLogger(__name__)

load_dotenv()

app = Flask(__name__)

Compress(app)
app.secret_key = os.environ.get('SECRET_KEY', 'a_default_dev_secret_key')


@app.template_filter('markdown')
def markdown_filter(text):
    return Markup(markdown.markdown(text))

try:
    redis_client = redis.from_url(os.environ.get('REDIS_URL'))
except Exception:
    redis_client = None

@app.context_processor
def inject_user_status():
    if 'user' in session:
        user_id = session['user']['id']
        active_community_id = session.get('active_community_id')
        user_status = get_user_status(user_id, active_community_id)
        is_embedded = session.get('is_embedded_whop_user', False)

        community_status = None
        if active_community_id:
            community_status = get_community_status(active_community_id)

        return dict(
            user_status=user_status,
            user=session.get('user'),
            is_embedded_whop_user=is_embedded,
            community_status=community_status,
            saved_channels={}
        )
    return dict(user_status=None, user=None, is_embedded_whop_user=False, community_status=None)

def get_user_channels():
    """
    Return all channels visible to the logged-in user using a single RPC call.
    """
    if 'user' not in session:
        return {}

    user_id = session['user']['id']
    active_community_id = session.get('active_community_id')

    # Caching logic remains the same
    cache_key = f"user_visible_channels:{user_id}:community:{active_community_id or 'none'}"
    if redis_client:
        try:
            cached_data = redis_client.get(cache_key)
            if cached_data:
                return json.loads(cached_data)
        except Exception as e:
            logging.error(f"Redis GET error: {e}")

    supabase = get_supabase_admin_client()
    all_channels_list = []
    try:
        # Call the new RPC function
        params = {'p_user_id': user_id, 'p_community_id': active_community_id}
        response = supabase.rpc('get_visible_channels', params).execute()
        if response.data:
            all_channels_list = response.data

    except APIError as e:
        logging.error(f"Supabase RPC error in get_user_channels: {e.message}")
        if 'JWT expired' in e.message:
            session.clear()

    # Deduplicate and format the results
    all_channels = {ch['channel_name']: ch for ch in all_channels_list if ch.get('channel_name')}

    if redis_client and all_channels:
        # Increase cache time for better performance
        redis_client.setex(cache_key, 60, json.dumps(all_channels))

    return all_channels

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return jsonify({'status': 'error', 'message': 'Authentication required.'}), 401
        try:
            return f(*args, **kwargs)
        except APIError as e:
            if 'JWT' in e.message and 'expired' in e.message:
                session.clear()
                return jsonify({'status': 'error', 'message': 'Session expired.', 'action': 'logout'}), 401
            raise e
    return decorated_function

# --- NEW: Whop Embed Authentication Route ---
@app.route('/whop/embed-auth')
def whop_embed_auth():
    """
    Handles the authentication handshake from the Next.js embed app.
    Verifies the JWT and creates a user session.
    """
    token = request.args.get('token')
    jwt_secret = os.environ.get('JWT_SECRET')

    if not token or not jwt_secret:
        flash("Authentication failed: Invalid token provided.", "error")
        return redirect(url_for('home'))

    try:
        decoded_payload = jwt.decode(token, jwt_secret, algorithms=['HS256'])
        
        session.clear()

        supabase_admin = get_supabase_admin_client()
        email = decoded_payload.get('email')
        whop_user_id = decoded_payload.get('whop_user_id')
        whop_company_id = decoded_payload.get('whop_company_id')

        logging.info(f"[EMBED AUTH] Authenticating user: {email} from Whop Company ID: {whop_company_id}")

        list_of_users = supabase_admin.auth.admin.list_users()
        auth_user = next((u for u in list_of_users if u.email == email), None)

        if not auth_user:
            logging.info(f"[EMBED AUTH] No existing user for {email}. Creating new user.")
            auth_user = supabase_admin.auth.admin.create_user({
                'email': email, 'email_confirm': True, 'password': secrets.token_urlsafe(16)
            }).user

        app_user_id = str(auth_user.id)
        
        # Determine user role (assuming owner/admin for now, can be enhanced)
        # This part can be expanded if the JWT includes role information
        user_role = 'member' # Default role
        # A more robust check might involve an API call or JWT claim
        # For now, we'll rely on the is_community_owner flag in the profile
        
        profile_data = {
            'id': app_user_id,
            'whop_user_id': whop_user_id,
            'full_name': decoded_payload.get('name'),
            'avatar_url': decoded_payload.get('profile_pic_url'),
            'email': email,
        }
        
        profile = db_utils.create_or_update_profile(profile_data)
        if not profile:
            raise Exception(f"Failed to create or update profile for user {app_user_id}")
        
        db_utils.create_initial_usage_stats(app_user_id)

        community_res = supabase_admin.table('communities').select('id, owner_user_id').eq('whop_community_id', whop_company_id).maybe_single().execute()
        
        community_id = None
        if community_res.data:
            community_id = community_res.data['id']
            # If the user logging in is the owner of this community, update their profile
            if str(community_res.data['owner_user_id']) == app_user_id:
                supabase_admin.table('profiles').update({'is_community_owner': True}).eq('id', app_user_id).execute()
        else:
            # If the community doesn't exist, it means an admin hasn't installed the app yet.
            flash("This community has not been set up yet. An admin must install the app first.", "error")
            return redirect(url_for('home'))

        session['user'] = auth_user.model_dump()
        session['active_community_id'] = community_id
        session['is_embedded_whop_user'] = True

        db_utils.link_user_to_community(app_user_id, community_id)
        
        flash("Welcome! You've been securely logged in.", 'success')
        return redirect(url_for('channel'))

    except jwt.ExpiredSignatureError:
        flash("Your authentication link has expired. Please try again.", "error")
        return redirect(url_for('home'))
    except jwt.InvalidTokenError:
        flash("Invalid authentication token.", "error")
        return redirect(url_for('home'))
    except Exception as e:
        logging.error(f"An error occurred during Whop embed auth: {e}", exc_info=True)
        flash("An unexpected error occurred during login.", "error")
        return redirect(url_for('home'))


@app.route('/auth/whop/installation-callback')
def whop_installation_callback():
    token = request.args.get('token')
    jwt_secret = os.environ.get('JWT_SECRET')
    if not token or not jwt_secret:
        flash('Installation failed: Invalid token.', 'error')
        return redirect(url_for('home'))

    try:
        decoded_payload = jwt.decode(token, jwt_secret, algorithms=['HS256'])
        owner_email = decoded_payload['owner_email']
        whop_community_id = decoded_payload['whop_community_id']

        supabase_admin = get_supabase_admin_client()
        list_of_users = supabase_admin.auth.admin.list_users()
        auth_user = next((u for u in list_of_users if u.email == owner_email), None)

        if not auth_user:
            new_user_res = supabase_admin.auth.admin.create_user({'email': owner_email, 'email_confirm': True, 'password': secrets.token_urlsafe(16)})
            auth_user = new_user_res.user

        app_user_id = str(auth_user.id)

        community_data = {
            'whop_community_id': whop_community_id,
            'owner_user_id': app_user_id
        }
        community = db_utils.add_community(community_data)
        if not community:
            flash('Failed to create community record.', 'error')
            return redirect(url_for('home'))

        profile_data = {
            'id': app_user_id,
            'whop_user_id': decoded_payload.get('owner_user_id'),
            'full_name': decoded_payload.get('owner_full_name'),
            'avatar_url': decoded_payload.get('owner_avatar_url'),
            'email': owner_email,
            'is_community_owner': True,
            'community_id': community['id']
        }
        db_utils.create_or_update_profile(profile_data)
        db_utils.create_initial_usage_stats(app_user_id)

        session['user'] = auth_user.model_dump()
        flash('Your community has been successfully installed! You have 10 free queries to test out the bot.', 'success')
        return redirect(url_for('channel'))

    except Exception as e:
        logging.error(f"An error occurred during Whop installation callback: {e}", exc_info=True)
        flash('An unexpected error occurred during installation.', 'error')
        return redirect(url_for('home'))


# --- Whop Webhooks (No changes needed here) ---
def validate_whop_webhook(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        signature_header = request.headers.get('X-Whop-Signature-SHA256')
        if not signature_header:
            logging.warning("Webhook received without signature header.")
            return jsonify({'status': 'error', 'message': 'Missing signature'}), 401

        secret = os.environ.get('WHOP_WEBHOOK_SECRET')
        if not secret:
            logging.error("WHOP_WEBHOOK_SECRET is not configured. Cannot validate webhook.")
            return jsonify({'status': 'error', 'message': 'Server configuration error'}), 500

        request_body = request.get_data()

        try:
            digest = hmac.new(secret.encode('utf-8'), request_body, digestmod=hashlib.sha256).digest()
            computed_hmac = base64.b64encode(digest)
        except Exception:
            return jsonify({'status': 'error', 'message': 'Internal server error during validation'}), 500

        if not hmac.compare_digest(computed_hmac, signature_header.encode('utf-8')):
            logging.warning("Invalid webhook signature.")
            return jsonify({'status': 'error', 'message': 'Invalid signature'}), 403

        return f(*args, **kwargs)
    return decorated_function

# --- All webhook routes remain unchanged ---
@app.route('/whop/webhook/membership-update', methods=['POST'])
@validate_whop_webhook
def whop_membership_update_webhook():
    payload = request.get_json()
    logging.info(f"Received Whop membership webhook: {payload}")
    data = payload.get('data', {})
    whop_user_id = data.get('whop_user_id')
    new_plan_id = data.get('new_plan_id')
    if not whop_user_id or not new_plan_id:
        return jsonify({'status': 'error', 'message': 'Missing required fields in payload'}), 400
    if new_plan_id not in PLANS:
        return jsonify({'status': 'error', 'message': 'Unrecognized plan_id'}), 400
    try:
        supabase_admin = get_supabase_admin_client()
        profile_res = supabase_admin.table('profiles').select('id').eq('whop_user_id', whop_user_id).maybe_single().execute()
        if not profile_res.data:
            return jsonify({'status': 'not_found', 'message': 'User not found'}), 200
        app_user_id = profile_res.data['id']
        supabase_admin.table('profiles').update({'personal_plan_id': new_plan_id}).eq('id', app_user_id).execute()
        if redis_client:
            user_communities_res = supabase_admin.table('user_communities').select('community_id').eq('user_id', app_user_id).execute()
            if user_communities_res.data:
                for item in user_communities_res.data:
                    redis_client.delete(f"user_status:{app_user_id}:community:{item['community_id']}")
            redis_client.delete(f"user_status:{app_user_id}:community:none")
        return jsonify({'status': 'success'})
    except Exception as e:
        logging.error(f"Error processing membership webhook for {whop_user_id}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': 'An internal server error occurred.'}), 500

@app.route('/whop/webhook/community-update', methods=['POST'])
@validate_whop_webhook
def whop_community_update_webhook():
    payload = request.get_json()
    data = payload.get('data', {})
    whop_community_id = data.get('whop_community_id')
    member_count = data.get('member_count')
    if not whop_community_id or member_count is None:
        return jsonify({'status': 'error', 'message': 'Missing required fields'}), 400
    try:
        new_query_limit = int(member_count) * 100
        supabase_admin = get_supabase_admin_client()
        update_res = supabase_admin.table('communities').update({'query_limit': new_query_limit}).eq('whop_community_id', whop_community_id).execute()
        if not update_res.data:
             return jsonify({'status': 'not_found', 'message': 'Community not found'}), 200
        return jsonify({'status': 'success'})
    except (ValueError, TypeError):
        return jsonify({'status': 'error', 'message': 'Invalid member_count value'}), 400
    except Exception as e:
        logging.error(f"Error processing community webhook for {whop_community_id}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': 'An internal server error occurred.'}), 500

@app.route('/whop/webhook/community-plan-update', methods=['POST'])
@validate_whop_webhook
def whop_community_plan_update_webhook():
    payload = request.get_json()
    data = payload.get('data', {})
    whop_community_id = data.get('whop_community_id')
    new_plan_id = data.get('new_plan_id')
    if not whop_community_id or not new_plan_id:
        return jsonify({'status': 'error', 'message': 'Missing required fields'}), 400
    new_plan_details = COMMUNITY_PLANS.get(new_plan_id)
    if not new_plan_details:
        return jsonify({'status': 'error', 'message': f'Invalid plan_id: {new_plan_id}'}), 400
    try:
        supabase_admin = get_supabase_admin_client()
        community_update = {
            'plan_id': new_plan_id,
            'shared_channel_limit': new_plan_details['shared_channels_allowed']
        }
        update_res = supabase_admin.table('communities').update(community_update).eq('whop_community_id', whop_community_id).execute()
        if not update_res.data:
             return jsonify({'status': 'not_found', 'message': 'Community not found'}), 200
        if redis_client:
            redis_client.delete(f"community_status:{update_res.data[0]['id']}")
        return jsonify({'status': 'success'})
    except Exception as e:
        logging.error(f"Error processing community plan update for {whop_community_id}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': 'An internal server error occurred.'}), 500

# --- Standard Auth and App Routes (Largely unchanged) ---
@app.before_request
def check_token_expiry():
    if 'user' in session and session.get('expires_at') and session.get('refresh_token'):
        if session['expires_at'] < (time.time() + 60):
            try:
                new_session = refresh_supabase_session(session.get('refresh_token'))
                if new_session:
                    session['access_token'] = new_session.get('access_token')
                    session['refresh_token'] = new_session.get('refresh_token')
                    session['expires_at'] = new_session.get('expires_at')
                else:
                    # If refresh fails for a generic reason, clear the session
                    session.clear()
            except AuthApiError as e:
                # If the token was already used, it's a sign of a stale session.
                # Clear the session to force a fresh login.
                logger.warning(f"Handled an AuthApiError during token refresh: {e}. Clearing session.")
                session.clear()

@app.route('/auth/callback')
def auth_callback():
    return render_template('callback.html', SUPABASE_URL=os.environ.get('SUPABASE_URL'), SUPABASE_ANON_KEY=os.environ.get('SUPABASE_ANON_KEY'))

@app.route('/auth/set-cookie', methods=['POST'])
def set_auth_cookie():
    try:
        data = request.get_json()
        access_token = data.get('access_token')
        refresh_token = data.get('refresh_token')
        expires_at = data.get('expires_at')
        if not all([access_token, refresh_token, expires_at]):
            return jsonify({'status': 'error', 'message': 'Incomplete session data provided.'}), 400

        supabase = get_supabase_client()
        if not supabase:
            return jsonify({'status': 'error', 'message': 'Server configuration error.'}), 500

        supabase.auth.set_session(access_token, refresh_token)
        user_response = supabase.auth.get_user()

        if not user_response or not hasattr(user_response, 'user') or not user_response.user:
            return jsonify({'status': 'error', 'message': 'Invalid authentication token.'}), 401

        user = user_response.user
        profile = db_utils.get_profile(user.id)

        profile_payload = {
            'id': user.id,
            'email': user.email,
            'full_name': user.user_metadata.get('full_name'),
            'avatar_url': user.user_metadata.get('avatar_url')
        }

        # --- START: MODIFIED REFERRAL LOGIC ---
        # Always check for a referral ID in the session.
        if 'referred_by_channel_id' in session:
            # Only apply the referral ID if the user's profile doesn't already have one.
            # This prevents existing users from being re-assigned if they click another referral link.
            if not profile or not profile.get('referred_by_channel_id'):
                profile_payload['referred_by_channel_id'] = session.pop('referred_by_channel_id', None)
                print(f"Applying referral from channel ID {profile_payload.get('referred_by_channel_id')} to user {user.email}")
            else:
                # If they already have a referral ID, just clear the session variable.
                session.pop('referred_by_channel_id', None)
        # --- END: MODIFIED REFERRAL LOGIC ---

        # The create_or_update_profile function uses 'upsert', so it's safe to call every time.
        # It will correctly add the referral_id if it's in the payload.
        db_utils.create_or_update_profile(profile_payload)

        # Only create initial stats if the user was genuinely new (no profile existed before).
        if not profile:
            db_utils.create_initial_usage_stats(user.id)

        session['user'] = user.model_dump()
        session['access_token'] = access_token
        session['refresh_token'] = refresh_token
        session['expires_at'] = expires_at
        return jsonify({'status': 'success', 'message': 'Session set successfully.'})

    except Exception as e:
        logging.error(f"Error in set-cookie: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': 'An internal error occurred.'}), 500

@app.route('/')
def home():
    if 'user' in session:
        return redirect(url_for('channel'))
    return render_template('landing.html')

# --- All other routes like /channel, /ask, /stream_answer, etc. remain unchanged ---
# ... (The rest of your app.py file from /channel downwards remains the same)
@app.route('/channel', methods=['GET', 'POST'])
def channel():
    try:
        if request.method == 'POST':
            if 'user' not in session:
                return jsonify({'status': 'error', 'message': 'Authentication required.'}), 401

            user_id = session['user']['id']
            active_community_id = session.get('active_community_id')
            user_status = get_user_status(user_id, active_community_id)

            if not user_status:
                return jsonify({'status': 'error', 'message': 'Could not verify user status.'}), 500

            if user_status.get('is_active_community_owner'):
                community_status = get_community_status(active_community_id)
                if not community_status:
                    return jsonify({'status': 'error', 'message': 'Could not verify community status.'}), 500
                current_total_channels = db_utils.count_channels_for_user(user_id)
                max_total_channels = community_status['limits'].get('shared_channel_limit', 0)
                if current_total_channels >= max_total_channels:
                    return jsonify({'status': 'limit_reached', 'message': f"As a community admin, your total channel limit is {max_total_channels}. You have reached this limit."}), 403
            else:
                max_channels = user_status['limits'].get('max_channels', 0)
                current_channels = user_status['usage'].get('channels_processed', 0)
                if max_channels != float('inf') and current_channels >= max_channels:
                    message = f"You have reached the maximum of {int(max_channels)} personal channels for your plan."
                    if user_status.get('is_whop_user'):
                        return jsonify({'status': 'limit_reached', 'message': message, 'action': 'show_upgrade_popup'}), 403
                    else:
                        return jsonify({'status': 'limit_reached', 'message': message}), 403
            
            def guarded_personal_channel_add():
                user_id = session['user']['id']
                channel_url = request.form.get('channel_url', '').strip()
                if not channel_url:
                    return jsonify({'status': 'error', 'message': 'Channel URL is required'}), 400

                cleaned_url = clean_youtube_url(channel_url)
                existing = db_utils.find_channel_by_url(cleaned_url)

                if existing:
                    link_response = db_utils.link_user_to_channel(user_id, existing['id'])
                    if link_response:
                        db_utils.increment_channels_processed(user_id)
                    if redis_client: redis_client.delete(f"user_channels:{user_id}")
                    return jsonify({'status': 'success', 'message': 'Channel added to your list.'})
                else:
                    community_id_for_channel = None
                    if user_status.get('is_active_community_owner'):
                        community_id_for_channel = active_community_id
                    
                    # This call is now correct. The user_id is passed to create_channel,
                    # which internally assigns it to the `creator_id` field.
                    new_channel = db_utils.create_channel(cleaned_url, user_id, is_shared=False, community_id=community_id_for_channel)
                    
                    if not new_channel:
                        return jsonify({'status': 'error', 'message': 'Could not create channel record.'}), 500
                    
                    db_utils.link_user_to_channel(user_id, new_channel['id'])
                    db_utils.increment_channels_processed(user_id)
                    task = process_channel_task.schedule(args=(new_channel['id'],), delay=1)
                    if redis_client: redis_client.delete(f"user_channels:{user_id}")
                    return jsonify({'status': 'processing', 'task_id': task.id})
            return guarded_personal_channel_add()

        return render_template(
            'channel.html',
            saved_channels=get_user_channels(),
            SUPABASE_URL=os.environ.get('SUPABASE_URL'),
            SUPABASE_ANON_KEY=os.environ.get('SUPABASE_ANON_KEY')
        )
    except Exception as e:
        logging.error(f"Error in /channel: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': 'An internal server error occurred.'}), 500

@app.route('/add_shared_channel', methods=['POST'])
@login_required
@community_channel_limit_enforcer
def add_shared_channel():
    user_id = session['user']['id']
    community_id = session.get('active_community_id')
    channel_url = request.form.get('channel_url', '').strip()
    if not channel_url:
        return jsonify({'status': 'error', 'message': 'Channel URL is required'}), 400
    cleaned_url = clean_youtube_url(channel_url)
    existing = db_utils.find_channel_by_url(cleaned_url)
    if existing:
        pass
    new_channel = db_utils.create_channel(cleaned_url, user_id, is_shared=True, community_id=community_id)
    if not new_channel:
        return jsonify({'status': 'error', 'message': 'Could not create shared channel record.'}), 500
    task = process_channel_task.schedule(args=(new_channel['id'],), delay=1)
    return jsonify({'status': 'processing', 'task_id': task.id, 'message': 'Processing shared channel...'})

@app.route('/set-default-channel/<int:channel_id>', methods=['POST'])
@login_required
def set_default_channel(channel_id):
    user_id = session['user']['id']
    active_community_id = session.get('active_community_id')
    if not active_community_id:
        return jsonify({'status': 'error', 'message': 'No active community context.'}), 400
    supabase_admin = get_supabase_admin_client()
    community_resp = supabase_admin.table('communities').select('owner_user_id').eq('id', active_community_id).single().execute()
    if not community_resp.data or str(community_resp.data['owner_user_id']) != str(user_id):
        return jsonify({'status': 'error', 'message': 'You are not the owner of this community.'}), 403
    channel_resp = supabase_admin.table('channels').select('id').eq('id', channel_id).eq('is_shared', True).eq('community_id', active_community_id).single().execute()
    if not channel_resp.data:
        return jsonify({'status': 'error', 'message': 'This channel is not a shared channel in your community.'}), 403
    supabase_admin.table('communities').update({'default_channel_id': channel_id}).eq('id', active_community_id).execute()
    return jsonify({'status': 'success', 'message': 'Default channel has been updated.'})

@app.route('/ask', defaults={'channel_name': None})
@app.route('/ask/channel/<path:channel_name>')
def ask(channel_name):
    user_id = session.get('user', {}).get('id')
    access_token = session.get('access_token')

    # Start with an empty list for channels
    all_user_channels = {}
    if user_id:
        all_user_channels = get_user_channels()
    
    current_channel = None
    history = []

    if channel_name:
        current_channel = all_user_channels.get(channel_name)
        
        # If the channel isn't in the user's list (or the user is logged out),
        # fetch its public data to display the page correctly.
        if not current_channel:
             supabase_admin = get_supabase_admin_client()
             channel_res = supabase_admin.table('channels').select('*').eq('channel_name', channel_name).maybe_single().execute()
             
             # --- START: THE FIX ---
             # Check if the query returned any data before trying to access it.
             if channel_res and channel_res.data:
                 current_channel = channel_res.data
             else:
                # If no data is found, the channel doesn't exist. Redirect with a message.
                flash(f"The channel '{channel_name}' could not be found.", 'error')
                return redirect(url_for('channel'))
             # --- END: THE FIX ---

        # Only fetch history if the user is logged in
        if user_id:
            history = get_chat_history(user_id, channel_name, access_token)

    elif user_id: # Only fetch general history if logged in
        history = get_chat_history(user_id, 'general', access_token)

    return render_template(
        'ask.html',
        history=history,
        channel_name=channel_name,
        current_channel=current_channel,
        saved_channels=all_user_channels, # This will be empty for logged-out users
        SUPABASE_URL=os.environ.get('SUPABASE_URL'),
        SUPABASE_ANON_KEY=os.environ.get('SUPABASE_ANON_KEY')
    )

@app.route('/api/channel_details/<path:channel_name>')
@login_required
def get_channel_details(channel_name):
    all_user_channels = get_user_channels()
    current_channel = all_user_channels.get(channel_name)
    if not current_channel:
        return jsonify({'error': 'Channel not found or permission denied'}), 404
    return jsonify({'current_channel': current_channel, 'saved_channels': all_user_channels})

@app.route('/api/chat_history/<path:channel_name>')
@login_required
def get_chat_history_api(channel_name):
    user_id = session['user']['id']
    access_token = session.get('access_token')
    history = get_chat_history(user_id, channel_name, access_token)
    return jsonify({'history': history})

@app.route('/stream_answer', methods=['POST'])
@login_required
@limit_enforcer('query')
def stream_answer():
    user_id = session['user']['id']
    question = request.form.get('question', '').strip()
    channel_name = request.form.get('channel_name')

    access_token = session.get('access_token')
    active_community_id = session.get('active_community_id')
    is_owner_in_trial = False

    if active_community_id:
        user_status = get_user_status(user_id, active_community_id)
        if user_status.get('is_active_community_owner'):
            community_status = get_community_status(active_community_id)
            if community_status and community_status['usage']['trial_queries_used'] < community_status['limits']['owner_trial_limit']:
                is_owner_in_trial = True

    def on_complete_callback():
        user_status = get_user_status(user_id, active_community_id)
        if user_status.get('has_personal_plan'):
            db_utils.increment_personal_query_usage(user_id)
        elif active_community_id:
            db_utils.increment_community_query_usage(active_community_id, is_trial=is_owner_in_trial)
            db_utils.increment_personal_query_usage(user_id)
        else:
            db_utils.increment_personal_query_usage(user_id)
        
        if hasattr(db_utils, 'get_profile') and hasattr(db_utils.get_profile, 'cache_clear'):
            db_utils.get_profile.cache_clear()
        
        if redis_client:
            user_cache_key = f"user_status:{user_id}:community:{active_community_id or 'none'}"
            redis_client.delete(user_cache_key)
            if active_community_id:
                community_cache_key = f"community_status:{active_community_id}"
                redis_client.delete(community_cache_key)
        
        fresh_user_status = get_user_status(user_id, active_community_id)
        fresh_community_status = get_community_status(active_community_id) if active_community_id else None
        
        query_string = ""
        if fresh_user_status and (fresh_user_status.get('has_personal_plan') or not fresh_user_status.get('is_whop_user')):
            max_queries = fresh_user_status['limits'].get('max_queries_per_month', 0)
            if max_queries == float('inf'):
                query_string = "You have <strong>Unlimited</strong> personal queries."
            else:
                queries_used = fresh_user_status['usage'].get('queries_this_month', 0)
                remaining = int(max_queries - queries_used)
                query_string = f"You have <strong>{remaining}</strong> personal queries remaining."
        elif fresh_community_status:
            max_queries = fresh_community_status['limits'].get('query_limit', 0)
            queries_used = fresh_community_status['usage'].get('queries_used', 0)
            remaining = int(max_queries - queries_used)
            query_string = f"The community has <strong>{remaining}</strong> shared queries remaining."
            
        return query_string

    MAX_CHAT_MESSAGES = 20
    current_channel_name_for_history = channel_name or 'general'
    is_regenerating = request.form.get('is_regenerating') == 'true'
    history = get_chat_history(user_id, current_channel_name_for_history, access_token=access_token)
    
    if len(history) >= MAX_CHAT_MESSAGES:
        def limit_exceeded_stream():
            error_data = {'error': 'QUERY_LIMIT_REACHED', 'message': f"You have reached the chat limit of {MAX_CHAT_MESSAGES} messages. Please use the 'Clear Chat' button to start a new conversation."}
            yield f"data: {json.dumps(error_data)}\n\n"
            yield "data: [DONE]\n\n"
        return Response(limit_exceeded_stream(), mimetype='text/event-stream')
    
    # --- START: MODIFIED LOGIC ---
    # Decide which part of the history to use for building the prompt context.
    history_for_prompt = history
    if is_regenerating and history:
        # If the user is regenerating, exclude the last (unhelpful) Q&A pair.
        history_for_prompt = history[:-1]
    
    chat_history_for_prompt = ''
    # Use the (potentially shorter) history_for_prompt list to build the context.
    for qa in history_for_prompt[-5:]:
        chat_history_for_prompt += f"Human: {qa['question']}\nAI: {qa['answer']}\n\n"

    
    final_question_with_history = question
    if chat_history_for_prompt:
        final_question_with_history = (f"Given the following conversation history:\n{chat_history_for_prompt}--- End History ---\n\nNow, answer this new question, considering the history as context:\n{question}")

    channel_data = None
    video_ids = None
    if channel_name:
        all_user_channels = get_user_channels()
        channel_data = all_user_channels.get(channel_name)

        # --- START: THE FIX ---
        # If the channel is not in the user's saved list, it might be a temporary
        # public session. Fetch its data directly as a fallback.
        if not channel_data:
            logging.info(f"Channel '{channel_name}' not in user's list. Attempting public fetch for temporary session.")
            supabase_admin = get_supabase_admin_client()
            public_channel_res = supabase_admin.table('channels').select('*').eq('channel_name', channel_name).maybe_single().execute()
            if public_channel_res.data:
                channel_data = public_channel_res.data
        # --- END: THE FIX ---

        if channel_data:
            video_ids = {v['video_id'] for v in channel_data.get('videos', [])}
            
    stream = answer_question_stream(
        question_for_prompt=final_question_with_history, 
        question_for_search=question, 
        channel_data=channel_data, 
        video_ids=video_ids, 
        user_id=user_id, 
        access_token=access_token, 
        on_complete=on_complete_callback
    )
    return Response(stream, mimetype='text/event-stream')

@app.route('/delete_channel/<int:channel_id>', methods=['POST'])
@login_required
def delete_channel_route(channel_id):
    user_id = session['user']['id']
    supabase_admin = get_supabase_admin_client()
    try:
        supabase_admin.table('user_channels').select('channel_id').eq('user_id', user_id).eq('channel_id', channel_id).limit(1).single().execute()
        delete_channel_task(channel_id, user_id)
        return jsonify({'status': 'success', 'message': 'Channel deletion has been started in the background.'})
    except APIError as e:
        if 'PGRST116' in e.message:
            return jsonify({'status': 'error', 'message': 'Channel not found or you do not have permission.'}), 404
        return jsonify({'status': 'error', 'message': 'A database error occurred.'}), 500
    except Exception as e:
        return jsonify({'status': 'error', 'message': 'An error occurred while starting the deletion process.'}), 500

@app.route('/refresh_channel/<int:channel_id>', methods=['POST'])
@login_required
def refresh_channel_route(channel_id):
    user_id = session['user']['id']
    access_token = session.get('access_token')
    try:
        supabase = get_supabase_client(access_token)
        supabase.table('user_channels').select('channel_id').eq('user_id', user_id).eq('channel_id', channel_id).limit(1).single().execute()
        task = sync_channel_task(channel_id)
        return jsonify({'status': 'success', 'message': 'Channel refresh has been queued.', 'task_id': task.id})
    except APIError:
        return jsonify({'status': 'error', 'message': 'Channel not found or you do not have permission.'}), 404
    except Exception as e:
        return jsonify({'status': 'error', 'message': 'An error occurred while starting the refresh.'}), 500

@app.route('/telegram/webhook/<webhook_secret>', methods=['POST'])
def telegram_webhook(webhook_secret):
    config = load_config()
    token = config.get('telegram_bot_token')
    if not token:
        return 'Configuration error', 500
    expected_secret = token.split(':')[-1][:10]
    header_secret = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
    if not (secrets.compare_digest(webhook_secret, expected_secret) and header_secret and secrets.compare_digest(header_secret, expected_secret)):
        return 'Unauthorized', 403
    update = request.get_json()
    process_telegram_update_task(update)
    return jsonify({'status': 'ok'})

@app.template_filter('format_subscribers')
def format_subscribers_filter(value):
    try:
        num = int(value)
        if num < 1000: return str(num)
        if num < 1_000_000: return f"{num / 1000:.1f}".replace('.0', '') + 'K'
        return f"{num / 1_000_000:.1f}".replace('.0', '') + 'M'
    except (ValueError, TypeError):
        return ''

@app.route('/task_result/<task_id>')
@login_required
def task_result(task_id):
    if redis_client:
        progress_data = redis_client.get(f"task_progress:{task_id}")
        if progress_data:
            return jsonify(json.loads(progress_data))
    try:
        result = huey.result(task_id, preserve=True)
        if result is not None:
            return jsonify({'status': 'complete', 'progress': 100, 'message': str(result)})
    except TaskException as e:
        return jsonify({'status': 'failed', 'progress': 0, 'message': str(e)})
    return jsonify({'status': 'processing', 'progress': 5, 'message': 'Task is starting...'})

@app.route('/clear_chat', methods=['POST'])
@login_required
def clear_chat():
    channel_name = request.form.get('channel_name') or 'general'
    user_id = session['user']['id']
    try:
        supabase = get_supabase_client(session.get('access_token'))
        supabase.table('chat_history').delete().eq('user_id', user_id).eq('channel_name', channel_name).execute()
        return jsonify({'status': 'success', 'message': f'Chat history cleared for {channel_name}'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/dashboard')
@login_required
def dashboard():
    """
    Renders the main dashboard hub and provides the status of each integration.
    """
    user_id = session['user']['id']
    supabase_admin = get_supabase_admin_client()
    
    # --- Check Discord Status ---
    discord_is_active = False
    profile = db_utils.get_profile(user_id)
    if profile and profile.get('discord_user_id'):
        discord_is_active = True
    else:
        bots_res = supabase_admin.table('discord_bots').select('id', count='exact').eq('user_id', user_id).execute()
        if bots_res.count > 0:
            discord_is_active = True

    # --- Check Telegram Status ---
    telegram_is_active = False
    personal_conn = supabase_admin.table('telegram_connections').select('id', count='exact').eq('app_user_id', user_id).eq('is_active', True).execute()
    if personal_conn.count > 0:
        telegram_is_active = True
    else:
        group_conn = supabase_admin.table('group_connections').select('id', count='exact').eq('owner_user_id', user_id).eq('is_active', True).execute()
        if group_conn.count > 0:
            telegram_is_active = True

    # --- Get Creator Channels and Their Stats ---
    user_creator_channels = db_utils.get_channels_created_by_user(user_id)
    creator_stats = db_utils.get_creator_dashboard_stats(user_id)

    # Merge stats into the channel data before sending to the template
    for channel_data in user_creator_channels.values():
        channel_stats = creator_stats.get(channel_data.get('id'), {})
        # --- START: THE FIX ---
        channel_data['stats'] = {
            'referrals': channel_stats.get('referrals', 0),
            'paid_referrals': channel_stats.get('paid_referrals', 0), # <-- This line was missing
            'creator_mrr': channel_stats.get('creator_mrr', 0.0),
            'current_adds': channel_stats.get('current_adds', 0)
        }
        # --- END: THE FIX ---

    return render_template(
        'dashboard.html',
        discord_is_active=discord_is_active,
        telegram_is_active=telegram_is_active,
        saved_channels=user_creator_channels
    )

@app.route('/integrations/discord')
@login_required
def discord_dashboard():
    user_id = session['user']['id']
    supabase_admin = get_supabase_admin_client()

    # --- Check if the user's Discord account is linked (no change here) ---
    profile = db_utils.get_profile(user_id)
    discord_account_linked = profile and profile.get('discord_user_id') is not None

    # --- START: MODIFIED LOGIC ---
    # 1. Fetch only the channels this user has created
    creator_channels = db_utils.get_channels_created_by_user(user_id)
    creator_channel_ids = [c['id'] for c in creator_channels.values()]

    # 2. Fetch branded bots that belong to those specific channels
    branded_bots = []
    if creator_channel_ids:
        bots_res = supabase_admin.table('discord_bots').select('*, client_id, channel:youtube_channel_id(channel_name, channel_thumbnail)') \
            .in_('youtube_channel_id', creator_channel_ids) \
            .eq('user_id', user_id) \
            .execute()
        branded_bots = bots_res.data if bots_res.data else []
    # --- END: MODIFIED LOG-IC ---

    # --- MODIFIED LINK GENERATION: Use the reliable client_id from the database (no change here) ---
    for bot in branded_bots:
        client_id = bot.get('client_id')
        if client_id:
            permissions = "328565073920"  # A common permission set for Q&A bots
            bot['invite_link'] = f"https://discord.com/api/oauth2/authorize?client_id={client_id}&permissions={permissions}&scope=bot"
        else:
            # Fallback in case the client_id is missing (e.g., for older bots)
            bot['invite_link'] = '#'
            logging.warning(f"Could not generate invite link for bot ID {bot.get('id')} because client_id is missing.")

    # --- Logic for the shared bot invite link (no change here) ---
    DISCORD_CLIENT_ID = os.environ.get("DISCORD_SHARED_CLIENT_ID")
    discord_invite_link = "#"
    if DISCORD_CLIENT_ID:
        permissions = "328565073920"
        discord_invite_link = f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&permissions={permissions}&scope=bot%20applications.commands"

    return render_template(
        'discord_dashboard.html',
        branded_bots=branded_bots,
        discord_invite_link=discord_invite_link,
        discord_account_linked=discord_account_linked,
        saved_channels=creator_channels  # Pass the filtered list of creator channels
    )

@app.route('/integrations/telegram')
@login_required
def telegram_dashboard():
    """
    Handles the Telegram Integrations Dashboard, managing both personal
    and group bot connections.
    """
    user_id = session['user']['id']
    supabase_admin = get_supabase_admin_client()

    # --- Personal Bot Data (no change here) ---
    personal_connection_status = 'not_connected'
    telegram_username = None
    personal_connection_code = None

    personal_conn_res = supabase_admin.table('telegram_connections').select('*').eq('app_user_id', user_id).limit(1).execute()
    
    if personal_conn_res.data:
        connection_data = personal_conn_res.data[0]
        if connection_data['is_active']:
            personal_connection_status = 'connected'
            telegram_username = connection_data.get('telegram_username', 'N/A')
        else:
            created_at_str = connection_data.get('created_at')
            created_at_dt = isoparse(created_at_str)
            
            ten_minutes_ago = datetime.now(timezone.utc) - timedelta(minutes=10)
            
            if created_at_dt < ten_minutes_ago:
                personal_connection_status = 'not_connected'
            else:
                personal_connection_status = 'code_generated'
                personal_connection_code = connection_data['connection_code']

    # --- START: MODIFIED LOGIC ---
    # 1. Fetch only the channels this user has created
    creator_channels = db_utils.get_channels_created_by_user(user_id)
    # --- END: MODIFIED LOGIC ---

    # --- Group Bot Data (no change here) ---
    group_connection_status = 'not_connected'
    group_channel = None
    group_details = None
    group_connection_code = None

    channel_id = request.args.get('channel_id', type=int)
    if channel_id:
        # Check if the requested channel_id is one the user actually created
        if any(c['id'] == channel_id for c in creator_channels.values()):
            try:
                # Since we already confirmed ownership, we can proceed
                # This logic is simplified as we no longer need the complex join
                group_channel_data = supabase_admin.table('channels').select('id, channel_name').eq('id', channel_id).single().execute()
                group_channel = group_channel_data.data if group_channel_data.data else None

                group_conn = supabase_admin.table('group_connections').select('*').eq('linked_channel_id', channel_id).limit(1).execute()
                if group_conn.data:
                    if group_conn.data[0]['is_active']:
                        group_connection_status = 'connected'
                        group_details = group_conn.data[0]
                    else:
                        group_connection_status = 'code_generated'
                        group_connection_code = group_conn.data[0]['connection_code']
            except Exception as e:
                logging.error(f"Error fetching group connections for user {user_id}, channel {channel_id}: {e}")
                group_channel = None
    
    token, _ = get_bot_token_and_url()
    bot_username = os.environ.get("TELEGRAM_BOT_USERNAME")

    return render_template(
        'telegram_dashboard.html',
        saved_channels=creator_channels, # Pass the filtered list
        personal_connection_status=personal_connection_status,
        telegram_username=telegram_username,
        personal_connection_code=personal_connection_code,
        group_connection_status=group_connection_status,
        group_channel=group_channel,
        group_details=group_details,
        group_connection_code=group_connection_code,
        bot_username=bot_username,
        channel_id=channel_id
    )

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html', saved_channels=get_user_channels())

@app.route('/terms')
def terms():
    return render_template('terms.html', saved_channels=get_user_channels())

@app.route('/api/toggle_channel_privacy/<int:channel_id>', methods=['POST'])
@login_required
@community_channel_limit_enforcer(check_on_increase_only=True)
def toggle_channel_privacy(channel_id):
    user_id = session['user']['id']
    supabase_admin = get_supabase_admin_client()
    try:
        channel_res = supabase_admin.table('channels').select('community_id, is_shared, creator_id').eq('id', channel_id).single().execute()
        if not channel_res.data:
            return jsonify({'status': 'error', 'message': 'Channel not found.'}), 404
        channel_data = channel_res.data
        community_id = channel_data.get('community_id')
        if not community_id or str(channel_data.get('creator_id')) != str(user_id):
             return jsonify({'status': 'error', 'message': 'This action is not allowed for this channel.'}), 403
        community_res = supabase_admin.table('communities').select('owner_user_id').eq('id', community_id).single().execute()
        if not community_res.data or str(community_res.data.get('owner_user_id')) != str(user_id):
            return jsonify({'status': 'error', 'message': 'You are not the owner of this community.'}), 403
        new_is_shared = not channel_data['is_shared']
        if not new_is_shared:
            community_details = supabase_admin.table('communities').select('default_channel_id').eq('id', community_id).single().execute()
            if community_details.data and community_details.data.get('default_channel_id') == channel_id:
                return jsonify({'status': 'error', 'message': 'You cannot make a default channel private. Set a different default channel first.'}), 400
        update_res = supabase_admin.table('channels').update({'is_shared': new_is_shared}).eq('id', channel_id).execute()
        if not update_res.data:
            raise Exception("Failed to update channel privacy.")
        return jsonify({'status': 'success', 'message': f"Channel is now {'shared' if new_is_shared else 'personal'}.", 'is_shared': new_is_shared})
    except Exception as e:
        logging.error(f"Error toggling privacy for channel {channel_id}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': 'An internal server error occurred.'}), 500

if os.environ.get("FLASK_ENV") == "development":
    @app.route('/dev/login')
    def dev_login():
        user_id = 'a_test_user_id'
        session['user'] = {'id': user_id, 'user_metadata': {'full_name': 'Test User', 'avatar_url': 'https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_272x92dp.png'}, 'email': 'test@example.com'}
        session['access_token'] = 'test_access_token'
        session['refresh_token'] = 'test_refresh_token'
        session['expires_at'] = time.time() + 3600
        db_utils.create_or_update_profile({'id': user_id, 'email': 'test@example.com', 'full_name': 'Test User'})
        db_utils.create_initial_usage_stats(user_id)
        return 'Logged in as test user'

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('home'))
        admin_user_id = '2f092c41-e0c5-4533-98a2-9e5da027d0ed'
        if str(session['user']['id']) != admin_user_id:
            flash('You do not have permission to access this page.', 'error')
            return redirect(url_for('channel'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    supabase_admin = get_supabase_admin_client()
    communities_res = supabase_admin.table('communities').select('*, owner:owner_user_id(full_name, email)').execute()
    communities = communities_res.data if communities_res.data else []

    non_whop_users_res = supabase_admin.table('profiles').select('*, usage:usage_stats!inner(*)').is_('whop_user_id', None).execute()
    non_whop_users = non_whop_users_res.data if non_whop_users_res.data else []

    # --- START: NEW LOGIC ---
    # Fetch all creator payouts and join with the creator's profile info
    payouts_res = supabase_admin.table('creator_payouts').select('*, creator:creator_id(email, full_name)').order('requested_at', desc=True).execute()
    payouts = payouts_res.data if payouts_res.data else []
    # --- END: NEW LOGIC ---

    saved_channels = get_user_channels() 
    return render_template('admin.html', 
                           communities=communities, 
                           non_whop_users=non_whop_users, 
                           all_plans=PLANS, 
                           COMMUNITY_PLANS=COMMUNITY_PLANS, 
                           saved_channels=saved_channels,
                           payouts=payouts) # Pass payouts to the template

@app.route('/api/admin/complete_payout/<payout_id>', methods=['POST'])
@admin_required
def api_admin_complete_payout(payout_id):
    """
    Updates a payout's status from 'pending' to 'paid'.
    """
    try:
        supabase_admin = get_supabase_admin_client()
        supabase_admin.table('creator_payouts').update({
            'status': 'paid',
            'paid_at': datetime.now(timezone.utc).isoformat()
        }).eq('id', payout_id).execute()
        return jsonify({'status': 'success', 'message': 'Payout marked as paid.'})
    except Exception as e:
        logger.error(f"Error completing payout {payout_id}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': 'An internal server error occurred.'}), 500
    
@app.route('/api/admin/create_plan', methods=['POST'])
@admin_required
def api_admin_create_plan():
    data = request.get_json()
    plan_id = data.get('plan_id')
    plan_name = data.get('plan_name')
    max_channels = data.get('max_channels')
    max_queries = data.get('max_queries')
    plan_type = data.get('plan_type')
    if not all([plan_id, plan_name, max_channels, max_queries, plan_type]):
        return jsonify({'status': 'error', 'message': 'All fields are required.'}), 400
    try:
        if plan_type == 'user':
            PLANS[plan_id] = {'name': plan_name, 'max_channels': int(max_channels), 'max_queries_per_month': int(max_queries)}
        elif plan_type == 'community':
            COMMUNITY_PLANS[plan_id] = {'name': plan_name, 'shared_channels_allowed': int(max_channels), 'queries_per_month': int(max_queries)}
        else:
            return jsonify({'status': 'error', 'message': 'Invalid plan type.'}), 400
        return jsonify({'status': 'success', 'message': f'Plan "{plan_name}" created successfully.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/admin/set_default_plan', methods=['POST'])
@admin_required
def api_admin_set_default_plan():
    data = request.get_json()
    plan_id = data.get('plan_id')
    plan_type = data.get('type')
    if not all([plan_id, plan_type]):
        return jsonify({'status': 'error', 'message': 'Missing required fields.'}), 400
    print(f"Default plan for {plan_type} set to: {plan_id}")
    return jsonify({'status': 'success', 'message': f'Default plan for {plan_type} has been set to "{plan_id}".'})

@app.route('/api/admin/set_current_plan', methods=['POST'])
@admin_required
def api_admin_set_current_plan():
    data = request.get_json()
    target_id = data.get('id')
    plan_id = data.get('plan_id')
    plan_type = data.get('type')
    if not all([target_id, plan_id, plan_type]):
        return jsonify({'status': 'error', 'message': 'Missing required fields.'}), 400
    supabase_admin = get_supabase_admin_client()
    try:
        if plan_type == 'community':
            plan_details = COMMUNITY_PLANS.get(plan_id)
            if not plan_details:
                return jsonify({'status': 'error', 'message': 'Invalid community plan ID.'}), 400
            update_data = {'plan_id': plan_id, 'shared_channel_limit': plan_details['shared_channels_allowed'], 'query_limit': plan_details['queries_per_month']}
            supabase_admin.table('communities').update(update_data).eq('id', target_id).execute()
        elif plan_type == 'user':
            if plan_id not in PLANS:
                return jsonify({'status': 'error', 'message': 'Invalid user plan ID.'}), 400
            supabase_admin.table('profiles').update({'direct_subscription_plan': plan_id}).eq('id', target_id).execute()
            db_utils.record_creator_earning(referred_user_id=target_id, plan_id=plan_id)
        return jsonify({'status': 'success', 'message': 'Plan updated successfully.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/admin/remove_plan', methods=['POST'])
@admin_required
def api_admin_remove_plan():
    data = request.get_json()
    target_id = data.get('id')
    target_type = data.get('type')
    if not all([target_id, target_type]):
        return jsonify({'status': 'error', 'message': 'Missing required fields.'}), 400
    supabase_admin = get_supabase_admin_client()
    try:
        if target_type == 'community':
            default_plan = COMMUNITY_PLANS.get('basic_community')
            update_data = {'plan_id': 'basic_community', 'shared_channel_limit': default_plan['shared_channels_allowed'], 'query_limit': default_plan['queries_per_month']}
            supabase_admin.table('communities').update(update_data).eq('id', target_id).execute()
        elif target_type == 'user':
            supabase_admin.table('profiles').update({'direct_subscription_plan': None}).eq('id', target_id).execute()
        return jsonify({'status': 'success', 'message': 'Plan removed successfully.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/admin/delete_user/<user_id>', methods=['POST'])
@admin_required
def api_admin_delete_user(user_id):
    """
    Permanently deletes a user and all their associated data.
    """
    if not user_id:
        return jsonify({'status': 'error', 'message': 'User ID is required.'}), 400

    try:
        supabase_admin = get_supabase_admin_client()
        
        # Step 1: Manually delete channels created by the user.
        supabase_admin.table('channels').delete().eq('creator_id', user_id).execute()

        # --- START: THE FIX ---
        # Step 2: Explicitly delete the user's profile first.
        # This ensures that if they sign up again, our app's logic will
        # correctly see them as a new user and apply referral benefits.
        # The CASCADE rule on the profiles table will handle deleting their
        # chat history, usage stats, etc.
        supabase_admin.table('profiles').delete().eq('id', user_id).execute()
        # --- END: THE FIX ---

        # Step 3: Delete the user from the main authentication system.
        supabase_admin.auth.admin.delete_user(user_id)
        
        return jsonify({'status': 'success', 'message': f'Successfully deleted user {user_id}.'})
    except Exception as e:
        logger.error(f"Error deleting user {user_id}: {e}", exc_info=True)
        if 'User not found' in str(e):
            return jsonify({'status': 'error', 'message': 'User not found. They may have already been deleted.'}), 404
        return jsonify({'status': 'error', 'message': 'An internal server error occurred.'}), 500

@app.route('/earnings')
@login_required
def earnings_page():
    creator_id = session['user']['id']
    earnings_data = db_utils.get_creator_balance_and_history(creator_id)
    return render_template('earnings.html', earnings_data=earnings_data, saved_channels=get_user_channels())

@app.route('/api/request_payout', methods=['POST'])
@login_required
def request_payout():
    creator_id = session['user']['id']
    amount = request.json.get('amount')

    try:
        amount_float = float(amount)
        if amount_float <= 0:
            return jsonify({'status': 'error', 'message': 'Please enter a valid amount.'}), 400
    except (ValueError, TypeError):
        return jsonify({'status': 'error', 'message': 'Invalid amount specified.'}), 400

    # For simulation, we'll just store a mock detail.
    mock_details = {"method": "Simulated Bank Transfer", "account_ending": "XX1234"}

    payout, message = db_utils.create_payout_request(creator_id, amount_float, mock_details)

    if payout:
        return jsonify({'status': 'success', 'message': message})
    else:
        return jsonify({'status': 'error', 'message': message}), 400

@app.route('/integrations/telegram/connect_personal', methods=['POST'])
@login_required
def connect_telegram():
    user_id = session['user']['id']
    supabase_admin = get_supabase_admin_client()
    connection_code = secrets.token_hex(8)
    data_to_store = {
        'app_user_id': user_id,
        'telegram_chat_id': 0, # Reset chat_id
        'connection_code': connection_code,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'is_active': False
    }
    
    # Using 'upsert' with 'on_conflict' ensures that if a record for the user
    # already exists, it will be updated with the new code and timestamp.
    supabase_admin.table('telegram_connections').upsert(
        data_to_store, on_conflict='app_user_id'
    ).execute()
    flash('Connection code generated.', 'success')
    return redirect(url_for('telegram_dashboard', _anchor='telegram-personal'))

@app.route('/integrations/telegram/disconnect_personal', methods=['POST'])
@login_required
def disconnect_telegram():
    user_id = session['user']['id']
    supabase_admin = get_supabase_admin_client()
    try:
        supabase_admin.table('telegram_connections').delete().eq('app_user_id', user_id).execute()
        flash('Personal Telegram bot disconnected.', 'success')
    except APIError as e:
        flash(f"An error occurred while disconnecting: {e.message}", 'error')
    return redirect(url_for('telegram_dashboard', _anchor='telegram-personal'))

@app.route('/integrations/telegram/connect_group', methods=['POST'])
@login_required
def connect_group():
    user_id = session['user']['id']
    channel_id = request.form.get('channel_id', type=int)
    if not channel_id:
        flash('Channel ID is required.', 'error')
        return redirect(url_for('integrations'))

    supabase = get_supabase_client(session.get('access_token'))
    link_check = supabase.table('user_channels').select('channel_id').eq('user_id', user_id).eq('channel_id', channel_id).limit(1).execute()
    if not link_check.data:
        flash("You do not have permission to access this channel.", 'error')
        return redirect(url_for('integrations'))

    supabase_admin = get_supabase_admin_client()
    connection_code = secrets.token_hex(10)
    supabase_admin.table('group_connections').upsert({
        'owner_user_id': user_id,
        'linked_channel_id': channel_id,
        'connection_code': connection_code,
        'is_active': False
    }, on_conflict='linked_channel_id').execute()
    flash('Group connection code generated.', 'success')
    return redirect(url_for('telegram_dashboard', channel_id=channel_id, _anchor='telegram-group'))

@app.route('/integrations/telegram/disconnect_group/<int:channel_id>', methods=['POST'])
@login_required
def disconnect_group(channel_id):
    user_id = session['user']['id']
    supabase = get_supabase_client(session.get('access_token'))
    supabase_admin = get_supabase_admin_client()
    link_check = supabase.table('user_channels').select('channels(channel_name)') \
        .eq('user_id', user_id).eq('channel_id', channel_id).single().execute()
    if not (link_check.data and link_check.data.get('channels')):
        flash("You do not have permission to modify this channel's connection.", 'error')
        return redirect(url_for('integrations'))

    try:
        supabase_admin.table('group_connections').delete().eq('linked_channel_id', channel_id).execute()
        flash('Telegram group successfully disconnected.', 'success')
    except APIError as e:
        flash(f"An error occurred while disconnecting: {e.message}", 'error')
    return redirect(url_for('telegram_dashboard', channel_id=channel_id, _anchor='telegram-group'))

@app.route('/integrations/discord/update/<int:bot_id>', methods=['POST'])
@login_required
def update_discord_bot(bot_id):
    channel_url = request.form.get('youtube_channel_url')
    user_id = session['user']['id']

    if not channel_url:
        return jsonify({'status': 'error', 'message': 'YouTube channel URL is required.'}), 400

    try:
        supabase_admin = get_supabase_admin_client()
        # Verify the bot belongs to the user
        bot_res = supabase_admin.table('discord_bots').select('id').eq('id', bot_id).eq('user_id', user_id).single().execute()
        if not bot_res.data:
            return jsonify({'status': 'error', 'message': 'Bot not found or permission denied.'}), 404

        # Find or create the new channel
        cleaned_url = clean_youtube_url(channel_url)
        existing_channel = db_utils.find_channel_by_url(cleaned_url)
        if existing_channel:
            channel_id = existing_channel['id']
            db_utils.link_user_to_channel(user_id, channel_id)
        else:
            new_channel = db_utils.create_channel(cleaned_url, user_id)
            if not new_channel:
                 return jsonify({'status': 'error', 'message': 'Could not create channel record.'}), 500
            channel_id = new_channel['id']
            db_utils.link_user_to_channel(user_id, channel_id)
            process_channel_task.schedule(args=(channel_id,), delay=1)

        # Update the bot's linked channel and set status to 'online'
        # The service will detect this and restart the bot with the new channel info.
        supabase_admin.table('discord_bots').update({
            'youtube_channel_id': channel_id, 
            'status': 'online'
        }).eq('id', bot_id).execute()

        return jsonify({'status': 'success', 'message': 'Bot updated. The service will restart it with the new settings shortly.'})

    except Exception as e:
        logging.error(f"Error updating bot {bot_id}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': 'An unexpected error occurred.'}), 500
    

@app.route('/integrations/discord/create', methods=['POST'])
@login_required
def create_discord_bot():
    bot_token = request.form.get('discord_bot_token')
    channel_url = request.form.get('youtube_channel_url')
    user_id = session['user']['id']

    if not bot_token or not channel_url:
        return jsonify({'status': 'error', 'message': 'Bot token and YouTube channel URL are required.'}), 400

    try:
        # Verify the token and get the bot's Client ID from Discord's API
        try:
            headers = {'Authorization': f'Bot {bot_token}'}
            user_res = requests.get('https://discord.com/api/v10/users/@me', headers=headers)
            user_res.raise_for_status()
            client_id = user_res.json()['id']
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                return jsonify({'status': 'error', 'message': 'The provided Discord Bot Token is invalid.'}), 400
            else:
                return jsonify({'status': 'error', 'message': f'Discord API error: {e.response.text}'}), 500
        except Exception as e:
             logging.error(f"Failed to verify bot token: {e}", exc_info=True)
             return jsonify({'status': 'error', 'message': 'Could not verify the bot token with Discord.'}), 500

        supabase_admin = get_supabase_admin_client()
        existing_bot = supabase_admin.table('discord_bots').select('id').eq('user_id', user_id).eq('bot_token', bot_token).maybe_single().execute()

        if existing_bot and existing_bot.data:
            return jsonify({'status': 'error', 'message': 'This bot token is already in use.'}), 409

        cleaned_url = clean_youtube_url(channel_url)
        existing_channel = db_utils.find_channel_by_url(cleaned_url)

        if existing_channel:
            channel_id = existing_channel['id']
            db_utils.link_user_to_channel(user_id, channel_id)
        else:
            new_channel = db_utils.create_channel(cleaned_url, user_id)
            if not new_channel:
                 return jsonify({'status': 'error', 'message': 'Could not create channel record.'}), 500
            channel_id = new_channel['id']
            db_utils.link_user_to_channel(user_id, channel_id)
            process_channel_task.schedule(args=(channel_id,), delay=1)

        bot_data = {
            'user_id': user_id,
            'bot_token': bot_token,
            'client_id': client_id,
            'youtube_channel_id': channel_id,
            'is_active': True,
            'status': 'online'  # Set status directly to 'online'
        }
        new_bot = db_utils.create_discord_bot(bot_data)
        if not new_bot:
            return jsonify({'status': 'error', 'message': 'Failed to save bot to database.'}), 500

        # The service will pick up this new 'online' bot on its next sync.
        return jsonify({'status': 'success', 'message': 'Bot created! The service will bring it online shortly.'})

    except Exception as e:
        logging.error(f"Error in create_discord_bot: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': f'An unexpected error occurred: {str(e)}'}), 500

@app.route('/integrations/discord/start/<int:bot_id>', methods=['POST'])
@login_required
def start_discord_bot(bot_id):
    user_id = session['user']['id']
    supabase_admin = get_supabase_admin_client()

    # Verify the user owns the bot
    bot_res = supabase_admin.table('discord_bots').select('id').eq('id', bot_id).eq('user_id', user_id).single().execute()
    if not bot_res.data:
        return jsonify({'status': 'error', 'message': 'Bot not found or permission denied.'}), 404

    # Set status to 'online'. The service will handle the rest.
    supabase_admin.table('discord_bots').update({'status': 'online'}).eq('id', bot_id).execute()

    return jsonify({'status': 'success', 'message': 'Bot activation signal sent. The service will bring it online shortly.'})


@app.route('/integrations/discord/delete/<int:bot_id>', methods=['POST'])
@login_required
def delete_discord_bot(bot_id):
    """Deletes a discord bot after verifying ownership."""
    user_id = session['user']['id']
    
    # We'll create a helper function in db_utils to handle the database logic
    success = db_utils.delete_discord_bot_for_user(bot_id, user_id)

    if success:
        return jsonify({'status': 'success', 'message': 'Bot has been successfully deleted.'})
    else:
        return jsonify({'status': 'error', 'message': 'Bot not found or you do not have permission to delete it.'}), 404
@app.route('/api/discord_bots/status')
@login_required
def get_discord_bots_status():
    """
    API endpoint for the frontend to poll for live bot statuses.
    """
    user_id = session['user']['id']
    supabase_admin = get_supabase_admin_client()
    bots_res = supabase_admin.table('discord_bots').select('id, status, channel:youtube_channel_id(channel_name, channel_thumbnail)').eq('user_id', user_id).execute()
    return jsonify(bots_res.data)  

@app.route('/auth/discord')
@login_required
def discord_auth():
    DISCORD_CLIENT_ID = os.environ.get("DISCORD_SHARED_CLIENT_ID")
    # This callback URL must be added to your Discord App's OAuth2 settings
    REDIRECT_URI = url_for('discord_auth_callback', _external=True)
    # The 'identify' scope allows us to get the user's ID, username, etc.
    OAUTH2_URL = f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20email"
    return redirect(OAUTH2_URL)

@app.route('/auth/discord/callback')
@login_required
def discord_auth_callback():
    code = request.args.get('code')
    user_id = session['user']['id']
    
    # Exchange the code for an access token
    DISCORD_CLIENT_ID = os.environ.get("DISCORD_SHARED_CLIENT_ID")
    DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_SHARED_CLIENT_SECRET")
    REDIRECT_URI = url_for('discord_auth_callback', _external=True)
    
    token_data = {
        'client_id': DISCORD_CLIENT_ID,
        'client_secret': DISCORD_CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': REDIRECT_URI,
    }
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    
    r = requests.post('https://discord.com/api/v10/oauth2/token', data=token_data, headers=headers)
    r.raise_for_status()
    access_token = r.json()['access_token']
    
    # Use the access token to get the user's info
    user_info_r = requests.get('https://discord.com/api/v10/users/@me', headers={'Authorization': f'Bearer {access_token}'})
    user_info = user_info_r.json()
    discord_user_id = user_info['id']
    
    # Save the Discord User ID to the user's profile
    db_utils.create_or_update_profile({
        'id': user_id, 
        'discord_user_id': discord_user_id,
        'email': user_info.get('email') # Pass the email to the database
    })
    
    flash('Your Discord account has been successfully linked!', 'success')
    return redirect(url_for('discord_dashboard'))

# --- NEW: Function to restart bots on server startup ---



@app.route('/integrations/discord/toggle_bot/<int:bot_id>', methods=['POST'])
@login_required
def toggle_discord_bot(bot_id):
    user_id = session['user']['id']
    supabase_admin = get_supabase_admin_client()

    bot_res = supabase_admin.table('discord_bots').select('status').eq('id', bot_id).eq('user_id', user_id).single().execute()

    if not bot_res.data:
        return jsonify({'status': 'error', 'message': 'Bot not found or permission denied.'}), 404

    current_status = bot_res.data['status']

    # Determine the new status
    new_status = 'offline' if current_status == 'online' else 'online'

    # Update the status in the database
    supabase_admin.table('discord_bots').update({'status': new_status}).eq('id', bot_id).execute()

    message = f"Signal sent. The bot will be brought {new_status} by the service shortly."
    return jsonify({'status': 'success', 'message': message})

@app.route('/integrations/creator_links')
@login_required
def creator_links():
    """
    Renders the page that displays the public shareable links for each of the user's channels.
    """
    return render_template('creator_links.html', saved_channels=get_user_channels())


@app.route('/c/<path:channel_name>')
def public_chat_page(channel_name):
    """
    Public-facing page for a creator's AI persona.
    - For logged-out users, it shows the page and prompts login.
    - For logged-in users, it adds the channel if they have space,
      or provides a temporary session if they are at their plan limit.
    """
    supabase_admin = get_supabase_admin_client()
    channel_response = supabase_admin.table('channels').select('*').eq('channel_name', channel_name).maybe_single().execute()

    if not channel_response.data:
        return render_template('error.html', error_message="This AI persona could not be found."), 404

    channel = channel_response.data
    
    # --- START: NEW FEATURE LOGIC ---
    if 'user' in session:
        user_id = session['user']['id']
        channel_id = channel['id']
        
        # Check if the user is already linked to this channel
        link_check = supabase_admin.table('user_channels').select('user_id').eq('user_id', user_id).eq('channel_id', channel_id).execute()

        if link_check.data:
            # User already has this channel, just redirect them
            return redirect(url_for('ask', channel_name=channel['channel_name']))

        # This is a new channel for the user, so we must check their limits
        user_status = get_user_status(user_id)
        max_channels = user_status['limits'].get('max_channels', 0)
        current_channels = user_status['usage'].get('channels_processed', 0)

        if current_channels < max_channels:
            # The user has space, so add the channel permanently
            db_utils.link_user_to_channel(user_id, channel_id)
            db_utils.increment_channels_processed(user_id)
            flash(f"'{channel['channel_name']}' has been added to your channels.", "success")
            return redirect(url_for('ask', channel_name=channel['channel_name']))
        else:
            # The user is at their limit, provide a temporary session
            flash(f"You have reached your channel limit. You can view this channel for this session only.", "info")
            
            # Render the page directly instead of redirecting.
            # The channel will not be saved to their sidebar.
            return render_template(
                'ask.html', 
                history=[],
                channel_name=channel['channel_name'], 
                current_channel=channel,
                saved_channels=get_user_channels(), # This shows their actual saved channels
                SUPABASE_URL=os.environ.get('SUPABASE_URL'),
                SUPABASE_ANON_KEY=os.environ.get('SUPABASE_ANON_KEY')
            )
    # --- END: NEW FEATURE LOGIC ---

    # This part remains the same for logged-out users
    shared_history = []
    history_id = request.args.get('history_id')
    if history_id and redis_client:
        try:
            history_json = redis_client.get(f"shared_chat:{history_id}")
            if history_json:
                shared_history = json.loads(history_json)
        except Exception as e:
            logging.error(f"Error retrieving shared chat {history_id} from Redis: {e}")

    session['referred_by_channel_id'] = channel['id']

    return render_template('ask.html', 
        history=shared_history,
        channel_name=channel['channel_name'], 
        current_channel=channel,
        saved_channels={}, # Sidebar is empty for logged-out users
        SUPABASE_URL=os.environ.get('SUPABASE_URL'),
        SUPABASE_ANON_KEY=os.environ.get('SUPABASE_ANON_KEY')
    )

@app.route('/api/share_chat', methods=['POST'])
@login_required
def share_chat_history():
    """
    Saves a chat history to Redis for temporary sharing and returns a unique ID.
    """
    if not redis_client:
        return jsonify({'status': 'error', 'message': 'Sharing feature is not configured.'}), 500

    history_data = request.json.get('history')
    if not history_data:
        return jsonify({'status': 'error', 'message': 'No history provided.'}), 400

    history_id = str(uuid.uuid4())
    # Store the history in Redis for 24 hours (86400 seconds)
    redis_client.setex(f"shared_chat:{history_id}", 86400, json.dumps(history_data))
    
    return jsonify({'status': 'success', 'history_id': history_id})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)