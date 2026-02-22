"""
WhatsApp Integration Routes
Handles webhook, configuration, and dashboard for WhatsApp Business integration
"""

from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for
from functools import wraps
from datetime import datetime, timezone
import logging
import os

from utils.supabase_client import get_supabase_admin_client
from utils.whatsapp_api import (
    parse_webhook_message,
    send_whatsapp_message,
    mark_message_as_read,
    get_phone_number_info,
    verify_webhook_signature
)
from utils.qa_utils import answer_question_stream
from utils.crypto import encrypt_token, decrypt_token
from utils import db_utils

logger = logging.getLogger(__name__)

# Create Blueprint
whatsapp_bp = Blueprint('whatsapp', __name__, url_prefix='/api/whatsapp')


def login_required(f):
    """Decorator to require login for routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


# ==========================================
# WEBHOOK ENDPOINTS (Called by Meta)
# ==========================================

@whatsapp_bp.route('/webhook', methods=['GET'])
def verify_webhook():
    """
    Webhook verification endpoint.
    Meta sends a GET request to verify the webhook URL.
    """
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')
    
    if mode == 'subscribe':
        # Look up the verify token in our database
        supabase = get_supabase_admin_client()
        config_res = supabase.table('whatsapp_configs').select('id').eq('verify_token', token).limit(1).execute()
        
        if config_res.data:
            logger.info(f"WhatsApp webhook verified for config {config_res.data[0]['id']}")
            return challenge, 200
    
    logger.warning(f"WhatsApp webhook verification failed. Token: {token}")
    return 'Forbidden', 403


@whatsapp_bp.route('/webhook', methods=['POST'])
def receive_message():
    """
    Webhook endpoint to receive messages from WhatsApp.
    Verifies Meta's X-Hub-Signature-256 header before processing.
    """
    try:
        # Verify webhook signature from Meta
        app_secret = os.environ.get('WHATSAPP_APP_SECRET')
        if app_secret:
            signature = request.headers.get('X-Hub-Signature-256', '')
            if not verify_webhook_signature(request.get_data(), signature, app_secret):
                logger.warning("WhatsApp webhook received with invalid signature")
                return jsonify({'status': 'invalid_signature'}), 403

        data = request.get_json()
        
        # Parse the incoming message
        parsed = parse_webhook_message(data)
        
        if not parsed:
            # Not a message event (could be status update, etc.)
            return jsonify({'status': 'ok'}), 200
        
        phone_number_id = parsed['phone_number_id']
        from_phone = parsed['from_phone']
        message_text = parsed['text']
        message_id = parsed['message_id']
        sender_name = parsed['sender_name']
        
        logger.info(f"Received WhatsApp message from {from_phone} to phone ID {phone_number_id}")
        
        # Find the config for this phone number
        supabase = get_supabase_admin_client()
        config_res = supabase.table('whatsapp_configs').select(
            '*, channels(*)'
        ).eq('phone_number_id', phone_number_id).eq('is_active', True).limit(1).execute()
        
        if not config_res.data:
            logger.warning(f"No active config found for phone number ID: {phone_number_id}")
            return jsonify({'status': 'no_config'}), 200
        
        config = config_res.data[0]
        channel = config.get('channels')
        
        if not channel:
            logger.warning(f"No channel linked to WhatsApp config {config['id']}")
            return jsonify({'status': 'no_channel'}), 200
        
        # Decrypt the access token for API calls
        decrypted_token = decrypt_token(config['access_token'])
        
        # Mark message as read
        mark_message_as_read(phone_number_id, decrypted_token, message_id)
        
        # Get or create conversation
        conv_res = supabase.table('whatsapp_conversations').upsert({
            'config_id': config['id'],
            'customer_phone': from_phone,
            'customer_name': sender_name,
            'last_message_at': datetime.now(timezone.utc).isoformat(),
            'is_active': True
        }, on_conflict='config_id,customer_phone').execute()
        
        conversation_id = conv_res.data[0]['id'] if conv_res.data else None
        
        # Store incoming message
        if conversation_id:
            supabase.table('whatsapp_messages').insert({
                'conversation_id': conversation_id,
                'message_id': message_id,
                'direction': 'inbound',
                'content': message_text
            }).execute()
            
            # Update message count
            supabase.table('whatsapp_conversations').update({
                'message_count': conv_res.data[0].get('message_count', 0) + 1
            }).eq('id', conversation_id).execute()
        
        # Generate AI response
        if message_text:
            try:
                # Get chat history for context
                history = []
                if conversation_id:
                    history_res = supabase.table('whatsapp_messages').select('direction, content').eq(
                        'conversation_id', conversation_id
                    ).order('created_at', desc=True).limit(10).execute()
                    
                    if history_res.data:
                        for msg in reversed(history_res.data):
                            role = 'user' if msg['direction'] == 'inbound' else 'assistant'
                            history.append({'role': role, 'content': msg['content']})
                
                # Get AI response
                response_text = ""
                for chunk in answer_question_stream(
                    question=message_text,
                    channel_id=channel['id'],
                    user_id=config['user_id'],
                    chat_history=history[:-1] if history else []  # Exclude current message
                ):
                    response_text += chunk
                
                # Send response back to WhatsApp
                if response_text:
                    send_result = send_whatsapp_message(
                        phone_number_id=phone_number_id,
                        access_token=decrypted_token,
                        to_phone=from_phone,
                        message_text=response_text
                    )
                    
                    # Store outgoing message
                    if conversation_id and send_result.get('success'):
                        outbound_msg_id = send_result.get('data', {}).get('messages', [{}])[0].get('id')
                        supabase.table('whatsapp_messages').insert({
                            'conversation_id': conversation_id,
                            'message_id': outbound_msg_id,
                            'direction': 'outbound',
                            'content': response_text
                        }).execute()
                        
            except Exception as e:
                logger.error(f"Error generating AI response: {e}", exc_info=True)
        
        return jsonify({'status': 'ok'}), 200
        
    except Exception as e:
        logger.error(f"Error processing WhatsApp webhook: {e}", exc_info=True)
        return jsonify({'status': 'error'}), 500


# ==========================================
# USER DASHBOARD API ENDPOINTS
# ==========================================

@whatsapp_bp.route('/config', methods=['GET'])
@login_required
def get_config():
    """Get user's WhatsApp configuration."""
    user_id = session['user']['id']
    supabase = get_supabase_admin_client()
    
    config_res = supabase.table('whatsapp_configs').select(
        '*, channels(id, channel_name)'
    ).eq('user_id', user_id).execute()
    
    configs = config_res.data or []
    
    # Mask access tokens for security (decrypt first, then mask)
    for config in configs:
        if config.get('access_token'):
            plain_token = decrypt_token(config['access_token'])
            config['access_token'] = plain_token[:10] + '...' + plain_token[-4:]
    
    return jsonify({'status': 'success', 'configs': configs})


