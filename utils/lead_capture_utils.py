# utils/lead_capture_utils.py
"""
Utilities for the Lead Capture chatbot feature.

How it works:
  1. When lead_capture_enabled=True on a chatbot, the system prompt is extended
     with instructions built by `build_lead_prompt()` which lists all the custom
     fields defined by the chatbot owner.
  2. The LLM is instructed to collect fields one-by-one and, once done, to emit
     a special JSON marker:  [LEAD_COMPLETE: {"Field Label": "value", ...}]
  3. The frontend JavaScript detects that marker in the stream and calls
     POST /api/submit-lead with the chatbot_id and the collected responses.
  4. The server looks up the lead_capture_email, renders an HTML email and sends
     it via Flask-Mail (Mailgun SMTP already configured in .env).
"""

import json
import logging
from datetime import datetime
from typing import Optional
from flask_mail import Message

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_lead_prompt(fields: list) -> str:
    """
    Given a list of field defs like:
        [{"label": "Name", "type": "text", "required": true, "options": []}]
    Returns a system-prompt snippet instructing the bot to collect them one
    at a time and emit a [LEAD_COMPLETE: {...}] marker when done.
    """
    if not fields:
        return ""

    lines = []
    for i, f in enumerate(fields, 1):
        label = f.get("label", f"Field {i}")
        ftype = f.get("type", "text")
        options = f.get("options", [])
        required = f.get("required", True)

        desc = f"  {i}. **{label}**"
        if ftype == "select" and options:
            desc += f" — ask the user to choose one of: {', '.join(options)}"
        elif ftype == "date":
            desc += " — ask for a date (e.g. 10 March 2026 or 10/03/2026)"
        elif ftype == "number":
            desc += " — ask for a number"
        if not required:
            desc += " (optional — accept 'skip' or 'not sure')"
        lines.append(desc)

    fields_block = "\n".join(lines)

    prompt = f"""
---
## LEAD CAPTURE MODE (HIGHEST PRIORITY)

**⚠️ CRITICAL OVERRIDE — READ FIRST:**
If you can see a message in the conversation history that contains `[LEAD_COMPLETE:` anywhere in it, ALL information has ALREADY been collected. In that case:
- DO NOT ask any lead capture questions again.
- DO NOT re-ask about name, location, destination, dates, or any other field.
- Simply resume your primary persona (Business Support, General Assistant, or Creator) and answer the user's current question naturally.


---

Only if [LEAD_COMPLETE] has NOT yet appeared in the history, follow these collection rules:

You are operating in **Lead Capture Mode**. Your primary job is to collect the
following information from the visitor.
Be warm and conversational.

**Fields to collect:**
{fields_block}

**Collection Rules (STRICT):**

2. **UPFRONT/BULK DATA:** If the user provides ALL of the required fields in one message, DO NOT ask any questions. Simply thank them, and IMMEDIATELY emit the `[LEAD_COMPLETE]` marker.
3. **SEQUENTIAL COLLECTION:** If information is missing, ask for field #1 first. After the user answers, thank them briefly, then ask field #2, and so on. Do not ask two questions at once.
4. If the user goes off-topic or asks a question, politely say you will help them with that right after grabbing a few details, then ask the current lead capture question.
5. For select-type fields, only accept one of the listed options.
6. Once ALL fields have been answered (either sequentially or all at once), say something like:
  "Thanks so much! I've got all the details I need. We'll be in touch soon! 😊"
  Then on the very last line of your response (and ONLY there), emit this EXACT marker:
  [LEAD_COMPLETE: {{"<field_label>": "<value>", ...}}]
  Replace <field_label> and <value> with the actual labels and collected answers in strict JSON.
7. Never reveal the [LEAD_COMPLETE] marker to the user visually.
---
"""
    return prompt


# ---------------------------------------------------------------------------
# Email sender
# ---------------------------------------------------------------------------

