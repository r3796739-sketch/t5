"""
WhatsApp Integration Routes (YCloud)
Handles webhook, configuration, and dashboard for WhatsApp Business integration via YCloud.
"""

from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for
from functools import wraps
from datetime import datetime, timezone
import logging
import os
import time as _time

from utils.supabase_client import get_supabase_admin_client
from utils.whatsapp_api import (
    parse_webhook_message,
    send_whatsapp_message,
    send_whatsapp_buttons,
    send_whatsapp_list,
    mark_message_as_read,
    send_whatsapp_typing_indicator,
    verify_webhook_signature,
    send_whatsapp_image,
    send_whatsapp_video,
    send_whatsapp_audio,
    send_whatsapp_document,
    send_whatsapp_location,
    send_whatsapp_cta_url
)
from utils.flow_runner import get_active_flow, run_flow
from utils.qa_utils import answer_question_stream
from utils.crypto import encrypt_token, decrypt_token
from utils import db_utils
from postgrest.exceptions import APIError as PostgrestAPIError

logger = logging.getLogger(__name__)

# Create Blueprint
whatsapp_bp = Blueprint('whatsapp', __name__, url_prefix='/api/whatsapp')


def _supabase_retry(fn, max_retries=3, backoff_seconds=1):
    """
    Execute a Supabase query function with retries.
    Retries on transient errors (SSL 525, connection resets, timeouts).
    Returns the result on success, or re-raises the last exception.
    """
    last_exc = None
    for attempt in range(max_retries):
        try:
            return fn()
        except (PostgrestAPIError, ConnectionError, OSError) as e:
            last_exc = e
            err_str = str(e)
            # Only retry on transient infrastructure errors
            is_transient = any(marker in err_str for marker in ('525', 'SSL', 'handshake', 'ConnectionReset', 'timed out', 'Connection refused'))
            if not is_transient:
                raise  # Not transient — don't retry
            wait = backoff_seconds * (attempt + 1)
            logger.warning(f"[WhatsApp] Supabase transient error (attempt {attempt+1}/{max_retries}), retrying in {wait}s: {type(e).__name__}")
            _time.sleep(wait)
        except Exception:
            raise  # Unknown error — don't retry
    raise last_exc


