"""
WhatsApp Business API Utilities
Handles sending/receiving messages via Meta's WhatsApp Cloud API
"""

import requests
import logging
import hmac
import hashlib
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

WHATSAPP_API_URL = "https://graph.facebook.com/v18.0"


def verify_webhook_signature(payload: bytes, signature: str, app_secret: str) -> bool:
    """
    Verify that webhook payload came from Meta.
    """
    if not signature or not signature.startswith('sha256='):
        return False
    
    expected_signature = hmac.new(
        app_secret.encode('utf-8'),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(signature[7:], expected_signature)


def send_whatsapp_message(
    phone_number_id: str,
    access_token: str,
    to_phone: str,
    message_text: str
) -> Dict[str, Any]:
    """
    Send a text message via WhatsApp Business API.
    
    Args:
        phone_number_id: The WhatsApp Phone Number ID
        access_token: User's WhatsApp access token
        to_phone: Recipient's phone number (with country code)
        message_text: The message to send
        
    Returns:
        API response dict
    """
    url = f"{WHATSAPP_API_URL}/{phone_number_id}/messages"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": message_text
        }
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        logger.info(f"WhatsApp message sent to {to_phone}: {result.get('messages', [{}])[0].get('id')}")
        return {"success": True, "data": result}
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send WhatsApp message: {e}")
        return {"success": False, "error": str(e)}


def send_whatsapp_template(
    phone_number_id: str,
    access_token: str,
    to_phone: str,
    template_name: str,
    language_code: str = "en"
) -> Dict[str, Any]:
    """
    Send a template message via WhatsApp Business API.
    Templates are pre-approved messages required for initiating conversations.
    """
    url = f"{WHATSAPP_API_URL}/{phone_number_id}/messages"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {
                "code": language_code
            }
        }
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        return {"success": True, "data": response.json()}
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send WhatsApp template: {e}")
        return {"success": False, "error": str(e)}


def mark_message_as_read(
    phone_number_id: str,
    access_token: str,
    message_id: str
) -> bool:
    """
    Mark a received message as read (shows blue checkmarks to sender).
    """
    url = f"{WHATSAPP_API_URL}/{phone_number_id}/messages"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        return response.status_code == 200
    except:
        return False


def parse_webhook_message(data: Dict) -> Optional[Dict]:
    """
    Parse incoming webhook data from WhatsApp.
    
    Returns parsed message dict or None if not a message event.
    """
    try:
        entry = data.get('entry', [{}])[0]
        changes = entry.get('changes', [{}])[0]
        value = changes.get('value', {})
        
        # Get phone number ID this message was sent to
        phone_number_id = value.get('metadata', {}).get('phone_number_id')
        
        # Check if this is a message event
        messages = value.get('messages', [])
        if not messages:
            return None
        
        message = messages[0]
        
        # Get sender info
        contacts = value.get('contacts', [{}])
        sender_name = contacts[0].get('profile', {}).get('name', 'Unknown') if contacts else 'Unknown'
        
        return {
            'phone_number_id': phone_number_id,
            'message_id': message.get('id'),
            'from_phone': message.get('from'),
            'sender_name': sender_name,
            'timestamp': message.get('timestamp'),
            'type': message.get('type'),
            'text': message.get('text', {}).get('body', '') if message.get('type') == 'text' else None,
            'raw': message
        }
    except Exception as e:
        logger.error(f"Error parsing webhook message: {e}")
        return None


def get_phone_number_info(phone_number_id: str, access_token: str) -> Optional[Dict]:
    """
    Get information about a WhatsApp phone number.
    """
    url = f"{WHATSAPP_API_URL}/{phone_number_id}"
    
    headers = {
        "Authorization": f"Bearer {access_token}"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        return {
            'display_phone_number': data.get('display_phone_number'),
            'verified_name': data.get('verified_name'),
            'quality_rating': data.get('quality_rating')
        }
    except Exception as e:
        logger.error(f"Error fetching phone number info: {e}")
        return None