def send_lead_email(mail, chatbot_name: str, recipient_email: str, responses: dict, submitted_at: Optional[str] = None) -> bool:
    """
    Sends a lead notification email using Flask-Mail.

    :param mail: The Flask-Mail `mail` instance (from extensions.py)
    :param chatbot_name: The chatbot's display name
    :param recipient_email: Where to send the lead
    :param responses: dict of {field_label: value}
    :param submitted_at: ISO timestamp string (optional)
    :returns: True on success, False on failure
    """
    try:
        ts = submitted_at or datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        # Build the HTML email
        html_body = _render_lead_email_html(chatbot_name, responses, ts)

        # Plain-text fallback
        text_lines = [f"New lead from {chatbot_name} — {ts}", ""]
        for label, value in responses.items():
            text_lines.append(f"  {label}: {value}")
        text_body = "\n".join(text_lines)

        msg = Message(
            subject=f"🎯 New Lead from {chatbot_name}",
            recipients=[recipient_email],
            html=html_body,
            body=text_body
        )
        mail.send(msg)
        logger.info(f"Lead email sent to {recipient_email} for chatbot '{chatbot_name}'")
        return True

    except Exception as e:
        logger.error(f"Failed to send lead email to {recipient_email}: {e}", exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Internal HTML renderer
# ---------------------------------------------------------------------------

def _render_lead_email_html(chatbot_name: str, responses: dict, submitted_at: str) -> str:
    rows_html = ""
    for label, value in responses.items():
        rows_html += f"""
        <tr>
          <td style="padding:10px 16px;border-bottom:1px solid #f0f0f0;
                     font-weight:600;color:#555;width:40%;vertical-align:top;">
            {_escape_html(str(label))}
          </td>
          <td style="padding:10px 16px;border-bottom:1px solid #f0f0f0;
                     color:#222;vertical-align:top;">
            {_escape_html(str(value))}
          </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f4f6f8;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f8;padding:40px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0"
             style="background:#fff;border-radius:12px;overflow:hidden;
                    box-shadow:0 4px 24px rgba(0,0,0,0.08);">

        <!-- Header -->
        <tr>
          <td style="background:linear-gradient(135deg,#667eea,#764ba2);
                     padding:32px 32px 24px;text-align:center;">
            <h1 style="margin:0;color:#fff;font-size:22px;font-weight:700;letter-spacing:-0.3px;">
              🎯 New Lead Received
            </h1>
            <p style="margin:8px 0 0;color:rgba(255,255,255,0.85);font-size:14px;">
              From <strong>{_escape_html(chatbot_name)}</strong> · {_escape_html(submitted_at)}
            </p>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:28px 32px 8px;">
            <p style="margin:0 0 20px;color:#444;font-size:15px;">
              A visitor just completed your lead capture form. Here are the details:
            </p>
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="border-radius:8px;overflow:hidden;border:1px solid #ebebeb;">
              {rows_html}
            </table>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="padding:24px 32px 32px;text-align:center;">
            <p style="margin:0;color:#aaa;font-size:12px;">
              This lead was captured automatically by your chatbot.<br>
              Powered by <strong style="color:#667eea;">YoppyChat</strong>
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _escape_html(text: str) -> str:
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))

# ---------------------------------------------------------------------------
# WhatsApp sender
# ---------------------------------------------------------------------------

def send_lead_whatsapp(chatbot_id: int, owner_id: str, chatbot_name: str, whatsapp_number: str, responses: dict, submitted_at: Optional[str] = None) -> bool:
    """
    Sends a lead notification via WhatsApp using YCloud API.
    Uses the creator's configured YCloud API key from the whatsapp integration.
    """
    import requests
    from utils.supabase_client import get_supabase_admin_client
    from utils.crypto import decrypt_token
    
    supabase = get_supabase_admin_client()
    
    # 1. Fetch user's WhatsApp config
    config_res = supabase.table('whatsapp_configs').select('*').eq('channel_id', chatbot_id).eq('user_id', owner_id).eq('is_active', True).limit(1).execute()
    
    if not config_res.data:
        logger.error(f"Skipping WhatsApp lead capture: No active whatsapp config found for chatbot {chatbot_id}")
        return False
        
    config = config_res.data[0]
    try:
        api_key = decrypt_token(config.get('access_token'))
        phone_number_id = config.get('phone_number_id')
    except Exception as e:
        logger.error(f"Error decrypting WhatsApp configs for channel {chatbot_id}: {e}")
        return False
        
    if not api_key or not phone_number_id:
        logger.error(f"WhatsApp config missing credentials for {chatbot_id}")
        return False

    ts = submitted_at or datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    
    # Format message text
    lines = [f"🎯 *New Lead Received!*"]
    lines.append(f"🤖 Bot: {chatbot_name}")
    lines.append(f"⏰ Time: {ts}")
    lines.append("")
    lines.append("📋 *Lead Details:*")
    
    for label, value in responses.items():
        lines.append(f"• *{label}:* {value}")
        
    lines.append("")
    lines.append("Powered by YoppyChat 🚀")
    
    message_text = "\n".join(lines)
    
    try:
        url = "https://api.ycloud.com/v2/whatsapp/messages/sendDirectly"
        payload = {
            "to": whatsapp_number,
            "type": "text",
            "from": phone_number_id,
            "text": {
                "body": message_text
            }
        }
            
        headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json"
        }
        
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        
        if response.status_code == 200 or response.status_code == 201:
            logger.info(f"WhatsApp lead notification sent successfully to {whatsapp_number}")
            return True
        else:
            logger.error(f"Failed to send WhatsApp lead to {whatsapp_number}. Status: {response.status_code}, Msg: {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Error sending WhatsApp lead notification: {e}", exc_info=True)
        return False
