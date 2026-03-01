"""
WhatsApp Integration Routes (YCloud)
Handles webhook, configuration, and dashboard for WhatsApp Business integration via YCloud.
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
    send_whatsapp_typing_indicator,
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
            is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' \
                      or 'application/json' in request.headers.get('Accept', '') \
                      or request.content_type == 'application/json'
            if is_ajax or request.method != 'GET':
                return jsonify({'status': 'error', 'message': 'Authentication required.'}), 401
            return redirect(url_for('channel') + '?login=1')
        return f(*args, **kwargs)
    return decorated_function


# ==========================================
# WEBHOOK ENDPOINTS (Called by YCloud)
# ==========================================

@whatsapp_bp.route('/webhook', methods=['GET'])
def verify_webhook():
    """
    Webhook verification endpoint.
    YCloud sends POST requests directly; this GET handler
    returns 200 for basic health checks.
    """
    return 'OK', 200


@whatsapp_bp.route('/webhook', methods=['POST'])
def receive_message():
    """
    Webhook endpoint to receive inbound messages from YCloud.
    Validates YCloud-Signature header and processes incoming WhatsApp messages.
    """
    try:
        data = request.get_json()
        logger.info(f"raw webhook data: {data}")
        
        # Read YCloud-Signature header (for optional verification)
        signature = request.headers.get('YCloud-Signature', '')
        
        # Parse the incoming webhook event
        parsed = parse_webhook_message(data)
        
        if not parsed:
            # Not a message event (status update, receipt, etc.)
            return jsonify({'status': 'ok'}), 200
        
        phone_number_id = parsed['phone_number_id']
        from_phone = parsed['from_phone']
        message_text = parsed.get('text', '')
        message_id = parsed['message_id']
        sender_name = parsed.get('sender_name', 'Unknown')
        
        logger.info(f"Received WhatsApp message from {from_phone} to phone ID {phone_number_id}")
        
        supabase = get_supabase_admin_client()
        
        # Idempotency check: see if we already processed this message
        existing_msg = supabase.table('whatsapp_messages').select('id').eq('message_id', message_id).eq('direction', 'inbound').limit(1).execute()
        if existing_msg.data:
            logger.info(f"Message {message_id} already processed. Ignoring duplicate webhook.")
            return jsonify({'status': 'ok'}), 200
            
        # Find the config for this phone number
        config_res = supabase.table('whatsapp_configs').select(
            '*, channels(*)'
        ).eq('phone_number_id', phone_number_id).eq('is_active', True).limit(1).execute()
        
        if not config_res.data:
            logger.warning(f"No active config found for phone number ID: {phone_number_id}")
            return jsonify({'status': 'no_config'}), 200
        
        config = config_res.data[0]
        
        # Validate YCloud signature if webhook secret is configured
        webhook_secret = config.get('verify_token')
        if webhook_secret and signature:
            if not verify_webhook_signature(request.get_data(), signature, webhook_secret):
                logger.warning("WhatsApp webhook received with invalid YCloud-Signature")
        
        channel = config.get('channels')
        
        if not channel:
            logger.warning(f"No channel linked to WhatsApp config {config['id']}")
            return jsonify({'status': 'no_channel'}), 200
        
        # Decrypt the user's YCloud API key
        api_key = decrypt_token(config['access_token'])
        
        # Mark message as read and show typing indicator
        send_whatsapp_typing_indicator(message_id, api_key)
        
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
                    history_limit = 40 if channel and isinstance(channel, dict) and channel.get('lead_capture_enabled') else 10
                    history_res = supabase.table('whatsapp_messages').select('direction, content').eq(
                        'conversation_id', conversation_id
                    ).order('created_at', desc=True).limit(history_limit).execute()
                    
                    if history_res.data:
                        for msg in reversed(history_res.data):
                            role = 'user' if msg['direction'] == 'inbound' else 'assistant'
                            history.append({'role': role, 'content': msg['content']})
                
                # Build chat history prompt (matching app.py pattern)
                chat_history_for_prompt = ""
                for h in history[:-1]:  # Exclude current message
                    prefix = "Human" if h['role'] == 'user' else "AI"
                    chat_history_for_prompt += f"{prefix}: {h['content']}\n\n"
                
                final_question = message_text
                if chat_history_for_prompt:
                    final_question = (
                        f"Given the following conversation history:\n{chat_history_for_prompt}"
                        f"--- End History ---\n\n"
                        f"Now, answer this new question, considering the history as context:\n{message_text}"
                    )
                # Get channel data dict
                channel_data = channel  # already fetched from DB via channels(*)
                
                # Check if the sender is the manager
                is_manager = False
                if channel_data and channel_data.get('lead_capture_email'):
                    parts = channel_data['lead_capture_email'].split('|')
                    if len(parts) > 1:
                        manager_phone = parts[1].strip().replace('+', '').replace(' ', '')
                        sender_phone = from_phone.replace('+', '').replace(' ', '')
                        if manager_phone and sender_phone == manager_phone:
                            is_manager = True

                # Process image if present
                image_base64 = None
                image_mime_type = None
                if parsed.get('type') == 'image' and parsed.get('media_id'):
                    from utils.whatsapp_api import download_whatsapp_media
                    media_data = download_whatsapp_media(parsed['media_id'], api_key)
                    if media_data:
                        image_base64 = media_data.get('base64')
                        image_mime_type = media_data.get('mime_type')
                        logger.info(f"Successfully downloaded image {parsed['media_id']} for {from_phone}")

                # Get AI response (returns SSE-formatted strings)
                import json as _json
                response_text = ""
                for chunk in answer_question_stream(
                    question_for_prompt=final_question,
                    question_for_search=message_text,
                    channel_data=channel_data,
                    user_id=config['user_id'],
                    is_manager=is_manager,
                    image_base64=image_base64,
                    image_mime_type=image_mime_type
                ):
                    if chunk.startswith('data: '):
                        data_str = chunk.replace('data: ', '').strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            parsed_data = _json.loads(data_str)
                            if parsed_data.get('answer'):
                                response_text += parsed_data['answer']
                        except _json.JSONDecodeError:
                            continue
                
                # Extract lead capture marker if present
                import re
                lead_complete_marker = None
                lead_match = re.search(r'\[LEAD_COMPLETE:\s*(\{.*?\})\]', response_text, re.DOTALL)
                if lead_match:
                    try:
                        lead_complete_marker = _json.loads(lead_match.group(1))
                        response_text = re.sub(r'\[LEAD_COMPLETE:\s*\{.*?\}\]', '', response_text, flags=re.DOTALL).strip()
                    except _json.JSONDecodeError:
                        pass
                
                # Send response back via YCloud
                if response_text:
                    send_result = send_whatsapp_message(
                        phone_number_id=phone_number_id,
                        to_phone=from_phone,
                        message_text=response_text,
                        api_key=api_key
                    )
                    
                    # Store outgoing message
                    if conversation_id and send_result.get('success'):
                        outbound_msg_id = send_result.get('data', {}).get('id')
                        supabase.table('whatsapp_messages').insert({
                            'conversation_id': conversation_id,
                            'message_id': outbound_msg_id,
                            'direction': 'outbound',
                            'content': response_text
                        }).execute()
                        
                # Submit lead if captured
                if lead_complete_marker and channel_data.get('lead_capture_enabled'):
                    from app import process_lead_submission
                    try:
                        process_lead_submission(channel_data['id'], lead_complete_marker)
                    except Exception as lead_e:
                        logger.error(f"Error submitting whatsapp lead: {lead_e}")
                        
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
    
    # Mask API keys for security
    for config in configs:
        if config.get('access_token'):
            plain_token = decrypt_token(config['access_token'])
            config['access_token'] = plain_token[:8] + '...' + plain_token[-4:]
    
    return jsonify({'status': 'success', 'configs': configs})


@whatsapp_bp.route('/config', methods=['POST'])
@login_required
def save_config():
    """Save or update WhatsApp configuration."""
    user_id = session['user']['id']
    data = request.get_json()
    
    required_fields = ['phone_number_id', 'channel_id', 'api_key']
    for field in required_fields:
        if not data.get(field):
            return jsonify({'status': 'error', 'message': f'{field} is required'}), 400
    
    supabase = get_supabase_admin_client()
    
    # Verify the channel belongs to this user
    channel_res = supabase.table('channels').select('id, creator_id').eq('id', data['channel_id']).limit(1).execute()
    if not channel_res.data or str(channel_res.data[0].get('creator_id')) != str(user_id):
        return jsonify({'status': 'error', 'message': 'Invalid channel'}), 403
    
    # Encrypt the user's YCloud API key before storing
    encrypted_api_key = encrypt_token(data['api_key'])
    
    # Store the webhook secret provided by the user (if any)
    webhook_secret = data.get('webhook_secret', '').strip()
    
    config_data = {
        'user_id': user_id,
        'channel_id': data['channel_id'],
        'phone_number_id': data['phone_number_id'],
        'access_token': encrypted_api_key,
        'verify_token': webhook_secret,
        'is_active': True
    }
    
    try:
        # Upsert config
        result = supabase.table('whatsapp_configs').upsert(
            config_data,
            on_conflict='user_id,phone_number_id'
        ).execute()
        
        logger.info(f"WhatsApp config saved for user {user_id}")
        
        # Return the webhook URL they need to configure in YCloud
        webhook_url = request.host_url.rstrip('/') + '/api/whatsapp/webhook'
        
        return jsonify({
            'status': 'success',
            'message': 'Configuration saved',
            'config': {
                'id': result.data[0]['id'] if result.data else None,
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