def _markdown_to_whatsapp(text):
    """
    Convert Markdown formatting to WhatsApp-native formatting.
    
    Markdown → WhatsApp:
      **bold**  or __bold__   →  *bold*
      *italic*  or _italic_   →  _italic_
      ~~strike~~              →  ~strike~
      ```code```              →  ```code```  (WhatsApp supports this)
      # Heading               →  *Heading*
      - item / * item         →  • item
      [text](url)             →  text (url)
      ---                     →  (removed)
    """
    import re

    # Protect code blocks from transformation
    code_blocks = []
    def _save_code(m):
        code_blocks.append(m.group(0))
        return f"__CODE_BLOCK_{len(code_blocks)-1}__"
    text = re.sub(r'```[\s\S]*?```', _save_code, text)

    # Bold: **text** or __text__  →  *text* (WhatsApp bold)
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
    text = re.sub(r'__(.+?)__', r'*\1*', text)

    # Strikethrough: ~~text~~ → ~text~
    text = re.sub(r'~~(.+?)~~', r'~\1~', text)

    # Headers: ## Heading → *Heading*
    text = re.sub(r'^#{1,6}\s+(.+)$', r'*\1*', text, flags=re.MULTILINE)

    # Bullet lists: lines starting with * or - followed by a space → •
    text = re.sub(r'^[\*\-]\s+', '• ', text, flags=re.MULTILINE)

    # Numbered list cleanup: "1. " already works fine on WhatsApp, leave as-is

    # Links: [text](url) → text (url)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1 (\2)', text)

    # Horizontal rules: --- or *** → remove
    text = re.sub(r'^[\-\*]{3,}\s*$', '', text, flags=re.MULTILINE)

    # Restore code blocks
    for i, block in enumerate(code_blocks):
        text = text.replace(f"__CODE_BLOCK_{i}__", block)

    # Clean up excess blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


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
        is_button_reply = parsed.get('is_button_reply', False)
        
        logger.info(
            f"[WhatsApp] Received message from {from_phone} | "
            f"type={parsed.get('type')} | is_button_reply={is_button_reply} | "
            f"text='{message_text[:80]}' | msg_id={message_id}"
        )
        
        supabase = get_supabase_admin_client()
        
        # Idempotency check (with retry for transient Supabase/Cloudflare errors)
        try:
            existing_msg = _supabase_retry(
                lambda: supabase.table('whatsapp_messages').select('id').eq('message_id', message_id).eq('direction', 'inbound').limit(1).execute()
            )
        except Exception as db_err:
            logger.error(f"[WhatsApp] Supabase unreachable for idempotency check after retries: {db_err}")
            # Return 503 so YCloud will retry webhook delivery later
            return jsonify({'status': 'error', 'message': 'Database temporarily unavailable'}), 503

        if existing_msg.data:
            logger.info(f"Message {message_id} already processed. Ignoring duplicate webhook.")
            return jsonify({'status': 'ok'}), 200
            
        # Find the config for this phone number (with retry)
        try:
            config_res = _supabase_retry(
                lambda: supabase.table('whatsapp_configs').select(
                    '*, channels(*)'
                ).eq('phone_number_id', phone_number_id).eq('is_active', True).limit(1).execute()
            )
        except Exception as db_err:
            logger.error(f"[WhatsApp] Supabase unreachable for config lookup after retries: {db_err}")
            return jsonify({'status': 'error', 'message': 'Database temporarily unavailable'}), 503
        
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
        
        # Mark message as read / show typing indicator (fast)
        send_whatsapp_typing_indicator(message_id, api_key)
        
        # --- Return 200 OK immediately so Gunicorn never times out ---
        # All slow work (DB writes, AI call, sending reply) runs in a
        # daemon thread that outlives the HTTP request.
        import threading
        from flask import current_app
        app_ref = current_app._get_current_object()

        def _bg(app_obj):
            with app_obj.app_context():
                try:
                    _handle_whatsapp_message(
                        supabase=supabase,
                        config=config,
                        channel=channel,
                        api_key=api_key,
                        phone_number_id=phone_number_id,
                        from_phone=from_phone,
                        message_text=message_text,
                        message_id=message_id,
                        sender_name=sender_name,
                        parsed=parsed,
                    )
                except Exception as bg_err:
                    logger.error(f"[WhatsApp BG] Unhandled error: {bg_err}", exc_info=True)

        threading.Thread(target=_bg, args=(app_ref,), daemon=True).start()
        return jsonify({'status': 'ok'}), 200

    except Exception as e:
        logger.error(f"Error processing WhatsApp webhook: {e}", exc_info=True)
        return jsonify({'status': 'error'}), 500


