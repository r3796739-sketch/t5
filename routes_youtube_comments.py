"""
YouTube Comment Auto-Reply Routes
Handles OAuth connection, fetching comments, and AI-driven replies.
"""

from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for, flash, current_app
from functools import wraps
import logging
import os
import json
import requests
import threading
import re

from utils.supabase_client import get_supabase_admin_client
from utils.qa_utils import answer_question_stream
from utils.history_utils import save_chat_history

logger = logging.getLogger(__name__)

youtube_comments_bp = Blueprint('youtube_comments', __name__)

# ── OAuth scopes needed ──────────────────────────────────────────────────────
YT_SCOPES = "https://www.googleapis.com/auth/youtube.force-ssl"

# ── Auth guard ───────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            is_ajax = (
                request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                or 'application/json' in request.headers.get('Accept', '')
                or request.content_type == 'application/json'
            )
            if is_ajax or request.method != 'GET':
                return jsonify({'status': 'error', 'message': 'Authentication required.'}), 401
            return redirect(url_for('channel') + '?login=1')
        return f(*args, **kwargs)
    return decorated_function


# ══════════════════════════════════════════════════════════════════════════════
# OAUTH FLOW
# ══════════════════════════════════════════════════════════════════════════════

@youtube_comments_bp.route('/youtube-comments/connect/<int:channel_id>', methods=['GET'])
@login_required
def connect_youtube_comments(channel_id):
    """
    Redirect user to Google OAuth to authorise YouTube comment management.
    """
    client_id = os.environ.get('GOOGLE_OAUTH_CLIENT_ID')
    if not client_id:
        flash("Google OAuth is not configured on this server.", "error")
        return redirect(url_for('chatbot_settings', chatbot_id=channel_id) + '?tab=integrations')

    redirect_uri = url_for('youtube_comments.youtube_comments_callback', _external=True)
    oauth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope={YT_SCOPES}"
        f"&access_type=offline"
        f"&prompt=consent"
        f"&state={channel_id}"
    )
    return redirect(oauth_url)


@youtube_comments_bp.route('/youtube-comments/callback', methods=['GET'])
@login_required
def youtube_comments_callback():
    """
    Handle Google OAuth callback, exchange code for tokens, save to DB.
    """
    code = request.args.get('code')
    state = request.args.get('state')   # channel_id
    error = request.args.get('error')

    if error:
        flash(f"Google OAuth Error: {error}", "error")
        return redirect(url_for('chatbot_settings', chatbot_id=state) + '?tab=integrations')

    if not code or not state:
        flash("Invalid callback from Google.", "error")
        return redirect(url_for('channel'))

    client_id = os.environ.get('GOOGLE_OAUTH_CLIENT_ID')
    client_secret = os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET')
    redirect_uri = url_for('youtube_comments.youtube_comments_callback', _external=True)

    # Exchange code for tokens
    token_res = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    token_data = token_res.json()

    if "error" in token_data:
        flash(f"Failed to get tokens: {token_data.get('error_description', token_data['error'])}", "error")
        return redirect(url_for('chatbot_settings', chatbot_id=state) + '?tab=integrations')

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")

    supabase = get_supabase_admin_client()
    supabase.table('channels').update({
        'yt_comments_access_token': access_token,
        'yt_comments_refresh_token': refresh_token,
        'yt_comments_enabled': True,
    }).eq('id', int(state)).execute()

    flash("YouTube account connected! You can now manage comment replies.", "success")
    return redirect(url_for('youtube_comments.youtube_comments_dashboard', channel_id=int(state)))


@youtube_comments_bp.route('/youtube-comments/disconnect/<int:channel_id>', methods=['POST'])
@login_required
def disconnect_youtube_comments(channel_id):
    user_id = session['user']['id']
    supabase = get_supabase_admin_client()

    check = supabase.table('user_channels').select('*').eq('channel_id', channel_id).eq('user_id', user_id).execute()
    if not check.data:
        return jsonify({'status': 'error', 'message': 'Permission denied'}), 403

    supabase.table('channels').update({
        'yt_comments_access_token': None,
        'yt_comments_refresh_token': None,
        'yt_comments_enabled': False,
        'yt_comments_auto_reply': False,
    }).eq('id', channel_id).execute()

    return jsonify({'status': 'success', 'message': 'YouTube Comments disconnected'})


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

