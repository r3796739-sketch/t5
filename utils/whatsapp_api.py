"""
WhatsApp Business API Utilities (YCloud)
Handles sending/receiving messages via YCloud's WhatsApp Cloud API.
Each user provides their own YCloud API key.
"""

import os
import requests
import logging
import hmac
import hashlib
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

YCLOUD_BASE_URL = "https://api.ycloud.com/v2"
YCLOUD_SEND_URL = f"{YCLOUD_BASE_URL}/whatsapp/messages/sendDirectly"


def _ycloud_headers(api_key: str) -> Dict[str, str]:
    """Build standard YCloud API headers with the user's API key."""
    return {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }


def verify_webhook_signature(payload: bytes, signature: str, webhook_secret: str) -> bool:
    """
    Verify that a webhook payload came from YCloud.
    YCloud-Signature format: t={timestamp},s={hmac_sha256_hex}
    The signed payload is: "{timestamp}.{raw_body}"
    """
    if not signature:
        return False

    try:
        # Parse: "t=1234567890,s=abcdef..."
        parts = dict(p.split('=', 1) for p in signature.split(','))
        timestamp = parts.get('t', '')
        received_sig = parts.get('s', '')

        if not timestamp or not received_sig:
            return False

        # Build the signed payload string
        signed_payload = f"{timestamp}.{payload.decode('utf-8')}"

        expected_sig = hmac.new(
            webhook_secret.encode('utf-8'),
            signed_payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(expected_sig, received_sig)
    except Exception as e:
        logger.error(f"Error verifying YCloud webhook signature: {e}")
        return False


def send_whatsapp_message(
    phone_number_id: str,
    to_phone: str,
    message_text: str,
    api_key: str
) -> Dict[str, Any]:
    """
    Send a text message via YCloud WhatsApp API.

    Args:
        phone_number_id: The sender's WhatsApp phone number (e.g. "+14155552671")
        to_phone: The recipient's phone number
        message_text: The text message body
        api_key: The user's YCloud API key
    """
    payload = {
        "from": phone_number_id,
        "to": to_phone,
        "type": "text",
        "text": {
            "body": message_text
        }
    }

    try:
        response = requests.post(
            YCLOUD_SEND_URL,
            headers=_ycloud_headers(api_key),
            json=payload,
            timeout=10
        )
        response.raise_for_status()
        return {"success": True, "data": response.json()}
    except Exception as e:
        logger.error(f"Failed to send YCloud message: {e}")
        return {"success": False, "error": str(e)}


def send_whatsapp_template(
    phone_number_id: str,
    to_phone: str,
    template_name: str,
    api_key: str,
    language_code: str = "en",
    components: list = None
) -> Dict[str, Any]:
    """
    Send a template message via YCloud WhatsApp API.
    Templates are pre-approved messages required for initiating conversations
    outside the 24-hour customer service window.

    Args:
        phone_number_id: The sender's WhatsApp phone number
        to_phone: The recipient's phone number
        template_name: Name of the approved template
        api_key: The user's YCloud API key
        language_code: Language code for the template (default: "en")
        components: Optional template components (header, body, button params)
    """
    template_obj = {
        "name": template_name,
        "language": {
            "code": language_code
        }
    }
    if components:
        template_obj["components"] = components

    payload = {
        "from": phone_number_id,
        "to": to_phone,
        "type": "template",
        "template": template_obj
    }

    try:
        response = requests.post(
            YCLOUD_SEND_URL,
            headers=_ycloud_headers(api_key),
            json=payload,
            timeout=30
        )
        response.raise_for_status()
        return {"success": True, "data": response.json()}
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send WhatsApp template: {e}")
        return {"success": False, "error": str(e)}


def mark_message_as_read(
    message_id: str,
    api_key: str
) -> bool:
    """
    Mark a received message as read (shows blue checkmarks to sender).
    Uses YCloud endpoint: POST /v2/whatsapp/inboundMessages/{messageId}/markAsRead

    Args:
        message_id: The WhatsApp message ID (e.g. "wamid.HBgL...")
        api_key: The user's YCloud API key
    """
    url = f"{YCLOUD_BASE_URL}/whatsapp/inboundMessages/{message_id}/markAsRead"

    try:
        response = requests.post(
            url,
            headers=_ycloud_headers(api_key),
            timeout=10
        )
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Failed to mark message as read: {e}")
        return False


def send_whatsapp_typing_indicator(
    message_id: str,
    api_key: str
) -> bool:
    """
    Mark a message as read and show a typing indicator (max 25s).
    Uses YCloud endpoint: POST /v2/whatsapp/inboundMessages/{messageId}/typingIndicator

    Args:
        message_id: The WhatsApp message ID (e.g. "wamid.HBgL...")
        api_key: The user's YCloud API key
    """
    url = f"{YCLOUD_BASE_URL}/whatsapp/inboundMessages/{message_id}/typingIndicator"

    try:
        response = requests.post(
            url,
            headers=_ycloud_headers(api_key),
            timeout=10
        )
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Failed to send typing indicator: {e}")
        return False


def parse_webhook_message(data: Dict) -> Optional[Dict]:
    """
    Parse incoming webhook data from YCloud.
    Returns a normalized dict or None if not a message event.
    """
    try:
        event_type = data.get('type', '')

        # Only process inbound message events
        if event_type != 'whatsapp.inbound_message.received':
            logger.debug(f"Ignoring non-message webhook event: {event_type}")
            return None

        msg = data.get('whatsappInboundMessage')
        if not msg:
            logger.warning("whatsapp.inbound_message.received event with no message body")
            return None

        msg_type = msg.get('type', 'text')
        
        parsed = {
            'phone_number_id': msg.get('to'),           # Bot's phone number
            'message_id': msg.get('id'),                 # WhatsApp message ID
            'from_phone': msg.get('from'),               # Customer's phone number
            'sender_name': msg.get('customerProfile', {}).get('name', 'Unknown'),
            'timestamp': msg.get('timestamp'),
            'type': msg_type,
            'waba_id': msg.get('wabaId'),                # WhatsApp Business Account ID
            'raw': msg
        }
        
        if msg_type == 'text':
            parsed['text'] = msg.get('text', {}).get('body', '')
        elif msg_type == 'image':
            parsed['media_id'] = msg.get('image', {}).get('id')
            parsed['text'] = msg.get('image', {}).get('caption', '') # Fallback caption to text property
            parsed['mime_type'] = msg.get('image', {}).get('mimeType', 'image/jpeg')

        return parsed
    except Exception as e:
        logger.error(f"Error parsing webhook message: {e}")
        return None


def download_whatsapp_media(media_id: str, api_key: str) -> Optional[Dict]:
    """
    Download media from YCloud by media_id.
    Returns a dict with 'content' (bytes) and 'mime_type' if successful.
    """
    import base64
    url = f"{YCLOUD_BASE_URL}/whatsapp/media/{media_id}/download"
    try:
        response = requests.get(
            url,
            headers=_ycloud_headers(api_key),
            timeout=20
        )
        if response.status_code == 200:
            return {
                "base64": base64.b64encode(response.content).decode('utf-8'),
                "mime_type": response.headers.get("Content-Type", "image/jpeg")
            }
        else:
            logger.error(f"Failed to download YCloud media {media_id}. Status: {response.status_code}, Response: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Error downloading YCloud media {media_id}: {e}")
        return None


def get_phone_number_info(phone_number_id: str, api_key: str) -> Optional[Dict]:
    """
    Get information about a WhatsApp phone number from YCloud.
    Note: This requires knowing the WABA ID. Since we may not have it
    at config time, this returns None gracefully.

    Args:
        phone_number_id: The phone number to look up
        api_key: The user's YCloud API key
    """
    # YCloud's phone number retrieval requires wabaId + phoneNumber path.
    # We don't store wabaId separately, so we skip this for now.
    # Phone number display info will come from incoming webhook messages instead.
    return None
