"""
Flow Builder Blueprint
Routes for the visual conversation flow editor and flow CRUD API.

Flow data (nodes, edges, ai_instructions) is stored as local JSON files
in data/flows/<chatbot_id>.json to bypass Supabase column size limits.
Supabase only stores lightweight metadata: id, channel_id, name, is_active.
"""

from flask import Blueprint, request, jsonify, render_template, session, redirect, url_for
from functools import wraps
from utils.supabase_client import get_supabase_admin_client
from utils.local_flow_store import save_flow_local, load_flow_local, delete_flow_local
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

    # Load metadata from Supabase (no flow_data — that's local)
    meta_res = (
        supabase.table('channel_flows')
        .select('id, name, is_active')
        .eq('channel_id', chatbot_id)
        .order('created_at', desc=True)
        .limit(1)
        .execute()
    )
    meta = meta_res.data[0] if meta_res.data else None

    # Load actual flow_data from local filesystem
    flow = None
    if meta:
        local_data = load_flow_local(chatbot_id)
        flow = {
            'id': meta['id'],
            'is_active': meta.get('is_active', False),
            'flow_data': local_data or {'nodes': [], 'edges': [], 'ai_instructions': ''}
        }

    return render_template('flow_builder.html', chatbot=chatbot, flow=flow)


# ── Flow CRUD API ─────────────────────────────────────────────────────────────

@flow_bp.route('/api/<int:chatbot_id>', methods=['GET'])
@login_required
def get_flow(chatbot_id):
    supabase = get_supabase_admin_client()
    user_id = session['user']['id']

    if not _verify_chatbot_ownership(supabase, chatbot_id, user_id):
        return jsonify({'status': 'error', 'message': 'Not found'}), 404

    meta_res = (
        supabase.table('channel_flows')
        .select('id, name, is_active')
        .eq('channel_id', chatbot_id)
        .order('created_at', desc=True)
        .limit(1)
        .execute()
    )
    if meta_res.data:
        meta = meta_res.data[0]
        local_data = load_flow_local(chatbot_id)
        flow = {
            **meta,
            'flow_data': local_data or {'nodes': [], 'edges': [], 'ai_instructions': ''}
        }
        return jsonify({'status': 'ok', 'flow': flow})
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

    # 1. Save flow_data to local filesystem (unlimited size)
    ok = save_flow_local(chatbot_id, flow_data)
    if not ok:
        return jsonify({'status': 'error', 'message': 'Failed to save flow to local storage. Check server disk permissions.'}), 500

    # 2. Save/update lightweight metadata in Supabase (no flow_data column)
    try:
        if flow_id:
            supabase.table('channel_flows').update({
                'name': name,
            }).eq('id', flow_id).eq('channel_id', chatbot_id).execute()
        else:
            res = supabase.table('channel_flows').insert({
                'channel_id': chatbot_id,
                'name': name,
                'flow_data': {},   # empty placeholder — real data is in local file
                'is_active': False,
            }).execute()
            if res.data:
                flow_id = res.data[0]['id']

        return jsonify({'status': 'ok', 'flow_id': flow_id})
    except Exception as e:
        logger.error(f"Failed to save flow metadata for channel {chatbot_id}: {e}", exc_info=True)
        # Local file is already saved — return ok with a warning
        return jsonify({'status': 'ok', 'flow_id': flow_id, 'warning': 'Saved locally but metadata sync failed.'})


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


@flow_bp.route('/api/<int:chatbot_id>/export', methods=['GET'])
@login_required
def export_flow(chatbot_id):
    """Download the flow as a JSON file."""
    supabase = get_supabase_admin_client()
    user_id = session['user']['id']

    if not _verify_chatbot_ownership(supabase, chatbot_id, user_id):
        return jsonify({'status': 'error', 'message': 'Not found'}), 404

    local_data = load_flow_local(chatbot_id)
    if not local_data:
        return jsonify({'status': 'error', 'message': 'No flow found'}), 404

    import json
    from flask import Response
    response = Response(
        json.dumps(local_data, indent=2),
        mimetype='application/json',
        headers={'Content-Disposition': f'attachment; filename=flow_{chatbot_id}.json'}
    )
    return response