@whatsapp_bp.route('/config', methods=['POST'])
@login_required
def save_config():
    """Save or update WhatsApp configuration."""
    user_id = session['user']['id']
    data = request.get_json()
    
    required_fields = ['phone_number_id', 'access_token', 'channel_id']
    for field in required_fields:
        if not data.get(field):
            return jsonify({'status': 'error', 'message': f'{field} is required'}), 400
    
    supabase = get_supabase_admin_client()
    
    # Verify the channel belongs to this user
    channel_res = supabase.table('channels').select('id, creator_id').eq('id', data['channel_id']).limit(1).execute()
    if not channel_res.data or str(channel_res.data[0].get('creator_id')) != str(user_id):
        return jsonify({'status': 'error', 'message': 'Invalid channel'}), 403
    
    # Generate a unique verify token
    import secrets
    verify_token = secrets.token_urlsafe(32)
    
    # Try to get phone number info from Meta (use plain token for the API call)
    phone_info = get_phone_number_info(data['phone_number_id'], data['access_token'])
    
    # Encrypt the access token before storing in database
    encrypted_access_token = encrypt_token(data['access_token'])
    
    config_data = {
        'user_id': user_id,
        'channel_id': data['channel_id'],
        'phone_number_id': data['phone_number_id'],
        'business_account_id': data.get('business_account_id'),
        'access_token': encrypted_access_token,
        'verify_token': verify_token,
        'display_phone_number': phone_info.get('display_phone_number') if phone_info else None,
        'phone_number_name': phone_info.get('verified_name') if phone_info else None,
        'is_active': True
    }
    
    try:
        # Upsert config
        result = supabase.table('whatsapp_configs').upsert(
            config_data,
            on_conflict='user_id,phone_number_id'
        ).execute()
        
        logger.info(f"WhatsApp config saved for user {user_id}")
        
        # Return the webhook URL they need to configure in Meta
        webhook_url = request.host_url.rstrip('/') + '/api/whatsapp/webhook'
        
        return jsonify({
            'status': 'success',
            'message': 'Configuration saved',
            'config': {
                'id': result.data[0]['id'] if result.data else None,
                'verify_token': verify_token,
                'webhook_url': webhook_url
            }
        })
        
    except Exception as e:
        logger.error(f"Error saving WhatsApp config: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@whatsapp_bp.route('/config/<int:config_id>', methods=['DELETE'])
@login_required
def delete_config(config_id):
    """Delete a WhatsApp configuration."""
    user_id = session['user']['id']
    supabase = get_supabase_admin_client()
    
    # Verify ownership
    config_res = supabase.table('whatsapp_configs').select('user_id').eq('id', config_id).limit(1).execute()
    if not config_res.data or str(config_res.data[0]['user_id']) != str(user_id):
        return jsonify({'status': 'error', 'message': 'Not found'}), 404
    
    supabase.table('whatsapp_configs').delete().eq('id', config_id).execute()
    
    return jsonify({'status': 'success', 'message': 'Configuration deleted'})


@whatsapp_bp.route('/conversations', methods=['GET'])
@login_required
def get_conversations():
    """Get all WhatsApp conversations for the user."""
    user_id = session['user']['id']
    supabase = get_supabase_admin_client()
    
    # Get user's configs first
    configs_res = supabase.table('whatsapp_configs').select('id').eq('user_id', user_id).execute()
    config_ids = [c['id'] for c in (configs_res.data or [])]
    
    if not config_ids:
        return jsonify({'status': 'success', 'conversations': []})
    
    # Get conversations
    conv_res = supabase.table('whatsapp_conversations').select('*').in_('config_id', config_ids).order(
        'last_message_at', desc=True
    ).limit(50).execute()
    
    return jsonify({'status': 'success', 'conversations': conv_res.data or []})


@whatsapp_bp.route('/conversations/<int:conversation_id>/messages', methods=['GET'])
@login_required  
def get_conversation_messages(conversation_id):
    """Get messages for a specific conversation."""
    user_id = session['user']['id']
    supabase = get_supabase_admin_client()
    
    # Verify ownership through config
    conv_res = supabase.table('whatsapp_conversations').select(
        '*, whatsapp_configs!inner(user_id)'
    ).eq('id', conversation_id).limit(1).execute()
    
    if not conv_res.data or str(conv_res.data[0]['whatsapp_configs']['user_id']) != str(user_id):
        return jsonify({'status': 'error', 'message': 'Not found'}), 404
    
    # Get messages
    messages_res = supabase.table('whatsapp_messages').select('*').eq(
        'conversation_id', conversation_id
    ).order('created_at').execute()
    
    return jsonify({
        'status': 'success',
        'conversation': conv_res.data[0],
        'messages': messages_res.data or []
    })


@whatsapp_bp.route('/stats', methods=['GET'])
@login_required
def get_stats():
    """Get WhatsApp statistics for the user."""
    user_id = session['user']['id']
    supabase = get_supabase_admin_client()
    
    # Get user's configs
    configs_res = supabase.table('whatsapp_configs').select('id').eq('user_id', user_id).execute()
    config_ids = [c['id'] for c in (configs_res.data or [])]
    
    stats = {
        'total_conversations': 0,
        'total_messages': 0,
        'active_configs': len(config_ids)
    }
    
    if config_ids:
        # Count conversations
        conv_res = supabase.table('whatsapp_conversations').select('id', count='exact').in_(
            'config_id', config_ids
        ).execute()
        stats['total_conversations'] = conv_res.count or 0
        
        # Sum message counts
        conv_msgs = supabase.table('whatsapp_conversations').select('message_count').in_(
            'config_id', config_ids
        ).execute()
        stats['total_messages'] = sum(c.get('message_count', 0) for c in (conv_msgs.data or []))
    
    return jsonify({'status': 'success', 'stats': stats})
