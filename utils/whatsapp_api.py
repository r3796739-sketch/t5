"""
WhatsApp Business API Utilities
Handles sending/receiving messages via Meta's WhatsApp Cloud API
"""

import os
import requests
import logging
import hmac
import hashlib
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

WHATSAPP_API_URL = "https://graph.facebook.com/v18.0"
YCLOUD_API_URL = "https://api.ycloud.com/v2/whatsapp/messages/sendDirectly"

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
    to_phone: str, 
    message_text: str
) -> Dict[str, Any]:
    """
    Send a text message via YCloud WhatsApp API.
    """
    # Fetch your Master Key from the .env file
    api_key = os.environ.get("YCLOUD_API_KEY")
    
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    # YCloud's simplified payload format
    payload = {
        "from": phone_number_id,  # This is the user's specific phone ID
        "to": to_phone,
        "type": "text",
        "text": {
            "body": message_text
        }
    }
    
    try:
        response = requests.post(YCLOUD_API_URL, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        # Return in a format compatible with existing code expecting {"success": True, "data": ...}
        return {"success": True, "data": response.json()}
    except Exception as e:
        logger.error(f"Failed to send YCloud message: {e}")
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
    Parse incoming webhook data from YCloud/WhatsApp.
    
    YCloud payload format:
    {
      "id": "wamid.HBgL...",
      "from": "1234567890",
      "to": "0987654321",
      "type": "text",
      "text": {
        "body": "Hello!"
      },
      "timestamp": "1691234567"
    }
    """
    try:
        # Check if this is a YCloud message event
        if 'id' not in data or 'from' not in data or 'to' not in data:
            return None
        
        # YCloud directly sends the message object
        return {
            'phone_number_id': data.get('to'),      # User's bot number
            'message_id': data.get('id'),
            'from_phone': data.get('from'),         # Customer's number
            'sender_name': data.get('profile', {}).get('name', 'Unknown'), # YCloud might include this
            'timestamp': data.get('timestamp'),
            'type': data.get('type'),
            'text': data.get('text', {}).get('body', '') if data.get('type') == 'text' else None,
            'raw': data
        }
    except Exception as e:
        logger.error(f"Error parsing webhook message: {e}")
        return None


def get_phone_number_info(phone_number_id: str, access_token: str) -> Optional[Dict]:
    """
    Get information about a WhatsApp phone number.
    Since we are using YCloud, we don't have a direct equivalent to the Meta API for this 
    that works with the same token, so we return None to allow the config to save.
    """
    return None