def _handle_whatsapp_message(
    supabase, config, channel, api_key,
    phone_number_id, from_phone, message_text,
    message_id, sender_name, parsed
):
    """
    Background handler: all DB writes and AI processing happen here,
    after the webhook has already returned 200 OK to YCloud.
    """
    import json as _json
    import re
    import threading

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

        supabase.table('whatsapp_conversations').update({
            'message_count': conv_res.data[0].get('message_count', 0) + 1
        }).eq('id', conversation_id).execute()

    if not message_text:
        # For button replies, log the raw interactive data so we can debug
        if parsed.get('is_button_reply'):
            logger.error(
                f"[WhatsApp] Button reply received but text is empty! "
                f"Raw interactive data: {parsed.get('raw', {}).get('interactive')}"
            )
        else:
            logger.info(f"[WhatsApp] Skipping message with no text (type={parsed.get('type')})")
        return

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

    # Build chat history prompt
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

    channel_data = channel

    # Check if the sender is the manager
    is_manager = False
    if channel_data and channel_data.get('lead_capture_email'):
        parts = channel_data['lead_capture_email'].split('|')
        if len(parts) > 1:
            manager_phone = parts[1].strip().replace('+', '').replace(' ', '')
            sender_phone = from_phone.replace('+', '').replace(' ', '')
            if manager_phone and sender_phone == manager_phone:
                is_manager = True

    # ── Custom button answer short-circuit ────────────────────────────────
    # If this is a button tap (manual mode) AND that button has a fixed answer
    # configured, skip the AI entirely and send the fixed reply right away.
    if parsed.get('is_button_reply') and channel_data.get('quick_reply_mode') == 'manual':
        import json as _json_btn
        raw_buttons = channel_data.get('quick_reply_buttons') or []
        if isinstance(raw_buttons, str):
            try:
                raw_buttons = _json_btn.loads(raw_buttons)
            except Exception:
                raw_buttons = []
        # Find the button whose title matches what the user tapped
        matched_answer = None
        for btn in raw_buttons:
            if str(btn.get('title', '')).strip().lower() == message_text.strip().lower():
                matched_answer = (btn.get('answer') or '').strip()
                break
        if matched_answer:
            logger.info(f"[WhatsApp] Using custom button answer for '{message_text}'")
            wa_reply = _markdown_to_whatsapp(matched_answer)
            send_result = send_whatsapp_message(
                phone_number_id=phone_number_id,
                to_phone=from_phone,
                message_text=wa_reply,
                api_key=api_key,
            )
            if conversation_id and send_result.get('success'):
                supabase.table('whatsapp_messages').insert({
                    'conversation_id': conversation_id,
                    'message_id': send_result.get('data', {}).get('id'),
                    'direction': 'outbound',
                    'content': wa_reply
                }).execute()
            # Still show buttons after the custom reply
            _send_quick_reply_buttons(channel_data, phone_number_id, from_phone, api_key, wa_reply)
            return
    # ── End custom button answer ──────────────────────────────────────────

    # ── Visual Flow Execution ─────────────────────────────────────────────
    flow = get_active_flow(supabase, channel_data['id']) if channel_data else None
    if flow:
        # We need the conversation state for the flow
        conversation = conv_res.data[0] if conv_res.data else {}
        flow_res = run_flow(
            supabase=supabase,
            flow=flow,
            conversation=conversation,
            message_text=message_text,
            is_button_reply=parsed.get('is_button_reply', False),
            sender_name=sender_name
        )

        # Update conversation state with next node and variables
        if conversation_id and ('next_node_id' in flow_res or 'variables' in flow_res):
            updates = {}
            if 'next_node_id' in flow_res:
                updates['flow_node_id'] = flow_res['next_node_id']
            if 'variables' in flow_res:
                updates['flow_variables'] = flow_res['variables']
            if updates:
                supabase.table('whatsapp_conversations').update(updates).eq('id', conversation_id).execute()

        # If the flow handled it completely (did not fall through to AI)
        if flow_res.get('handled'):
            # Check if it was a lead node that needs the AI lead capture
            if flow_res.get('activate_lead_capture'):
                # We let it fall through to the AI but we could inject a system instruction
                # For now, if the AI handles lead capture based on settings, we just pass
                pass
            else:
                # Execute all discrete actions returned by the flow
                _execute_flow_actions(
                    actions=flow_res.get('actions', []),
                    phone_number_id=phone_number_id,
                    from_phone=from_phone,
                    api_key=api_key,
                    conversation_id=conversation_id,
                    supabase=supabase
                )
                return

    # ── AI Processing ─────────────────────────────────────────────────────
    image_base64 = None
    image_mime_type = None
    if parsed.get('type') == 'image' and parsed.get('media_id'):
        from utils.whatsapp_api import download_whatsapp_media
        media_data = download_whatsapp_media(parsed['media_id'], api_key)
        if media_data:
            image_base64 = media_data.get('base64')
            image_mime_type = media_data.get('mime_type')
            logger.info(f"Successfully downloaded image {parsed['media_id']} for {from_phone}")

    # Get AI response — materialize the full stream before processing
    response_text = ""

    try:
        all_chunks = list(answer_question_stream(
            question_for_prompt=final_question,
            question_for_search=message_text,
            channel_data=channel_data,
            user_id=config['user_id'],
            is_manager=is_manager,
            image_base64=image_base64,
            image_mime_type=image_mime_type,
            integration_source='whatsapp',
            conversation_id=f"whatsapp_{from_phone}"
        ))
    except Exception as stream_err:
        logger.error(f"[WhatsApp BG] Error materializing AI stream: {stream_err}", exc_info=True)
        all_chunks = []

    print(f"[WhatsApp BG] Received {len(all_chunks)} SSE chunks from AI stream")

    for chunk in all_chunks:
        if chunk.startswith('data: '):
            data_str = chunk.replace('data: ', '').strip()
            if data_str == "[DONE]":
                break
            try:
                parsed_data = _json.loads(data_str)
                if parsed_data.get('error') == 'QUERY_LIMIT_REACHED':
                    response_text = ""
                    break
                if parsed_data.get('answer'):
                    response_text += parsed_data['answer']
            except _json.JSONDecodeError:
                continue

    print(f"[WhatsApp BG] Final response length: {len(response_text)} chars")
    print(f"[WhatsApp BG] Response preview: {response_text[:300]}...")

    # Extract lead capture marker if present
    lead_complete_marker = None
    lead_match = re.search(r'\[LEAD_COMPLETE:\s*(\{.*?\})\]', response_text, re.DOTALL)
    if lead_match:
        try:
            lead_complete_marker = _json.loads(lead_match.group(1))
            response_text = re.sub(r'\[LEAD_COMPLETE:\s*\{.*?\}\]', '', response_text, flags=re.DOTALL).strip()
        except _json.JSONDecodeError:
            pass

    # Extract flow trigger marker if present
    trigger_flow_marker = None
    trigger_match = re.search(r'\[TRIGGER_FLOW:\s*"(.*?)"\]', response_text)
    if trigger_match:
        trigger_flow_marker = trigger_match.group(1)
        response_text = re.sub(r'\[TRIGGER_FLOW:\s*".*?"\]', '', response_text).strip()

    # ── AI Flow Trigger Execution ─────────────────────────────────────────
    if trigger_flow_marker:
        # User requested to trigger a flow
        target_flow = None
        # Try to find a flow by name first
        flow_res_by_name = supabase.table('channel_flows').select('id, flow_data').eq('channel_id', channel_data['id']).ilike('name', trigger_flow_marker).limit(1).execute()
        
        if flow_res_by_name.data:
            target_flow = {'flow_id': flow_res_by_name.data[0]['id'], **flow_res_by_name.data[0]['flow_data']}
        else:
            # Maybe they passed the actual UUID
            try:
                flow_res_by_id = supabase.table('channel_flows').select('id, flow_data').eq('channel_id', channel_data['id']).eq('id', trigger_flow_marker).limit(1).execute()
                if flow_res_by_id.data:
                    target_flow = {'flow_id': flow_res_by_id.data[0]['id'], **flow_res_by_id.data[0]['flow_data']}
            except Exception:
                pass

        if target_flow:
            logger.info(f"AI triggered flow: {trigger_flow_marker}")
            
            # Reset conversation flow state to start the new flow
            conv_data = conv_res.data[0] if conv_res.data else {}
            new_variables = dict(conv_data.get('flow_variables') or {})
            if sender_name and 'name' not in new_variables:
                new_variables['name'] = sender_name
                
            trigger_res = run_flow(
                supabase=supabase,
                flow=target_flow,
                conversation={'flow_node_id': None, 'flow_variables': new_variables},
                message_text='', # Start node evaluation doesn't need text
                is_button_reply=False,
                sender_name=sender_name
            )
            
            # Update DB with new flow state
            if conversation_id and ('next_node_id' in trigger_res or 'variables' in trigger_res):
                updates = {}
                if 'next_node_id' in trigger_res:
                    updates['flow_node_id'] = trigger_res['next_node_id']
                if 'variables' in trigger_res:
                    updates['flow_variables'] = trigger_res['variables']
                if updates:
                    supabase.table('whatsapp_conversations').update(updates).eq('id', conversation_id).execute()
            
            # Execute triggered actions
            if trigger_res.get('handled'):
                if trigger_res.get('actions'):
                    _execute_flow_actions(
                        actions=trigger_res.get('actions', []),
                        phone_number_id=phone_number_id,
                        from_phone=from_phone,
                        api_key=api_key,
                        conversation_id=conversation_id,
                        supabase=supabase
                    )
                # If we triggered a flow, we shouldn't send the rest of the AI text and we're done here
                return

    # Send response back via YCloud
    if response_text:
        response_text = _markdown_to_whatsapp(response_text)
        send_result = send_whatsapp_message(
            phone_number_id=phone_number_id,
            to_phone=from_phone,
            message_text=response_text,
            api_key=api_key
        )

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
        from flask import current_app
        app_ref = current_app._get_current_object()
        def _send_lead_bg(app_obj, cid, marker):
            with app_obj.app_context():
                try:
                    process_lead_submission(cid, marker)
                except Exception as lead_e:
                    logger.error(f"Error submitting whatsapp lead: {lead_e}")
        threading.Thread(
            target=_send_lead_bg,
            args=(app_ref, channel_data['id'], lead_complete_marker),
            daemon=True
        ).start()

    # ─── Quick-reply buttons ───────────────────────────────────────────────────
    # Only send buttons if the AI actually produced a meaningful response
    # and the channel has quick reply enabled.
    if response_text and send_result.get('success'):
        # If the flow returned post-AI actions (like standard flow reply buttons), execute those instead
        post_ai_actions = flow_res.get('post_ai_actions', []) if flow and 'flow_res' in locals() else []
        if post_ai_actions:
            _execute_flow_actions(
                actions=post_ai_actions,
                phone_number_id=phone_number_id,
                from_phone=from_phone,
                api_key=api_key,
                conversation_id=conversation_id,
                supabase=supabase
            )
        else:
            _send_quick_reply_buttons(
                channel_data=channel_data,
                phone_number_id=phone_number_id,
                from_phone=from_phone,
                api_key=api_key,
                response_text=response_text,
            )

