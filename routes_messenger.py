"""
Messenger Integration Routes
Handles webhook, configuration, and dashboard connection for Facebook Messenger integration.
"""

from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for, flash, current_app
from functools import wraps
import logging
import os
import hmac
import hashlib
import json as _json
import re
import requests
import threading

from utils.supabase_client import get_supabase_admin_client
from utils.qa_utils import answer_question_stream
from utils.history_utils import save_chat_history

logger = logging.getLogger(__name__)

# Create Blueprint
messenger_bp = Blueprint('messenger', __name__)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' \
                      or 'application/json' in request.headers.get('Accept', '') \
                      or request.content_type == 'application/json'
            if is_ajax or request.method != 'GET':
                return jsonify({'status': 'error', 'message': 'Authentication required.'}), 401
            return redirect(url_for('channel') + '?login=1')
        return f(*args, **kwargs)
    return decorated_function

def validate_messenger_signature(request):
    signature = request.headers.get('X-Hub-Signature-256', '')
    if not signature.startswith('sha256='):
        return False
    expected = hmac.new(
        os.environ.get('MESSENGER_APP_SECRET', '').encode('utf-8'),
        request.data,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature[7:], expected)


# ==========================================
# WEBHOOK ENDPOINTS (Called by Meta)
# ==========================================

@messenger_bp.route('/webhook/messenger', methods=['GET'])
def verify_webhook():
    """
    Webhook verification endpoint for Meta Developer Portal.
    """
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')
    
    verify_token = os.environ.get('MESSENGER_VERIFY_TOKEN')
    
    if mode and token:
        if mode == 'subscribe' and token == verify_token:
            logger.info("WEBHOOK_VERIFIED")
            return challenge, 200
        else:
            return 'Forbidden', 403
    return 'Not Found', 404


@messenger_bp.route('/webhook/messenger', methods=['POST'])
def webhook_event():
    """
    Webhook endpoint to receive inbound messages from Messenger.
    """
    # Validate Signature
    if not validate_messenger_signature(request):
        logger.warning("Invalid Messenger webhook signature")
        # Still return 200 so Meta doesn't disable the webhook, but don't process
        return 'OK', 200
        
    data = request.get_json()
    
    if data.get('object') == 'page':
        for entry in data.get('entry', []):
            for messaging_event in entry.get('messaging', []):
                
                # Check if it's a message
                if 'message' in messaging_event:
                    sender_psid = messaging_event['sender']['id']
                    recipient_page_id = messaging_event['recipient']['id']
                    
                    if 'text' in messaging_event['message']:
                        message_text = messaging_event['message']['text']
                        
                        # Return 200 OK immediately
                        app_ref = current_app._get_current_object()
                        threading.Thread(
                            target=_handle_messenger_message, 
                            args=(app_ref, sender_psid, recipient_page_id, message_text), 
                            daemon=True
                        ).start()
                        
        return 'EVENT_RECEIVED', 200
    else:
        return 'Not Found', 404

def _handle_messenger_message(app_obj, sender_psid, recipient_page_id, message_text):
    """
    Background handler for Messenger messages.
    """
    with app_obj.app_context():
        try:
            supabase = get_supabase_admin_client()
            
            # Find the channel linked to this Page ID
            channel_res = supabase.table('channels').select('*').eq('messenger_page_id', recipient_page_id).eq('messenger_enabled', True).limit(1).execute()
            
            if not channel_res.data:
                logger.warning(f"No active channel found for Messenger Page ID: {recipient_page_id}")
                return
                
            channel = channel_res.data[0]
            page_access_token = channel.get('messenger_page_access_token')
            user_id = str(channel.get('user_id'))
            channel_id = channel.get('id')
            
            if not page_access_token:
                logger.error(f"Missing page access token for channel {channel_id}")
                return
                
            # Send typing "on" indicator (optional, nice for UX)
            _send_messenger_action(sender_psid, 'typing_on', page_access_token)
            
            # Call AI
            response_text = ""

            try:
                all_chunks = list(answer_question_stream(
                    question_for_prompt=message_text,
                    question_for_search=message_text,
                    channel_data=channel,
                    user_id=user_id,
                    integration_source='messenger',
                    conversation_id=f"messenger_{sender_psid}"
                ))
            except Exception as stream_err:
                logger.error(f"[Messenger BG] Error materializing AI stream: {stream_err}")
                all_chunks = []

            for chunk in all_chunks:
                if chunk.startswith('data: '):
                    data_str = chunk.replace('data: ', '').strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        parsed_data = _json.loads(data_str)
                        if parsed_data.get('error') == 'QUERY_LIMIT_REACHED':
                            response_text = "Sorry, this chatbot has reached its query limit."
                            break
                        if parsed_data.get('answer'):
                            response_text += parsed_data['answer']
                    except _json.JSONDecodeError:
                        continue

            # Remove any embedded marker tokens before sending
            response_text = re.sub(r'\[LEAD_COMPLETE:\s*\{.*?\}\]', '', response_text, flags=re.DOTALL).strip()
            response_text = re.sub(r'\[TRIGGER_FLOW:\s*".*?"\]', '', response_text).strip()
            
            if response_text:
                # Ensure the message is sent
                _send_messenger_text(sender_psid, response_text, page_access_token)
                
                # Log to chat history
                try:
                    save_chat_history(
                        supabase_client=supabase,
                        user_id=user_id,
                        channel_name=channel.get('channel_name', 'Unknown'),
                        question=message_text,
                        answer=response_text,
                        sources=[],
                        integration_source='messenger'
                    )
                except Exception as e:
                    logger.error(f"Error saving chat history for messenger: {e}")
            
        except Exception as e:
            logger.error(f"Unhandled error in Messenger background task: {e}", exc_info=True)


