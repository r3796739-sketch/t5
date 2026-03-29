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


def send_whatsapp_buttons(
    phone_number_id: str,
    to_phone: str,
    body_text: str,
    buttons: list,
    api_key: str,
    header_text: str = None,
    footer_text: str = None,
) -> Dict[str, Any]:
    """
    Send an interactive button message via YCloud WhatsApp API.
    WhatsApp allows max 3 buttons, each title max 20 chars.

    Args:
        phone_number_id: The sender's WhatsApp phone number
        to_phone: The recipient's phone number
        body_text: The main message body (shown above buttons)
        buttons: List of dicts with 'id' and 'title' keys
                 e.g. [{"id": "bali", "title": "🏖️ Bali Package"}]
        api_key: The user's YCloud API key
        header_text: Optional header text above body
        footer_text: Optional footer text below buttons
    """
    # Enforce WhatsApp limits
    buttons = buttons[:3]
    for btn in buttons:
        btn['title'] = btn['title'][:20]  # Hard 20-char limit

    interactive: Dict[str, Any] = {
        "type": "button",
        "body": {"text": body_text},
        "action": {
            "buttons": [
                {"type": "reply", "reply": {"id": btn.get("id", btn["title"]), "title": btn["title"]}}
                for btn in buttons
            ]
        }
    }
    if header_text:
        interactive["header"] = {"type": "text", "text": header_text[:60]}
    if footer_text:
        interactive["footer"] = {"text": footer_text[:60]}

    payload = {
        "from": phone_number_id,
        "to": to_phone,
        "type": "interactive",
        "interactive": interactive
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
        logger.error(f"Failed to send WhatsApp buttons: {e}")
        return {"success": False, "error": str(e)}


def send_whatsapp_list(
    phone_number_id: str,
    to_phone: str,
    body_text: str,
    rows: list,
    api_key: str,
    button_label: str = "See Options",
    section_title: str = "Options",
    header_text: str = None,
    footer_text: str = None,
) -> Dict[str, Any]:
    """
    Send an interactive list message via YCloud WhatsApp API.
    Best for 4-10 options (more than 3 buttons).

    Args:
        phone_number_id: The sender's WhatsApp phone number
        to_phone: The recipient's phone number
        body_text: The main message body
        rows: List of dicts with 'id', 'title', and optional 'description'
              e.g. [{"id": "bali", "title": "Bali Package", "description": "7 nights from $899"}]
        api_key: The user's YCloud API key
        button_label: Label on the button that opens the list (max 20 chars)
        section_title: Title of the section in the list
        header_text: Optional header text
        footer_text: Optional footer text
    """
    rows = rows[:10]  # WhatsApp max 10 rows
    for row in rows:
        row['title'] = row['title'][:24]  # Max 24 chars for list titles
        if row.get('description'):
            row['description'] = row['description'][:72]

    interactive: Dict[str, Any] = {
        "type": "list",
        "body": {"text": body_text},
        "action": {
            "button": button_label[:20],
            "sections": [{
                "title": section_title[:24],
                "rows": [
                    {"id": r.get("id", r["title"]), "title": r["title"],
                     **({"description": r["description"]} if r.get("description") else {})}
                    for r in rows
                ]
            }]
        }
    }
    if header_text:
        interactive["header"] = {"type": "text", "text": header_text[:60]}
    if footer_text:
        interactive["footer"] = {"text": footer_text[:60]}

    payload = {
        "from": phone_number_id,
        "to": to_phone,
        "type": "interactive",
        "interactive": interactive
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
        logger.error(f"Failed to send WhatsApp list message: {e}")
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


def send_whatsapp_image(
    phone_number_id: str,
    to_phone: str,
    image_url: str,
    api_key: str,
    caption: str = None,
) -> Dict[str, Any]:
    """
    Send an image message via YCloud WhatsApp API.

    Args:
        image_url: Public HTTPS URL of the image (jpg/png/gif/webp, max 5MB)
        caption:   Optional caption shown below the image (max 1024 chars)
    """
    image_obj: Dict[str, Any] = {"link": image_url}
    if caption:
        image_obj["caption"] = caption[:1024]

    payload = {
        "from": phone_number_id,
        "to": to_phone,
        "type": "image",
        "image": image_obj,
    }
    try:
        r = requests.post(YCLOUD_SEND_URL, headers=_ycloud_headers(api_key), json=payload, timeout=15)
        r.raise_for_status()
        return {"success": True, "data": r.json()}
    except Exception as e:
        logger.error(f"Failed to send WhatsApp image: {e}")
        return {"success": False, "error": str(e)}


def send_whatsapp_document(
    phone_number_id: str,
    to_phone: str,
    document_url: str,
    api_key: str,
    filename: str = None,
    caption: str = None,
) -> Dict[str, Any]:
    """
    Send a document (PDF, DOCX, XLSX …) via YCloud WhatsApp API.

    Args:
        document_url: Public HTTPS URL of the document (max 100MB)
        filename:     File name shown to the recipient (e.g. "brochure.pdf")
        caption:      Optional caption (max 1024 chars)
    """
    doc_obj: Dict[str, Any] = {"link": document_url}
    if filename:
        doc_obj["filename"] = filename
    if caption:
        doc_obj["caption"] = caption[:1024]

    payload = {
        "from": phone_number_id,
        "to": to_phone,
        "type": "document",
        "document": doc_obj,
    }
    try:
        r = requests.post(YCLOUD_SEND_URL, headers=_ycloud_headers(api_key), json=payload, timeout=15)
        r.raise_for_status()
        return {"success": True, "data": r.json()}
    except Exception as e:
        logger.error(f"Failed to send WhatsApp document: {e}")
        return {"success": False, "error": str(e)}


def send_whatsapp_video(
    phone_number_id: str,
    to_phone: str,
    video_url: str,
    api_key: str,
    caption: str = None,
) -> Dict[str, Any]:
    """
    Send a video message via YCloud WhatsApp API.

    Args:
        video_url: Public HTTPS URL of the video (mp4/3gpp, max 16MB)
        caption:   Optional caption (max 1024 chars)
    """
    video_obj: Dict[str, Any] = {"link": video_url}
    if caption:
        video_obj["caption"] = caption[:1024]

    payload = {
        "from": phone_number_id,
        "to": to_phone,
        "type": "video",
        "video": video_obj,
    }
    try:
        r = requests.post(YCLOUD_SEND_URL, headers=_ycloud_headers(api_key), json=payload, timeout=15)
        r.raise_for_status()
        return {"success": True, "data": r.json()}
    except Exception as e:
        logger.error(f"Failed to send WhatsApp video: {e}")
        return {"success": False, "error": str(e)}


def send_whatsapp_audio(
    phone_number_id: str,
    to_phone: str,
    audio_url: str,
    api_key: str,
) -> Dict[str, Any]:
    """
    Send an audio message via YCloud WhatsApp API.

    Args:
        audio_url: Public HTTPS URL of the audio file (aac/mp4/mpeg/amr/ogg, max 16MB)
    """
    payload = {
        "from": phone_number_id,
        "to": to_phone,
        "type": "audio",
        "audio": {"link": audio_url},
    }
    try:
        r = requests.post(YCLOUD_SEND_URL, headers=_ycloud_headers(api_key), json=payload, timeout=15)
        r.raise_for_status()
        return {"success": True, "data": r.json()}
    except Exception as e:
        logger.error(f"Failed to send WhatsApp audio: {e}")
        return {"success": False, "error": str(e)}


def send_whatsapp_location(
    phone_number_id: str,
    to_phone: str,
    latitude: float,
    longitude: float,
    api_key: str,
    name: str = None,
    address: str = None,
) -> Dict[str, Any]:
    """
    Send a location pin via YCloud WhatsApp API.

    Args:
        latitude:  Decimal latitude  (e.g. 23.0225)
        longitude: Decimal longitude (e.g. 72.5714)
        name:      Optional place name shown on the pin
        address:   Optional street address shown below the pin
    """
    loc_obj: Dict[str, Any] = {
        "latitude": latitude,
        "longitude": longitude,
    }
    if name:
        loc_obj["name"] = name
    if address:
        loc_obj["address"] = address

    payload = {
        "from": phone_number_id,
        "to": to_phone,
        "type": "location",
        "location": loc_obj,
    }
    try:
        r = requests.post(YCLOUD_SEND_URL, headers=_ycloud_headers(api_key), json=payload, timeout=15)
        r.raise_for_status()
        return {"success": True, "data": r.json()}
    except Exception as e:
        logger.error(f"Failed to send WhatsApp location: {e}")
        return {"success": False, "error": str(e)}


def send_whatsapp_cta_url(
    phone_number_id: str,
    to_phone: str,
    body_text: str,
    cta_buttons: list,
    api_key: str,
    header_text: str = None,
    footer_text: str = None,
) -> Dict[str, Any]:
    """
    Send an interactive message with CTA (Call-To-Action) URL buttons.
    Each button can be a URL link or phone call (up to 2 total).

    Args:
        cta_buttons: List of dicts:
            URL   button → {"type": "url",   "text": "Book Now",    "url":   "https://..."}
            Phone button → {"type": "phone", "text": "Call Us",     "phone": "+14155552671"}
    """
    wa_buttons = []
    for btn in cta_buttons[:2]:
        btype = btn.get("type", "url")
        if btype == "url":
            wa_buttons.append({
                "type": "url",
                "text": btn.get("text", "Click Here")[:20],
                "url":  btn.get("url", ""),
            })
        elif btype == "phone":
            wa_buttons.append({
                "type": "phone_number",
                "text":         btn.get("text", "Call Us")[:20],
                "phone_number": btn.get("phone", ""),
            })

    interactive: Dict[str, Any] = {
        "type": "cta_url",
        "body": {"text": body_text},
        "action": {"buttons": wa_buttons},
    }
    if header_text:
        interactive["header"] = {"type": "text", "text": header_text[:60]}
    if footer_text:
        interactive["footer"] = {"text": footer_text[:60]}

    payload = {
        "from": phone_number_id,
        "to":   to_phone,
        "type": "interactive",
        "interactive": interactive,
    }
    try:
        r = requests.post(YCLOUD_SEND_URL, headers=_ycloud_headers(api_key), json=payload, timeout=15)
        r.raise_for_status()
        return {"success": True, "data": r.json()}
    except Exception as e:
        logger.error(f"Failed to send WhatsApp CTA URL: {e}")
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
            parsed['text'] = msg.get('image', {}).get('caption', '')  # Fallback caption to text property
            parsed['mime_type'] = msg.get('image', {}).get('mimeType', 'image/jpeg')
        elif msg_type == 'button':
            # YCloud sends quick-reply button taps as type='button'
            # The text the user selected is in msg['button']['text'] or msg['button']['payload']
            btn_data = msg.get('button', {})
            parsed['text'] = btn_data.get('text', '') or btn_data.get('payload', '')
            parsed['button_payload'] = btn_data.get('payload', '')
            parsed['is_button_reply'] = True
            logger.info(f"[WhatsApp] Button reply (type=button): text='{parsed['text']}'")
        elif msg_type == 'interactive':
            # User selected from a list message (type=list), or newer inline button format
            interactive_data = msg.get('interactive', {})
            interactive_type = interactive_data.get('type', '')
            if interactive_type == 'button_reply':
                # YCloud uses snake_case: 'button_reply' (not camelCase 'buttonReply')
                btn = interactive_data.get('button_reply') or interactive_data.get('buttonReply') or {}
                parsed['text'] = btn.get('title', '')
                parsed['button_id'] = btn.get('id', '')
                parsed['is_button_reply'] = True
            elif interactive_type == 'list_reply':
                # YCloud uses snake_case: 'list_reply'
                item = interactive_data.get('list_reply') or interactive_data.get('listReply') or {}
                parsed['text'] = item.get('title', '')
                parsed['list_id'] = item.get('id', '')
                parsed['is_button_reply'] = True
            logger.info(f"[WhatsApp] Interactive reply: type={interactive_type}, text='{parsed.get('text', '')}'")

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
