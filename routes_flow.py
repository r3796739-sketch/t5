"""
Flow Builder Blueprint
Routes for the visual conversation flow editor and flow CRUD API.
"""

from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for
from functools import wraps
from utils.supabase_client import get_supabase_admin_client
import logging

logger = logging.getLogger(__name__)
flow_bp = Blueprint('flow', __name__, url_prefix='/flow')


# ── Auth helpers ──────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('channel') + '?login=1')
        return f(*args, **kwargs)
    return decorated


def _verify_chatbot_ownership(supabase, chatbot_id: int, user_id: str):
    """Return chatbot dict if user owns it, else None. Mirrors chatbot_settings auth logic."""
    res = supabase.table('channels').select(
        'id, channel_name, creator_id, user_id'
    ).eq('id', chatbot_id).maybe_single().execute()
    if not res or not res.data:
        return None
    chatbot = res.data
    owner_id = chatbot.get('creator_id') or chatbot.get('user_id')
    if str(owner_id) != str(user_id):
        return None
    return chatbot


# ── Editor page ───────────────────────────────────────────────────────────────

@flow_bp.route('/builder/<int:chatbot_id>')
@login_required
def flow_builder(chatbot_id):
    """Render the visual flow builder for a chatbot."""
    supabase = get_supabase_admin_client()
    user_id = session['user']['id']

    chatbot = _verify_chatbot_ownership(supabase, chatbot_id, user_id)
    if not chatbot:
        return redirect(url_for('dashboard'))

    # Load existing flow if any
    flow_res = (
        supabase.table('channel_flows')
        .select('*')
        .eq('channel_id', chatbot_id)
        .order('created_at', desc=True)
        .limit(1)
        .execute()
    )
    flow = flow_res.data[0] if flow_res.data else None

    return render_template('flow_builder.html', chatbot=chatbot, flow=flow)


# ── Flow CRUD API ─────────────────────────────────────────────────────────────

@flow_bp.route('/api/<int:chatbot_id>', methods=['GET'])
@login_required
def get_flow(chatbot_id):
    supabase = get_supabase_admin_client()
    user_id = session['user']['id']

    if not _verify_chatbot_ownership(supabase, chatbot_id, user_id):
        return jsonify({'status': 'error', 'message': 'Not found'}), 404

    res = (
        supabase.table('channel_flows')
        .select('*')
        .eq('channel_id', chatbot_id)
        .order('created_at', desc=True)
        .limit(1)
        .execute()
    )
    if res.data:
        return jsonify({'status': 'ok', 'flow': res.data[0]})
    return jsonify({'status': 'ok', 'flow': None})


@flow_bp.route('/api/<int:chatbot_id>', methods=['POST'])
@login_required
def save_flow(chatbot_id):
    supabase = get_supabase_admin_client()
    user_id = session['user']['id']

    if not _verify_chatbot_ownership(supabase, chatbot_id, user_id):
        return jsonify({'status': 'error', 'message': 'Not found'}), 404

    body = request.get_json() or {}
    flow_data = body.get('flow_data', {})
    name = str(body.get('name', 'Main Flow'))[:100]
    flow_id = body.get('flow_id')

    try:
        if flow_id:
            # Update existing flow
            supabase.table('channel_flows').update({
                'flow_data': flow_data,
                'name': name,
            }).eq('id', flow_id).eq('channel_id', chatbot_id).execute()
        else:
            # Insert new flow
            res = supabase.table('channel_flows').insert({
                'channel_id': chatbot_id,
                'name': name,
                'flow_data': flow_data,
                'is_active': False,
            }).execute()
            if res.data:
                flow_id = res.data[0]['id']
                
        return jsonify({'status': 'ok', 'flow_id': flow_id})
    except Exception as e:
        logger.error(f"Failed to save flow for channel {chatbot_id}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': 'Failed to save flow. The data might be too large or there is a connection issue.'}), 500


@flow_bp.route('/api/<int:chatbot_id>/activate', methods=['POST'])
@login_required
def activate_flow(chatbot_id):
    supabase = get_supabase_admin_client()
    user_id = session['user']['id']

    if not _verify_chatbot_ownership(supabase, chatbot_id, user_id):
        return jsonify({'status': 'error', 'message': 'Not found'}), 404

    body = request.get_json() or {}
    flow_id = body.get('flow_id')
    activate = body.get('active', True)

    # Deactivate all flows for this channel first
    supabase.table('channel_flows').update({'is_active': False}).eq('channel_id', chatbot_id).execute()

    if activate and flow_id:
        supabase.table('channel_flows').update({'is_active': True}).eq('id', flow_id).execute()

    return jsonify({'status': 'ok', 'active': activate})