def _send_messenger_text(recipient_id, message_text, access_token):
    url = f"https://graph.facebook.com/v19.0/me/messages"
    params = {"access_token": access_token}
    headers = {"Content-Type": "application/json"}
    
    # Messenger limits text to 2000 chars, chunk if necessary
    chunks = [message_text[i:i+2000] for i in range(0, len(message_text), 2000)]
    
    for chunk in chunks:
        data = {
            "recipient": {"id": recipient_id},
            "message": {"text": chunk}
        }
        res = requests.post(url, params=params, headers=headers, json=data)
        if res.status_code != 200:
            logger.error(f"Error sending Messenger message: {res.text}")


def _send_messenger_action(recipient_id, action, access_token):
    # Action can be 'typing_on', 'typing_off', 'mark_seen'
    url = f"https://graph.facebook.com/v19.0/me/messages"
    params = {"access_token": access_token}
    headers = {"Content-Type": "application/json"}
    data = {
        "recipient": {"id": recipient_id},
        "sender_action": action
    }
    requests.post(url, params=params, headers=headers, json=data)


# ==========================================
# OAUTH & SETUP
# ==========================================

@messenger_bp.route('/messenger/connect/<channel_id>', methods=['GET'])
@login_required
def connect_messenger(channel_id):
    """
    Redirects user to Facebook OAuth.
    """
    app_id = os.environ.get('MESSENGER_APP_ID')
    redirect_uri = url_for('messenger.messenger_callback', _external=True)
    scopes = "pages_messaging,pages_show_list,pages_read_engagement"
    
    oauth_url = (
        f"https://www.facebook.com/v19.0/dialog/oauth?"
        f"client_id={app_id}&"
        f"redirect_uri={redirect_uri}&"
        f"scope={scopes}&"
        f"state={channel_id}"
    )
    
    return redirect(oauth_url)