def _execute_flow_actions(actions, phone_number_id, from_phone, api_key, conversation_id, supabase):
    """Execute a list of actions returned by the visual flow runner."""
    for action in actions:
        atype = action.get('type')
        res = None
        txt = ''
        
        if atype == 'text':
            txt = _markdown_to_whatsapp(action.get('text', ''))
            res = send_whatsapp_message(phone_number_id, from_phone, txt, api_key)
        elif atype == 'image':
            txt = action.get('caption', '')
            res = send_whatsapp_image(phone_number_id, from_phone, action.get('url'), txt, api_key)
        elif atype == 'video':
            txt = action.get('caption', '')
            res = send_whatsapp_video(phone_number_id, from_phone, action.get('url'), txt, api_key)
        elif atype == 'audio':
            res = send_whatsapp_audio(phone_number_id, from_phone, action.get('url'), api_key)
        elif atype == 'document':
            txt = action.get('caption', '')
            res = send_whatsapp_document(phone_number_id, from_phone, action.get('url'), action.get('filename'), txt, api_key)
        elif atype == 'location':
            res = send_whatsapp_location(phone_number_id, from_phone, action.get('latitude'), action.get('longitude'), api_key, action.get('name'), action.get('address'))
        elif atype == 'buttons':
            txt = _markdown_to_whatsapp(action.get('body', ''))
            from utils.whatsapp_api import send_whatsapp_buttons
            res = send_whatsapp_buttons(phone_number_id, from_phone, txt, action.get('buttons', []), api_key)
        elif atype == 'cta_url':
            txt = _markdown_to_whatsapp(action.get('body', ''))
            from utils.whatsapp_api import send_whatsapp_cta_url
            if 'cta_buttons' in action:
                btns = action['cta_buttons']
            else:
                cta_button = {
                    "type": action.get("action_type") or "url",
                    "text": action.get("label", "Click"),
                }
                if cta_button["type"] == "url":
                    cta_button["url"] = action.get("url", "")
                else:
                    cta_button["phone"] = action.get("url", "")
                btns = [cta_button]
            res = send_whatsapp_cta_url(phone_number_id, from_phone, txt, btns, api_key)
        elif atype == 'list':
            txt = _markdown_to_whatsapp(action.get('body', ''))
            from utils.whatsapp_api import send_whatsapp_list
            res = send_whatsapp_list(
                phone_number_id=phone_number_id,
                to_phone=from_phone,
                body_text=txt,
                rows=action.get('rows', []),
                api_key=api_key,
                button_label=action.get('button_label', 'See Options'),
                section_title=action.get('section_title', 'Options')
            )

        # Log outbound message in history
        if conversation_id and res and res.get('success'):
            supabase.table('whatsapp_messages').insert({
                'conversation_id': conversation_id,
                'message_id': res.get('data', {}).get('id'),
                'direction': 'outbound',
                'content': txt or f"[{atype} media sent]"
            }).execute()



