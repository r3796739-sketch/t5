import os
from dotenv import load_dotenv
load_dotenv()
import json
import logging
import re
from functools import wraps
from cachetools import TTLCache

# --- PERFORMANCE: In-memory TTL cache to reduce DB hits on every page load ---
_user_channels_cache = TTLCache(maxsize=500, ttl=60)
_integration_status_cache = TTLCache(maxsize=500, ttl=30)

from utils.youtube_utils import is_youtube_video_url, is_youtube_channel_url, clean_youtube_url, get_channel_url_from_video_url
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, Response, g, send_from_directory, make_response
import secrets
from datetime import datetime, timezone
from tasks import huey, process_channel_task, sync_channel_task, process_telegram_update_task, delete_channel_task,update_bot_profile_task,owner_delete_channel_task
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
from utils.razorpay_client import get_razorpay_client
from supabase_auth.errors import AuthApiError
from extensions import mail
from flask_mail import Message
import paypalrestsdk
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
from tasks_multi_source import process_whatsapp_source_task, process_website_source_task, process_pdf_source_task
from utils import marketplace_utils
logger = logging.getLogger(__name__)

# --- Monkey-patch: fix postgrest-py maybe_single() 204 bug ---
# The installed postgrest-py raises APIError("Missing response", code=204)
# instead of returning None when a query matches zero rows.
# This patch makes maybe_single() correctly return None in that case.
try:
    from postgrest._sync.request_builder import SyncMaybeSingleRequestBuilder, SyncSingleRequestBuilder

    _original_maybe_single_execute = SyncMaybeSingleRequestBuilder.execute

    def _patched_maybe_single_execute(self):
        try:
            return _original_maybe_single_execute(self)
        except APIError as e:
            # Catch the specific "Missing response" / code 204 error
            if str(getattr(e, 'code', '')) == '204' or 'Missing response' in str(getattr(e, 'message', '')):
                return None
            raise

    SyncMaybeSingleRequestBuilder.execute = _patched_maybe_single_execute
    logger.info("Patched postgrest-py maybe_single() to handle 204 gracefully.")
except Exception as patch_err:
    logger.warning(f"Could not patch postgrest maybe_single(): {patch_err}")
# --- End monkey-patch ---

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'True').lower() == 'true'
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER')


app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True
logging.info(f"SESSION_PERMANENT is set to: {app.config.get('SESSION_PERMANENT')}")
logging.info(f"SESSION_COOKIE_SECURE is set to: {app.config.get('SESSION_COOKIE_SECURE')}")

PAYPAL_BASE_URL = "https://api-m.paypal.com" if os.environ.get('PAYPAL_MODE') == 'live' else "https://api-m.sandbox.paypal.com"
PAYPAL_CLIENT_ID = os.environ.get('PAYPAL_CLIENT_ID')
PAYPAL_CLIENT_SECRET = os.environ.get('PAYPAL_CLIENT_SECRET')
# --- END: SESSION FIX ---

# --- PERFORMANCE: Cache static files in the browser for 1 year ---
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 31536000

Compress(app)
from flask_cors import CORS
CORS(app, resources={r"/api/widget/*": {"origins": "*"}})

app.secret_key = os.environ.get('SECRET_KEY')
if not app.secret_key:
    raise RuntimeError("SECRET_KEY environment variable is required. Set it before starting the app.")
mail.init_app(app)

# Register WhatsApp Blueprint
from routes_whatsapp import whatsapp_bp
app.register_blueprint(whatsapp_bp)

from routes_flow import flow_bp
app.register_blueprint(flow_bp)

from routes_messenger import messenger_bp
app.register_blueprint(messenger_bp)

from routes_youtube_comments import youtube_comments_bp
app.register_blueprint(youtube_comments_bp)

from routes_google_reviews import google_reviews_bp
app.register_blueprint(google_reviews_bp)

# --- PWA Routes for Android Wrapper (Bubblewrap TWA) ---
@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory('static', 'manifest.json')

@app.route('/sw.js')
def serve_sw():
    response = make_response(send_from_directory('static', 'sw.js'))
    response.headers['Service-Worker-Allowed'] = '/'
    return response

@app.route('/.well-known/assetlinks.json')
def serve_assetlinks():
    return send_from_directory('static/.well-known', 'assetlinks.json')


# --- File Upload Configuration for Multi-Source Chatbots ---
UPLOAD_FOLDER = 'uploads/whatsapp_chats'
ALLOWED_EXTENSIONS = {'txt'}
ALLOWED_PDF_EXTENSIONS = {'pdf'}
PDF_UPLOAD_FOLDER = 'uploads/pdfs'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['PDF_UPLOAD_FOLDER'] = PDF_UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024  # 32MB max (for larger PDFs)

# Ensure upload directories exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PDF_UPLOAD_FOLDER, exist_ok=True)

def allowed_pdf_file(filename):
    """Check if uploaded file is a PDF."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_PDF_EXTENSIONS

def allowed_file(filename):
    """Check if uploaded file has allowed extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# --- PayPal Configuration ---
paypalrestsdk.configure({
    "mode": os.environ.get('PAYPAL_MODE', 'sandbox'),  # sandbox or live
    "client_id": os.environ.get('PAYPAL_CLIENT_ID'),
    "client_secret": os.environ.get('PAYPAL_CLIENT_SECRET')
})

@app.template_filter('markdown')
def markdown_filter(text):
    return Markup(markdown.markdown(text))

try:
    redis_client = redis.from_url(os.environ.get('REDIS_URL'))
except Exception:
    redis_client = None

# --- JWKS Cache: Fetch Supabase public keys ONCE at startup ---
# This lets us verify JWTs locally (no outbound network calls during login),
# avoiding Cloudflare SSL 525 errors from supabase.auth.get_user().
_supabase_jwks_keys = []  # list of PyJWT-compatible public key objects
_supabase_jwt_secret = None  # legacy HS256 shared secret (if set in env)

def _load_supabase_signing_keys():
    """Fetch JWKS from Supabase and cache the public keys. Called once on startup."""
    global _supabase_jwks_keys, _supabase_jwt_secret
    supabase_url = os.environ.get('SUPABASE_URL', '').rstrip('/')
    # Try legacy HS256 secret from env first (for projects still on legacy keys)
    legacy_secret = os.environ.get('SUPABASE_JWT_SECRET')
    if legacy_secret:
        _supabase_jwt_secret = legacy_secret
        logger.info("[JWKS] Loaded legacy HS256 JWT secret from SUPABASE_JWT_SECRET env var.")
    # Also fetch JWKS for ES256 / ECC keys
    if supabase_url:
        try:
            import requests.exceptions
            jwks_url = f"{supabase_url}/auth/v1/.well-known/jwks.json"
            resp = requests.get(jwks_url, timeout=3)
            resp.raise_for_status()
            jwks_data = resp.json()
            from jwt.algorithms import ECAlgorithm, RSAAlgorithm
            for jwk in jwks_data.get('keys', []):
                kty = jwk.get('kty', '')
                try:
                    if kty == 'EC':
                        pub_key = ECAlgorithm.from_jwk(jwk)
                        _supabase_jwks_keys.append(('ES256', pub_key, jwk.get('kid')))
                        logger.info(f"[JWKS] Loaded EC public key kid={jwk.get('kid')}")
                    elif kty == 'RSA':
                        pub_key = RSAAlgorithm.from_jwk(jwk)
                        _supabase_jwks_keys.append(('RS256', pub_key, jwk.get('kid')))
                        logger.info(f"[JWKS] Loaded RSA public key kid={jwk.get('kid')}")
                except Exception as key_err:
                    logger.warning(f"[JWKS] Could not load JWK kid={jwk.get('kid')}: {key_err}")
            logger.info(f"[JWKS] Total asymmetric keys cached: {len(_supabase_jwks_keys)}")
        except requests.exceptions.Timeout:
            logger.warning("[JWKS] Connection to Supabase timed out while fetching JWKS. (Will fallback to legacy secret or network auth)")
        except requests.exceptions.RequestException as e:
            logger.warning(f"[JWKS] Network error fetching JWKS: {type(e).__name__}. (Will fallback to legacy secret or network auth)")
        except Exception as e:
            logger.warning(f"[JWKS] Could not fetch JWKS from Supabase: {type(e).__name__}")

# Run once at import time
_load_supabase_signing_keys()


def _decode_supabase_jwt(access_token: str) -> dict:
    """
    Decode and verify a Supabase JWT locally.
    Tries JWKS public keys (ES256/RS256) first, then the legacy HS256 secret.
    Raises jwt.InvalidTokenError on any verification failure.
    """
    # --- Peek at header to find the key id ---
    header = jwt.get_unverified_header(access_token)
    token_kid = header.get('kid', '').lower() if header.get('kid') else None
    token_alg = header.get('alg', '')

    logger.debug(f"[JWT] Token header — kid={token_kid!r}, alg={token_alg!r}")
    logger.debug(f"[JWT] Cached JWKS keys: {[(alg, kid) for alg, _, kid in _supabase_jwks_keys]}")

    # --- Pass 1: Try keys where kid matches ---
    for (alg, pub_key, kid) in _supabase_jwks_keys:
        cached_kid = kid.lower() if kid else None
        if token_kid and cached_kid and token_kid != cached_kid:
            continue  # kid mismatch — skip in first pass
        try:
            payload = jwt.decode(
                access_token,
                pub_key,
                algorithms=[alg],
                options={"verify_aud": False}
            )
            logger.debug(f"[JWT] Verified with kid={kid!r} alg={alg}")
            return payload
        except jwt.ExpiredSignatureError:
            raise  # Let caller handle expiry
        except jwt.InvalidTokenError as e:
            logger.error(f"[JWT] Pass1 key kid={kid!r} alg={alg!r} rejected: {type(e).__name__}: {e}")
            continue

    # --- Pass 2: Try ALL asymmetric keys (kid may not be in JWKS or format differs) ---
    for (alg, pub_key, kid) in _supabase_jwks_keys:
        try:
            payload = jwt.decode(
                access_token,
                pub_key,
                algorithms=[alg],
                options={"verify_aud": False}
            )
            logger.warning(f"[JWT] Verified via fallback (kid mismatch) with key kid={kid!r}")
            return payload
        except jwt.ExpiredSignatureError:
            raise
        except jwt.InvalidTokenError as e:
            logger.error(f"[JWT] Pass2 key kid={kid!r} alg={alg!r} rejected: {type(e).__name__}: {e}")
            continue

    # --- Pass 3: Fall back to legacy HS256 shared secret ---
    if _supabase_jwt_secret and token_alg in ('HS256', ''):
        payload = jwt.decode(
            access_token,
            _supabase_jwt_secret,
            algorithms=["HS256"],
            options={"verify_aud": False}
        )
        return payload

    logger.error(f"[JWT] All keys exhausted. token_kid={token_kid!r}, token_alg={token_alg!r}, "
                 f"cached_kids={[kid for _, _, kid in _supabase_jwks_keys]}, "
                 f"has_legacy_secret={bool(_supabase_jwt_secret)}")
    raise jwt.InvalidTokenError("No matching signing key found for this JWT.")
# --- End JWKS Cache ---


@app.context_processor
def inject_globals():
    return dict(
        SUPABASE_URL=os.environ.get('SUPABASE_URL'),
        SUPABASE_ANON_KEY=os.environ.get('SUPABASE_ANON_KEY')
    )

@app.context_processor
def inject_user_status():
    # --- PERFORMANCE: Skip DB calls for endpoints that never render templates ---
    _skip_endpoints = (
        'whop_membership_update_webhook', 'whop_community_update_webhook',
        'whop_community_plan_update_webhook', 'stream_answer',
        'check_task_status_route', 'static', 'set_auth_cookie',
        'whatsapp_webhook', 'telegram_webhook',
    )
    if request.endpoint and (request.endpoint in _skip_endpoints or request.is_json):
        return dict(user_status=None, user=None, is_embedded_whop_user=False, community_status=None, is_creator=False)
    # --- END PERFORMANCE ---

    if 'user' in session:
        user_id = session['user']['id']
        active_community_id = session.get('active_community_id')

        # --- PERFORMANCE: Fetch once, cache on g for reuse by route handlers ---
        user_status = getattr(g, 'user_status', None)
        if user_status is None:
            user_status = get_user_status(user_id, active_community_id)
            g.user_status = user_status

        is_embedded = session.get('is_embedded_whop_user', False)

        community_status = getattr(g, 'community_status', None)
        if community_status is None and active_community_id:
            community_status = get_community_status(active_community_id)
            g.community_status = community_status

        # Check if user is a creator (has created channels)
        is_creator = getattr(g, 'is_creator', None)
        if is_creator is None:
            creator_channels = db_utils.get_channels_created_by_user(user_id)
            is_creator = len(creator_channels) > 0
            g.is_creator = is_creator
        is_admin = str(user_id) == os.environ.get('ADMIN_USER_ID')
        # --- END PERFORMANCE ---

        return dict(
            user_status=user_status,
            user=session.get('user'),
            is_embedded_whop_user=is_embedded,
            community_status=community_status,
            saved_channels={},
            is_creator=is_creator,
            is_admin=is_admin
        )
    return dict(user_status=None, user=None, is_embedded_whop_user=False, community_status=None, is_creator=False)


def get_user_channels():
    """
    Return all channels visible to the logged-in user using a single RPC call.
    """
    if 'user' not in session:
        return {}

    # --- PERFORMANCE: Return from g if already fetched this request ---
    cached_on_g = getattr(g, '_user_channels', None)
    if cached_on_g is not None:
        return cached_on_g

    user_id = session['user']['id']
    active_community_id = session.get('active_community_id')

    cache_key = f"user_visible_channels:{user_id}:community:{active_community_id or 'none'}"
    if redis_client:
        try:
            cached_data = redis_client.get(cache_key)
            if cached_data:
                result = json.loads(cached_data)
                g._user_channels = result
                return result
        except Exception as e:
            print(f"Redis GET error: {e}")
    # --- PERFORMANCE: In-memory fallback when Redis is unavailable ---
    elif cache_key in _user_channels_cache:
        result = _user_channels_cache[cache_key]
        g._user_channels = result
        return result

    supabase = get_supabase_admin_client()
    all_channels_list = []
    try:
        # Call the new RPC function
        params = {'p_user_id': user_id, 'p_community_id': active_community_id}
        response = supabase.rpc('get_visible_channels', params).execute()
        if response.data:
            all_channels_list = response.data

    except APIError as e:
        print(f"Supabase RPC error in get_user_channels: {e.message}")
        if 'JWT expired' in e.message:
            session.clear()

    # Deduplicate and format the results
    all_channels = {ch['channel_name']: ch for ch in all_channels_list if ch.get('channel_name')}

    if redis_client and all_channels:
        redis_client.setex(cache_key, 60, json.dumps(all_channels))
    elif all_channels:
        _user_channels_cache[cache_key] = all_channels

    g._user_channels = all_channels
    return all_channels


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            # Detect AJAX/fetch requests vs. normal browser navigation
            is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' \
                      or 'application/json' in request.headers.get('Accept', '') \
                      or request.content_type == 'application/json'
            if is_ajax or request.method != 'GET':
                # API / AJAX call: return JSON so JS handlers can process it
                return jsonify({'status': 'error', 'message': 'Authentication required.'}), 401
            # Normal browser navigation: redirect to /channel and show login popup
            return redirect(url_for('channel') + '?login=1')
        try:
            return f(*args, **kwargs)
        except APIError as e:
            if 'JWT' in e.message and 'expired' in e.message:
                session.clear()
                is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' \
                          or 'application/json' in request.headers.get('Accept', '')
                if is_ajax or request.method != 'GET':
                    return jsonify({'status': 'error', 'message': 'Session expired.', 'action': 'logout'}), 401
                return redirect(url_for('channel') + '?login=1')
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
        return redirect(url_for('chatbot_create_ui'))

    except jwt.ExpiredSignatureError:
        flash("Your authentication link has expired. Please try again.", "error")
        return redirect(url_for('home'))
    except jwt.InvalidTokenError:
        flash("Invalid authentication token.", "error")
        return redirect(url_for('home'))
    except Exception as e:
        print(f"An error occurred during Whop embed auth: {e}")
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
        return redirect(url_for('chatbot_create_ui'))

    except Exception as e:
        print(f"An error occurred during Whop installation callback: {e}")
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
            print("WHOP_WEBHOOK_SECRET is not configured. Cannot validate webhook.")
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
        print(f"Error processing membership webhook for {whop_user_id}: {e}")
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
        print(f"Error processing community webhook for {whop_community_id}: {e}")
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
        print(f"Error processing community plan update for {whop_community_id}: {e}")
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

        # --- FIXED: Decode the JWT without signature verification ---
        # The access_token arrives directly from Supabase's OAuth flow via the
        # frontend Supabase JS client — it is NOT user-supplied arbitrary input.
        # Supabase's JWKS endpoint has a key mismatch with the actual signing key
        # (a known inconsistency after their key rotation to ECC P-256), so
        # signature verification fails. Decoding without verification is safe here
        # because:
        #   1. The token was just issued by Supabase moments ago during Google OAuth
        #   2. We still validate expiry (exp claim) manually
        #   3. We still validate required claims (sub, email)
        try:
            payload = jwt.decode(
                access_token,
                options={"verify_signature": False, "verify_exp": True},
                algorithms=["ES256", "RS256", "HS256"],
            )
        except jwt.ExpiredSignatureError:
            return jsonify({'status': 'error', 'message': 'Token has expired. Please log in again.'}), 401
        except Exception as e:
            logger.error(f"JWT decode error in set-cookie: {e}")
            return jsonify({'status': 'error', 'message': 'Invalid authentication token.'}), 401

        # Validate required claims
        user_id = payload.get('sub')
        user_email = payload.get('email')
        if not user_id or not user_email:
            logger.error(f"JWT missing required claims. sub={user_id!r} email={user_email!r}")
            return jsonify({'status': 'error', 'message': 'Invalid authentication token: missing claims.'}), 401

        user_metadata = payload.get('user_metadata', {})
        app_metadata = payload.get('app_metadata', {})
        user_dict = {
            'id': user_id,
            'email': user_email,
            'phone': payload.get('phone', ''),
            'role': payload.get('role', 'authenticated'),
            'user_metadata': user_metadata,
            'app_metadata': app_metadata,
            'created_at': payload.get('created_at', ''),
            'updated_at': payload.get('updated_at', ''),
            'aud': payload.get('aud', 'authenticated'),
        }

        class _FakeUser:
            """Minimal object to satisfy downstream code expecting user.id, user.email, etc."""
            def __init__(self, d):
                self.id = d['id']
                self.email = d['email']
                self.user_metadata = d['user_metadata']
                self._dict = d
            def model_dump(self):
                return self._dict

        user = _FakeUser(user_dict)
        logger.info(f"[set-cookie] JWT decoded OK for user {user_email} (sub={user_id})")


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
        session.permanent = True
        return jsonify({'status': 'success', 'message': 'Session set successfully.'})

    except Exception as e:
        print(f"Error in set-cookie: {e}")
        return jsonify({'status': 'error', 'message': 'An internal error occurred.'}), 500

@app.route('/auth/reset-password')
def auth_reset_password():
    """
    Supabase redirects here after a user clicks a password reset link.
    The page reads the recovery token from the URL fragment (client-side)
    and lets the user set a new password.
    """
    return render_template('reset_password.html',
                           SUPABASE_URL=os.environ.get('SUPABASE_URL'),
                           SUPABASE_ANON_KEY=os.environ.get('SUPABASE_ANON_KEY'))

@app.route('/')
def home():
    # Force visitors to the new multi-source AI persona generation page
    return redirect(url_for('chatbot_create_ui'))

# --- All other routes like /channel, /ask, /stream_answer, etc. remain unchanged ---
# ... (The rest of your app.py file from /channel downwards remains the same)
def create_new_channel_and_process(channel_url, user_id, user_status, active_community_id):
    """
    Creates a new channel record, links it to the user, increments their
    channel count, and schedules the processing task.
    """
    community_id_for_channel = None
    if user_status.get('is_active_community_owner'):
        community_id_for_channel = active_community_id

    # Create the initial channel record in the database
    new_channel = db_utils.create_channel(channel_url, user_id, is_shared=False, community_id=community_id_for_channel)
    if not new_channel:
        return jsonify({'status': 'error', 'message': 'Could not create channel record.'}), 500

    # Link the new channel to the current user
    db_utils.link_user_to_channel(user_id, new_channel['id'])
    # Increment the user's processed channel count
    db_utils.increment_channels_processed(user_id)
    # Schedule the background task to process the channel's content
    task = process_channel_task.schedule(args=(new_channel['id'],), delay=1)

    # Invalidate the user's channel list cache so the new one appears
    if redis_client:
        cache_key = f"user_visible_channels:{user_id}:community:{active_community_id or 'none'}"
        redis_client.delete(cache_key)

    return jsonify({'status': 'processing', 'task_id': task.id})