@messenger_bp.route('/messenger/callback', methods=['GET'])
@login_required
def messenger_callback():
    """
    Handles Facebook OAuth callback, exchanges code for token, and lets user select a Page.
    """
    code = request.args.get('code')
    state = request.args.get('state') # This is our channel_id
    error = request.args.get('error')
    
    if error:
        flash(f"Facebook Connect Error: {error}", "error")
        return redirect(url_for('chatbot_settings', id=state) + "?tab=integrations")
        
    if not code or not state:
        flash("Invalid callback from Facebook.", "error")
        return redirect(url_for('channel'))
        
    app_id = os.environ.get('MESSENGER_APP_ID')
    app_secret = os.environ.get('MESSENGER_APP_SECRET')
    redirect_uri = url_for('messenger.messenger_callback', _external=True)
    
    # 1. Exchange code for user access token
    token_url = f"https://graph.facebook.com/v19.0/oauth/access_token?client_id={app_id}&client_secret={app_secret}&redirect_uri={redirect_uri}&code={code}"
    
    try:
        res = requests.get(token_url)
        res_data = res.json()
        
        if 'error' in res_data:
            flash(f"Failed to get access token: {res_data['error'].get('message', 'Unknown Error')}", "error")
            return redirect(url_for('chatbot_settings', id=state) + "?tab=integrations")
            
        user_access_token = res_data.get('access_token')
        
        # 2. Get User's Pages
        pages_url = f"https://graph.facebook.com/v19.0/me/accounts?access_token={user_access_token}"
        pages_res = requests.get(pages_url).json()
        
        if 'error' in pages_res:
            flash(f"Failed to fetch Facebook Pages: {pages_res['error'].get('message', 'Unknown Error')}", "error")
            return redirect(url_for('chatbot_settings', id=state) + "?tab=integrations")
            
        pages = pages_res.get('data', [])
        
        if not pages:
            flash("No Facebook Pages found. You need to be an admin of a Facebook Page.", "error")
            return redirect(url_for('chatbot_settings', id=state) + "?tab=integrations")
            
        # If multiple pages, we should theoretically let them pick.
        # For simplicity, we auto-select the first one. (Per requirements: "If the creator has only 1 page, auto-select it. If multiple, let them pick (render a simple page selection template).")
        # Let's render a selection template if multiple, or auto-process if 1.
        
        if len(pages) == 1:
            return _subscribe_page(state, pages[0]['id'], pages[0]['access_token'], pages[0]['name'])
        else:
            # Render a simple selection form (we'll just pass variables to a template)
            return render_template(
                'messenger_page_select.html', 
                pages=pages, 
                channel_id=state
            )
            
    except Exception as e:
        logger.error(f"Error in Messenger callback: {e}")
        flash("An unexpected error occurred during Facebook connection.", "error")
        return redirect(url_for('chatbot_settings', id=state) + "?tab=integrations")


@messenger_bp.route('/messenger/select_page', methods=['POST'])
@login_required
def select_page():
    channel_id = request.form.get('channel_id')
    page_id = request.form.get('page_id')
    page_access_token = request.form.get('page_access_token')
    page_name = request.form.get('page_name')
    
    if not all([channel_id, page_id, page_access_token]):
        flash("Missing parameters.", "error")
        return redirect(url_for('channel'))
        
    return _subscribe_page(channel_id, page_id, page_access_token, page_name)


def _subscribe_page(channel_id, page_id, page_access_token, page_name="Your Page"):
    """
    Subscribes the selected page to the webhook and saves to Supabase.
    """
    try:
        # Subscribe Page to the webhook
        sub_url = f"https://graph.facebook.com/v19.0/{page_id}/subscribed_apps"
        sub_params = {
            "access_token": page_access_token,
            "subscribed_fields": "messages,messaging_postbacks"
        }
        res = requests.post(sub_url, params=sub_params)
        
        if res.status_code != 200:
            logger.error(f"Failed to subscribe page: {res.text}")
            flash(f"Failed to subscribe Facebook Page to our webhook.", "error")
            return redirect(url_for('chatbot_settings', id=channel_id) + "?tab=integrations")
            
        # Save to DB
        supabase = get_supabase_admin_client()
        supabase.table('channels').update({
            'messenger_page_id': page_id,
            'messenger_page_access_token': page_access_token,
            'messenger_enabled': True
        }).eq('id', channel_id).execute()
        
        flash(f"Successfully connected Facebook Page: {page_name}", "success")
        return redirect(url_for('chatbot_settings', id=channel_id) + "?tab=integrations")
        
    except Exception as e:
        logger.error(f"Error subscribing page: {e}")
        flash("An error occurred during final setup.", "error")
        return redirect(url_for('chatbot_settings', id=channel_id) + "?tab=integrations")


@messenger_bp.route('/messenger/disconnect/<channel_id>', methods=['POST'])
@login_required
def disconnect_messenger(channel_id):
    """
    Disconnects Messenger from the channel.
    """
    user_id = session['user']['id']
    supabase = get_supabase_admin_client()
    
    # Verify ownership
    check = supabase.table('user_channels').select('*').eq('channel_id', channel_id).eq('user_id', user_id).execute()
    if not check.data:
        return jsonify({'status': 'error', 'message': 'Permission denied'}), 403
        
    try:
        supabase.table('channels').update({
            'messenger_page_id': None,
            'messenger_page_access_token': None,
            'messenger_enabled': False
        }).eq('id', channel_id).execute()
        
        return jsonify({'status': 'success', 'message': 'Messenger disconnected successfully'})
    except Exception as e:
        logger.error(f"Error disconnecting messenger: {e}")
        return jsonify({'status': 'error', 'message': 'Failed to disconnect Messenger'}), 500
