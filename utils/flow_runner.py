"""
Flow Runner — executes a stored visual flow when a WhatsApp message arrives.

Supports node types:
  start, message, ai, lead, end,
  image_node, video_node, audio_node, document_node, location_node,
  list_node, condition_node, action

Variable substitution: {{var_name}} placeholders are filled from
conversation['flow_variables'] (a JSON dict stored per conversation).

Flow JSON shape:
  {
    "nodes": [
      {"id": "n1", "type": "start",   "data": {"message": "Hi {{name}}!", "buttons": [...]}},
      {"id": "n2", "type": "message", "data": {"message": "...", "imageUrl": "...", "buttons": [...]}},
      {"id": "n3", "type": "image_node", "data": {"mediaUrl": "...", "caption": "..."}},
      {"id": "n4", "type": "list_node",  "data": {"message": "Choose:", "buttonLabel": "See Options",
                                                    "sectionTitle": "Options", "rows": [...]}},
      {"id": "n5", "type": "condition_node", "data": {"conditions": [
          {"id": "c1", "operator": "contains", "value": "price"},
          {"id": "c2", "operator": "equals",   "value": "yes"},
          {"id": "c3", "operator": "else"}
      ]}},
      {"id": "n6", "type": "location_node", "data": {"latitude": 23.02, "longitude": 72.57,
                                                       "locationName": "Office", "address": "..."}},
    ],
    "edges": [
      {"source_node": "n1", "button_id": "b1", "target_node": "n2"},
      {"source_node": "n5", "button_id": "c1", "target_node": "n7"}
    ]
  }
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_active_flow(supabase, channel_id: int) -> Optional[dict]:
    """Return the active flow for a channel, or None."""
    try:
        res = (supabase.table('channel_flows')
               .select('id, flow_data')
               .eq('channel_id', channel_id)
               .eq('is_active', True)
               .limit(1)
               .execute())
        if res.data:
            return {'flow_id': res.data[0]['id'], **res.data[0]['flow_data']}
        return None
    except Exception as e:
        logger.warning(f"[FlowRunner] Could not fetch active flow: {e}")
        return None


def run_flow(
    supabase,
    flow: dict,
    conversation: dict,
    message_text: str,
    is_button_reply: bool,
    sender_name: str = '',
) -> dict:
    """
    Returns a dict with:
      handled       bool   – False → caller should fall through to AI
      actions       list   – ordered list of send-actions to execute
      next_node_id  str    – node ID to persist on the conversation
      variables     dict   – updated flow_variables to persist
      end           bool   – True if the flow is finished
      post_ai_actions list – actions to send AFTER the AI response (for ai nodes)
    """
    nodes = {n['id']: n for n in flow.get('nodes', [])}
    edges = flow.get('edges', [])

    current_node_id = conversation.get('flow_node_id')
    variables = dict(conversation.get('flow_variables') or {})

    def _resolve_cascade(result: dict) -> dict:
        """Loop through _auto_advance_edge outputs and accumulate actions."""
        visited = set()
        acc_actions = list(result.get('actions', []))
        curr_res = result

        while curr_res.get('_auto_advance_edge'):
            edge = curr_res['_auto_advance_edge']
            target_id = edge.get('target_node')
            if not target_id or target_id in visited:
                break  # prevent infinite loops
            visited.add(target_id)
            next_node = nodes.get(target_id)
            if not next_node:
                break
            
            # evaluate the next node
            curr_res = _emit_node(next_node, nodes, edges, curr_res.get('variables', variables))
            acc_actions.extend(curr_res.get('actions', []))
        
        curr_res['actions'] = acc_actions
        return curr_res

    # Auto-populate {{name}} from sender name on first contact
    if sender_name and 'name' not in variables:
        variables['name'] = sender_name

    # ── First contact or no current node → find start node ────────────────
    if not current_node_id:
        start = _find_start(nodes)
        if not start:
            return {'handled': False}
        return _resolve_cascade(_emit_node(start, nodes, edges, variables))

    current_node = nodes.get(current_node_id)
    if not current_node:
        return {'handled': False}

    ntype = current_node['type']

    # ── AI node → let normal AI handle the message ────────────────────────
    if ntype == 'ai':
        return {
            'handled': False,  # Caller runs AI
            'post_ai_actions': _list_node_buttons_as_actions(current_node, variables),
            'next_node_id': current_node_id,
            'variables': variables,
        }

    # ── Condition node — evaluate against user's free text ────────────────
    if ntype == 'condition_node':
        matched_edge = _evaluate_condition(current_node, edges, current_node_id, message_text)
        if matched_edge:
            next_node = nodes.get(matched_edge['target_node'])
            if next_node:
                # Save input as {{last_input}}
                variables['last_input'] = message_text
                return _emit_node(next_node, nodes, edges, variables)
        # No condition matched → stay
        return _emit_node(current_node, nodes, edges, variables)

    # ── List node — user tapped a list row ────────────────────────────────
    if ntype == 'list_node':
        if is_button_reply or message_text:
            row = _match_list_row(current_node, message_text)
            if row:
                edge = _find_edge(edges, current_node_id, row['id'])
                if edge:
                    next_node = nodes.get(edge['target_node'])
                    if next_node:
                        variables['last_choice'] = row['title']
                        return _emit_node(next_node, nodes, edges, variables)
        # Re-emit the list
        return _resolve_cascade(_emit_node(current_node, nodes, edges, variables))

    # ── Media / passthrough nodes (no buttons) — auto-advance ────────────
    if ntype in ('image_node', 'video_node', 'audio_node', 'document_node', 'location_node', 'variable_node'):
        if ntype == 'variable_node':
            var_name = current_node.get('data', {}).get('varName', '').strip()
            var_val = current_node.get('data', {}).get('varValue', '')
            if var_name:
                variables[var_name] = _sub(var_val, variables)

        # These nodes have one output edge (button_id == 'out')
        edge = _find_edge(edges, current_node_id, 'out')
        if edge:
            next_node = nodes.get(edge['target_node'])
            if next_node:
                return _resolve_cascade(_emit_node(next_node, nodes, edges, variables))
        return {'handled': True, 'actions': [], 'next_node_id': current_node_id,
                'variables': variables, 'end': False}

    # ── Button tap → follow the edge ──────────────────────────────────────
    if is_button_reply:
        tapped_btn = _match_button(current_node, message_text)
        if tapped_btn:
            # For URL/phone buttons there may be no edge — just re-emit
            edge = _find_edge(edges, current_node_id, tapped_btn['id'])
            if edge:
                next_node = nodes.get(edge['target_node'])
                if next_node:
                    variables['last_choice'] = message_text
                    return _resolve_cascade(_emit_node(next_node, nodes, edges, variables))
        return _resolve_cascade(_emit_node(current_node, nodes, edges, variables))

    # ── Free text at a message/start node → nudge ─────────────────────────
    btns = _node_reply_buttons(current_node)
    if btns:
        return {
            'handled': True,
            'actions': [{'type': 'text', 'text': 'Please choose one of the options below 👇'}],
            'next_node_id': current_node_id,
            'variables': variables,
            'end': False,
        }

    # Nothing matched → fall through to AI
    return {'handled': False}


# ---------------------------------------------------------------------------
# Variable substitution
# ---------------------------------------------------------------------------

def _sub(text: str, variables: dict) -> str:
    """Replace {{var}} placeholders with values from variables dict."""
    if not text:
        return text
    def replacer(m):
        key = m.group(1).strip()
        return str(variables.get(key, m.group(0)))
    return re.sub(r'\{\{([^}]+)\}\}', replacer, text)


# ---------------------------------------------------------------------------
# Node emission — builds the actions list for each node type
# ---------------------------------------------------------------------------

def _emit_node(node: dict, nodes: dict, edges: list, variables: dict) -> dict:
    """Build the full response dict for a given node."""
    ntype = node['type']
    data  = node.get('data', {})
    nid   = node['id']

    # ── End ───────────────────────────────────────────────────────────────
    if ntype == 'end':
        msg = _sub(data.get('message', '') or 'Thank you! Have a great day. 👋', variables)
        return {
            'handled': True,
            'actions': [{'type': 'text', 'text': msg}] if msg else [],
            'next_node_id': None,
            'variables': variables,
            'end': True,
        }

    # ── AI node ───────────────────────────────────────────────────────────
    if ntype == 'ai':
        return {
            'handled': False,
            'post_ai_actions': _list_node_buttons_as_actions(node, variables),
            'next_node_id': nid,
            'variables': variables,
        }

    # ── Lead form ─────────────────────────────────────────────────────────
    if ntype == 'lead':
        # Lead nodes are handled by the existing lead-capture AI system.
        # We just signal the caller to activate lead capture mode.
        return {
            'handled': True,
            'activate_lead_capture': True,
            'lead_completion_node_id': _first_out_target(edges, nid),
            'next_node_id': nid,
            'variables': variables,
            'end': False,
        }

    # ── Variable node ─────────────────────────────────────────────────────
    if ntype == 'variable_node':
        var_name = data.get('varName', '').strip()
        var_val = data.get('varValue', '')
        if var_name:
            variables[var_name] = _sub(var_val, variables)
        return _passthrough(nid, [], edges, variables)

    # ── Image ─────────────────────────────────────────────────────────────
    if ntype == 'image_node':
        actions = []
        if data.get('mediaUrl'):
            actions.append({
                'type': 'image',
                'url': data['mediaUrl'],
                'caption': _sub(data.get('caption', ''), variables),
            })
        return _passthrough(nid, actions, edges, variables)

    # ── Video ─────────────────────────────────────────────────────────────
    if ntype == 'video_node':
        actions = []
        if data.get('mediaUrl'):
            actions.append({
                'type': 'video',
                'url': data['mediaUrl'],
                'caption': _sub(data.get('caption', ''), variables),
            })
        return _passthrough(nid, actions, edges, variables)

    # ── Audio ─────────────────────────────────────────────────────────────
    if ntype == 'audio_node':
        actions = []
        if data.get('mediaUrl'):
            actions.append({'type': 'audio', 'url': data['mediaUrl']})
        return _passthrough(nid, actions, edges, variables)

    # ── Document ──────────────────────────────────────────────────────────
    if ntype == 'document_node':
        actions = []
        if data.get('mediaUrl'):
            actions.append({
                'type': 'document',
                'url':      data['mediaUrl'],
                'filename': data.get('filename', ''),
                'caption':  _sub(data.get('caption', ''), variables),
            })
        return _passthrough(nid, actions, edges, variables)

    # ── Location ──────────────────────────────────────────────────────────
    if ntype == 'location_node':
        actions = []
        try:
            lat = float(data.get('latitude', 0))
            lng = float(data.get('longitude', 0))
            actions.append({
                'type':      'location',
                'latitude':  lat,
                'longitude': lng,
                'name':      data.get('locationName', ''),
                'address':   data.get('address', ''),
            })
        except (TypeError, ValueError):
            pass
        return _passthrough(nid, actions, edges, variables)

    # ── List menu ─────────────────────────────────────────────────────────
    if ntype == 'list_node':
        actions = []
        # Optional image before the list
        if data.get('imageUrl'):
            actions.append({'type': 'image', 'url': data['imageUrl'], 'caption': ''})
        rows = []
        for r in (data.get('rows') or []):
            rows.append({
                'id':          r['id'],
                'title':       _sub(r.get('title', ''), variables)[:24],
                'description': _sub(r.get('description', ''), variables)[:72],
            })
        actions.append({
            'type':         'list',
            'body':         _sub(data.get('message', 'Please choose:'), variables),
            'button_label': _sub(data.get('buttonLabel', 'See Options'), variables)[:20],
            'section_title': _sub(data.get('sectionTitle', 'Options'), variables)[:24],
            'rows':         rows,
        })
        return {
            'handled': True,
            'actions': actions,
            'next_node_id': nid,
            'variables': variables,
            'end': False,
        }

    # ── Condition node (when emitted directly, just show a text prompt) ───
    if ntype == 'condition_node':
        prompt = _sub(data.get('prompt', 'Please reply:'), variables)
        return {
            'handled': True,
            'actions': [{'type': 'text', 'text': prompt}] if prompt else [],
            'next_node_id': nid,
            'variables': variables,
            'end': False,
        }

    # ── Action node (CTA URL / link) ──────────────────────────────────────
    if ntype == 'action':
        actions = []
        msg = _sub(data.get('message', ''), variables)
        if msg:
            actions.append({'type': 'text', 'text': msg})
        if data.get('url'):
            actions.append({
                'type':  'cta_url',
                'body':  msg or 'Click below:',
                'label': _sub(data.get('actionLabel', 'Open'), variables)[:20],
                'url':   data['url'],
            })
        return _passthrough(nid, actions, edges, variables)

    # ── Start / Message ───────────────────────────────────────────────────
    # (includes image attachment before the text if imageUrl is set)
    actions = []
    if data.get('imageUrl'):
        actions.append({'type': 'image', 'url': data['imageUrl'], 'caption': ''})

    msg = _sub(data.get('message', ''), variables)
    buttons = _node_reply_buttons(node)
    cta_btns = _node_cta_buttons(node, variables)

    # 1. Send body text with any Quick Reply buttons, or just text msg
    if buttons:
        actions.append({
            'type':    'buttons',
            'body':    msg,
            'buttons': buttons,
        })
    elif msg and not cta_btns:
        actions.append({'type': 'text', 'text': msg})
        
    # 2. Send CTA (URL/Phone) buttons as a secondary message
    if cta_btns:
        actions.append({
            'type': 'cta_url',
            'body': msg if not buttons else 'Links:',
            'cta_buttons': cta_btns
        })

    return {
        'handled': True,
        'actions': actions,
        'next_node_id': nid,
        'variables': variables,
        'end': False,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _passthrough(node_id: str, actions: list, edges: list, variables: dict) -> dict:
    """For media nodes: auto-advance to next connected node if any."""
    # Check both 'out' sentinel and any edge from this node
    edge = _find_edge(edges, node_id, 'out')
    if not edge:
        # Try any edge from this node
        for e in edges:
            if e.get('source_node') == node_id:
                edge = e
                break
    return {
        'handled': True,
        'actions': actions,
        'next_node_id': node_id,
        'variables': variables,
        'end': False,
        '_auto_advance_edge': edge,  # Caller can emit the next node immediately
    }


def _first_out_target(edges: list, node_id: str) -> Optional[str]:
    """Return the target_node of the first edge originating from node_id."""
    for e in edges:
        if e.get('source_node') == node_id:
            return e.get('target_node')
    return None


def _find_start(nodes: dict) -> Optional[dict]:
    for n in nodes.values():
        if n['type'] == 'start':
            return n
    return next(iter(nodes.values()), None) if nodes else None


def _find_edge(edges: list, source_node: str, button_id: str) -> Optional[dict]:
    for e in edges:
        if e.get('source_node') == source_node and e.get('button_id') == button_id:
            return e
    return None


def _match_button(node: dict, text: str) -> Optional[dict]:
    text_lower = text.strip().lower()
    for btn in node.get('data', {}).get('buttons', []):
        if btn.get('label', '').strip().lower() == text_lower:
            return btn
    return None


def _match_list_row(node: dict, text: str) -> Optional[dict]:
    """Match a list row by title (case-insensitive)."""
    text_lower = text.strip().lower()
    for row in node.get('data', {}).get('rows', []):
        if row.get('title', '').strip().lower() == text_lower:
            return row
    return None


def _node_reply_buttons(node: dict) -> list:
    """Return reply buttons (type=='reply') as [{id, title}]."""
    return [
        {'id': b['id'], 'title': b.get('label', '')}
        for b in node.get('data', {}).get('buttons', [])
        if b.get('label', '').strip() and b.get('type', 'reply') == 'reply'
    ]

def _node_cta_buttons(node: dict, variables: dict) -> list:
    """Return url/phone buttons as a list of dicts for CTA actions."""
    btns = []
    for b in node.get('data', {}).get('buttons', []):
        if not b.get('label', '').strip():
            continue
        btype = b.get('type', 'reply')
        if btype in ('url', 'phone'):
            btns.append({
                'type': btype,
                'text': _sub(b.get('label'), variables)[:20],
                **({'url': _sub(b.get('url', ''), variables)} if btype == 'url' else {}),
                **({'phone': _sub(b.get('phone', ''), variables)} if btype == 'phone' else {})
            })
    return btns


def _list_node_buttons_as_actions(node: dict, variables: dict) -> list:
    """For AI nodes: re-send reply buttons that follow as a separate action."""
    btns = _node_reply_buttons(node)
    cta_btns = _node_cta_buttons(node, variables)
    
    actions = []
    if btns:
        actions.append({'type': 'buttons', 'body': 'Choose an option:', 'buttons': btns})
    if cta_btns:
        actions.append({'type': 'cta_url', 'body': 'Links:' if btns else 'Choose an option:', 'cta_buttons': cta_btns})
        
    return actions


def _evaluate_condition(node: dict, edges: list, node_id: str, text: str) -> Optional[dict]:
    """
    Evaluate condition node branches against user's free-text input.
    Each condition has: {id, operator, value}
    Operators: contains | not_contains | equals | starts_with | ends_with | else
    Returns the first matching edge, or the 'else' edge if nothing matched.
    """
    text_lower = text.strip().lower()
    else_edge = None

    for cond in node.get('data', {}).get('conditions', []):
        cid      = cond.get('id')
        operator = cond.get('operator', 'contains')
        value    = (cond.get('value') or '').strip().lower()

        edge = _find_edge(edges, node_id, cid)

        if operator == 'else':
            else_edge = edge
            continue

        matched = False
        if operator == 'contains':
            matched = value in text_lower
        elif operator == 'not_contains':
            matched = value not in text_lower
        elif operator == 'equals':
            matched = text_lower == value
        elif operator == 'starts_with':
            matched = text_lower.startswith(value)
        elif operator == 'ends_with':
            matched = text_lower.endswith(value)

        if matched and edge:
            return edge

    return else_edge