@youtube_comments_bp.route('/youtube-comments/dashboard/<int:channel_id>', methods=['GET'])
@login_required
def youtube_comments_dashboard(channel_id):
    user_id = session['user']['id']
    supabase = get_supabase_admin_client()

    # Ownership check
    check = supabase.table('user_channels').select('*').eq('channel_id', channel_id).eq('user_id', user_id).execute()
    if not check.data:
        flash("Access denied.", "error")
        return redirect(url_for('channel'))

    channel_res = supabase.table('channels').select('*').eq('id', channel_id).limit(1).execute()
    if not channel_res.data:
        flash("Chatbot not found.", "error")
        return redirect(url_for('channel'))

    channel = channel_res.data[0]
    return render_template('youtube_comments_dashboard.html', chatbot=channel)


# ══════════════════════════════════════════════════════════════════════════════
# API: FETCH COMMENTS
# ══════════════════════════════════════════════════════════════════════════════

@youtube_comments_bp.route('/api/youtube-comments/list/<int:channel_id>', methods=['GET'])
@login_required
def list_comments(channel_id):
    """
    Fetch the most recent top-level comments across the channel's latest videos.
    Returns a JSON list for the dashboard UI.
    """
    user_id = session['user']['id']
    supabase = get_supabase_admin_client()

    check = supabase.table('user_channels').select('*').eq('channel_id', channel_id).eq('user_id', user_id).execute()
    if not check.data:
        return jsonify({'status': 'error', 'message': 'Permission denied'}), 403

    channel_res = supabase.table('channels').select('*').eq('id', channel_id).limit(1).execute()
    if not channel_res.data:
        return jsonify({'status': 'error', 'message': 'Channel not found'}), 404

    channel = channel_res.data[0]
    access_token = channel.get('yt_comments_access_token')
    if not access_token:
        return jsonify({'status': 'error', 'message': 'YouTube not connected'}), 400

    # Refresh token if needed
    access_token = _ensure_valid_token(channel, supabase)
    if not access_token:
        return jsonify({'status': 'error', 'message': 'Could not refresh YouTube token'}), 401

    video_id_param = request.args.get('video_id')
    max_results = int(request.args.get('max_results', 20))

    try:
        if video_id_param:
            comments = _fetch_comments_for_video(access_token, video_id_param, max_results)
        else:
            # Get latest videos for this channel
            videos = _fetch_recent_videos(access_token, max_videos=5)
            comments = []
            for video in videos:
                try:
                    vid_comments = _fetch_comments_for_video(access_token, video['id'], max_results=5)
                    for c in vid_comments:
                        c['video_title'] = video.get('title', '')
                        c['video_id'] = video['id']
                    comments.extend(vid_comments)
                except ValueError as ve:
                    # Video probably has comments disabled, skip it
                    logger.warning(f"Skipping video {video['id']}: {ve}")
                    continue
                if len(comments) >= max_results:
                    break

        return jsonify({'status': 'success', 'comments': comments})
    except Exception as e:
        logger.error(f"Error fetching YouTube comments for channel {channel_id}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# API: GENERATE AI REPLY PREVIEW
# ══════════════════════════════════════════════════════════════════════════════

@youtube_comments_bp.route('/api/youtube-comments/generate-reply', methods=['POST'])
@login_required
def generate_reply():
    """
    Generate an AI-powered reply for a specific comment.
    Returns the suggested reply text without posting it yet.
    """
    data = request.get_json()
    channel_id = data.get('channel_id')
    comment_text = data.get('comment_text', '')
    comment_author = data.get('comment_author', 'a fan')
    video_title = data.get('video_title', 'the video')

    if not channel_id or not comment_text:
        return jsonify({'status': 'error', 'message': 'Missing required fields'}), 400

    user_id = session['user']['id']
    supabase = get_supabase_admin_client()

    check = supabase.table('user_channels').select('*').eq('channel_id', channel_id).eq('user_id', user_id).execute()
    if not check.data:
        return jsonify({'status': 'error', 'message': 'Permission denied'}), 403

    channel_res = supabase.table('channels').select('*').eq('id', channel_id).limit(1).execute()
    if not channel_res.data:
        return jsonify({'status': 'error', 'message': 'Channel not found'}), 404

    channel = channel_res.data[0]

    # Build the prompt context
    prompt = (
        f"A viewer named \"{comment_author}\" left the following comment on "
        f"the YouTube video titled \"{video_title}\":\n\n"
        f"\"{comment_text}\"\n\n"
        f"Reply to this comment naturally, as if you are the creator themselves "
        f"responding to a fan/viewer. Keep it conversational, warm, and brief (1-3 sentences max)."
    )

    try:
        response_text = ""
        all_chunks = list(answer_question_stream(
            question_for_prompt=prompt,
            question_for_search=comment_text,
            channel_data=channel,
            user_id=str(user_id),
            integration_source='youtube_comments',
            conversation_id=f"ytcomment_{channel_id}_preview"
        ))
        for chunk in all_chunks:
            if chunk.startswith('data: '):
                data_str = chunk.replace('data: ', '').strip()
                if data_str == "[DONE]":
                    break
                try:
                    parsed = json.loads(data_str)
                    if parsed.get('answer'):
                        response_text += parsed['answer']
                except json.JSONDecodeError:
                    continue

        # Strip marker tokens
        response_text = re.sub(r'\[LEAD_COMPLETE:\s*\{.*?\}\]', '', response_text, flags=re.DOTALL).strip()
        response_text = re.sub(r'\[TRIGGER_FLOW:\s*".*?"\]', '', response_text).strip()

        return jsonify({'status': 'success', 'reply': response_text})
    except Exception as e:
        logger.error(f"Error generating AI reply: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# API: POST REPLY
# ══════════════════════════════════════════════════════════════════════════════

@youtube_comments_bp.route('/api/youtube-comments/post-reply', methods=['POST'])
@login_required
def post_reply():
    """
    Post a reply to a specific YouTube comment thread.
    """
    data = request.get_json()
    channel_id = data.get('channel_id')
    parent_id = data.get('parent_id')   # commentThreadId or parentId
    reply_text = data.get('reply_text', '').strip()

    if not channel_id or not parent_id or not reply_text:
        return jsonify({'status': 'error', 'message': 'Missing required fields'}), 400

    user_id = session['user']['id']
    supabase = get_supabase_admin_client()

    check = supabase.table('user_channels').select('*').eq('channel_id', channel_id).eq('user_id', user_id).execute()
    if not check.data:
        return jsonify({'status': 'error', 'message': 'Permission denied'}), 403

    channel_res = supabase.table('channels').select('*').eq('id', channel_id).limit(1).execute()
    if not channel_res.data:
        return jsonify({'status': 'error', 'message': 'Channel not found'}), 404

    channel = channel_res.data[0]
    access_token = _ensure_valid_token(channel, supabase)
    if not access_token:
        return jsonify({'status': 'error', 'message': 'Could not refresh YouTube token'}), 401

    try:
        _post_comment_reply(access_token, parent_id, reply_text)
        return jsonify({'status': 'success', 'message': 'Reply posted successfully!'})
    except Exception as e:
        logger.error(f"Error posting YouTube reply: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# API: TOGGLE AUTO-REPLY
# ══════════════════════════════════════════════════════════════════════════════

@youtube_comments_bp.route('/api/youtube-comments/toggle-auto-reply', methods=['POST'])
@login_required
def toggle_auto_reply():
    data = request.get_json()
    channel_id = data.get('channel_id')
    enabled = bool(data.get('enabled', False))

    if not channel_id:
        return jsonify({'status': 'error', 'message': 'Missing channel_id'}), 400

    user_id = session['user']['id']
    supabase = get_supabase_admin_client()

    check = supabase.table('user_channels').select('*').eq('channel_id', channel_id).eq('user_id', user_id).execute()
    if not check.data:
        return jsonify({'status': 'error', 'message': 'Permission denied'}), 403

    supabase.table('channels').update({
        'yt_comments_auto_reply': enabled
    }).eq('id', channel_id).execute()

    return jsonify({'status': 'success', 'auto_reply': enabled})


# ══════════════════════════════════════════════════════════════════════════════
# API: FETCH VIDEOS LIST
# ══════════════════════════════════════════════════════════════════════════════

@youtube_comments_bp.route('/api/youtube-comments/videos/<int:channel_id>', methods=['GET'])
@login_required
def list_videos(channel_id):
    """Return a short list of recent videos for the comment filter dropdown."""
    user_id = session['user']['id']
    supabase = get_supabase_admin_client()

    check = supabase.table('user_channels').select('*').eq('channel_id', channel_id).eq('user_id', user_id).execute()
    if not check.data:
        return jsonify({'status': 'error', 'message': 'Permission denied'}), 403

    channel_res = supabase.table('channels').select('*').eq('id', channel_id).limit(1).execute()
    if not channel_res.data:
        return jsonify({'status': 'error', 'message': 'Channel not found'}), 404

    channel = channel_res.data[0]
    access_token = _ensure_valid_token(channel, supabase)
    if not access_token:
        return jsonify({'status': 'error', 'message': 'Could not refresh YouTube token'}), 401

    try:
        videos = _fetch_recent_videos(access_token, max_videos=20)
        return jsonify({'status': 'success', 'videos': videos})
    except Exception as e:
        logger.error(f"Error fetching videos: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# API: AUTO-REPLY RULES CRUD
# ══════════════════════════════════════════════════════════════════════════════

def _owns_channel(channel_id, user_id, supabase):
    check = supabase.table('user_channels').select('channel_id').eq('channel_id', channel_id).eq('user_id', user_id).execute()
    return bool(check.data)


@youtube_comments_bp.route('/api/youtube-comments/rules/<int:channel_id>', methods=['GET'])
@login_required
def list_rules(channel_id):
    """Return all auto-reply rules for a channel."""
    user_id = session['user']['id']
    supabase = get_supabase_admin_client()
    if not _owns_channel(channel_id, user_id, supabase):
        return jsonify({'status': 'error', 'message': 'Permission denied'}), 403

    rules_res = supabase.table('yt_comment_rules').select('*').eq('channel_id', channel_id).order('created_at', desc=True).execute()
    return jsonify({'status': 'success', 'rules': rules_res.data or []})


@youtube_comments_bp.route('/api/youtube-comments/rules', methods=['POST'])
@login_required
def create_rule():
    """
    Create a new auto-reply rule.
    Body: {
        channel_id, video_id (optional), video_title (optional),
        keywords (comma-separated string),
        reply_type ('fixed' | 'ai'),
        reply_text (required when reply_type='fixed'),
        match_mode ('any' | 'all')  — default 'any'
    }
    """
    data = request.get_json()
    channel_id   = data.get('channel_id')
    video_id     = data.get('video_id') or None       # None = apply to all videos
    video_title  = data.get('video_title') or None
    keywords_raw = (data.get('keywords') or '').strip()
    reply_type   = data.get('reply_type', 'fixed')    # 'fixed' or 'ai'
    reply_text   = (data.get('reply_text') or '').strip()
    match_mode   = data.get('match_mode', 'any')      # 'any' or 'all'

    if not channel_id:
        return jsonify({'status': 'error', 'message': 'channel_id is required'}), 400
    if not keywords_raw:
        return jsonify({'status': 'error', 'message': 'At least one keyword is required'}), 400
    if reply_type == 'fixed' and not reply_text:
        return jsonify({'status': 'error', 'message': 'reply_text is required for fixed reply type'}), 400

    user_id = session['user']['id']
    supabase = get_supabase_admin_client()
    if not _owns_channel(channel_id, user_id, supabase):
        return jsonify({'status': 'error', 'message': 'Permission denied'}), 403

    # Normalise keywords: lowercase, strip whitespace
    keywords = [k.strip().lower() for k in keywords_raw.split(',') if k.strip()]

    row = {
        'channel_id':  channel_id,
        'video_id':    video_id,
        'video_title': video_title,
        'keywords':    keywords,          # stored as jsonb array
        'reply_type':  reply_type,
        'reply_text':  reply_text if reply_type == 'fixed' else None,
        'match_mode':  match_mode,
        'is_active':   True,
    }
    res = supabase.table('yt_comment_rules').insert(row).execute()
    if not res.data:
        return jsonify({'status': 'error', 'message': 'Failed to save rule'}), 500

    return jsonify({'status': 'success', 'rule': res.data[0]})


@youtube_comments_bp.route('/api/youtube-comments/rules/<int:rule_id>/toggle', methods=['POST'])
@login_required
def toggle_rule(rule_id):
    """Toggle is_active on a rule."""
    data = request.get_json()
    is_active = bool(data.get('is_active', True))
    user_id = session['user']['id']
    supabase = get_supabase_admin_client()

    # Verify ownership via channel_id on the rule
    rule_res = supabase.table('yt_comment_rules').select('channel_id').eq('id', rule_id).limit(1).execute()
    if not rule_res.data:
        return jsonify({'status': 'error', 'message': 'Rule not found'}), 404
    if not _owns_channel(rule_res.data[0]['channel_id'], user_id, supabase):
        return jsonify({'status': 'error', 'message': 'Permission denied'}), 403

    supabase.table('yt_comment_rules').update({'is_active': is_active}).eq('id', rule_id).execute()
    return jsonify({'status': 'success', 'is_active': is_active})


@youtube_comments_bp.route('/api/youtube-comments/rules/<int:rule_id>', methods=['DELETE'])
@login_required
def delete_rule(rule_id):
    """Delete a rule permanently."""
    user_id = session['user']['id']
    supabase = get_supabase_admin_client()

    rule_res = supabase.table('yt_comment_rules').select('channel_id').eq('id', rule_id).limit(1).execute()
    if not rule_res.data:
        return jsonify({'status': 'error', 'message': 'Rule not found'}), 404
    if not _owns_channel(rule_res.data[0]['channel_id'], user_id, supabase):
        return jsonify({'status': 'error', 'message': 'Permission denied'}), 403

    supabase.table('yt_comment_rules').delete().eq('id', rule_id).execute()
    return jsonify({'status': 'success', 'message': 'Rule deleted'})


# ══════════════════════════════════════════════════════════════════════════════
# INTERNAL: Apply Rules to a Comment
# ══════════════════════════════════════════════════════════════════════════════

def _comment_matches_rule(comment_text: str, rule: dict) -> bool:
    """
    Returns True if the comment text satisfies the rule's keyword conditions.
    match_mode='any' → at least one keyword present
    match_mode='all' → every keyword must be present
    """
    text_lower = comment_text.lower()
    keywords = rule.get('keywords') or []
    if not keywords:
        return False
    if rule.get('match_mode', 'any') == 'all':
        return all(kw in text_lower for kw in keywords)
    return any(kw in text_lower for kw in keywords)


def apply_rules_to_comment(channel: dict, comment: dict, supabase) -> str | None:
    """
    Given a channel and a new comment, check if any active rule matches.
    Returns the reply text to post, or None if no rule fires.
    Only rules whose video_id matches (or is NULL) are checked.
    """
    video_id = comment.get('video_id', '')
    text = comment.get('text', '')

    rules_res = supabase.table('yt_comment_rules').select('*').eq('channel_id', channel['id']).eq('is_active', True).execute()
    rules = rules_res.data or []

    for rule in rules:
        # Video scope check
        rule_video = rule.get('video_id')
        if rule_video and rule_video != video_id:
            continue  # rule targets a different video

        if not _comment_matches_rule(text, rule):
            continue

        if rule.get('reply_type') == 'fixed':
            return rule.get('reply_text') or None

        # AI reply (generated inline — used by background processor)
        # For now return a sentinel; the caller handles AI generation
        return '__AI__'

    return None


# ══════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _ensure_valid_token(channel: dict, supabase) -> str | None:
    """
    Returns a valid access token, refreshing it if necessary.
    Updates the DB with the new token on refresh.
    """
    access_token = channel.get('yt_comments_access_token')
    refresh_token = channel.get('yt_comments_refresh_token')

    if not refresh_token:
        return access_token  # use whatever we have (may be None)

    # Quick check — try a lightweight API call; if 401, refresh
    test = requests.get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={"part": "id", "mine": "true", "maxResults": 1},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if test.status_code != 401:
        return access_token

    logger.info("YouTube access token expired — refreshing...")
    client_id = os.environ.get('GOOGLE_OAUTH_CLIENT_ID')
    client_secret = os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET')

    refresh_res = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    refresh_data = refresh_res.json()
    new_token = refresh_data.get("access_token")
    if not new_token:
        logger.error(f"Token refresh failed: {refresh_data}")
        if refresh_data.get("error") == "invalid_grant":
            # Token revoked or expired. Clear it so the user sees they need to reconnect
            supabase.table('channels').update({
                'yt_comments_access_token': None,
                'yt_comments_refresh_token': None,
                'yt_comments_enabled': False,
                'yt_comments_auto_reply': False,
            }).eq('id', channel['id']).execute()
        return None

    supabase.table('channels').update({
        'yt_comments_access_token': new_token
    }).eq('id', channel['id']).execute()

    return new_token


def _fetch_recent_videos(access_token: str, max_videos: int = 10) -> list:
    """
    Returns a list of recent uploaded videos for the authenticated channel.
    Each item: {'id': video_id, 'title': ..., 'thumbnail': ...}
    """
    # 1. Get the channel's uploads playlist
    ch_res = requests.get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={"part": "contentDetails,snippet", "mine": "true"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    ch_data = ch_res.json()
    items = ch_data.get('items', [])
    if not items:
        return []

    uploads_playlist = items[0]['contentDetails']['relatedPlaylists']['uploads']

    # 2. Fetch from uploads playlist
    pl_res = requests.get(
        "https://www.googleapis.com/youtube/v3/playlistItems",
        params={
            "part": "contentDetails",
            "playlistId": uploads_playlist,
            "maxResults": max_videos,
        },
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    pl_data = pl_res.json()
    video_ids = [item['contentDetails']['videoId'] for item in pl_data.get('items', [])]
    if not video_ids:
        return []

    # 3. Get video details
    vd_res = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={
            "part": "snippet",
            "id": ",".join(video_ids),
        },
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    vd_data = vd_res.json()

    videos = []
    for v in vd_data.get('items', []):
        snippet = v.get('snippet', {})
        videos.append({
            'id': v['id'],
            'title': snippet.get('title', 'Untitled'),
            'thumbnail': snippet.get('thumbnails', {}).get('default', {}).get('url', ''),
            'published_at': snippet.get('publishedAt', ''),
        })
    return videos


def _fetch_comments_for_video(access_token: str, video_id: str, max_results: int = 20) -> list:
    """
    Retrieves top-level comment threads for a given video.
    Returns a list of comment dicts.
    """
    res = requests.get(
        "https://www.googleapis.com/youtube/v3/commentThreads",
        params={
            "part": "snippet",
            "videoId": video_id,
            "maxResults": max_results,
            "order": "relevance",
            "textFormat": "plainText",
        },
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    data = res.json()

    if 'error' in data:
        err_msg = data['error'].get('message', 'Unknown YouTube API error')
        raise ValueError(f"YouTube API error: {err_msg}")

    comments = []
    for item in data.get('items', []):
        top = item['snippet']['topLevelComment']['snippet']
        comments.append({
            'thread_id': item['id'],
            'comment_id': item['snippet']['topLevelComment']['id'],
            'author': top.get('authorDisplayName', 'Unknown'),
            'author_image': top.get('authorProfileImageUrl', ''),
            'text': top.get('textDisplay', ''),
            'likes': top.get('likeCount', 0),
            'published_at': top.get('publishedAt', ''),
            'reply_count': item['snippet'].get('totalReplyCount', 0),
            'video_id': video_id,
            'video_title': '',  # filled in by caller
        })
    return comments


def _post_comment_reply(access_token: str, parent_id: str, text: str):
    """
    Posts a reply to a comment thread using the YouTube Data API.
    parent_id is the comment thread/comment ID to reply to.
    """
    res = requests.post(
        "https://www.googleapis.com/youtube/v3/comments",
        params={"part": "snippet"},
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={
            "snippet": {
                "parentId": parent_id,
                "textOriginal": text,
            }
        },
        timeout=15,
    )
    if res.status_code not in (200, 201):
        raise ValueError(f"YouTube post failed ({res.status_code}): {res.text[:300]}")
    return res.json()