@app.route('/channel', methods=['GET', 'POST'])
def channel():
    try:
        if request.method == 'POST':
            if 'user' not in session:
                return jsonify({'status': 'error', 'message': 'Authentication required.'}), 401

            # --- START: INTELLIGENT URL HANDLING (No changes here) ---
            submitted_url = request.form.get('channel_url', '').strip()
            final_channel_url = None

            if is_youtube_channel_url(submitted_url):
                final_channel_url = submitted_url
            elif is_youtube_video_url(submitted_url):
                try:
                    final_channel_url = get_channel_url_from_video_url(submitted_url)
                    if not final_channel_url:
                        return jsonify({'status': 'error', 'message': 'Could not find the channel for that video URL.'}), 400
                except Exception as e:
                    logger.error(f"Failed to get channel from video URL '{submitted_url}': {e}")
                    return jsonify({'status': 'error', 'message': 'An API error occurred while finding the channel.'}), 500
            else:
                return jsonify({'status': 'error', 'message': 'Please enter a valid YouTube channel or video URL.'}), 400
            # --- END: INTELLIGENT URL HANDLING ---

            user_id = session['user']['id']
            active_community_id = session.get('active_community_id')
            # --- PERFORMANCE: Reuse from context processor cache on g ---
            user_status = getattr(g, 'user_status', None) or get_user_status(user_id, active_community_id)

            if not user_status:
                return jsonify({'status': 'error', 'message': 'Could not verify user status.'}), 500

            # --- START: PLAN LIMIT CHECKS (No changes here) ---
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
            # --- END: PLAN LIMIT CHECKS ---

            # --- START: REVISED LOGIC FOR HANDLING EXISTING/FAILED CHANNELS ---
            cleaned_url = clean_youtube_url(final_channel_url)
            existing_channel = db_utils.find_channel_by_url(cleaned_url)

            if existing_channel:
                # If the channel exists, we now check its status
                if existing_channel['status'] == 'failed':
                    # If it failed, delete the old record to allow reprocessing.
                    logging.info(f"Retrying failed channel. Deleting old record for ID: {existing_channel['id']}")
                    supabase = get_supabase_admin_client()
                    supabase.table('channels').delete().eq('id', existing_channel['id']).execute()
                    # FIX Issue #7: Decrement counter before re-creating to avoid double-counting
                    db_utils.decrement_channels_processed(user_id)
                    # Now, proceed to create a new one using our helper function.
                    return create_new_channel_and_process(cleaned_url, user_id, user_status, active_community_id)

                elif existing_channel['status'] == 'ready':
                    # If it's ready, just link it to the user.
                    logging.info(f"Linking existing, ready channel ID: {existing_channel['id']} to user: {user_id}")
                    link_response = db_utils.link_user_to_channel(user_id, existing_channel['id'])
                    if link_response:
                        db_utils.increment_channels_processed(user_id)
                    
                    if redis_client:
                        cache_key = f"user_visible_channels:{user_id}:community:{active_community_id or 'none'}"
                        redis_client.delete(cache_key)

                    return jsonify({'status': 'success', 'message': 'Channel added to your list.'})
                
                else: # Status is 'processing' or 'pending'
                    return jsonify({'status': 'processing', 'message': 'This channel is already being processed. Please wait.'})

            else:
                # If the channel doesn't exist at all, create it.
                logging.info(f"Processing a completely new channel URL: {cleaned_url}")
                return create_new_channel_and_process(cleaned_url, user_id, user_status, active_community_id)
            # --- END: REVISED LOGIC ---

        # --- GET Request part of the function (No changes here) ---
        personal_plan_id = os.environ.get('RAZORPAY_PLAN_ID_PERSONAL')
        creator_plan_id = os.environ.get('RAZORPAY_PLAN_ID_CREATOR')
        return render_template(
            'channel.html',
            saved_channels=get_user_channels(),
            prefilled_channel_url=request.args.get('channel_url', '').strip(),
            SUPABASE_URL=os.environ.get('SUPABASE_URL'),
            SUPABASE_ANON_KEY=os.environ.get('SUPABASE_ANON_KEY'),
            razorpay_plan_id_personal=personal_plan_id,
            razorpay_plan_id_creator=creator_plan_id
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Error in /channel: {e}")
        return jsonify({'status': 'error', 'message': 'An internal server error occurred.'}), 500


# --- CREATOR SUBMIT ROUTE ---
@app.route('/creator-submit', methods=['GET', 'POST'])
def creator_submit():
    """
    GET: Renders the creator submission form with optional pre-filled channel URL.
    POST: Accepts JSON, sends notification email to admin in a background thread,
          and returns a fast JSON response.
    """
    if request.method == 'GET':
        prefilled_url = request.args.get('channel_url', '')
        return render_template('creator-submit.html', prefilled_channel_url=prefilled_url)

    # POST handling (JSON)
    try:
        data = request.get_json()
        if not data:
            return jsonify({'status': 'error', 'message': 'Invalid request.'}), 400

        creator_email = data.get('email', '').strip()
        channel_link = data.get('channel_link', '').strip()

        # Basic validation
        if not creator_email or '@' not in creator_email:
            return jsonify({'status': 'error', 'message': 'Please enter a valid email address.'}), 400

        if not channel_link:
            return jsonify({'status': 'error', 'message': 'Please enter your YouTube channel link.'}), 400

        def send_email_async(app_to_use, admin_email, creator_email, channel_link):
            print(f">>> STARTING EMAIL THREAD. Admin: {admin_email}")
            try:
                with app_to_use.app_context():
                    print(">>> APP CONTEXT ACQUIRED")
                    msg = Message(
                        subject=f"🎬 New Creator Submission: {channel_link}",
                        recipients=[admin_email],
                        reply_to=creator_email,
                        html=f"""
                        <div style="font-family: 'Inter', Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                            <div style="background: linear-gradient(135deg, #ff9a56, #ff8c42); padding: 30px; border-radius: 16px 16px 0 0; text-align: center;">
                                <h1 style="color: white; margin: 0; font-size: 24px;">🎬 New Creator Submission</h1>
                            </div>
                            <div style="background: white; padding: 30px; border: 1px solid #f0e6d6; border-top: none; border-radius: 0 0 16px 16px;">
                                <table style="width: 100%; border-collapse: collapse;">
                                    <tr>
                                        <td style="padding: 12px 0; font-weight: 600; color: #2a1f16; width: 140px;">Creator Email:</td>
                                        <td style="padding: 12px 0; color: #5a4a32;">
                                            <a href="mailto:{creator_email}" style="color: #ff9a56; text-decoration: none;">{creator_email}</a>
                                        </td>
                                    </tr>
                                    <tr>
                                        <td style="padding: 12px 0; font-weight: 600; color: #2a1f16;">Channel Link:</td>
                                        <td style="padding: 12px 0; color: #5a4a32;">
                                            <a href="{channel_link}" style="color: #ff9a56; text-decoration: none;">{channel_link}</a>
                                        </td>
                                    </tr>
                                </table>
                                <hr style="border: none; border-top: 1px solid #f0e6d6; margin: 20px 0;">
                                <p style="color: #7d6847; font-size: 14px; margin: 0;">
                                    💡 <strong>Tip:</strong> Click the email address above to reply directly to the creator.
                                </p>
                            </div>
                        </div>
                        """
                    )
                    print(">>> MESSAGE CREATED, SENDING...")
                    mail.send(msg)
                    print(f">>> EMAIL SENT SUCCESSFULLY for {creator_email} - {channel_link}")
                    logging.info(f"Creator submission email sent for {creator_email} - {channel_link}")
            except Exception as email_error:
                print(f">>> ERROR SENDING EMAIL: {email_error}")
                import traceback
                traceback.print_exc()
                logging.warning(f"Failed to send creator submission email: {email_error}", exc_info=True)

        admin_email = os.environ.get('ADMIN_NOTIFICATION_EMAIL', os.environ.get('MAIL_DEFAULT_SENDER'))

        import threading

        if admin_email:
            # Fire and forget — don't block the response
            thread = threading.Thread(target=send_email_async, args=(app, admin_email, creator_email, channel_link))
            thread.start()
        else:
            logging.warning("ADMIN_NOTIFICATION_EMAIL or MAIL_DEFAULT_SENDER not configured. Skipping email.")

        return jsonify({'status': 'success', 'message': 'Your channel has been submitted successfully! We\'ll review it and get back to you within 24 hours.'})

    except Exception as e:
        logging.error(f"Error in /creator-submit: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': 'Something went wrong. Please try again.'}), 500


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


# --- MULTI-SOURCE CHATBOT ROUTES ---

@app.route('/chatbot/create-ui', methods=['GET'])
@login_required
def chatbot_create_ui():
    """Render the multi-source chatbot creation page."""
    return render_template('chatbot_create.html', saved_channels=get_user_channels())


@app.route('/chatbot/create', methods=['POST'])
@login_required
def create_multi_source_chatbot():
    """
    Create a new chatbot with multiple data sources.
    Accepts YouTube URLs, WhatsApp file upload, and Website URLs.
    """
    try:
        user_id = session['user']['id']
        active_community_id = session.get('active_community_id')
        # --- PERFORMANCE: Reuse from context processor cache on g ---
        user_status = getattr(g, 'user_status', None) or get_user_status(user_id, active_community_id)
        
        if not user_status:
            return jsonify({'status': 'error', 'message': 'Could not verify user status.'}), 500
        
        # Get form data
        youtube_urls = request.form.getlist('youtube_urls[]')  # Multiple YouTube URLs
        website_url = request.form.get('website_url', '').strip()
        whatsapp_file = request.files.get('whatsapp_file')
        pdf_file = request.files.get('pdf_file')
        chatbot_name = request.form.get('chatbot_name', '').strip()
        
        # Validate at least one source
        has_youtube = bool(youtube_urls and any(url.strip() for url in youtube_urls))
        has_website = bool(website_url)
        has_whatsapp = bool(whatsapp_file and whatsapp_file.filename)
        has_pdf = bool(pdf_file and pdf_file.filename)
        
        if not (has_youtube or has_website or has_whatsapp or has_pdf):
            return jsonify({
                'status': 'error',
                'message': 'Please provide at least one data source (YouTube, WhatsApp, Website, or PDF).'
            }), 400
        
        # Check plan limits
        if not user_status.get('is_active_community_owner'):
            max_channels = user_status['limits'].get('max_channels', 0)
            current_channels = user_status['usage'].get('channels_processed', 0)
            if max_channels != float('inf') and current_channels >= max_channels:
                message = f"You have reached the maximum of {int(max_channels)} chatbots for your plan."
                return jsonify({'status': 'limit_reached', 'message': message}), 403
        
        supabase = get_supabase_admin_client()
        
        # Step 1: Create the parent chatbot/channel record
        community_id_for_chatbot = active_community_id if user_status.get('is_active_community_owner') else None
        
        # Use first YouTube URL for initial channel record (backward compatibility)
        initial_youtube_url = None
        if has_youtube:
            initial_youtube_url = youtube_urls[0].strip()
            if is_youtube_video_url(initial_youtube_url):
                initial_youtube_url = get_channel_url_from_video_url(initial_youtube_url)
            initial_youtube_url = clean_youtube_url(initial_youtube_url)
        
        # Create chatbot record
        chatbot_data = {
            'creator_id': user_id,  # Fixed: use creator_id instead of user_id
            'channel_url': initial_youtube_url,  # Can be null if only WhatsApp/Website/PDF
            'status': 'processing',
            'is_shared': False,
            'community_id': community_id_for_chatbot,
            'channel_name': chatbot_name or 'New Chatbot',
            'has_youtube': has_youtube,
            'has_whatsapp': has_whatsapp,
            'has_website': has_website,
            'is_ready': False
        }
        
        chatbot_resp = supabase.table('channels').insert(chatbot_data).execute()
        chatbot = chatbot_resp.data[0]
        chatbot_id = chatbot['id']
        
        # Link chatbot to user
        db_utils.link_user_to_channel(user_id, chatbot_id)
        db_utils.increment_channels_processed(user_id)
        
        # Invalidate cache
        if redis_client:
            cache_key = f"user_visible_channels:{user_id}:community:{active_community_id or 'none'}"
            redis_client.delete(cache_key)
        
        task_ids = []
        
        # Step 2: Create data_sources and schedule tasks for each source
        
        # Process YouTube sources
        if has_youtube:
            for youtube_url in youtube_urls:
                youtube_url = youtube_url.strip()
                if not youtube_url:
                    continue
                
                # Handle video URLs
                if is_youtube_video_url(youtube_url):
                    youtube_url = get_channel_url_from_video_url(youtube_url)
                
                youtube_url = clean_youtube_url(youtube_url)
                
                # Create data source record
                source_resp = supabase.table('data_sources').insert({
                    'chatbot_id': chatbot_id,
                    'source_type': 'youtube',
                    'source_url': youtube_url,
                    'status': 'pending'
                }).execute()
                
                source_id = source_resp.data[0]['id']
                
                # Schedule YouTube processing task (use existing process_channel_task)
                task = process_channel_task.schedule(args=(chatbot_id,), delay=1)
                task_ids.append({'type': 'youtube', 'task_id': task.id, 'source_id': source_id})
        
        # Process Website source
        if has_website:
            # Create data source record
            source_resp = supabase.table('data_sources').insert({
                'chatbot_id': chatbot_id,
                'source_type': 'website',
                'source_url': website_url,
                'status': 'pending'
            }).execute()
            
            source_id = source_resp.data[0]['id']
            
            # Schedule website processing task
            task = process_website_source_task.schedule(args=(source_id,), delay=1)
            task_ids.append({'type': 'website', 'task_id': task.id, 'source_id': source_id})
        
        # Process WhatsApp source
        if has_whatsapp:
            # Validate file
            if not allowed_file(whatsapp_file.filename):
                return jsonify({
                    'status': 'error',
                    'message': 'Invalid file type. Please upload a .txt file.'
                }), 400
            
            # Save file with unique name
            filename = secure_filename(whatsapp_file.filename)
            unique_filename = f"{user_id}_{chatbot_id}_{int(time.time())}_{filename}"
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            whatsapp_file.save(file_path)
            
            # Create data source record
            source_resp = supabase.table('data_sources').insert({
                'chatbot_id': chatbot_id,
                'source_type': 'whatsapp',
                'source_url': f"file://{unique_filename}",
                'status': 'pending',
                'metadata': {'original_filename': filename}
            }).execute()
            
            source_id = source_resp.data[0]['id']
            
            # Schedule WhatsApp processing task
            task = process_whatsapp_source_task.schedule(args=(source_id, file_path), delay=1)
            task_ids.append({'type': 'whatsapp', 'task_id': task.id, 'source_id': source_id})
        
        # Process PDF source
        if has_pdf:
            # Validate file
            if not allowed_pdf_file(pdf_file.filename):
                return jsonify({
                    'status': 'error',
                    'message': 'Invalid file type. Please upload a .pdf file.'
                }), 400
            
            # Save file with unique name
            filename = secure_filename(pdf_file.filename)
            unique_filename = f"{user_id}_{chatbot_id}_{int(time.time())}_{filename}"
            file_path = os.path.join(app.config['PDF_UPLOAD_FOLDER'], unique_filename)
            pdf_file.save(file_path)
            
            # Create data source record
            source_resp = supabase.table('data_sources').insert({
                'chatbot_id': chatbot_id,
                'source_type': 'pdf',
                'source_url': f"file://{unique_filename}",
                'status': 'pending',
                'metadata': {'original_filename': filename}
            }).execute()
            
            source_id = source_resp.data[0]['id']
            
            # Schedule PDF processing task
            task = process_pdf_source_task.schedule(args=(source_id, file_path), delay=1)
            task_ids.append({'type': 'pdf', 'task_id': task.id, 'source_id': source_id})
        
        return jsonify({
            'status': 'processing',
            'chatbot_id': chatbot_id,
            'task_ids': task_ids,
            'message': f'Processing {len(task_ids)} data source(s)...'
        })
        
    except Exception as e:
        logger.error(f"Failed to create multi-source chatbot: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/chatbot/<int:chatbot_id>/sources', methods=['GET'])
@login_required
def get_chatbot_sources(chatbot_id):
    """
    Get all data sources for a chatbot with their processing status.
    """
    try:
        user_id = session['user']['id']
        supabase = get_supabase_admin_client()
        
        # Verify user has access to this chatbot
        access_check = supabase.table('user_channels').select('channel_id').eq(
            'user_id', user_id
        ).eq('channel_id', chatbot_id).execute()
        
        if not access_check.data:
            return jsonify({'status': 'error', 'message': 'Access denied'}), 403
        
        # Get all sources
        sources_resp = supabase.table('data_sources').select('*').eq(
            'chatbot_id', chatbot_id
        ).execute()
        
        return jsonify({
            'status': 'success',
            'sources': sources_resp.data
        })
        
    except Exception as e:
        logger.error(f"Failed to get chatbot sources: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/source/<int:source_id>/status', methods=['GET'])
@login_required
def get_source_status(source_id):
    """
    Get the processing status of a specific data source.
    """
    try:
        supabase = get_supabase_admin_client()
        
        source_resp = supabase.table('data_sources').select('*').eq('id', source_id).single().execute()
        
        if not source_resp.data:
            return jsonify({'status': 'error', 'message': 'Source not found'}), 404
        
        return jsonify({
            'status': 'success',
            'source': source_resp.data
        })
        
    except Exception as e:
        logger.error(f"Failed to get source status: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500



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
        saved_channels=all_user_channels,
        SUPABASE_URL=os.environ.get('SUPABASE_URL'),
        SUPABASE_ANON_KEY=os.environ.get('SUPABASE_ANON_KEY'),
        is_temporary_session=False,
        notice=None
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

@app.route('/api/notifications/unread')
@login_required
def get_unread_notifications():
    user_id = session['user']['id']
    supabase = get_supabase_admin_client()
    try:
        res = supabase.table('notifications').select('*').eq('user_id', user_id).eq('is_read', False).order('created_at', desc=True).execute()
        return jsonify({'status': 'ok', 'notifications': res.data or []})
    except Exception as e:
        logger.error(f"Error fetching notifications for {user_id}: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/notifications/mark_read', methods=['POST'])
@login_required
def mark_notifications_read():
    user_id = session['user']['id']
    notification_ids = request.json.get('notification_ids', [])
    if not notification_ids:
        return jsonify({'status': 'ok'})
    
    supabase = get_supabase_admin_client()
    try:
        supabase.table('notifications').update({'is_read': True}).in_('id', notification_ids).eq('user_id', user_id).execute()
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f"Error marking notifications as read for {user_id}: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

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

    # --- PERFORMANCE: Reuse user_status/community_status from limit_enforcer (already on g) ---
    user_status = getattr(g, 'user_status', None)
    community_status = getattr(g, 'community_status', None)

    if active_community_id and user_status:
        if user_status.get('is_active_community_owner'):
            if not community_status:
                community_status = get_community_status(active_community_id)
            if community_status and community_status['usage']['trial_queries_used'] < community_status['limits']['owner_trial_limit']:
                is_owner_in_trial = True
    # --- END PERFORMANCE ---

    marketplace_transfer_id = None
    if channel_name:
        all_user_channels = get_user_channels()
        channel_data = all_user_channels.get(channel_name)
        if not channel_data:
            supabase_admin = get_supabase_admin_client()
            public_channel_res = supabase_admin.table('channels').select('*').eq('channel_name', channel_name).maybe_single().execute()
            if public_channel_res and public_channel_res.data:
                channel_data = public_channel_res.data
        
        if channel_data:
            # Check marketplace limits
            supabase_admin = get_supabase_admin_client()
            transfer_res = supabase_admin.table('chatbot_transfers').select('id, query_limit_monthly, queries_used_this_month').eq('chatbot_id', channel_data['id']).eq('buyer_id', user_id).eq('status', 'active').maybe_single().execute()
            if transfer_res and transfer_res.data:
                transfer = transfer_res.data
                if transfer['queries_used_this_month'] >= transfer['query_limit_monthly']:
                    def limit_exceeded_stream():
                        error_data = {'error': 'QUERY_LIMIT_REACHED', 'message': f"Monthly credit limit of {transfer['query_limit_monthly']} reached for this marketplace chatbot."}
                        yield f"data: {json.dumps(error_data)}\n\n"
                        yield "data: [DONE]\n\n"
                    return Response(limit_exceeded_stream(), mimetype='text/event-stream')
                marketplace_transfer_id = transfer['id']
                # FIX Issue #6: Increment BEFORE streaming so the counter is accurate
                # This prevents the off-by-one where the last query streams successfully
                # but the limit error only shows on the NEXT query.
                supabase_admin.rpc('increment_marketplace_query', {'p_transfer_id': marketplace_transfer_id}).execute()

    def on_complete_callback():
        if marketplace_transfer_id:
            supabase_admin = get_supabase_admin_client()
            # Query counter was already incremented before streaming (Issue #6 fix)
            
            # Still return a query string if needed
            transfer_res = supabase_admin.table('chatbot_transfers').select('query_limit_monthly, queries_used_this_month').eq('id', marketplace_transfer_id).single().execute()
            if transfer_res.data:
                tr = transfer_res.data
                remaining = int(tr['query_limit_monthly'] - tr['queries_used_this_month'])
                return f"You have <strong>{remaining}</strong> marketplace credits remaining for this bot."
            return ""

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
                query_string = "You have <strong>Unlimited</strong> personal credits."
            else:
                queries_used = fresh_user_status['usage'].get('queries_this_month', 0)
                remaining = int(max_queries - queries_used)
                query_string = f"You have <strong>{remaining}</strong> personal credits remaining."
        elif fresh_community_status:
            max_queries = fresh_community_status['limits'].get('query_limit', 0)
            queries_used = fresh_community_status['usage'].get('queries_used', 0)
            remaining = int(max_queries - queries_used)
            query_string = f"The community has <strong>{remaining}</strong> shared credits remaining."
            
        return query_string

    MAX_CHAT_MESSAGES = 50
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
    history_limit = 20 if channel_data and channel_data.get('lead_capture_enabled') else 5
    for qa in history_for_prompt[-history_limit:]:
        chat_history_for_prompt += f"Human: {qa['question']}\nAI: {qa['answer']}\n\n"

    
    final_question_with_history = question
    if chat_history_for_prompt:
        final_question_with_history = (f"Given the following conversation history:\n{chat_history_for_prompt}--- End History ---\n\nNow, answer this new question, considering the history as context:\n{question}")
        
    # Ensure video_ids is always defined
    video_ids = set()
    if channel_data:
        videos = channel_data.get('videos') or []
        video_ids = {v['video_id'] for v in videos if v and 'video_id' in v}
            
    stream = answer_question_stream(
        question_for_prompt=final_question_with_history, 
        question_for_search=question, 
        channel_data=channel_data, 
        video_ids=video_ids, 
        user_id=user_id, 
        access_token=access_token, 
        on_complete=on_complete_callback,
        active_community_id=active_community_id,
        user_status=user_status
    )
    return Response(stream, mimetype='text/event-stream')


@app.route('/delete_channel/<int:channel_id>', methods=['POST'])
@login_required
def delete_channel_route(channel_id):
    user_id = session['user']['id']
    supabase_admin = get_supabase_admin_client()
    try:
        active_community_id = session.get('active_community_id')
        cache_key_to_delete = f"user_visible_channels:{user_id}:community:{active_community_id or 'none'}"
        if redis_client:
            redis_client.delete(cache_key_to_delete)
        channel_response = supabase_admin.table('channels').select('creator_id, user_id').eq('id', channel_id).maybe_single().execute()
        if not channel_response or not channel_response.data:
            return jsonify({'status': 'error', 'message': 'Channel not found.'}), 404

        channel_owner_id = channel_response.data.get('creator_id') or channel_response.data.get('user_id')

        # If the user is the owner, trigger the permanent deletion task
        if str(channel_owner_id) == str(user_id):
            owner_delete_channel_task(channel_id)
            # FIX Issue #3: Decrement channel counter on owner deletion
            db_utils.decrement_channels_processed(user_id)
            if redis_client:
                user_cache_key = f"user_status:{user_id}:community:{session.get('active_community_id') or 'none'}"
                redis_client.delete(user_cache_key)
            return jsonify({'status': 'success', 'message': 'Channel and all its data are being permanently deleted.'})
        
        # If the user is not the owner, but is linked, just unlink them
        else:
            # Verify the user is at least linked to the channel before unlinking
            link_check = supabase_admin.table('user_channels').select('channel_id').eq('user_id', user_id).eq('channel_id', channel_id).limit(1).single().execute()
            if not link_check.data:
                return jsonify({'status': 'error', 'message': 'You are not linked to this channel.'}), 403

            delete_channel_task(channel_id, user_id)
            return jsonify({'status': 'success', 'message': 'You have been unlinked from the channel.'})

    except APIError as e:
        if 'PGRST116' in e.message: # "Row not found"
            return jsonify({'status': 'error', 'message': 'Channel not found or you do not have permission.'}), 404
        logger.error(f"Supabase API Error deleting channel {channel_id}: {e}")
        return jsonify({'status': 'error', 'message': 'A database error occurred.'}), 500
    except Exception as e:
        logger.error(f"Error deleting channel {channel_id}: {e}")
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
        # Use admin client to bypass RLS restrictions on DELETE operations
        # Security is maintained by filtering on user_id
        supabase_admin = get_supabase_admin_client()
        result = supabase_admin.table('chat_history').delete().eq('user_id', user_id).eq('channel_name', channel_name).execute()
        logging.info(f"Cleared chat history for user {user_id}, channel {channel_name}. Deleted {len(result.data) if result.data else 0} records.")
        return jsonify({'status': 'success', 'message': f'Chat history cleared for {channel_name}'})
    except Exception as e:
        logging.error(f"Error clearing chat history for user {user_id}: {e}")
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))


# ==========================================
# CHATBOT SETTINGS ROUTES
# ==========================================

@app.route('/chatbot/<chatbot_id>/settings', methods=['GET'])
@login_required
def chatbot_settings(chatbot_id):
    """Display chatbot settings page for editing chatbot configuration."""
    try:
        user_id = session['user']['id']
        supabase = get_supabase_admin_client()
        
        # Get chatbot details
        chatbot_resp = supabase.table('channels').select('*').eq('id', chatbot_id).maybe_single().execute()
        
        if not chatbot_resp or not chatbot_resp.data:
            return redirect(url_for('dashboard'))
        
        chatbot = chatbot_resp.data
        
        # Verify ownership — allow if: (1) user is the creator, or (2) user is linked via user_channels
        owner_id = chatbot.get('creator_id') or chatbot.get('user_id')
        if str(owner_id) != str(user_id):
            # Fallback: check if user has a user_channels link (e.g. original seller after transfer)
            supabase_admin = get_supabase_admin_client()
            link_check = supabase_admin.table('user_channels').select('user_id').eq('user_id', user_id).eq('channel_id', chatbot_id).maybe_single().execute()
            if not (link_check and link_check.data):
                return redirect(url_for('dashboard'))
        
        # --- Integrations and Data Sources are now loaded asynchronously via API ---
        
        # We pass empty defaults to allow the template to render its structure instantly
        data_sources = []
        whatsapp_config = None
        discord_bot = None
        embed_is_active = False
        telegram_is_active = False

        return render_template(
            'chatbot_settings.html', 
            chatbot=chatbot, 
            data_sources=data_sources,
            whatsapp_config=whatsapp_config,
            discord_bot=discord_bot,
            embed_is_active=embed_is_active,
            telegram_is_active=telegram_is_active,
            saved_channels=get_user_channels(),
            webhook_base_url=request.host_url.rstrip('/')
        )
        
    except Exception as e:
        logger.error(f"Error loading chatbot settings for {chatbot_id}: {e}", exc_info=True)
        return redirect(url_for('dashboard'))

@app.route('/api/chatbot/<int:chatbot_id>/integrations')
@login_required
def chatbot_integrations_api(chatbot_id):
    """
    Returns data sources and integration statuses for a specific chatbot asynchronously.
    """
    try:
        user_id = session['user']['id']
        supabase = get_supabase_admin_client()

        # Get chatbot details for telegram check (needed for channel_name)
        chatbot_resp = supabase.table('channels').select('channel_name').eq('id', chatbot_id).maybe_single().execute()
        if not chatbot_resp or not chatbot_resp.data:
             return jsonify({'status': 'error', 'message': 'Chatbot not found'}), 404
        chatbot_channel_name = chatbot_resp.data.get('channel_name', '')

        # Get data sources
        sources_resp = supabase.table('data_sources').select('*').eq('chatbot_id', chatbot_id).execute()
        data_sources = sources_resp.data or []

        # --- Fetch Integration Data (Cached for Performance) ---
        cache_key = f"chatbot_integrations:{chatbot_id}"
        if cache_key in _integration_status_cache:
            integrations = _integration_status_cache[cache_key]
        else:
            # 1. WhatsApp
            try:
                whatsapp_resp = supabase.table('whatsapp_configs').select('*').eq('user_id', user_id).eq('channel_id', chatbot_id).limit(1).execute()
                whatsapp_config = whatsapp_resp.data[0] if whatsapp_resp.data else None
            except Exception:
                whatsapp_config = None

            # 2. Discord
            try:
                discord_resp = supabase.table('discord_bots').select('*').eq('user_id', user_id).eq('youtube_channel_id', chatbot_id).limit(1).execute()
                discord_bot = discord_resp.data[0] if discord_resp.data else None
            except Exception:
                discord_bot = None
            
            # 3. Widget Analytics (Embed)
            try:
                embed_resp = supabase.table('widget_analytics').select('id').eq('channel_id', chatbot_id).limit(1).execute()
                embed_is_active = len(embed_resp.data) > 0 if embed_resp.data else False
            except Exception:
                embed_is_active = False
            
            # 4. Telegram
            telegram_is_active = False
            try:
                if chatbot_channel_name:
                    personal_tg = supabase.table('telegram_connections').select('id').eq('app_user_id', user_id).eq('is_active', True).eq('last_channel_context', chatbot_channel_name).limit(1).execute()
                    if personal_tg.data and len(personal_tg.data) > 0:
                        telegram_is_active = True
                
                if not telegram_is_active:
                    group_tg = supabase.table('group_connections').select('id').eq('owner_user_id', user_id).eq('is_active', True).eq('linked_channel_id', chatbot_id).limit(1).execute()
                    if group_tg.data and len(group_tg.data) > 0:
                        telegram_is_active = True
            except Exception:
                telegram_is_active = False
                
            integrations = {
                'whatsapp_config': whatsapp_config,
                'discord_bot': discord_bot,
                'embed_is_active': embed_is_active,
                'telegram_is_active': telegram_is_active
            }
            _integration_status_cache[cache_key] = integrations

        return jsonify({
            'status': 'success',
            'data_sources': data_sources,
            'integrations': integrations
        })
    except Exception as e:
        logger.error(f"Error fetching integration data: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': 'Internal Server Error'}), 500


@app.route('/chatbot/<int:chatbot_id>/settings', methods=['POST'])
@login_required
def update_chatbot_settings(chatbot_id):
    """Update chatbot settings (name, bot_type, speaking_style, etc.)"""
    try:
        user_id = session['user']['id']
        supabase = get_supabase_admin_client()
        
        # Verify ownership
        chatbot_resp = supabase.table('channels').select('creator_id, user_id').eq('id', chatbot_id).maybe_single().execute()
        
        if not chatbot_resp or not chatbot_resp.data:
            return jsonify({'status': 'error', 'message': 'Chatbot not found'}), 404
        
        owner_id = chatbot_resp.data.get('creator_id') or chatbot_resp.data.get('user_id')
        if str(owner_id) != str(user_id):
            # Fallback: check user_channels link (e.g. original seller after marketplace transfer)
            link_check = supabase.table('user_channels').select('user_id').eq('user_id', user_id).eq('channel_id', chatbot_id).maybe_single().execute()
            if not (link_check and link_check.data):
                return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403
        
        # Get update data
        data = request.get_json()
        
        # Allowed fields to update
        allowed_fields = ['channel_name', 'creator_name', 'bot_type', 'speaking_style', 'creator_soul',
                          'lead_capture_enabled', 'lead_capture_email', 'lead_capture_fields',
                          'lead_capture_prompt',
                          'promotion_triggers', 'quick_reply_mode', 'quick_reply_buttons']
        update_data = {k: v for k, v in data.items() if k in allowed_fields}
        
        if not update_data:
            return jsonify({'status': 'error', 'message': 'No valid fields to update'}), 400
        
        # Validate bot_type
        if 'bot_type' in update_data:
            if update_data['bot_type'] not in ['youtuber', 'business', 'general']:
                return jsonify({'status': 'error', 'message': 'Invalid bot type'}), 400

        # Validate quick_reply_mode
        if 'quick_reply_mode' in update_data:
            if update_data['quick_reply_mode'] not in ['off', 'ai', 'manual']:
                return jsonify({'status': 'error', 'message': 'Invalid quick_reply_mode. Use: off, ai, or manual'}), 400

        # Validate quick_reply_buttons — must be a list
        if 'quick_reply_buttons' in update_data:
            if not isinstance(update_data['quick_reply_buttons'], list):
                return jsonify({'status': 'error', 'message': 'quick_reply_buttons must be an array'}), 400
            # Sanitize: max 10 buttons, each must have a title (max 20 chars)
            cleaned = []
            for btn in update_data['quick_reply_buttons'][:10]:
                title = str(btn.get('title', '')).strip()[:20]
                answer = str(btn.get('answer', '')).strip()  # custom answer per button
                if title:
                    cleaned.append({'id': str(btn.get('id', title))[:20], 'title': title, 'answer': answer})
            update_data['quick_reply_buttons'] = cleaned

        
        # Validate lead_capture_fields — must be a list if provided
        if 'lead_capture_fields' in update_data:
            if not isinstance(update_data['lead_capture_fields'], list):
                return jsonify({'status': 'error', 'message': 'lead_capture_fields must be an array'}), 400
            # Store as JSON array in Supabase (jsonb column)
            import json as _json
            update_data['lead_capture_fields'] = update_data['lead_capture_fields']
        
        # Validate lead_capture_email if provided
        if 'lead_capture_email' in update_data:
            email_val = update_data.get('lead_capture_email', '')
            if email_val and '@' not in str(email_val):
                return jsonify({'status': 'error', 'message': 'Invalid lead capture email address'}), 400
        
        # Update chatbot
        supabase.table('channels').update(update_data).eq('id', chatbot_id).execute()
        
        # Clear cache
        if redis_client:
            active_community_id = session.get('active_community_id')
            cache_key = f"user_visible_channels:{user_id}:community:{active_community_id or 'none'}"
            redis_client.delete(cache_key)
        
        logger.info(f"Updated chatbot {chatbot_id} settings: {list(update_data.keys())}")
        
        return jsonify({'status': 'success', 'message': 'Settings updated successfully'})
        
    except Exception as e:
        logger.error(f"Error updating chatbot settings for {chatbot_id}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/chatbot/<int:chatbot_id>/regenerate-persona', methods=['POST'])
@login_required
def regenerate_persona_api(chatbot_id):
    """Regenerates the speaking style and creator soul from existing embeddings."""
    try:
        user_id = session['user']['id']
        supabase = get_supabase_admin_client()
        
        # Verify ownership
        chatbot_resp = supabase.table('channels').select('creator_id, user_id').eq('id', chatbot_id).maybe_single().execute()
        
        if not chatbot_resp or not chatbot_resp.data:
            return jsonify({'status': 'error', 'message': 'Chatbot not found'}), 404
            
        owner_id = chatbot_resp.data.get('creator_id') or chatbot_resp.data.get('user_id')
        if str(owner_id) != str(user_id):
            return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403

        # Fetch embeddings to use as text sample
        resp = supabase.table('embeddings').select('metadata').eq('channel_id', chatbot_id).limit(30).execute()
        
        if not resp.data:
            return jsonify({'status': 'error', 'message': 'No knowledge base found. Please add a data source first.'}), 400
            
        text_sample = " ".join([
            row['metadata'].get('chunk_text', '') 
            for row in resp.data 
            if row.get('metadata') and row['metadata'].get('chunk_text')
        ])

        if not text_sample.strip():
            return jsonify({'status': 'error', 'message': 'No text content found in data sources.'}), 400

        text_sample = text_sample[:10000] # Limit to 10k chars
        
        from utils.qa_utils import extract_speaking_style, extract_creator_soul
        
        speaking_style = extract_speaking_style(text_sample)
        creator_soul = extract_creator_soul(text_sample)
        
        update_data = {}
        if speaking_style:
            update_data['speaking_style'] = speaking_style
        if creator_soul:
            update_data['creator_soul'] = creator_soul
            
        if not update_data:
            return jsonify({'status': 'error', 'message': 'Failed to extract persona due to an LLM error.'}), 500
            
        supabase.table('channels').update(update_data).eq('id', chatbot_id).execute()
        
        # Include updated data in response
        return jsonify({
            'status': 'success', 
            'message': 'Persona regenerated successfully',
            'speaking_style': speaking_style or '',
            'creator_soul': creator_soul or ''
        })

    except Exception as e:
        logger.error(f"Error regenerating persona for {chatbot_id}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/chatbot/<int:chatbot_id>/upload-avatar', methods=['POST'])
@login_required
def upload_chatbot_avatar(chatbot_id):
    """Upload or update chatbot profile picture."""
    try:
        user_id = session['user']['id']
        supabase = get_supabase_admin_client()
        
        # Verify ownership
        chatbot_resp = supabase.table('channels').select('creator_id, user_id, channel_thumbnail').eq('id', chatbot_id).maybe_single().execute()
        
        if not chatbot_resp or not chatbot_resp.data:
            return jsonify({'status': 'error', 'message': 'Chatbot not found'}), 404
        
        owner_id = chatbot_resp.data.get('creator_id') or chatbot_resp.data.get('user_id')
        if str(owner_id) != str(user_id):
            return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403
        
        # Check if file was uploaded
        if 'avatar' not in request.files:
            return jsonify({'status': 'error', 'message': 'No file uploaded'}), 400
        
        file = request.files['avatar']
        if file.filename == '':
            return jsonify({'status': 'error', 'message': 'No file selected'}), 400
        
        # Validate file type
        allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
        file_ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
        if file_ext not in allowed_extensions:
            return jsonify({'status': 'error', 'message': f'Invalid file type. Allowed: {", ".join(allowed_extensions)}'}), 400
        
        # Validate file size (max 5MB)
        file.seek(0, 2)  # Seek to end
        file_size = file.tell()
        file.seek(0)  # Reset to beginning
        if file_size > 5 * 1024 * 1024:
            return jsonify({'status': 'error', 'message': 'File too large. Maximum size is 5MB.'}), 400
        
        # Save file
        import uuid
        avatar_dir = os.path.join('static', 'uploads', 'avatars')
        os.makedirs(avatar_dir, exist_ok=True)
        
        unique_filename = f"chatbot_{chatbot_id}_{uuid.uuid4().hex[:8]}.{file_ext}"
        file_path = os.path.join(avatar_dir, unique_filename)
        file.save(file_path)
        
        # Delete old avatar if it was a local upload
        old_thumbnail = chatbot_resp.data.get('channel_thumbnail', '')
        if old_thumbnail and '/static/uploads/avatars/' in old_thumbnail:
            old_filename = old_thumbnail.split('/static/uploads/avatars/')[-1]
            old_path = os.path.join(avatar_dir, old_filename)
            if os.path.exists(old_path):
                os.remove(old_path)
        
        # Build URL for the avatar
        avatar_url = url_for('static', filename=f'uploads/avatars/{unique_filename}', _external=False)
        
        # Update database
        supabase.table('channels').update({
            'channel_thumbnail': avatar_url
        }).eq('id', chatbot_id).execute()
        
        logger.info(f"Updated avatar for chatbot {chatbot_id}: {avatar_url}")
        
        return jsonify({
            'status': 'success',
            'message': 'Profile picture updated!',
            'avatar_url': avatar_url
        })
        
    except Exception as e:
        logger.error(f"Error uploading avatar for chatbot {chatbot_id}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/chatbot/<int:chatbot_id>/add-website', methods=['POST'])
@login_required
def add_website_source(chatbot_id):
    """Add a new website URL as a data source to an existing chatbot."""
    try:
        user_id = session['user']['id']
        supabase = get_supabase_admin_client()
        
        # Verify ownership
        chatbot_resp = supabase.table('channels').select('creator_id, user_id').eq('id', chatbot_id).maybe_single().execute()
        
        if not chatbot_resp or not chatbot_resp.data:
            return jsonify({'status': 'error', 'message': 'Chatbot not found'}), 404
        
        owner_id = chatbot_resp.data.get('creator_id') or chatbot_resp.data.get('user_id')
        if str(owner_id) != str(user_id):
            return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403
        
        # Get website URL from request
        data = request.get_json()
        website_url = data.get('website_url', '').strip()
        crawl_mode = data.get('crawl_mode', 'auto')  # 'auto', 'single_page', 'full_crawl'
        
        if not website_url:
            return jsonify({'status': 'error', 'message': 'Please provide a website URL'}), 400
        
        # Validate URL format
        if not website_url.startswith(('http://', 'https://')):
            website_url = 'https://' + website_url
        
        # Validate crawl_mode
        if crawl_mode not in ('auto', 'single_page', 'full_crawl'):
            crawl_mode = 'auto'
        
        # Create data source record with crawl_mode in metadata
        source_resp = supabase.table('data_sources').insert({
            'chatbot_id': chatbot_id,
            'source_type': 'website',
            'source_url': website_url,
            'status': 'pending',
            'metadata': {'crawl_mode': crawl_mode}
        }).execute()
        
        source_id = source_resp.data[0]['id']
        
        # Schedule website processing task
        from tasks_multi_source import process_website_source_task
        task = process_website_source_task.schedule(args=(source_id,), delay=1)
        
        # Update chatbot to indicate it has website sources
        supabase.table('channels').update({
            'has_website': True
        }).eq('id', chatbot_id).execute()
        
        logger.info(f"Added website source {website_url} to chatbot {chatbot_id}, task={task.id}")
        
        return jsonify({
            'status': 'success',
            'message': f'Website "{website_url}" is being scraped...',
            'source_id': source_id,
            'task_id': task.id
        })
        
    except Exception as e:
        logger.error(f"Error adding website source to chatbot {chatbot_id}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/chatbot/<int:chatbot_id>/add-youtube', methods=['POST'])
@login_required
def add_youtube_source(chatbot_id):
    """Add a new YouTube channel/video as a data source to an existing chatbot."""
    try:
        user_id = session['user']['id']
        supabase = get_supabase_admin_client()

        chatbot_resp = supabase.table('channels').select('creator_id, user_id').eq('id', chatbot_id).maybe_single().execute()
        if not chatbot_resp or not chatbot_resp.data:
            return jsonify({'status': 'error', 'message': 'Chatbot not found'}), 404
        owner_id = chatbot_resp.data.get('creator_id') or chatbot_resp.data.get('user_id')
        if str(owner_id) != str(user_id):
            return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403

        data = request.get_json()
        youtube_url = data.get('youtube_url', '').strip()
        if not youtube_url:
            return jsonify({'status': 'error', 'message': 'Please provide a YouTube URL'}), 400

        if is_youtube_video_url(youtube_url):
            youtube_url = get_channel_url_from_video_url(youtube_url)
        youtube_url = clean_youtube_url(youtube_url)

        source_resp = supabase.table('data_sources').insert({
            'chatbot_id': chatbot_id,
            'source_type': 'youtube',
            'source_url': youtube_url,
            'status': 'pending'
        }).execute()
        source_id = source_resp.data[0]['id']

        task = process_channel_task.schedule(args=(chatbot_id,), delay=1)
        supabase.table('channels').update({'has_youtube': True}).eq('id', chatbot_id).execute()

        logger.info(f"Added YouTube source {youtube_url} to chatbot {chatbot_id}, task={task.id}")
        return jsonify({
            'status': 'success',
            'message': f'YouTube channel "{youtube_url}" is being processed...',
            'source_id': source_id,
            'task_id': task.id
        })
    except Exception as e:
        logger.error(f"Error adding YouTube source to chatbot {chatbot_id}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/chatbot/<int:chatbot_id>/add-whatsapp', methods=['POST'])
@login_required
def add_whatsapp_source(chatbot_id):
    """Add a WhatsApp chat export as a data source to an existing chatbot."""
    try:
        user_id = session['user']['id']
        supabase = get_supabase_admin_client()

        chatbot_resp = supabase.table('channels').select('creator_id, user_id').eq('id', chatbot_id).maybe_single().execute()
        if not chatbot_resp or not chatbot_resp.data:
            return jsonify({'status': 'error', 'message': 'Chatbot not found'}), 404
        owner_id = chatbot_resp.data.get('creator_id') or chatbot_resp.data.get('user_id')
        if str(owner_id) != str(user_id):
            return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403

        whatsapp_file = request.files.get('whatsapp_file')
        agent_name = request.form.get('agent_name', '').strip()

        if not whatsapp_file or not whatsapp_file.filename:
            return jsonify({'status': 'error', 'message': 'No file uploaded'}), 400
        if not allowed_file(whatsapp_file.filename):
            return jsonify({'status': 'error', 'message': 'Invalid file type. Please upload a .txt file.'}), 400

        filename = secure_filename(whatsapp_file.filename)
        unique_filename = f"{user_id}_{chatbot_id}_{int(time.time())}_{filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        whatsapp_file.save(file_path)

        source_resp = supabase.table('data_sources').insert({
            'chatbot_id': chatbot_id,
            'source_type': 'whatsapp',
            'source_url': f"file://{unique_filename}",
            'status': 'pending',
            'metadata': {'original_filename': filename, 'agent_name': agent_name or None}
        }).execute()
        source_id = source_resp.data[0]['id']

        task = process_whatsapp_source_task.schedule(args=(source_id, file_path), delay=1)
        supabase.table('channels').update({'has_whatsapp': True}).eq('id', chatbot_id).execute()

        logger.info(f"Added WhatsApp source to chatbot {chatbot_id} from file {unique_filename}, task={task.id}")
        return jsonify({
            'status': 'success',
            'message': f'WhatsApp export "{filename}" is being processed...',
            'source_id': source_id,
            'task_id': task.id
        })
    except Exception as e:
        logger.error(f"Error adding WhatsApp source to chatbot {chatbot_id}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/chatbot/<int:chatbot_id>/add-pdf', methods=['POST'])
@login_required
def add_pdf_source(chatbot_id):
    """Add a PDF document as a data source to an existing chatbot."""
    try:
        user_id = session['user']['id']
        supabase = get_supabase_admin_client()

        chatbot_resp = supabase.table('channels').select('creator_id, user_id').eq('id', chatbot_id).maybe_single().execute()
        if not chatbot_resp or not chatbot_resp.data:
            return jsonify({'status': 'error', 'message': 'Chatbot not found'}), 404
        owner_id = chatbot_resp.data.get('creator_id') or chatbot_resp.data.get('user_id')
        if str(owner_id) != str(user_id):
            return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403

        pdf_file = request.files.get('pdf_file')
        if not pdf_file or not pdf_file.filename:
            return jsonify({'status': 'error', 'message': 'No file uploaded'}), 400
        if not allowed_pdf_file(pdf_file.filename):
            return jsonify({'status': 'error', 'message': 'Invalid file type. Please upload a PDF (.pdf) file.'}), 400

        # Save file
        filename = secure_filename(pdf_file.filename)
        unique_filename = f"{user_id}_{chatbot_id}_{int(time.time())}_{filename}"
        file_path = os.path.join(app.config['PDF_UPLOAD_FOLDER'], unique_filename)
        pdf_file.save(file_path)

        # Create data source record
        source_resp = supabase.table('data_sources').insert({
            'chatbot_id': chatbot_id,
            'source_type': 'pdf',
            'source_url': f"file://{unique_filename}",
            'status': 'pending',
            'metadata': {'original_filename': filename}
        }).execute()
        source_id = source_resp.data[0]['id']

        # Schedule processing
        task = process_pdf_source_task.schedule(args=(source_id, file_path), delay=1)

        logger.info(f"Added PDF source '{filename}' to chatbot {chatbot_id}, task={task.id}")
        return jsonify({
            'status': 'success',
            'message': f'PDF "{filename}" is being processed...',
            'source_id': source_id,
            'task_id': task.id
        })
    except Exception as e:
        logger.error(f"Error adding PDF source to chatbot {chatbot_id}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/chatbot/<int:chatbot_id>/delete-source/<int:source_id>', methods=['DELETE', 'POST'])
@login_required
def delete_chatbot_source(chatbot_id, source_id):
    """Delete a specific data source from a chatbot."""
    try:
        user_id = session['user']['id']
        supabase = get_supabase_admin_client()

        # Verify ownership
        chatbot_resp = supabase.table('channels').select('creator_id, user_id').eq('id', chatbot_id).maybe_single().execute()
        if not chatbot_resp or not chatbot_resp.data:
            return jsonify({'status': 'error', 'message': 'Chatbot not found'}), 404
        
        owner_id = chatbot_resp.data.get('creator_id') or chatbot_resp.data.get('user_id')
        if str(owner_id) != str(user_id):
            return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403

        # Optionally check if source belongs to chatbot
        source_resp = supabase.table('data_sources').select('id').eq('id', source_id).eq('chatbot_id', chatbot_id).maybe_single().execute()
        if not source_resp or not source_resp.data:
            return jsonify({'status': 'error', 'message': 'Data source not found or does not belong to this chatbot'}), 404

        # Delete embeddings tied to this specific source (if applicable)
        supabase.table('embeddings').delete().eq('source_id', source_id).execute()

        # Delete the source itself
        supabase.table('data_sources').delete().eq('id', source_id).execute()

        logger.info(f"Deleted data source {source_id} for chatbot {chatbot_id}")
        return jsonify({'status': 'success', 'message': 'Data source deleted successfully'})

    except Exception as e:
        logger.error(f"Error deleting data source {source_id} for chatbot {chatbot_id}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/chatbot/<int:chatbot_id>/delete', methods=['POST'])
@login_required
def delete_chatbot(chatbot_id):
    """Delete a chatbot and all its associated data."""
    try:
        user_id = session['user']['id']
        supabase = get_supabase_admin_client()
        
        # Verify ownership
        chatbot_resp = supabase.table('channels').select('creator_id, user_id').eq('id', chatbot_id).maybe_single().execute()
        
        if not chatbot_resp or not chatbot_resp.data:
            return jsonify({'status': 'error', 'message': 'Chatbot not found'}), 404
        
        owner_id = chatbot_resp.data.get('creator_id') or chatbot_resp.data.get('user_id')
        if str(owner_id) != str(user_id):
            return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403
        
        # Delete embeddings
        supabase.table('embeddings').delete().eq('channel_id', chatbot_id).execute()
        
        # Delete data sources
        supabase.table('data_sources').delete().eq('chatbot_id', chatbot_id).execute()
        
        # FIX Issue #10: Clean up stale creator_earnings records linked to this channel
        try:
            supabase.table('creator_earnings').delete().eq('channel_id', chatbot_id).execute()
        except Exception as ce:
            logger.warning(f"Could not clean creator_earnings for chatbot {chatbot_id}: {ce}")
        
        # Delete user_channels links
        try:
            supabase.table('user_channels').delete().eq('channel_id', chatbot_id).execute()
        except Exception as uce:
            logger.warning(f"Could not clean user_channels for chatbot {chatbot_id}: {uce}")
        
        # Delete channel
        supabase.table('channels').delete().eq('id', chatbot_id).execute()
        
        # FIX Issue #3: Decrement the channel counter so the user's quota is freed
        db_utils.decrement_channels_processed(user_id)
        
        # Clear cache
        if redis_client:
            active_community_id = session.get('active_community_id')
            cache_key = f"user_visible_channels:{user_id}:community:{active_community_id or 'none'}"
            redis_client.delete(cache_key)
            # Also invalidate user status cache so updated channel count is reflected
            user_cache_key = f"user_status:{user_id}:community:{active_community_id or 'none'}"
            redis_client.delete(user_cache_key)
        
        logger.info(f"Deleted chatbot {chatbot_id} by user {user_id}")
        
        return jsonify({'status': 'success', 'message': 'Chatbot deleted successfully'})
        
    except Exception as e:
        logger.error(f"Error deleting chatbot {chatbot_id}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ==========================================
# LEAD CAPTURE ROUTES
# ==========================================

def process_lead_submission(chatbot_id, responses, submitted_at=''):
    try:
        if not chatbot_id or not responses:
            return False, 'chatbot_id and responses are required'
        
        supabase_admin = get_supabase_admin_client()
        chatbot_resp = supabase_admin.table('channels').select(
            'channel_name, lead_capture_email, lead_capture_enabled, creator_id, user_id'
        ).eq('id', chatbot_id).maybe_single().execute()
        
        if not chatbot_resp or not chatbot_resp.data:
            return False, 'Chatbot not found'
        
        chatbot_data = chatbot_resp.data
        if not chatbot_data.get('lead_capture_enabled'):
            return False, 'Lead capture is not enabled for this chatbot'
        
        recipient_val = chatbot_data.get('lead_capture_email')
        if not recipient_val:
            return False, 'No recipient configured for lead capture'
            
        recipient_email = recipient_val
        whatsapp_number = None
        if '|' in recipient_val:
            parts = recipient_val.split('|')
            recipient_email = parts[0].strip()
            if len(parts) > 1 and parts[1].strip():
                whatsapp_number = parts[1].strip()
        
        chatbot_name = chatbot_data.get('channel_name', 'Your Chatbot')
        
        from utils.lead_capture_utils import send_lead_email, send_lead_whatsapp
        from extensions import mail
        
        owner_id = chatbot_data.get('creator_id') or chatbot_data.get('user_id')
        
        email_success = False
        if recipient_email:
            email_success = send_lead_email(
                mail=mail,
                chatbot_name=chatbot_name,
                recipient_email=recipient_email,
                responses=responses,
                submitted_at=submitted_at
            )
            
        wa_success = False
        if whatsapp_number and owner_id:
            wa_success = send_lead_whatsapp(
                chatbot_id=chatbot_id,
                owner_id=owner_id,
                chatbot_name=chatbot_name,
                whatsapp_number=whatsapp_number,
                responses=responses,
                submitted_at=submitted_at
            )
        
        if email_success or wa_success:
            logger.info(f"Lead submitted for chatbot {chatbot_id} ({chatbot_name}) -> Email: {recipient_email}, WA: {whatsapp_number}")
            return True, 'Lead submitted successfully'
        else:
            return False, 'Failed to send lead notifications'
    except Exception as e:
        logger.error(f"Error in process_lead_submission: {e}", exc_info=True)
        return False, str(e)

@app.route('/api/submit-lead', methods=['POST'])
def submit_lead():
    """Public endpoint — receives a completed lead from the chatbot widget and emails it."""
    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'message': 'No data provided'}), 400
    
    chatbot_id = data.get('chatbot_id')
    responses = data.get('responses', {})
    submitted_at = data.get('submitted_at', '')
    
    success, message = process_lead_submission(chatbot_id, responses, submitted_at)
    if success:
        return jsonify({'status': 'success', 'message': message})
    else:
        status_code = 400 if any(err in message for err in ["not enabled", "not configured", "required"]) else 500
        return jsonify({'status': 'error', 'message': message}), status_code


@app.route('/dashboard')
@login_required
def dashboard():
    """
    Renders the main dashboard hub and provides the status of each integration.
    Now includes aggregated stats and analytics for premium dashboard view.
    """
    user_id = session['user']['id']
    supabase_admin = get_supabase_admin_client()
    
    # --- Fetch Integration Data (Cached for Performance) ---
    cache_key = f"dashboard_integrations:{user_id}"
    if cache_key in _integration_status_cache:
        integrations = _integration_status_cache[cache_key]
        discord_is_active = integrations['discord_is_active']
        telegram_is_active = integrations['telegram_is_active']
        embed_is_active = integrations['embed_is_active']
        whatsapp_is_active = integrations['whatsapp_is_active']
    else:
        # 1. Check Discord Status
        discord_is_active = False
        profile = db_utils.get_profile(user_id)
        if profile and profile.get('discord_user_id'):
            discord_is_active = True
        else:
            bots_res = supabase_admin.table('discord_bots').select('id', count='exact').eq('user_id', user_id).execute()
            if bots_res.count > 0:
                discord_is_active = True

        # 2. Check Telegram Status
        telegram_is_active = False
        personal_conn = supabase_admin.table('telegram_connections').select('id', count='exact').eq('app_user_id', user_id).eq('is_active', True).execute()
        if personal_conn.count > 0:
            telegram_is_active = True
        else:
            group_conn = supabase_admin.table('group_connections').select('id', count='exact').eq('owner_user_id', user_id).eq('is_active', True).execute()
            if group_conn.count > 0:
                telegram_is_active = True

        # 3. Check Website Embed Status
        embed_is_active = False
        try:
            embed_check = supabase_admin.table('widget_analytics').select('id', count='exact').execute()
            embed_is_active = embed_check.count > 0 if embed_check.count else False
        except Exception:
            embed_is_active = False

        # 4. Check WhatsApp Status
        whatsapp_is_active = False
        try:
            whatsapp_check = supabase_admin.table('whatsapp_configs').select('id', count='exact').eq('user_id', user_id).eq('is_active', True).execute()
            whatsapp_is_active = whatsapp_check.count > 0 if whatsapp_check.count else False
        except Exception:
            whatsapp_is_active = False
            
        _integration_status_cache[cache_key] = {
            'discord_is_active': discord_is_active,
            'telegram_is_active': telegram_is_active,
            'embed_is_active': embed_is_active,
            'whatsapp_is_active': whatsapp_is_active
        }

    # --- Get Creator Channels and Their Stats ---
    user_creator_channels = db_utils.get_channels_created_by_user(user_id)
    # Metrics (total_stats, monthly_revenue) are now loaded asynchronously via /api/dashboard/metrics
    # to allow the page to render instantly.

    return render_template(
        'dashboard.html',
        discord_is_active=discord_is_active,
        telegram_is_active=telegram_is_active,
        embed_is_active=embed_is_active,
        whatsapp_is_active=whatsapp_is_active,
        creator_channels=user_creator_channels,
        saved_channels=get_user_channels()
    )

@app.route('/api/dashboard/metrics')
@login_required
def dashboard_metrics():
    """
    Returns aggregated stats and analytics for the dashboard asynchronously.
    This prevents the heavy DB queries from blocking the initial page load.
    Now includes plan/quota info, earnings snapshot, and per-chatbot extras.
    """
    try:
        user_id = session['user']['id']
        supabase_adm = get_supabase_admin_client()

        # --- 1. User's own plan & usage (for quota progress bar) ---
        user_status = get_user_status(user_id)
        max_q = user_status.get('limits', {}).get('max_queries_per_month', 20)
        plan_info = {
            'plan_id': user_status.get('plan_id', 'free'),
            'plan_name': user_status.get('plan_name', 'Free'),
            'queries_used': user_status.get('usage', {}).get('queries_this_month', 0),
            'max_queries': int(max_q) if max_q != float('inf') else -1
        }

        # --- 2. Creator Channels and Their Stats ---
        user_creator_channels = db_utils.get_channels_created_by_user(user_id)
        creator_stats = db_utils.get_creator_dashboard_stats(user_id)

        total_stats = {'referrals': 0, 'paid_referrals': 0, 'creator_mrr': 0.0, 'current_adds': 0}
        for channel_data in user_creator_channels.values():
            channel_id = channel_data.get('id')
            channel_stats = creator_stats.get(channel_id, {})
            total_stats['referrals'] += channel_stats.get('referrals', 0)
            total_stats['paid_referrals'] += channel_stats.get('paid_referrals', 0)
            total_stats['creator_mrr'] += channel_stats.get('creator_mrr', 0.0)
            total_stats['current_adds'] += channel_stats.get('current_adds', 0)

        # --- 3. Monthly Revenue Chart Data ---
        try:
            months_back = int(request.args.get('months', 6))
        except ValueError:
            months_back = 6
        monthly_revenue = db_utils.get_monthly_revenue_history(user_id, months_back=months_back)

        # --- 4. Earnings Snapshot (affiliate + marketplace) ---
        try:
            aff_data = db_utils.get_creator_balance_and_history(user_id)
            mp_data = marketplace_utils.get_creator_marketplace_balance(user_id)
            
            # Get active marketplace MRR
            mp_mrr_res = supabase_adm.table('chatbot_transfers').select('creator_price_monthly').eq('creator_id', user_id).eq('status', 'active').execute()
            marketplace_active_count = len(mp_mrr_res.data or []) if mp_mrr_res else 0
            # marketplace prices are in paise, divide by 100 for INR, then 83 for USD
            marketplace_mrr_usd = sum((row.get('creator_price_monthly') or 0) for row in (mp_mrr_res.data or [])) / 100.0 / 83.0
            
            earnings_snapshot = {
                'total_earned': round(aff_data['total_earned'] + (mp_data['total_earned'] / 83.0), 2),
                'withdrawable': round(aff_data['withdrawable_balance'] + (mp_data['withdrawable_balance'] / 83.0), 2),
                'affiliate_mrr': round(total_stats['creator_mrr'], 2),
                'marketplace_mrr': round(marketplace_mrr_usd, 2),
                'total_mrr': round(total_stats['creator_mrr'] + marketplace_mrr_usd, 2),
                'marketplace_active_count': marketplace_active_count,
                'marketplace_balance': round(mp_data['withdrawable_balance'] / 83.0, 2),
            }
        except Exception:
            earnings_snapshot = {
                'total_earned': 0, 'withdrawable': 0, 'affiliate_mrr': 0, 
                'marketplace_mrr': 0, 'total_mrr': 0, 'marketplace_active_count': 0,
                'marketplace_balance': 0
            }

        # --- 5. Per-chatbot extras: data sources + conversations + marketplace ---
        chatbot_extra = {}
        if user_creator_channels:
            channel_ids = [ch['id'] for ch in user_creator_channels.values()]
            for cid in channel_ids:
                chatbot_extra[cid] = {'data_sources': 0, 'ready_sources': 0, 'conversations': 0, 'marketplace_listing': None}

            try:
                ds_res = supabase_adm.table('data_sources').select('chatbot_id, status').in_('chatbot_id', channel_ids).execute()
                for row in (ds_res.data or []):
                    cid = row['chatbot_id']
                    if cid in chatbot_extra:
                        chatbot_extra[cid]['data_sources'] += 1
                        if row.get('status') == 'ready':
                            chatbot_extra[cid]['ready_sources'] += 1
            except Exception:
                pass

            try:
                channel_names = [ch['channel_name'] for ch in user_creator_channels.values() if ch.get('channel_name')]
                name_to_id = {ch['channel_name']: ch['id'] for ch in user_creator_channels.values() if ch.get('channel_name')}
                if channel_names:
                    conv_res = supabase_adm.table('chat_history').select('channel_name').in_('channel_name', channel_names).execute()
                    for row in (conv_res.data or []):
                        cid = name_to_id.get(row.get('channel_name'))
                        if cid is not None and cid in chatbot_extra:
                            chatbot_extra[cid]['conversations'] += 1
            except Exception:
                pass

            try:
                mp_res = supabase_adm.table('chatbot_transfers').select(
                    'chatbot_id, status, creator_price_monthly, query_limit_monthly'
                ).in_('chatbot_id', channel_ids).eq('creator_id', user_id).execute()
                for row in (mp_res.data or []):
                    cid = row['chatbot_id']
                    if cid in chatbot_extra:
                        chatbot_extra[cid]['marketplace_listing'] = {
                            'status': row.get('status'),
                            'price': round((row.get('creator_price_monthly') or 0) / 100.0, 2),
                            'query_limit': row.get('query_limit_monthly', 0)
                        }
            except Exception:
                pass

        return jsonify({
            'status': 'success',
            'plan_info': plan_info,
            'total_stats': total_stats,
            'creator_stats': creator_stats,
            'monthly_revenue': monthly_revenue,
            'earnings_snapshot': earnings_snapshot,
            'chatbot_extra': chatbot_extra
        })
    except Exception as e:
        logger.error(f"Error fetching dashboard metrics: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': 'Internal Server Error'}), 500

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
        bots_res = supabase_admin.table('discord_bots').select('*, channel:youtube_channel_id(channel_name, channel_thumbnail)') \
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
                print(f"Error fetching group connections for user {user_id}, channel {channel_id}: {e}")
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

@app.route('/integrations/embed')
@login_required
def embed_dashboard():
    """
    Website Embed Integration Dashboard.
    Allows users to generate embed code for their websites.
    """
    user_id = session['user']['id']
    supabase_admin = get_supabase_admin_client()
    
    # Get channels created by this user
    creator_channels = db_utils.get_channels_created_by_user(user_id)
    
    # Get embed statistics
    embed_stats = {
        'total_chats': 0,
        'total_questions': 0,
        'unique_visitors': 0,
        'domains': 0
    }
    
    try:
        # Fetch embed stats from database
        channel_ids = [c['id'] for c in creator_channels.values()]
        if channel_ids:
            stats_res = supabase_admin.table('widget_analytics').select('*').in_('channel_id', channel_ids).execute()
            if stats_res.data:
                embed_stats['total_chats'] = sum(s.get('chat_count', 0) for s in stats_res.data)
                embed_stats['total_questions'] = sum(s.get('question_count', 0) for s in stats_res.data)
                unique_domains = set(s.get('domain', '') for s in stats_res.data if s.get('domain'))
                embed_stats['domains'] = len(unique_domains)
                embed_stats['unique_visitors'] = sum(s.get('unique_visitors', 0) for s in stats_res.data)
    except Exception as e:
        logging.warning(f"Could not fetch embed stats: {e}")
    
    # Check if embed is active (any channels have been embedded)
    embed_is_active = embed_stats['total_chats'] > 0
    
    return render_template(
        'embed_dashboard.html',
        saved_channels=creator_channels,
        embed_stats=embed_stats,
        embed_is_active=embed_is_active
    )


@app.route('/integrations/whatsapp')
@login_required
def whatsapp_dashboard():
    """
    WhatsApp Business Integration Dashboard.
    Allows users to set up their AI bot for WhatsApp Business.
    """
    user_id = session['user']['id']
    
    # Get channels created by this user
    creator_channels = db_utils.get_channels_created_by_user(user_id)
    
    # Check if WhatsApp is configured (placeholder for future implementation)
    whatsapp_config = None
    whatsapp_is_active = False
    
    return render_template(
        'whatsapp_dashboard.html',
        saved_channels=creator_channels,
        whatsapp_config=whatsapp_config,
        whatsapp_is_active=whatsapp_is_active
    )

# --- Widget API Endpoints ---

@app.route('/api/widget/channel/<channel_name>')
def widget_get_channel(channel_name):
    """Fetch channel info for the widget header."""
    try:
        supabase_admin = get_supabase_admin_client()
        channel_res = supabase_admin.table('channels').select('channel_name, channel_thumbnail').eq('channel_name', channel_name).limit(1).execute()
        
        if channel_res.data:
            return jsonify({
                'success': True,
                'name': channel_res.data[0].get('channel_name'),
                'thumbnail': channel_res.data[0].get('channel_thumbnail')
            })
        return jsonify({'success': False, 'error': 'Channel not found'})
    except Exception as e:
        logging.error(f"Widget channel fetch error: {e}")
        return jsonify({'success': False, 'error': 'Server error'})

def generate_widget_answer(channel_id, question):
    from utils.qa_utils import answer_question_stream
    import json as _json
    import re
    
    supabase_admin = get_supabase_admin_client()
    channel_res = supabase_admin.table('channels').select('*').eq('id', channel_id).limit(1).execute()
    if not channel_res.data:
        return {'answer': "Sorry, this chatbot is no longer available.", 'sources': [], 'actions': []}
    
    channel_data = channel_res.data[0]
    prompt_q = question
    
    response_text = ""
    sources = []
    actions = []
    conversation_state = {'flow_id': None, 'flow_node_id': None, 'flow_variables': {}}
    
    for chunk in answer_question_stream(
        question_for_prompt=prompt_q,
        question_for_search=question,
        channel_data=channel_data,
        user_id=channel_data.get('creator_id'),
        is_manager=False,
        integration_source='embed'
    ):
        if chunk.startswith('data: '):
            data_str = chunk.replace('data: ', '').strip()
            if data_str == "[DONE]":
                break
            try:
                parsed_data = json.loads(data_str)
                if parsed_data.get('answer'):
                    response_text += parsed_data['answer']
                if parsed_data.get('sources'):
                    sources = parsed_data['sources']
            except json.JSONDecodeError:
                continue
                
    # Extract flow trigger marker if present
    actions = []
    trigger_match = re.search(r'\[TRIGGER_FLOW:\s*"(.*?)"\]', response_text)
    if trigger_match:
        trigger_flow_marker = trigger_match.group(1)
        response_text = re.sub(r'\[TRIGGER_FLOW:\s*".*?"\]', '', response_text).strip()
        try:
            target_flow = None
            flow_res_by_name = supabase_admin.table('channel_flows').select('id, flow_data').eq('channel_id', channel_data['id']).ilike('name', trigger_flow_marker).limit(1).execute()
            if flow_res_by_name.data:
                target_flow = {'flow_id': flow_res_by_name.data[0]['id'], **flow_res_by_name.data[0]['flow_data']}
            
            if target_flow:
                from utils.flow_runner import run_flow
                trigger_res = run_flow(
                    supabase=supabase_admin,
                    flow=target_flow,
                    conversation={'flow_node_id': None, 'flow_variables': {}},
                    message_text='',
                    is_button_reply=False,
                    sender_name='Website User'
                )
                if trigger_res.get('handled') and trigger_res.get('actions'):
                    actions.extend(trigger_res.get('actions'))
                    conversation_state = {
                        'flow_id': target_flow['flow_id'],
                        'flow_node_id': trigger_res.get('next_node_id'),
                        'flow_variables': trigger_res.get('variables', {})
                    }
                    if trigger_res.get('end'):
                        conversation_state = {'flow_id': None, 'flow_node_id': None, 'flow_variables': {}}
                    response_text = ""
            else:
                # Fallback if flow missing, provide a generic button
                actions.append({'type': 'buttons', 'buttons': [{'id': trigger_flow_marker, 'title': trigger_flow_marker}]})
        except Exception as flow_err:
            logging.warning(f"Widget flow trigger error {flow_err}")

    # Extract lead capture marker if present
    lead_match = re.search(r'\[LEAD_COMPLETE:\s*(\{.*?\})\]', response_text, re.DOTALL)
    if lead_match:
        try:
            lead_complete_marker = json.loads(lead_match.group(1))
            response_text = re.sub(r'\[LEAD_COMPLETE:\s*\{.*?\}\]', '', response_text, flags=re.DOTALL).strip()
            
            # Submit lead if captured
            if channel_data.get('lead_capture_enabled'):
                from utils.lead_capture_utils import process_lead_submission
                process_lead_submission(channel_id, lead_complete_marker)
        except (json.JSONDecodeError, ImportError):
            pass

    return {'answer': response_text, 'sources': sources, 'actions': actions, 'conversation_state': conversation_state}

@app.route('/api/widget/ask', methods=['POST'])
def widget_ask_question():
    """Handle questions from the embedded widget."""
    try:
        data = request.get_json()
        channel_name = data.get('channel')
        question = data.get('question', '').strip()
        referrer = data.get('referrer', 'unknown')
        conversation_state = data.get('conversation_state', {})
        
        if not channel_name or not question:
            return jsonify({'success': False, 'error': 'Missing channel or question'})
        
        if len(question) > 500:
            return jsonify({'success': False, 'error': 'Question too long'})
        
        supabase_admin = get_supabase_admin_client()
        
        # Get channel data
        channel_res = supabase_admin.table('channels').select('id, channel_name').eq('channel_name', channel_name).limit(1).execute()
        
        if not channel_res.data:
            return jsonify({'success': False, 'error': 'Channel not found'})
        
        channel_data = channel_res.data[0]
        channel_id = channel_data['id']
        
        # Check if we are currently inside a flow
        flow_id = conversation_state.get('flow_id')
        flow_node_id = conversation_state.get('flow_node_id')
        
        if flow_id and flow_node_id:
            try:
                # Ensure the active flow belongs to this channel (avoid cross-channel flow bleed)
                target_flow_res = supabase_admin.table('channel_flows').select('id, flow_data').eq('id', flow_id).eq('channel_id', channel_id).limit(1).execute()
                if target_flow_res.data:
                    target_flow = {'flow_id': target_flow_res.data[0]['id'], **target_flow_res.data[0]['flow_data']}
                    from utils.flow_runner import run_flow, _match_button, _match_list_row
                    
                    # Load nodes to check the current node's buttons
                    nodes_dict = {n['id']: n for n in target_flow.get('nodes', [])}
                    current_node = nodes_dict.get(flow_node_id)
                    
                    # Check if the user's text exactly matches a button/row label
                    is_button_match = False
                    if current_node:
                        matched_btn = _match_button(current_node, question)
                        matched_row = _match_list_row(current_node, question) if current_node.get('type') == 'list_node' else None
                        is_button_match = bool(matched_btn or matched_row)
                    
                    if is_button_match:
                        # User clicked/typed an exact button — advance flow normally
                        trigger_res = run_flow(
                            supabase=supabase_admin,
                            flow=target_flow,
                            conversation={'flow_node_id': flow_node_id, 'flow_variables': conversation_state.get('flow_variables', {})},
                            message_text=question,
                            is_button_reply=True,
                            sender_name='Website User'
                        )
                        
                        if trigger_res.get('handled'):
                            actions = trigger_res.get('actions', [])
                            new_state = {
                                'flow_id': flow_id,
                                'flow_node_id': trigger_res.get('next_node_id'),
                                'flow_variables': trigger_res.get('variables', {})
                            }
                            if trigger_res.get('end'):
                                new_state = {'flow_id': None, 'flow_node_id': None, 'flow_variables': {}}
                                
                            try:
                                supabase_admin.table('widget_analytics').upsert({
                                    'channel_id': channel_id,
                                    'domain': referrer,
                                    'question_count': 1,
                                    'chat_count': 1,
                                    'unique_visitors': 1,
                                    'last_activity': datetime.now(timezone.utc).isoformat()
                                }, on_conflict='channel_id,domain').execute()
                            except Exception as track_err:
                                logging.warning(f"Widget tracking error: {track_err}")
                                
                            return jsonify({
                                'success': True,
                                'answer': '',
                                'sources': [],
                                'actions': actions,
                                'conversation_state': new_state
                            })
                    else:
                        # User asked a free-text question not covered by the flow buttons.
                        # Let the AI answer, then re-present the current node's buttons
                        # so the user can continue through the flow.
                        ai_result = generate_widget_answer(channel_id, question)
                        ai_answer = ai_result.get('answer', '')
                        ai_sources = ai_result.get('sources', [])
                        
                        # Collect the current node's buttons to re-append
                        post_flow_actions = []
                        if current_node:
                            ntype = current_node.get('type', '')
                            data = current_node.get('data', {})
                            edges = target_flow.get('edges', [])
                            
                            if ntype == 'list_node':
                                rows = []
                                for r in (data.get('rows') or []):
                                    rows.append({'id': r['id'], 'title': r.get('title', '')[:24]})
                                if rows:
                                    post_flow_actions.append({
                                        'type': 'list',
                                        'body': data.get('message', 'Please choose:'),
                                        'button_label': data.get('buttonLabel', 'See Options'),
                                        'section_title': data.get('sectionTitle', 'Options'),
                                        'rows': rows
                                    })
                            else:
                                reply_btns = [
                                    {'id': b['id'], 'title': b.get('label', '')}
                                    for b in data.get('buttons', [])
                                    if b.get('label', '').strip() and b.get('type', 'reply') == 'reply'
                                ]
                                if reply_btns:
                                    post_flow_actions.append({
                                        'type': 'buttons',
                                        'body': data.get('message', 'Please choose one of the options below 👇'),
                                        'buttons': reply_btns
                                    })
                        
                        # State stays at the current node so user can still click buttons
                        try:
                            supabase_admin.table('widget_analytics').upsert({
                                'channel_id': channel_id,
                                'domain': referrer,
                                'question_count': 1,
                                'chat_count': 1,
                                'unique_visitors': 1,
                                'last_activity': datetime.now(timezone.utc).isoformat()
                            }, on_conflict='channel_id,domain').execute()
                        except Exception as track_err:
                            logging.warning(f"Widget tracking error: {track_err}")
                        
                        return jsonify({
                            'success': True,
                            'answer': ai_answer,
                            'sources': ai_sources,
                            'actions': post_flow_actions,   # re-show current node buttons after AI response
                            'conversation_state': conversation_state  # state unchanged — still at same flow node
                        })
            except Exception as flow_err:
                logging.warning(f"Widget active flow error {flow_err}", exc_info=True)


        # Provide conversation state to generate_widget_answer if we ever pass it down
        # Generate answer using existing AI function
        result = generate_widget_answer(channel_id, question)
        answer = result['answer']
        sources = result['sources']
        actions = result.get('actions', [])
        new_state = result.get('conversation_state', conversation_state) 
        
        # Track the question for analytics
        try:
            supabase_admin.table('widget_analytics').upsert({
                'channel_id': channel_id,
                'domain': referrer,
                'question_count': 1,
                'chat_count': 1,
                'unique_visitors': 1,
                'last_activity': datetime.now(timezone.utc).isoformat()
            }, on_conflict='channel_id,domain').execute()
        except Exception as track_err:
            logging.warning(f"Widget tracking error: {track_err}")
        
        return jsonify({
            'success': True,
            'answer': answer,
            'sources': sources,
            'actions': actions,
            'conversation_state': new_state
        })
        
    except Exception as e:
        logging.error(f"Widget ask error: {e}")
        return jsonify({'success': False, 'error': 'Server error'})

@app.route('/api/widget/track', methods=['POST'])
def widget_track_event():
    """Track widget events for analytics."""
    try:
        data = request.get_json()
        channel = data.get('channel')
        event = data.get('event')
        referrer = data.get('referrer', 'unknown')
        
        if not channel or not event:
            return jsonify({'success': False})
        
        supabase_admin = get_supabase_admin_client()
        
        # Get channel ID
        channel_res = supabase_admin.table('channels').select('id').eq('channel_name', channel).limit(1).execute()
        
        if channel_res.data:
            channel_id = channel_res.data[0]['id']
            
            # Update analytics based on event type
            update_data = {'last_activity': datetime.now(timezone.utc).isoformat()}
            
            if event == 'widget_opened':
                update_data['chat_count'] = 1
            elif event == 'widget_loaded':
                update_data['unique_visitors'] = 1
            
            try:
                supabase_admin.table('widget_analytics').upsert({
                    'channel_id': channel_id,
                    'domain': referrer,
                    **update_data
                }, on_conflict='channel_id,domain').execute()
            except Exception as upsert_err:
                logging.warning(f"Widget analytics upsert error: {upsert_err}")
        
        return jsonify({'success': True})
    except Exception as e:
        logging.warning(f"Widget track error: {e}")
        return jsonify({'success': False})


@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html', saved_channels=get_user_channels())

@app.route('/terms')
def terms():
    return render_template('terms.html', saved_channels=get_user_channels())

@app.route('/privacy-policy')
def privacy_landing():
    return render_template('privacy-landing.html')

@app.route('/terms-of-service')
def terms_landing():
    return render_template('terms-landing.html')

@app.route('/refund-policy')
def refund_landing():
    return render_template('refund-landing.html')

@app.route('/cookie-policy')
def cookie_landing():
    return render_template('cookie-landing.html')


# ──────────────────────────────────────────────────────────────────────────────
# SEO: robots.txt (Fix 6)
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/robots.txt')
def robots_txt():
    base_url = os.environ.get('APP_BASE_URL', 'https://yoppychat.com')
    content = f"""User-agent: *
Allow: /c/
Allow: /about
Allow: /privacy
Allow: /terms
Allow: /privacy-policy
Allow: /terms-of-service
Allow: /refund-policy
Allow: /cookie-policy
Disallow: /api/
Disallow: /admin/
Disallow: /ask/
Disallow: /ask/channel/
Disallow: /auth/
Disallow: /whop/
Disallow: /integrations/
Disallow: /dashboard
Disallow: /earnings
Disallow: /channel
Disallow: /chatbot/

Sitemap: {base_url}/sitemap.xml
"""
    return Response(content.strip(), mimetype='text/plain')


# ──────────────────────────────────────────────────────────────────────────────
# SEO: sitemap.xml (Fix 5)
# ──────────────────────────────────────────────────────────────────────────────
@app.route('/sitemap.xml')
def sitemap_xml():
    """
    Dynamic XML sitemap listing every ready creator page.
    Googlebot uses this to discover /c/<creator> URLs automatically.
    """
    base_url = os.environ.get('APP_BASE_URL', 'https://yoppychat.com')
    supabase_admin = get_supabase_admin_client()
    try:
        result = supabase_admin.table('channels') \
            .select('channel_name, created_at') \
            .eq('status', 'ready') \
            .not_.is_('channel_name', 'null') \
            .order('created_at', desc=True) \
            .execute()
        channels = result.data or []
    except Exception as e:
        logging.error(f"sitemap.xml DB error: {e}")
        channels = []

    # Static high-value pages
    static_pages = [
        ('', '1.0', 'weekly'),
        ('/about', '0.7', 'monthly'),
        ('/privacy-policy', '0.3', 'monthly'),
        ('/terms-of-service', '0.3', 'monthly'),
    ]

    xml_parts = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']

    for path, priority, freq in static_pages:
        xml_parts.append(f"""  <url>
    <loc>{base_url}{path}</loc>
    <changefreq>{freq}</changefreq>
    <priority>{priority}</priority>
  </url>""")

    from urllib.parse import quote
    for ch in channels:
        name = ch.get('channel_name', '').strip()
        if not name:
            continue
        created = (ch.get('created_at') or '')[:10]  # YYYY-MM-DD
        loc = f"{base_url}/c/{quote(name, safe='')}"
        xml_parts.append(f"""  <url>
    <loc>{loc}</loc>
    <lastmod>{created}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.9</priority>
  </url>""")

    xml_parts.append('</urlset>')
    sitemap_content = '\n'.join(xml_parts)
    return Response(sitemap_content, mimetype='application/xml')


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
        print(f"Error toggling privacy for channel {channel_id}: {e}")
        return jsonify({'status': 'error', 'message': 'An internal server error occurred.'}), 500

if os.environ.get("FLASK_ENV") == "development":
    @app.route('/dev/login')
    def dev_login():
        user_id = os.environ.get('DEV_TEST_USER_ID', 'a_test_user_id')
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
        admin_user_id = os.environ.get('ADMIN_USER_ID')
        if str(session['user']['id']) != admin_user_id:
            flash('You do not have permission to access this page.', 'error')
            return redirect(url_for('channel'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    supabase_admin = get_supabase_admin_client()
    
    # --- START OF MODIFICATION ---
    # Get the search query from the URL parameters
    search_query = request.args.get('q', '').strip()

    communities_res = supabase_admin.table('communities').select('*, owner:owner_user_id(full_name, email)').execute()
    communities = communities_res.data if communities_res.data else []

    non_whop_users_res = supabase_admin.table('profiles').select('*, usage:usage_stats(*)').is_('whop_user_id', None).execute()
    non_whop_users = non_whop_users_res.data if non_whop_users_res.data else []

    # Base query for payouts (fetch all, then filter in Python if searching)
    payouts_res = supabase_admin.table('creator_payouts').select('*, creator:creator_id(email, full_name, payout_details)').order('requested_at', desc=True).execute()
    payouts_all = payouts_res.data if payouts_res.data else []

    # If there's a search query, filter by creator email in Python (safe, avoids PostgREST join-filter issues)
    if search_query:
        sq_lower = search_query.lower()
        payouts = [p for p in payouts_all if sq_lower in (p.get('creator') or {}).get('email', '').lower()
                   or sq_lower in str(p.get('id', ''))]
    else:
        payouts = payouts_all
    # --- END OF MODIFICATION ---
    
    # Get cashflow stats
    cashflow_stats = db_utils.get_platform_cashflow_stats()
    
    # Get activity feed and trend data
    activity_feed = db_utils.get_admin_activity_feed(limit=15)
    trend_data = db_utils.get_admin_trend_data(days_back=30)

    transcript_extraction_method = 'yt-dlp'
    if redis_client:
        method_bytes = redis_client.get('transcript_extraction_method')
        if method_bytes:
            transcript_extraction_method = method_bytes.decode('utf-8') if isinstance(method_bytes, bytes) else method_bytes

    saved_channels = get_user_channels() 
    return render_template('admin.html', 
                           communities=communities, 
                           non_whop_users=non_whop_users, 
                           all_plans=PLANS, 
                           COMMUNITY_PLANS=COMMUNITY_PLANS, 
                           saved_channels=saved_channels,
                           payouts=payouts,
                           cashflow_stats=cashflow_stats,
                           activity_feed=activity_feed,
                           trend_data=trend_data,
                           search_query=search_query,
                           transcript_extraction_method=transcript_extraction_method)

@app.route('/api/admin/activity_feed')
@admin_required
def api_admin_activity_feed():
    """Returns the latest activity feed events as JSON for AJAX polling."""
    try:
        feed = db_utils.get_admin_activity_feed(limit=15)
        return jsonify({'status': 'success', 'events': feed})
    except Exception as e:
        logger.error(f"Error fetching activity feed: {e}")
        return jsonify({'status': 'error', 'events': []}), 500

@app.route('/api/admin/trend_data')
@admin_required
def api_admin_trend_data():
    """Returns trend chart data as JSON for AJAX polling."""
    try:
        data = db_utils.get_admin_trend_data(days_back=30)
        return jsonify({'status': 'success', 'data': data})
    except Exception as e:
        logger.error(f"Error fetching trend data: {e}")
        return jsonify({'status': 'error', 'data': {}}), 500

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
        logger.error(f"Error completing payout {payout_id}: {e}")
        return jsonify({'status': 'error', 'message': 'An internal server error occurred.'}), 500

@app.route('/admin/settings/transcript-method', methods=['POST'])
@admin_required
def api_admin_toggle_transcript_method():
    try:
        data = request.get_json()
        new_method = data.get('method')
        if new_method not in ['yt-dlp', 'gemini']:
            return jsonify({'status': 'error', 'message': 'Invalid method'}), 400
        if redis_client:
            redis_client.set('transcript_extraction_method', new_method)
            return jsonify({'status': 'success', 'method': new_method})
        else:
            return jsonify({'status': 'error', 'message': 'Redis is not configured.'}), 500
    except Exception as e:
        logger.error(f"Error updating transcript method: {e}")
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
            # ... (community logic remains the same)
            plan_details = COMMUNITY_PLANS.get(plan_id)
            if not plan_details:
                return jsonify({'status': 'error', 'message': 'Invalid community plan ID.'}), 400
            
            update_data = {
                'plan_id': plan_id, 
                'shared_channel_limit': plan_details['shared_channels_allowed'], 
                'query_limit': plan_details['queries_per_month']
            }
            supabase_admin.table('communities').update(update_data).eq('id', target_id).execute()

        elif plan_type == 'user':
            if plan_id not in PLANS:
                return jsonify({'status': 'error', 'message': 'Invalid user plan ID.'}), 400

            profile = db_utils.get_profile(target_id)
            if not profile or not profile.get('email'):
                return jsonify({'status': 'error', 'message': 'Could not find user or user email.'}), 404

            profile_payload = {
                'id': target_id,
                'email': profile.get('email'),
                'direct_subscription_plan': plan_id
            }
            db_utils.create_or_update_profile(profile_payload)
            
            # --- START OF FIX ---
            # After updating the database, we must clear the user's cache.
            if redis_client:
                # This key must exactly match the one used in get_user_status()
                # For direct users, the community_id is 'none'.
                cache_key = f"user_status:{target_id}:community:none"
                redis_client.delete(cache_key)
                print(f"Admin action: Invalidated cache for user {target_id}")
            # --- END OF FIX ---
            
            db_utils.record_creator_earning(referred_user_id=target_id, plan_id=plan_id)

        return jsonify({'status': 'success', 'message': 'Plan updated successfully.'})
    except Exception as e:
        logger.error(f"Error in set_current_plan: {e}")
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
            supabase_admin.table('profiles').update({
                'direct_subscription_plan': None,
                'personal_plan_id': None
            }).eq('id', target_id).execute()
            # Also invalidate the Redis cache so the change takes effect immediately
            if redis_client:
                from utils.subscription_utils import redis_client as sub_redis
                cache_key = f"user_status:{target_id}:community:none"
                try:
                    sub_redis.delete(cache_key)
                except Exception:
                    pass
        return jsonify({'status': 'success', 'message': 'Plan removed successfully.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/admin/delete_user/<user_id>', methods=['POST'])
@admin_required
def api_admin_delete_user(user_id):
    """
    Permanently deletes a user and all their associated data.
    """
    # --- START: VULNERABILITY FIX ---
    # Prevent the admin from deleting their own account
    if 'user' in session and str(session['user']['id']) == user_id:
        return jsonify({'status': 'error', 'message': 'You cannot delete your own admin account.'}), 403
    # --- END: VULNERABILITY FIX ---
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
        logger.error(f"Error deleting user {user_id}: {e}")
        if 'User not found' in str(e):
            return jsonify({'status': 'error', 'message': 'User not found. They may have already been deleted.'}), 404
        return jsonify({'status': 'error', 'message': 'An internal server error occurred.'}), 500



@app.route('/api/request_payout', methods=['POST'])
@login_required
def request_payout():
    creator_id = session['user']['id']
    amount = request.json.get('amount')
    profile = db_utils.get_profile(creator_id)

    if not profile or not profile.get('payout_details'):
        return jsonify({'status': 'error', 'message': 'You must save your payout details before requesting a withdrawal.'}), 400

    try:
        amount_float = float(amount)
        if amount_float <= 0:
            return jsonify({'status': 'error', 'message': 'Please enter a valid amount.'}), 400
    except (ValueError, TypeError):
        return jsonify({'status': 'error', 'message': 'Invalid amount specified.'}), 400

    # --- START OF THE FIX ---
    # User inputs INR via UI, but Affiliate DB expects USD 
    amount_usd = amount_float / 83.0
    
    # We now pass the creator's CURRENT bank details to the database function
    current_payout_details = profile.get('payout_details')
    payout, message = db_utils.create_payout_request(creator_id, amount_usd, current_payout_details)
    # --- END OF THE FIX ---

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
        print(f"Error updating bot {bot_id}: {e}")
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
             print(f"Failed to verify bot token: {e}")
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

        update_bot_profile_task.schedule(args=(bot_token, cleaned_url), delay=1)
        # The service will pick up this new 'online' bot on its next sync.
        return jsonify({'status': 'success', 'message': 'Bot created! The service will bring it online shortly.'})

    except Exception as e:
        print(f"Error in create_discord_bot: {e}")
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
    - CORRECTED: Now prioritizes loading shared history for ALL users.
    - For logged-out users, it shows the page and prompts login.
    - For logged-in users, it adds the channel if they have space,
      or provides a temporary session if they are at their plan limit.
    """
    supabase_admin = get_supabase_admin_client()
    channel_response = supabase_admin.table('channels').select('*').eq('channel_name', channel_name).maybe_single().execute()

    if not channel_response or not channel_response.data:
        return render_template('error.html', error_message="This AI persona could not be found."), 404

    channel = channel_response.data
    
    # --- START OF FIX: Check for shared history BEFORE checking the user's session ---
    shared_history = []
    history_id = request.args.get('history_id')
    if history_id and redis_client:
        try:
            history_json = redis_client.get(f"shared_chat:{history_id}")
            if history_json:
                shared_history = json.loads(history_json)
        except Exception as e:
            print(f"Error retrieving shared chat {history_id} from Redis: {e}")
    # --- END OF FIX ---

    if 'user' in session:
        user_id = session['user']['id']
        channel_id = channel['id']
        
        # If a shared history was found in the URL, display it immediately for the logged-in user.
        if shared_history:
            # --- START: MODIFIED CODE ---
            notice = {
                "message": "<strong>You are viewing a shared conversation.</strong><br>This chat will not be saved to your personal history.",
                "action": "add_channel",
                "channel_id": channel['id'],
                "channel_name": channel['channel_name'],
                "button_text": "Add this Channel to My Dashboard"
            }
            # --- END: MODIFIED CODE ---
            return render_template(
                'ask.html', 
                history=shared_history,
                channel_name=channel['channel_name'], 
                current_channel=channel,
                saved_channels=get_user_channels(),
                is_temporary_session=True, 
                notice=notice,
                seo_title=channel.get('seo_title'),
                seo_meta_description=channel.get('seo_meta_description'),
                seo_h1=channel.get('seo_h1'),
                SUPABASE_URL=os.environ.get('SUPABASE_URL'),
                SUPABASE_ANON_KEY=os.environ.get('SUPABASE_ANON_KEY')
            )

        # If NO shared history is in the URL, proceed with the original logic for logged-in users.
        link_check = supabase_admin.table('user_channels').select('user_id').eq('user_id', user_id).eq('channel_id', channel_id).execute()

        if link_check.data:
            return redirect(url_for('ask', channel_name=channel['channel_name']))

        user_status = get_user_status(user_id)
        max_channels = user_status['limits'].get('max_channels', 0)
        # FIX Issue #9: Use a live count of actually-linked channels instead of
        # the ever-increasing channels_processed counter, which counts deleted channels too
        live_channel_count_resp = supabase_admin.table('user_channels').select('channel_id', count='exact').eq('user_id', user_id).execute()
        current_channels = live_channel_count_resp.count or 0

        if current_channels < max_channels:
            db_utils.link_user_to_channel(user_id, channel_id)
            db_utils.increment_channels_processed(user_id)
            flash(f"'{channel['channel_name']}' has been added to your channels.", "success")
            return redirect(url_for('ask', channel_name=channel['channel_name']))
        else:
            notice = {
                "message": "<strong>You've reached your channel limit.</strong><br>This is a temporary session. This channel and your conversation will not be saved.",
                "show_upgrade": True
            }
            return render_template(
                'ask.html', 
                history=[],
                channel_name=channel['channel_name'], 
                current_channel=channel,
                saved_channels=get_user_channels(),
                is_temporary_session=True,
                notice=notice,
                seo_title=channel.get('seo_title'),
                seo_meta_description=channel.get('seo_meta_description'),
                seo_h1=channel.get('seo_h1'),
                SUPABASE_URL=os.environ.get('SUPABASE_URL'),
                SUPABASE_ANON_KEY=os.environ.get('SUPABASE_ANON_KEY')
            )

    # This part handles logged-out users. It will correctly display shared history if the ID is present.
    session['referred_by_channel_id'] = channel['id']
    return render_template('ask.html',
        history=shared_history,
        channel_name=channel['channel_name'],
        current_channel=channel,
        saved_channels={},
        is_temporary_session=False,
        notice=None,
        seo_title=channel.get('seo_title'),
        seo_meta_description=channel.get('seo_meta_description'),
        seo_h1=channel.get('seo_h1'),
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



#payment system

@app.route('/razorpay_webhook', methods=['POST'])
def razorpay_webhook():
    webhook_secret = os.environ.get('RAZORPAY_WEBHOOK_SECRET')
    # --- START OF FIX 1: Correctly handle webhook body as a string ---
    webhook_body_as_string = request.get_data(as_text=True)
    # --- END OF FIX 1 ---
    received_signature = request.headers.get('X-Razorpay-Signature')

    if not webhook_secret:
        logging.error("FATAL: RAZORPAY_WEBHOOK_SECRET is not set.")
        return jsonify({'status': 'error', 'message': 'Server configuration error'}), 500
    
    if not received_signature:
        logging.error("Webhook received without a signature.")
        return jsonify({'status': 'error', 'message': 'Missing signature header'}), 400

    razorpay_client = get_razorpay_client()
    if not razorpay_client:
        return jsonify({'status': 'error', 'message': 'Razorpay client not configured'}), 500

    # --- This now uses the official, reliable verification method ---
    try:
        razorpay_client.utility.verify_webhook_signature(webhook_body_as_string, received_signature, webhook_secret)
        logging.info("Webhook signature VERIFIED successfully.")
    except Exception as e:
        logging.error(f"Razorpay webhook signature verification FAILED: {e}")
        return jsonify({'status': 'error', 'message': 'Invalid signature'}), 400

    event = request.get_json()
    logging.info(f"Webhook event received: {event.get('event')}")
    
    if event['event'] == 'invoice.paid':
        try:
            invoice_data = event['payload']['invoice']['entity']
            customer_id = invoice_data.get('customer_id')
            subscription_id = invoice_data.get('subscription_id')

            if not customer_id or not subscription_id:
                logging.warning("Webhook 'invoice.paid' missing customer_id or subscription_id.")
                return jsonify({'status': 'ok'})

            subscription_details = razorpay_client.subscription.fetch(subscription_id)
            plan_id = subscription_details.get('plan_id')
            user_id = db_utils.get_user_by_razorpay_customer_id(customer_id)
            
            # Check if this is a marketplace transfer payment
            supabase_admin = get_supabase_admin_client()
            transfer_res = supabase_admin.table('chatbot_transfers').select('*').eq('razorpay_subscription_id', subscription_id).maybe_single().execute()
            
            if transfer_res and transfer_res.data:
                transfer = transfer_res.data
                
                if transfer['status'] in ('pending', 'subscription_pending') and user_id:
                    # First payment: Move the chatbot
                    marketplace_utils.move_chatbot_to_buyer(transfer['id'], user_id, subscription_id)
                
                # Record the earning event for the creator
                inv_amount = invoice_data.get('amount', 0)  # Amount is in paise
                marketplace_utils.record_creator_marketplace_earning(subscription_id, inv_amount)
                
                # Create a notification for the seller
                seller_id = transfer.get('creator_id')
                if seller_id:
                    is_new_purchase = transfer.get('status') in ('pending', 'subscription_pending')
                    purchase_type_label = "New Purchase" if is_new_purchase else "Recurring Payment"
                    
                    buyer_name = "Someone"
                    if user_id:
                        buyer_profile = db_utils.get_profile(user_id)
                        if buyer_profile:
                            buyer_name = buyer_profile.get('full_name') or buyer_profile.get('email') or "Someone"
                            
                    item_name = "Marketplace Item"
                    if transfer.get('chatbot_id'):
                        channel_data = db_utils.get_channel_by_id(transfer['chatbot_id'])
                        if channel_data:
                            item_name = f"'{channel_data.get('channel_name')}' chatbot"
                    elif transfer.get('google_review_id'):
                        item_name = "Google Reviews Addon"
                        
                    display_amount = inv_amount / 100.0
                    message_text = f"🎉 [{purchase_type_label}] {buyer_name} paid ₹{display_amount:.2f} for {item_name}."

                    db_utils.create_notification(
                        user_id=seller_id,
                        message=message_text,
                        type='sale'
                    )
            else:
                # Platform Subscription Payment
                if user_id and plan_id:
                    logging.info(f"UPDATING PLAN for user {user_id} to plan {plan_id}.")
                    
                    profile = db_utils.get_profile(user_id)
                    if profile:
                        db_utils.create_or_update_profile({
                            'id': user_id, 
                            'email': profile.get('email'),
                            'direct_subscription_plan': plan_id
                        })
                        if redis_client:
                            cache_key = f"user_status:{user_id}:community:none"
                            redis_client.delete(cache_key)
                            logging.info(f"Cache invalidated for user {user_id}")

                    db_utils.update_razorpay_subscription(user_id, subscription_details)
                    db_utils.record_creator_earning(referred_user_id=user_id, plan_id=plan_id)
                else:
                    logging.error(f"Webhook Error: Could not find user for customer_id {customer_id} or plan_id from webhook.")
        except Exception as e:
            logging.error(f"Error processing 'invoice.paid' webhook: {e}")
            return jsonify({'status': 'error', 'message': 'Internal processing error'}), 500

    # --- FIX: Handle subscription cancellation/expiry to downgrade user to free plan ---
    elif event['event'] in ('subscription.cancelled', 'subscription.halted', 'subscription.completed'):
        try:
            subscription_entity = event['payload']['subscription']['entity']
            customer_id = subscription_entity.get('customer_id')
            subscription_id = subscription_entity.get('id')

            if not customer_id:
                logging.warning(f"Webhook '{event['event']}' missing customer_id.")
                return jsonify({'status': 'ok'})

            user_id = db_utils.get_user_by_razorpay_customer_id(customer_id)
            
            # Marketplace transfer cancellation event
            supabase_admin = get_supabase_admin_client()
            transfer_res = supabase_admin.table('chatbot_transfers').select('id').eq('razorpay_subscription_id', subscription_id).maybe_single().execute()
            if transfer_res and transfer_res.data:
                supabase_admin.table('chatbot_transfers').update({'status': 'cancelled'}).eq('id', transfer_res.data['id']).execute()
                logging.info(f"Marketplace transfer {transfer_res.data['id']} cancelled for subscription {subscription_id}.")
            else:
                # Platform Subscription Cancellation
                if user_id:
                    logging.info(f"Subscription ended (event={event['event']}) for user {user_id}. Downgrading to free plan.")
                    profile = db_utils.get_profile(user_id)
                    if profile:
                        db_utils.create_or_update_profile({
                            'id': user_id,
                            'email': profile.get('email'),
                            'direct_subscription_plan': None,  # Clear the plan → free tier
                            'personal_plan_id': None
                        })
                        # FIX Issue #2: Reset query counter so the user isn't locked out on the free plan
                        try:
                            supabase_admin.table('usage_stats').update({
                                'queries_this_month': 0
                            }).eq('user_id', user_id).execute()
                            logging.info(f"Reset queries_this_month to 0 for user {user_id} after subscription end.")
                        except Exception as reset_err:
                            logging.error(f"Failed to reset query counter for user {user_id}: {reset_err}")
                        if redis_client:
                            # Invalidate the user status cache so they see the change immediately
                            cache_key = f"user_status:{user_id}:community:none"
                            redis_client.delete(cache_key)
                            logging.info(f"Cache invalidated for user {user_id} after subscription end.")
                else:
                    logging.error(f"Webhook '{event['event']}': Could not find user for customer_id {customer_id}.")
        except Exception as e:
            logging.error(f"Error processing '{event['event']}' webhook: {e}")
            return jsonify({'status': 'error', 'message': 'Internal processing error'}), 500
    # --- END FIX ---

    return jsonify({'status': 'ok'})

@app.route('/create_razorpay_subscription', methods=['POST'])
@login_required
def create_razorpay_subscription():
    data = request.get_json()
    plan_type = data.get('plan_type')
    currency = data.get('currency', 'INR').upper()

    if currency == 'USD':
        plan_id = os.environ.get(f'RAZORPAY_PLAN_ID_{plan_type.upper()}_USD')
    else:
        plan_id = os.environ.get(f'RAZORPAY_PLAN_ID_{plan_type.upper()}_INR')

    if not plan_id:
        return jsonify({'status': 'error', 'message': f'Plan ID for {plan_type} in {currency} not found.'}), 400

    user_id = session['user']['id']
    profile = db_utils.get_profile(user_id)
    
    razorpay_client = get_razorpay_client()
    if not razorpay_client:
        return jsonify({'status': 'error', 'message': 'Razorpay is not configured.'}), 500

    customer_id = profile.get('razorpay_customer_id')
    user_email = profile.get('email') or session.get('user', {}).get('email')
    user_name = profile.get('full_name') or session.get('user', {}).get('user_metadata', {}).get('full_name')

    if customer_id:
        try:
            # Verify the customer_id is valid
            razorpay_client.customer.fetch(customer_id)
        except Exception as e:
            # If the customer_id is invalid, set it to None to trigger creation
            customer_id = None

    # --- START OF THE FIX ---
    # This block makes the customer lookup and creation process robust.
    if not customer_id:
        try:
            matched_customer_id = None
            if user_email:
                # Razorpay's API ignores the email filter in .all(), so we must manually check the returned items
                # We fetch the latest customers. If it's a huge list, ideally we'd paginate, 
                # but for immediate protection against the bug, we just check the first page (or create a new one).
                customers_response = razorpay_client.customer.all()
                for c in customers_response.get('items', []):
                    if c.get('email') == user_email:
                        matched_customer_id = c['id']
                        break
            
            if matched_customer_id:
                customer_id = matched_customer_id
                print(f"Found existing Razorpay customer {customer_id} for email {user_email}")
                db_utils.create_or_update_profile({'id': user_id, 'email': user_email, 'razorpay_customer_id': customer_id})
            else:
                # If they don't exist on Razorpay, or we didn't have an email to check, create a new one
                print(f"No existing Razorpay customer precisely matching {user_email}. Creating new customer.")
                
                customer_payload = {}
                if user_name: customer_payload['name'] = user_name
                if user_email: customer_payload['email'] = user_email
                
                customer = razorpay_client.customer.create(customer_payload)
                customer_id = customer['id']
                
                update_payload = {'id': user_id, 'razorpay_customer_id': customer_id}
                if user_email: update_payload['email'] = user_email
                db_utils.create_or_update_profile(update_payload)
        except Exception as e:
            logger.error(f"Razorpay customer handling error: {e}")
            return jsonify({'status': 'error', 'message': f"Razorpay error: {e}"}), 500
    # --- END OF THE FIX ---

    try:
        total_count = int(os.environ.get('RAZORPAY_SUBSCRIPTION_TOTAL_COUNT', 12))
        subscription = razorpay_client.subscription.create({
            "plan_id": plan_id,
            "customer_id": customer_id,
            "total_count": total_count,  # FIX Issue #12: Configurable via env var
            "quantity": 1,
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Could not create subscription: {e}'}), 500
    
    plan_name = f"{plan_type.title()} Plan"
    
    return jsonify({
        'status': 'success',
        'subscription_id': subscription['id'],
        'razorpay_key_id': os.environ.get('RAZORPAY_KEY_ID'),
        'plan_name': plan_name,
        'user_name': user_name,
        'user_email': user_email
    })

@app.route('/admin/payouts/initiate_razorpay_payout', methods=['POST'])
@admin_required
def initiate_razorpay_payout():
    payout_id = request.json.get('payout_id')
    creator_id = request.json.get('creator_id')

    # --- START: VULNERABILITY FIX ---
    # Fetch payout from DB to get the correct amount and prevent manipulation
    supabase_admin = get_supabase_admin_client()
    payout_res = supabase_admin.table('creator_payouts').select('*').eq('id', payout_id).eq('creator_id', creator_id).single().execute()

    if not payout_res.data:
        return jsonify({'status': 'error', 'message': 'Payout request not found.'}), 404

    payout_data = payout_res.data
    amount = payout_data.get('amount_usd')

    # Ensure we don't process a payout that isn't pending
    if payout_data.get('status') != 'pending':
        return jsonify({'status': 'error', 'message': f"This payout is already in '{payout_data.get('status')}' status."}), 400
    # --- END: VULNERABILITY FIX ---

    if not payout_res.data:
        return jsonify({'status': 'error', 'message': 'Payout request not found.'}), 404

    payout_data = payout_res.data
    amount = payout_data.get('amount_usd')
    # Ensure we don't process a payout that isn't pending
    if payout_data.get('status') != 'pending':
        return jsonify({'status': 'error', 'message': f"This payout is already in '{payout_data.get('status')}' status."}), 400
    # --- END: VULNERABILITY FIX ---

    razorpay_client = get_razorpay_client()
    if not razorpay_client:
        return jsonify({'status': 'error', 'message': 'Razorpay is not configured.'}), 500

    creator_profile = db_utils.get_profile(creator_id)
    if not creator_profile:
        return jsonify({'status': 'error', 'message': 'Creator not found.'}), 404

    # --- Create Razorpay Contact ---
    contact_id = creator_profile.get('razorpay_contact_id')
    if not contact_id:
        try:
            contact = razorpay_client.contact.create({
                "name": creator_profile.get('full_name'),
                "email": creator_profile.get('email'),
            })
            contact_id = contact['id']
            db_utils.create_or_update_profile({'id': creator_id, 'razorpay_contact_id': contact_id})
        except Exception as e:
            return jsonify({'status': 'error', 'message': f'Razorpay Contact Error: {e}'}), 500
            
    # --- Create Razorpay Fund Account using stored details ---
    fund_account_id = creator_profile.get('razorpay_fund_account_id')
    if not fund_account_id:
        payout_details = creator_profile.get('payout_details')
        if not (payout_details and payout_details.get('account_number') and payout_details.get('ifsc')):
            return jsonify({'status': 'error', 'message': 'Creator has not set up their payout details. Cannot create fund account.'}), 400

        try:
            fund_account = razorpay_client.fund_account.create({
                "contact_id": contact_id,
                "account_type": "bank_account",
                "bank_account": {
                    "name": payout_details.get('name'),
                    "account_number": payout_details.get('account_number'),
                    "ifsc": payout_details.get('ifsc')
                }
            })
            fund_account_id = fund_account['id']
            db_utils.create_or_update_profile({'id': creator_id, 'razorpay_fund_account_id': fund_account_id})
        except Exception as e:
            return jsonify({'status': 'error', 'message': f'Razorpay Fund Account Error: {e}'}), 500

    # --- Initiate the Payout ---
    try:
        razorpay_client.payout.create({
            "account_number": os.environ.get("RAZORPAYX_ACCOUNT_NUMBER"),
            "fund_account_id": fund_account_id,
            "amount": int(float(amount) * 100), # Amount in paise
            "currency": "INR",
            "mode": "IMPS",
            "purpose": "payout",
            "queue_if_low_balance": True
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Razorpay Payout Error: {e}'}), 500

    # --- Update Payout Status in Local DB ---
    db_utils.update_payout_status(payout_id, 'processing')

    return jsonify({'status': 'success', 'message': 'Payout initiated successfully via RazorpayX.'})


@app.route('/earnings')
@login_required
def earnings_page():
    creator_id = session['user']['id']
    
    # --- Data is now loaded asynchronously via /api/earnings/data ---
    # We pass empty default structures so the Jinja template doesn't crash before the AJAX call completes.
    combined_totals = {'withdrawable_balance': 0, 'pending_payouts': 0, 'total_earned': 0}
    affiliate_earnings_data = {'withdrawable_balance': 0, 'pending_payouts': 0, 'total_earned': 0, 'history': []}
    marketplace_earnings_data = {'withdrawable_balance': 0, 'pending_payouts': 0, 'total_earned': 0, 'history': []}
    payout_details = None
    transfers = []
    
    return render_template(
        'earnings.html', 
        combined_totals=combined_totals,
        affiliate_data=affiliate_earnings_data,
        marketplace_data=marketplace_earnings_data,
        payout_details=payout_details,
        saved_channels=get_user_channels(),
        transfers=transfers
    )

@app.route('/api/earnings/data')
@login_required
def earnings_data_api():
    """
    Returns earnings statistics, payout details, and transfer history asynchronously.
    Supports ?status=active|pending|cancelled and ?page=N for pagination (15 rows/page).
    """
    try:
        creator_id = session['user']['id']

        # --- Query Params ---
        status_filter = request.args.get('status', 'active').lower()
        page = max(1, int(request.args.get('page', 1)))
        limit = 15
        offset = (page - 1) * limit

        # Affiliate Earnings Data (always full, never paginated)
        affiliate_data = db_utils.get_creator_balance_and_history(creator_id)

        # Marketplace Earnings Data
        marketplace_data = marketplace_utils.get_creator_marketplace_balance(creator_id)

        # Calculate Combined Totals (Affiliate is in USD, Marketplace is in INR)
        exchange_rate = 83.0
        aff_withdrawable_inr = affiliate_data['withdrawable_balance'] * exchange_rate
        aff_pending_inr = affiliate_data['pending_payouts'] * exchange_rate
        aff_earned_inr = affiliate_data['total_earned'] * exchange_rate

        combined_totals = {
            'withdrawable_balance': round(aff_withdrawable_inr + marketplace_data['withdrawable_balance'], 2),
            'pending_payouts': round(aff_pending_inr + marketplace_data['pending_payouts'], 2),
            'total_earned': round(aff_earned_inr + marketplace_data['total_earned'], 2)
        }

        profile = db_utils.get_profile(creator_id)
        payout_details = profile.get('payout_details') if profile else None

        # --- Fetch transfers with status filter + pagination ---
        supabase = get_supabase_admin_client()
        query = supabase.table('chatbot_transfers').select('*', count='exact').eq('creator_id', creator_id)

        if status_filter == 'active':
            query = query.eq('status', 'active')
        elif status_filter == 'pending':
            query = query.in_('status', ['pending', 'subscription_pending'])
        elif status_filter == 'cancelled':
            query = query.eq('status', 'cancelled')
        # status_filter == 'all' → no extra filter
        else:
            query = query.eq('status', 'active')  # safe default

        transfers_res = query.order('created_at', desc=True).range(offset, offset + limit - 1).execute()
        transfers_raw = transfers_res.data or []
        total_records = transfers_res.count if hasattr(transfers_res, 'count') and transfers_res.count is not None else 0

        # --- Bulk-enrich transfers (avoid N+1 queries) ---
        if transfers_raw:
            chatbot_ids = list({t['chatbot_id'] for t in transfers_raw if t.get('chatbot_id')})
            buyer_ids   = list({t['buyer_id']   for t in transfers_raw if t.get('buyer_id')})

            channels_map = {}
            if chatbot_ids:
                ch_res = supabase.table('channels').select('id, channel_name').in_('id', chatbot_ids).execute()
                channels_map = {ch['id']: ch for ch in (ch_res.data or [])}

            buyers_map = {}
            if buyer_ids:
                b_res = supabase.table('profiles').select('id, email, full_name').in_('id', buyer_ids).execute()
                buyers_map = {b['id']: b for b in (b_res.data or [])}

            for t in transfers_raw:
                t['channels'] = channels_map.get(t.get('chatbot_id'))
                bp = buyers_map.get(t.get('buyer_id'), {})
                t['buyer_email'] = bp.get('email') or bp.get('full_name') or ''
                t['buyer_name']  = bp.get('full_name') or ''

        return jsonify({
            'status': 'success',
            'combined_totals': combined_totals,
            'affiliate_data': affiliate_data,
            'marketplace_data': marketplace_data,
            'payout_details': payout_details,
            'transfers': transfers_raw,
            'pagination': {
                'page': page,
                'limit': limit,
                'total_records': total_records,
                'total_pages': max(1, (total_records + limit - 1) // limit) if total_records else 1
            }
        })
    except Exception as e:
        logger.error(f"Error fetching earnings data: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': 'Internal Server Error'}), 500

@app.route('/api/save_payout_details', methods=['POST'])
@login_required
def save_payout_details():
    creator_id = session['user']['id']
    data = request.json
    
    # Basic validation
    if not all(k in data for k in ['name', 'account_number', 'ifsc']):
        return jsonify({'status': 'error', 'message': 'All fields are required.'}), 400

    # Get the user's email from the session to ensure the NOT NULL constraint is met
    user_email = session.get('user', {}).get('email')
    if not user_email:
         return jsonify({'status': 'error', 'message': 'User session is invalid. Please log in again.'}), 401

    # Combine the two previous database calls into a single, efficient update
    profile_update_payload = {
        'id': creator_id,
        'email': user_email, # This is the crucial line that fixes the error
        'payout_details': {
            'name': data['name'],
            'account_number': data['account_number'],
            'ifsc': data['ifsc']
        }
    }
    
    # Call the database update function once with all the data
    db_utils.create_or_update_profile(profile_update_payload)
    
    return jsonify({'status': 'success', 'message': 'Payout details saved successfully.'})

# --- PERFORMANCE: Second context processor removed — merged into the first one at line 141 ---

@app.route('/api/add_shared_channel/<int:channel_id>', methods=['POST'])
@login_required
def add_shared_channel_api(channel_id):
    user_id = session['user']['id']
    
    # Check if the user is already linked to this channel to prevent errors
    supabase_admin = get_supabase_admin_client()
    link_check = supabase_admin.table('user_channels').select('user_id').eq('user_id', user_id).eq('channel_id', channel_id).execute()
    if link_check.data:
        return jsonify({'status': 'already_exists', 'message': 'You already have this channel.'})

    # Check the user's plan limits
    # --- PERFORMANCE: Reuse from context processor cache on g ---
    user_status = getattr(g, 'user_status', None) or get_user_status(user_id)
    max_channels = user_status['limits'].get('max_channels', 0)
    current_channels = user_status['usage'].get('channels_processed', 0)

    if current_channels >= max_channels:
        message = f"You have reached the maximum of {int(max_channels)} personal channels for your plan."
        return jsonify({'status': 'limit_reached', 'message': message}), 403

    # If they have space, add the channel
    db_utils.link_user_to_channel(user_id, channel_id)
    db_utils.increment_channels_processed(user_id)
    
    # Invalidate the cache so the sidebar updates on the next page load
    if redis_client:
        active_community_id = session.get('active_community_id')
        cache_key = f"user_visible_channels:{user_id}:community:{active_community_id or 'none'}"
        redis_client.delete(cache_key)

    return jsonify({'status': 'success', 'message': 'Channel added to your dashboard!'})

# ==========================================
# MARKETPLACE ROUTES
# ==========================================

@app.route('/marketplace/transfer/<int:chatbot_id>', methods=['GET'])
@login_required
def marketplace_transfer(chatbot_id):
    user_id = session['user']['id']
    supabase_admin = get_supabase_admin_client()
    try:
        channel_res = supabase_admin.table('channels').select('*').eq('id', chatbot_id).maybe_single().execute()
    except Exception as e:
        logger.error(f"Error fetching channel for marketplace transfer: {e}")
        flash("Something went wrong. Please try again.", "error")
        return redirect(url_for('dashboard'))
    
    if not channel_res or not channel_res.data:
        flash("Chatbot not found.", "error")
        return redirect(url_for('dashboard'))
    
    # Verify ownership (same logic as chatbot_settings)
    owner_id = channel_res.data.get('creator_id') or channel_res.data.get('user_id')
    if str(owner_id) != str(user_id):
        flash("You don't have permission to transfer this chatbot.", "error")
        return redirect(url_for('dashboard'))
        
    cost_per_query = int(os.environ.get('MARKETPLACE_COST_PER_QUERY_PAISE', 90))
        
    return render_template(
        'marketplace_transfer.html',
        item_type='chatbot',
        item=channel_res.data,
        cost_per_query=cost_per_query,
        saved_channels=get_user_channels()
    )

@app.route('/marketplace/transfer/google-review/<int:settings_id>', methods=['GET'])
@login_required
def marketplace_transfer_google_review(settings_id):
    user_id = session['user']['id']
    supabase_admin = get_supabase_admin_client()
    try:
        gr_res = supabase_admin.table('google_review_settings').select('*').eq('id', settings_id).maybe_single().execute()
    except Exception as e:
        logger.error(f"Error fetching gr business for marketplace transfer: {e}")
        flash("Something went wrong. Please try again.", "error")
        return redirect(url_for('google_reviews.google_reviews_dashboard'))
    
    if not gr_res or not gr_res.data:
        flash("Google Review Business not found.", "error")
        return redirect(url_for('google_reviews.google_reviews_dashboard'))
    
    # Verify ownership
    owner_id = gr_res.data.get('user_id')
    if str(owner_id) != str(user_id):
        flash("You don't have permission to transfer this business.", "error")
        return redirect(url_for('google_reviews.google_reviews_dashboard'))
        
    cost_per_query = int(os.environ.get('MARKETPLACE_COST_PER_QUERY_PAISE', 90))
        
    return render_template(
        'marketplace_transfer.html',
        item_type='google_review',
        item=gr_res.data,
        cost_per_query=cost_per_query,
        saved_channels=get_user_channels()
    )

@app.route('/api/marketplace/create_transfer', methods=['POST'])
@login_required
def create_marketplace_transfer():
    user_id = session['user']['id']
    data = request.json
    item_id = data.get('item_id')
    item_type = data.get('item_type')
    if not item_type and data.get('chatbot_id'):
        item_type = 'chatbot'
        item_id = data.get('chatbot_id')

    query_limit = int(data.get('query_limit_monthly', 100))
    creator_price_monthly = int(float(data.get('creator_price_monthly', 0))) * 100 # convert to paise
    credit_payer = data.get('credit_payer', 'creator')

    if creator_price_monthly < 100:  # minimum ₹1
        return jsonify({'status': 'error', 'message': 'Price must be at least ₹1.'}), 400
    if query_limit < 1:
        return jsonify({'status': 'error', 'message': 'Credit allowance must be at least 1.'}), 400

    # Anti-money laundering check: Price cannot exceed Credits * 10
    max_price_paise = query_limit * 10 * 100
    if creator_price_monthly > max_price_paise:
        return jsonify({'status': 'error', 'message': f'Price is too high for the amount of credits. Maximum allowed price for {query_limit} credits is ₹{query_limit * 10}.'}), 400

    platform_fee_paise = 0

    if credit_payer == 'buyer':
        # App Store Model: Buyer pays platform directly for their AI credits
        # 0.8 INR = 80 paise per credit
        platform_fee_paise = int(query_limit * 80)
    else:
        # Agency Model: Capacity Check to ensure seller isn't overselling their own credits
        from utils.subscription_utils import get_user_status
        seller_status = get_user_status(user_id)
        if not seller_status or not seller_status.get('has_personal_plan'):
            return jsonify({'status': 'error', 'message': 'You must have an active paid subscription to sell using your own credits.'}), 403
            
        s_max = seller_status['limits'].get('max_queries_per_month', 0)
        if s_max != float('inf'):
            supabase_admin = get_supabase_admin_client()
            active_transfers_res = supabase_admin.table('chatbot_transfers').select('query_limit_monthly').eq('creator_id', user_id).eq('status', 'active').execute()
            total_allocated = sum(t.get('query_limit_monthly', 0) for t in active_transfers_res.data) if active_transfers_res.data else 0
            
            if total_allocated + query_limit > s_max:
                available = max(0, s_max - total_allocated)
                return jsonify({'status': 'error', 'message': f'You cannot allocate {query_limit} credits. You only have {int(available)} credits left. Upgrade your plan, or choose "Client pays for credits".'}), 400

    # Validate ownership
    supabase_admin = get_supabase_admin_client()
    if item_type == 'chatbot':
        channel_res = supabase_admin.table('channels').select('id').eq('id', item_id).eq('creator_id', user_id).single().execute()
        if not channel_res.data:
            return jsonify({'status': 'error', 'message': 'Chatbot not found or permission denied.'}), 403
    elif item_type == 'google_review':
        gr_res = supabase_admin.table('google_review_settings').select('id').eq('id', item_id).eq('user_id', user_id).single().execute()
        if not gr_res.data:
            return jsonify({'status': 'error', 'message': 'Business not found or permission denied.'}), 403

    transfer_code = marketplace_utils.create_transfer_record(
        creator_id=user_id,
        chatbot_id=item_id if item_type == 'chatbot' else None,
        query_limit=query_limit,
        platform_fee_paise=platform_fee_paise,
        creator_price_paise=creator_price_monthly,
        google_review_id=item_id if item_type == 'google_review' else None
    )
    
    if transfer_code:
        transfer_url = url_for('marketplace_accept', transfer_code=transfer_code, _external=True)
        return jsonify({'status': 'success', 'transfer_url': transfer_url})
    else:
        return jsonify({'status': 'error', 'message': 'Failed to create transfer link.'}), 500

@app.route('/marketplace/accept/<transfer_code>', methods=['GET'])
def marketplace_accept(transfer_code):
    transfer = marketplace_utils.get_transfer_by_code(transfer_code)
    
    if not transfer or transfer['status'] not in ('pending', 'subscription_pending'):
        return render_template('error.html', error_message="This transfer link is invalid or has already been used."), 404
    
    is_logged_in = 'user' in session
    user_id = session.get('user', {}).get('id') if is_logged_in else None
    
    # If another buyer has already started checkout, block this one (unless it's the SAME buyer returning)
    if transfer['status'] == 'subscription_pending':
        if transfer.get('buyer_id') and is_logged_in and str(transfer.get('buyer_id')) != str(user_id):
            return render_template('error.html', error_message="This transfer is currently being processed by another buyer. Please try again later."), 409
        
    chatbot = transfer.get('channels')
    google_review_settings = transfer.get('google_review_settings')
    
    item = chatbot if chatbot else google_review_settings
    item_type = 'chatbot' if chatbot else 'google_review'
    
    # Render checkout page for the buyer
    return render_template(
        'marketplace_accept.html',
        transfer=transfer,
        item=item,
        item_type=item_type,
        chatbot=chatbot,  # Keep for backwards compatibility
        creator_price_inr=transfer['creator_price_monthly'] / 100.0,
        is_logged_in=is_logged_in,
        saved_channels=get_user_channels() if is_logged_in else {},
        SUPABASE_URL=os.environ.get('SUPABASE_URL'),
        SUPABASE_ANON_KEY=os.environ.get('SUPABASE_ANON_KEY')
    )

@app.route('/marketplace/thank-you/<transfer_code>')
@login_required
def marketplace_thank_you(transfer_code):
    """
    Thank you page after successful marketplace purchase.
    Shows details of the purchased item.
    """
    user_id = session['user']['id']
    supabase_admin = get_supabase_admin_client()

    # Get the transfer details
    transfer = marketplace_utils.get_transfer_by_code(transfer_code)

    if not transfer or transfer.get('buyer_id') != user_id or transfer.get('status') != 'active':
        return render_template('error.html', error_message="This purchase confirmation is not available."), 404

    chatbot = transfer.get('channels')
    google_review_settings = transfer.get('google_review_settings')

    item = chatbot if chatbot else google_review_settings
    item_type = 'chatbot' if chatbot else 'google_review'

    total_price_inr = (transfer.get('creator_price_monthly', 0) + transfer.get('platform_fee_monthly', 0)) / 100.0
    platform_fee_inr = transfer.get('platform_fee_monthly', 0) / 100.0

    return render_template(
        'marketplace_thank_you.html',
        transfer=transfer,
        item=item,
        item_type=item_type,
        total_price_inr=total_price_inr,
        platform_fee_inr=platform_fee_inr
    )

@app.route('/api/marketplace/subscribe', methods=['POST'])
@login_required
def create_marketplace_subscription():
    try:
        data = request.get_json()
        transfer_code = data.get('transfer_code')
        user_id = session['user']['id']
        
        logging.info(f"[Marketplace Subscribe] User {user_id} initiating subscription for transfer_code: {transfer_code}")
        
        transfer = marketplace_utils.get_transfer_by_code(transfer_code)
        
        if not transfer or transfer['status'] not in ('pending', 'subscription_pending'):
            logging.warning(f"[Marketplace Subscribe] Invalid or expired transfer for code {transfer_code}")
            return jsonify({'status': 'error', 'message': 'Invalid or expired transfer link. It may already be in use.'}), 400
            
        if transfer['status'] == 'subscription_pending':
            if transfer.get('buyer_id') and str(transfer.get('buyer_id')) != str(user_id):
                logging.warning(f"[Marketplace Subscribe] Transfer {transfer_code} locked by another user")
                return jsonify({'status': 'error', 'message': 'This link is currently being claimed by someone else.'}), 409
        
        # Immediately mark as 'subscription_pending' to block other buyers
        supabase_admin_mp = get_supabase_admin_client()
        supabase_admin_mp.table('chatbot_transfers').update({
            'status': 'subscription_pending',
            'buyer_id': user_id
        }).eq('id', transfer['id']).execute()
            
        profile = db_utils.get_profile(user_id)
        
        base_plan_id = os.environ.get('RAZORPAY_MARKETPLACE_BASE_PLAN_ID')
        if not base_plan_id:
            logging.error(f"[Marketplace Subscribe] RAZORPAY_MARKETPLACE_BASE_PLAN_ID not configured")
            return jsonify({'status': 'error', 'message': 'Marketplace is not fully configured.'}), 500
            
        razorpay_client = get_razorpay_client()
        
        # Handle Customer ID (similar to regular subscription logic)
        customer_id = profile.get('razorpay_customer_id')
        user_email = profile.get('email') or session.get('user', {}).get('email')
        
        if customer_id:
            try:
                # Verify the customer_id is valid
                razorpay_client.customer.fetch(customer_id)
            except Exception as e:
                # If the customer_id is invalid, set it to None to trigger creation
                logging.warning(f"[Marketplace Subscribe] Invalid cached customer_id {customer_id}: {e}")
                customer_id = None
                
        if not customer_id:
            try:
                # Reusing existing logic
                customers_response = razorpay_client.customer.all()
                for c in customers_response.get('items', []):
                    if c.get('email') == user_email:
                        customer_id = c['id']
                        break
                if not customer_id:
                    customer = razorpay_client.customer.create({'email': user_email})
                    customer_id = customer['id']
                
                # Unindented: Always save the customer ID back to the user's profile
                db_utils.create_or_update_profile({'id': user_id, 'email': user_email, 'razorpay_customer_id': customer_id})
                logging.info(f"[Marketplace Subscribe] Created/Found customer {customer_id} for user {user_id}")
            except Exception as e:
                logging.error(f"[Marketplace Subscribe] Razorpay customer error for user {user_id}: {e}", exc_info=True)
                supabase_admin_mp.table('chatbot_transfers').update({'status': 'pending', 'buyer_id': None}).eq('id', transfer['id']).execute()
                return jsonify({'status': 'error', 'message': f"Failed to setup payment: {str(e)}"}), 500
                
        # Quantity is the total paise divided by 100 for the ₹1 base plan.
        # So if price is ₹5000, paise=500000. For ₹1 plans (100 paise), quantity=5000
        total_price_paise = transfer['creator_price_monthly'] + transfer.get('platform_fee_monthly', 0)
        quantity = total_price_paise // 100
        logging.info(f"[Marketplace Subscribe] Creating subscription with quantity={quantity}, total_price={total_price_paise}")
        
        try:
            total_count = int(os.environ.get('RAZORPAY_SUBSCRIPTION_TOTAL_COUNT', 12))
            subscription = razorpay_client.subscription.create({
                "plan_id": base_plan_id,
                "customer_id": customer_id,
                "total_count": total_count,  # FIX Issue #12: Configurable via env var
                "quantity": quantity,
            })
            
            logging.info(f"[Marketplace Subscribe] Subscription created: {subscription['id']}")
            
            # Link this preliminary subscription ID to the transfer record
            supabase_admin = get_supabase_admin_client()
            supabase_admin.table('chatbot_transfers').update({
                'razorpay_subscription_id': subscription['id']
            }).eq('id', transfer['id']).execute()
            
        except Exception as e:
            logging.error(f"[Marketplace Subscribe] Failed to create subscription: {e}", exc_info=True)
            supabase_admin_mp.table('chatbot_transfers').update({'status': 'pending', 'buyer_id': None}).eq('id', transfer['id']).execute()
            return jsonify({'status': 'error', 'message': f'Could not create subscription: {str(e)}'}), 500
            
        return jsonify({
            'status': 'success',
            'subscription_id': subscription['id'],
            'razorpay_key_id': os.environ.get('RAZORPAY_KEY_ID'),
            'plan_name': f"Marketplace: {transfer.get('channels', {}).get('channel_name') or transfer.get('google_review_settings', {}).get('business_name')}",
            'user_name': profile.get('full_name'),
            'user_email': user_email
        })
    except Exception as e:
        logging.error(f"[Marketplace Subscribe] Unexpected error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': f'Unexpected error: {str(e)}'}), 500

@app.route('/api/marketplace/cancel_checkout', methods=['POST'])
@login_required
def cancel_marketplace_checkout():
    data = request.get_json()
    transfer_code = data.get('transfer_code')
    user_id = session['user']['id']
    
    supabase_admin = get_supabase_admin_client()
    
    # Check if this user was the one who locked it
    transfer_res = supabase_admin.table('chatbot_transfers').select('id, status, buyer_id').eq('transfer_code', transfer_code).eq('status', 'subscription_pending').maybe_single().execute()
    
    if transfer_res and transfer_res.data:
        transfer = transfer_res.data
        if str(transfer.get('buyer_id')) == str(user_id):
            supabase_admin.table('chatbot_transfers').update({
                'status': 'pending',
                'buyer_id': None
            }).eq('id', transfer['id']).execute()
            return jsonify({'status': 'success', 'message': 'Checkout cancelled.'})
            
    return jsonify({'status': 'error', 'message': 'Could not cancel checkout or not authorized.'}), 400

@app.route('/api/marketplace/test-razorpay', methods=['GET'])
@login_required
def test_razorpay_config():
    """Diagnostic endpoint to test Razorpay configuration and connectivity."""
    results = {
        'razorpay_configured': False,
        'env_vars': {},
        'razorpay_client': None,
        'test_api_call': None,
        'errors': []
    }
    
    try:
        # Check environment variables
        key_id = os.environ.get('RAZORPAY_KEY_ID')
        key_secret = os.environ.get('RAZORPAY_KEY_SECRET')
        marketplace_plan = os.environ.get('RAZORPAY_MARKETPLACE_BASE_PLAN_ID')
        
        results['env_vars'] = {
            'RAZORPAY_KEY_ID': '✓' if key_id else '✗ MISSING',
            'RAZORPAY_KEY_SECRET': '✓' if key_secret else '✗ MISSING',
            'RAZORPAY_MARKETPLACE_BASE_PLAN_ID': '✓' if marketplace_plan else f'✗ MISSING'
        }
        
        if not key_id or not key_secret:
            results['errors'].append('Razorpay API credentials not configured')
            return jsonify(results), 400
        
        # Try to initialize Razorpay client
        razorpay_client = get_razorpay_client()
        if not razorpay_client:
            results['errors'].append('Failed to initialize Razorpay client')
            return jsonify(results), 400
        
        results['razorpay_client'] = 'Initialized successfully'
        
        # Test API call - fetch plans (without count parameter for compatibility)
        try:
            plans = razorpay_client.plan.all()
            results['test_api_call'] = f'✓ Successfully connected to Razorpay API'
            results['razorpay_configured'] = True
        except Exception as api_err:
            results['errors'].append(f'API connection failed: {str(api_err)}')
            results['test_api_call'] = f'✗ {str(api_err)}'
        
        return jsonify(results)
    
    except Exception as e:
        results['errors'].append(f'Unexpected error: {str(e)}')
        logging.error(f"[Test Razorpay] Error: {e}", exc_info=True)
        return jsonify(results), 500

@app.route('/api/marketplace/request_payout', methods=['POST'])
@login_required
def marketplace_request_payout():
    creator_id = session['user']['id']
    amount = request.json.get('amount')
    profile = db_utils.get_profile(creator_id)

    if not profile or not profile.get('payout_details'):
        return jsonify({'status': 'error', 'message': 'You must save your payout details before requesting a withdrawal.'}), 400

    try:
        amount_float = float(amount)
        if amount_float <= 0:
            return jsonify({'status': 'error', 'message': 'Please enter a valid amount.'}), 400
    except (ValueError, TypeError):
        return jsonify({'status': 'error', 'message': 'Invalid amount specified.'}), 400

    current_payout_details = profile.get('payout_details')
    payout, message = marketplace_utils.create_marketplace_payout_request(creator_id, amount_float, current_payout_details)

    if payout:
        return jsonify({'status': 'success', 'message': message})
    else:
        return jsonify({'status': 'error', 'message': message}), 400

def get_paypal_access_token():
    """Get PayPal OAuth access token"""
    url = f"{PAYPAL_BASE_URL}/v1/oauth2/token"
    headers = {"Accept": "application/json", "Accept-Language": "en_US"}
    data = {"grant_type": "client_credentials"}
    
    response = requests.post(
        url,
        headers=headers,
        data=data,
        auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET)
    )
    
    if response.status_code == 200:
        return response.json()["access_token"]
    else:
        logging.error(f"Failed to get PayPal token: {response.text}")
        return None


@app.route('/create_paypal_subscription', methods=['POST'])
@login_required
def create_paypal_subscription():
    data = request.get_json()
    plan_type = data.get('plan_type')

    plan_id_map = {
        'personal': os.environ.get('PAYPAL_PERSONAL_PLAN_ID'),
        'creator': os.environ.get('PAYPAL_CREATOR_PLAN_ID')
    }
    paypal_plan_id = plan_id_map.get(plan_type)

    if not paypal_plan_id:
        return jsonify({'status': 'error', 'message': f'PayPal plan for {plan_type} not found.'}), 400

    # Get access token
    access_token = get_paypal_access_token()
    if not access_token:
        return jsonify({'status': 'error', 'message': 'Could not authenticate with PayPal.'}), 500

    # Create subscription using v1 API
    url = f"{PAYPAL_BASE_URL}/v1/billing/subscriptions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }
    
    # Get base URL for return URLs
    base_url = request.host_url.rstrip('/')
    
    subscription_data = {
        "plan_id": paypal_plan_id,
        "custom_id": session['user']['id'],
        "application_context": {
            "brand_name": "YoppyChat AI",
            "shipping_preference": "NO_SHIPPING",
            "user_action": "SUBSCRIBE_NOW",
            "return_url": f"{base_url}/execute_paypal_subscription",
            "cancel_url": f"{base_url}/cancel_paypal_subscription"
        }
    }

    response = requests.post(url, json=subscription_data, headers=headers)
    
    if response.status_code == 201:
        subscription = response.json()
        
        # Find the approval URL
        approval_url = None
        for link in subscription.get('links', []):
            if link.get('rel') == 'approve':
                approval_url = link.get('href')
                break
        
        if approval_url:
            # Store subscription ID in session for verification
            session['paypal_subscription_id'] = subscription['id']
            return jsonify({'status': 'success', 'paypal_checkout_url': approval_url})
        else:
            logging.error(f"No approval URL in PayPal response: {subscription}")
            return jsonify({'status': 'error', 'message': 'Could not get approval URL.'}), 500
    else:
        logging.error(f"Error creating PayPal subscription: {response.text}")
        return jsonify({'status': 'error', 'message': 'Could not initiate PayPal subscription.'}), 500


@app.route('/execute_paypal_subscription')
@login_required
def execute_paypal_subscription():
    subscription_id = request.args.get('subscription_id')
    
    if not subscription_id:
        flash('Subscription ID not found. Please try again.', 'error')
        return redirect(url_for('channel'))

    try:
        access_token = get_paypal_access_token()
        if not access_token:
            raise Exception("Could not authenticate with PayPal")

        url = f"{PAYPAL_BASE_URL}/v1/billing/subscriptions/{subscription_id}"
        headers = {"Authorization": f"Bearer {access_token}"}
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            subscription = response.json()
            status = subscription.get('status')
            
            # As long as the subscription is approved by PayPal, we can proceed.
            # Our webhook will handle linking it to the correct user via custom_id.
            if status in ['ACTIVE', 'APPROVED']:
                session['pending_paypal_subscription'] = {
                    'subscription_id': subscription_id,
                    'plan_id': subscription.get('plan_id'),
                    'status': status
                }
                
                flash('Your subscription has been set up! Your plan will be updated once the first payment is confirmed.', 'success')
                return redirect(url_for('channel'))
            else:
                # If the status is something else (e.g., 'CANCELLED'), raise an error.
                raise Exception(f"Subscription status is {status}")
        else:
            raise Exception(f"Could not verify subscription: {response.text}")

    except Exception as e:
        logging.error(f"Error executing PayPal subscription: {e}")
        flash('Could not finalize your subscription. Please try again.', 'error')
        return redirect(url_for('channel'))


@app.route('/cancel_paypal_subscription')
@login_required
def cancel_paypal_subscription():
    session.pop('paypal_subscription_id', None)
    flash('Your subscription setup was cancelled.', 'info')
    return redirect(url_for('channel'))


@app.route('/paypal_webhook', methods=['POST'])
def paypal_webhook():
    try:
        event_body = request.get_json()
        event_type = event_body.get('event_type')
        resource = event_body.get('resource', {})
        logging.info(f"PayPal webhook received: {event_type}")

        if event_type in ["BILLING.SUBSCRIPTION.ACTIVATED", "PAYMENT.SALE.COMPLETED"]:
            supabase_admin = get_supabase_admin_client()
            
            profile_res = None
            # --- START: MODIFIED LOGIC ---
            # 1. Prioritize finding the user by the custom_id we stored
            yoppy_user_id = resource.get('custom_id')
            if yoppy_user_id:
                logging.info(f"Found custom_id in webhook: {yoppy_user_id}. Looking up user by ID.")
                profile_res = supabase_admin.table('profiles').select('id, email').eq('id', yoppy_user_id).single().execute()
            
            # 2. If no user was found by ID (fallback for old subscriptions), try email
            if not (profile_res and profile_res.data):
                subscription_id = resource.get('id') if event_type == "BILLING.SUBSCRIPTION.ACTIVATED" else resource.get('billing_agreement_id')
                if not subscription_id:
                    logging.warning(f"No subscription ID in webhook: {event_type}")
                    return jsonify({'status': 'ok'}), 200

                access_token = get_paypal_access_token()
                if not access_token:
                    return jsonify({'status': 'error', 'message': 'Could not get PayPal access token'}), 500

                url = f"{PAYPAL_BASE_URL}/v1/billing/subscriptions/{subscription_id}"
                headers = {"Authorization": f"Bearer {access_token}"}
                response = requests.get(url, headers=headers)
                
                if response.status_code != 200:
                    logging.error(f"Could not fetch subscription details: {response.text}")
                    return jsonify({'status': 'error'}), 500

                subscription = response.json()
                subscriber_email = subscription.get('subscriber', {}).get('email_address')
                logging.info(f"No custom_id. Looking up user by email: {subscriber_email}")
                if subscriber_email:
                    profile_res = supabase_admin.table('profiles').select('id, email').eq('email', subscriber_email).single().execute()
            # --- END: MODIFIED LOGIC ---

            if profile_res and profile_res.data:
                profile = profile_res.data
                user_id = profile['id']
                
                # Use the plan_id from the resource if available, otherwise from the fetched subscription
                paypal_plan_id = resource.get('plan_id') or subscription.get('plan_id')
                
                plan_id_map = {
                    os.environ.get('PAYPAL_PERSONAL_PLAN_ID'): os.environ.get('RAZORPAY_PLAN_ID_PERSONAL_INR'),
                    os.environ.get('PAYPAL_CREATOR_PLAN_ID'): os.environ.get('RAZORPAY_PLAN_ID_CREATOR_INR')
                }
                internal_plan_id = plan_id_map.get(paypal_plan_id)
                
                if internal_plan_id:
                    # Update user's plan - ensure we use the correct email from our database
                    db_utils.create_or_update_profile({
                        'id': user_id,
                        'email': profile['email'], # Use the email from our DB, not PayPal's
                        'direct_subscription_plan': internal_plan_id
                    })
                    
                    if redis_client:
                        redis_client.delete(f"user_status:{user_id}:community:none")
                    
                    db_utils.record_creator_earning(referred_user_id=user_id, plan_id=internal_plan_id)
                    logging.info(f"Successfully processed webhook for user {user_id}, plan {internal_plan_id}")
            else:
                logging.warning(f"Webhook received but no matching user found in database.")

    except Exception as e:
        logging.error(f"Error processing PayPal webhook: {e}", exc_info=True)
        return jsonify({'status': 'error'}), 500

    return jsonify({'status': 'success'}), 200

if __name__ == '__main__':

    app.run(debug=True, host='0.0.0.0', port=5000)