def _call_llm_for_buttons(prompt: str) -> str:
    """
    Makes a single, non-streaming LLM call for button suggestion.
    Uses the SAME provider/model/key as the main chatbot (LLM_PROVIDER + MODEL_NAME env vars).
    Returns raw text from the LLM, or empty string on failure.
    """
    provider = os.environ.get('LLM_PROVIDER', 'gemini').lower()
    model = os.environ.get('MODEL_NAME', '')
    if not model:
        logger.warning("[QuickReply] MODEL_NAME not set in env, cannot call LLM for buttons.")
        return ''

    try:
        if provider == 'gemini':
            api_key = os.environ.get('GEMINI_API_KEY2') or os.environ.get('GEMINI_API_KEY', '')
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            gemini_model = genai.GenerativeModel(model)
            response = gemini_model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=120,
                    temperature=0.2,
                )
            )
            return response.text.strip() if response.text else ''

        elif provider in ('openai', 'groq'):
            import openai
            if provider == 'groq':
                api_key = os.environ.get('GROQ_API_KEY', '')
                base_url = 'https://api.groq.com/openai/v1'
            else:
                api_key = os.environ.get('OPENAI_API_KEY', '')
                base_url = os.environ.get('OPENAI_API_BASE_URL') or None
            client = openai.OpenAI(api_key=api_key, base_url=base_url)
            completion = client.chat.completions.create(
                model=model,
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0.2,
                max_tokens=120,
            )
            return (completion.choices[0].message.content or '').strip()

        elif provider == 'ollama':
            ollama_url = os.environ.get('OLLAMA_URL', 'http://localhost:11434')
            import requests as _req
            resp = _req.post(
                f"{ollama_url}/api/chat",
                json={"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False},
                timeout=15
            )
            resp.raise_for_status()
            return resp.json().get('message', {}).get('content', '').strip()

        else:
            logger.warning(f"[QuickReply] Unknown LLM_PROVIDER '{provider}', cannot generate buttons.")
            return ''

    except Exception as e:
        logger.warning(f"[QuickReply] LLM call failed (provider={provider}): {e}")
        return ''


def _send_quick_reply_buttons(
    channel_data: dict,
    phone_number_id: str,
    from_phone: str,
    api_key: str,
    response_text: str,
) -> None:
    """
    After the main AI response is sent, optionally send interactive quick-reply buttons.

    Behaviour depends on channel_data['quick_reply_mode']:
      'off'    → do nothing (default)
      'manual' → use the fixed buttons saved in channel_data['quick_reply_buttons']
      'ai'     → ask the same LLM (from LLM_PROVIDER env) to suggest 1-3 contextual buttons
    """
    import json as _json

    mode = (channel_data or {}).get('quick_reply_mode', 'off')
    if mode == 'off' or not channel_data:
        return

    buttons = []

    try:
        if mode == 'manual':
            # Use the operator-configured static buttons
            raw_buttons = channel_data.get('quick_reply_buttons') or []
            if isinstance(raw_buttons, str):
                raw_buttons = _json.loads(raw_buttons)
            buttons = [
                {'id': str(b.get('id', b.get('title', '')))[:20], 'title': str(b.get('title', ''))[:20]}
                for b in raw_buttons
                if b.get('title', '').strip()
            ]

        elif mode == 'ai':
            business_name = channel_data.get('channel_name', 'the business')
            bot_type = channel_data.get('bot_type', 'business')

            # Single combined prompt (works for all providers including Gemini which has no system role)
            prompt = (
                "You are a quick-reply button suggestion engine for a business chatbot.\n"
                "Based on the AI response below, suggest 1-3 short follow-up button labels "
                "the customer might want to tap next.\n\n"
                "RULES:\n"
                "- Each label MUST be 20 characters or fewer\n"
                "- Labels must be specific and actionable (e.g. 'Book Now', 'See Prices', 'Get Details')\n"
                "- Never suggest generic labels like 'Yes', 'No', 'OK', 'Sure'\n"
                "- Return ONLY a valid JSON array of strings, nothing else\n"
                "- If no buttons make sense, return []\n\n"
                f"Business: {business_name} (type: {bot_type})\n\n"
                f"AI Response:\n{response_text[:800]}"
            )

            raw = _call_llm_for_buttons(prompt)
            if not raw:
                return

            # Strip markdown code fences if LLM wrapped the JSON
            if '```' in raw:
                raw = raw.split('```')[1]
                if raw.startswith('json'):
                    raw = raw[4:]
                raw = raw.strip()

            labels = _json.loads(raw)
            if isinstance(labels, list):
                buttons = [
                    {'id': str(label)[:20], 'title': str(label)[:20]}
                    for label in labels
                    if isinstance(label, str) and label.strip()
                ]

    except Exception as btn_err:
        logger.warning(f"[QuickReply] Could not build buttons (mode={mode}): {btn_err}")
        return

    if not buttons:
        return

    try:
        num = len(buttons)
        if num <= 3:
            send_whatsapp_buttons(
                phone_number_id=phone_number_id,
                to_phone=from_phone,
                body_text="What would you like to do next?",
                buttons=buttons,
                api_key=api_key,
            )
        else:
            rows = [{'id': b['id'], 'title': b['title']} for b in buttons]
            send_whatsapp_list(
                phone_number_id=phone_number_id,
                to_phone=from_phone,
                body_text="What would you like to explore?",
                rows=rows,
                api_key=api_key,
                button_label="See Options",
            )
        logger.info(f"[QuickReply] Sent {num} button(s) to {from_phone} (mode={mode}, provider={os.environ.get('LLM_PROVIDER', 'gemini')})")
    except Exception as send_err:
        logger.warning(f"[QuickReply] Failed to send buttons: {send_err}")




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
