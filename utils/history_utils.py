import logging
import json
import time
import threading
from collections import OrderedDict
from datetime import datetime
from .supabase_client import get_supabase_client, get_supabase_admin_client
from supabase import Client as SupabaseClient

# ===========================================================================
# IN-PROCESS WRITE-THROUGH HISTORY CACHE
# ===========================================================================
# Purpose: Eliminate redundant Supabase DB reads for users mid-conversation.
# Each WhatsApp/Discord message previously did a full DB round-trip to fetch
# the last N history rows — even when the user sent 5 messages in 30 seconds.
#
# Design:
#   - Write-through: every new Q&A is appended to BOTH cache AND DB.
#     The bot never "forgets" a message — cache is always up to date.
#   - TTL: after CACHE_TTL_SECONDS of inactivity, entry expires and the
#     next read goes back to DB (safe cold-start for resumed conversations).
#   - LRU + MAX_ENTRIES: once the cache holds MAX_ENTRIES conversations,
#     the least-recently-used entry is evicted. Hard cap on RAM usage.
#
# RAM math at MAX_ENTRIES=500:
#   500 conv × 5 Q&A pairs × ~500 chars = ~1.25 MB max. Essentially free.
#
# Multi-worker note: this cache is per-Gunicorn-worker (each worker has its
# own Python process and memory). It still eliminates redundant reads within
# a worker. For true cross-worker sharing, replace with Redis.
# ===========================================================================

CACHE_TTL_SECONDS = 120   # Expire entry after 2 minutes of inactivity
MAX_ENTRIES = 500         # Hard cap — evicts LRU when exceeded

# OrderedDict used for O(1) LRU eviction (move_to_end on access)
_history_cache: OrderedDict = OrderedDict()
_cache_lock = threading.Lock()


def _cache_key(user_id: str, channel_name: str) -> str:
    return f"{user_id}::{channel_name}"


def _cache_get(user_id: str, channel_name: str, limit: int):
    """
    Returns cached history list if present and not expired, else None.
    Thread-safe. Moves entry to end of OrderedDict on access (LRU).
    """
    key = _cache_key(user_id, channel_name)
    with _cache_lock:
        entry = _history_cache.get(key)
        if entry is None:
            return None
        if time.monotonic() - entry['ts'] > CACHE_TTL_SECONDS:
            # Expired — remove it and signal a DB read
            del _history_cache[key]
            return None
        # Refresh TTL + move to end (most recently used)
        entry['ts'] = time.monotonic()
        _history_cache.move_to_end(key)
        # Return the last `limit` items (cache may hold more than requested)
        return entry['data'][-limit:]


def _cache_set(user_id: str, channel_name: str, history: list):
    """
    Stores/replaces the full history list for this conversation.
    Evicts LRU entry if MAX_ENTRIES is reached.
    Thread-safe.
    """
    key = _cache_key(user_id, channel_name)
    with _cache_lock:
        if key in _history_cache:
            # Update in place and refresh TTL
            _history_cache[key] = {'data': history, 'ts': time.monotonic()}
            _history_cache.move_to_end(key)
        else:
            if len(_history_cache) >= MAX_ENTRIES:
                # Evict the least-recently-used (front of OrderedDict)
                evicted_key, _ = _history_cache.popitem(last=False)
                logging.debug(f"[HISTORY_CACHE] LRU eviction: {evicted_key}")
            _history_cache[key] = {'data': history, 'ts': time.monotonic()}


def _cache_append(user_id: str, channel_name: str, question: str, answer: str):
    """
    Appends a new Q&A turn to an existing cache entry.
    If the entry doesn't exist (e.g. this worker never read it), this is a no-op —
    the next read will cold-start from DB which will have the correct data.
    Thread-safe.
    """
    key = _cache_key(user_id, channel_name)
    with _cache_lock:
        entry = _history_cache.get(key)
        if entry is None:
            return  # Not in this worker's cache — skip, DB has it
        entry['data'].append({'question': question, 'answer': answer})
        entry['ts'] = time.monotonic()
        _history_cache.move_to_end(key)
        logging.debug(f"[HISTORY_CACHE] Appended turn for {key}. Cache size: {len(_history_cache)}")


# ===========================================================================
# PUBLIC API
# ===========================================================================

def save_chat_history(supabase_client: SupabaseClient, user_id, channel_name, question, answer, sources, integration_source="web"):
    """
    Save chat history to the database using the provided Supabase client.
    This allows the caller to decide whether to use a user-authenticated
    client or an admin client.
    """
    try:
        data = {
            'user_id': user_id,
            'channel_name': channel_name,
            'question': question,
            'answer': answer,
            'sources': sources,
            'integration_source': integration_source,
            'created_at': datetime.utcnow().isoformat()
        }
        try:
            supabase_client.table('chat_history').insert(data).execute()
        except Exception as insert_e:
            if 'does not exist' in str(insert_e).lower() or 'not found' in str(insert_e).lower() or 'integration_source' in str(insert_e):
                del data['integration_source']
                supabase_client.table('chat_history').insert(data).execute()
                logging.warning(f"Saved chat history without integration_source for user {user_id}. Please add the column to Supabase.")
            else:
                raise insert_e

    except Exception as e:
        logging.error(f"Error saving chat history for user {user_id}: {e}", exc_info=True)


def get_chat_history(user_id, channel_name, access_token: str, limit=None):
    """Get chat history from the database using an authenticated client."""
    supabase = get_supabase_client(access_token=access_token)
    if not supabase:
        logging.error("Failed to initialize Supabase client in get_chat_history.")
        return []

    try:
        query = supabase.table('chat_history')\
            .select('*')\
            .eq('user_id', user_id)\
            .eq('channel_name', channel_name)\
            .order('created_at', desc=True)
        if limit is not None:
            query = query.limit(limit)

        response = query.execute()
        history = list(reversed(response.data))

        for qa in history:
            if isinstance(qa.get('sources'), str):
                try:
                    qa['sources'] = json.loads(qa['sources'])
                except Exception:
                    qa['sources'] = []
            if qa.get('sources') is None:
                qa['sources'] = []
        return history
    except Exception as e:
        logging.error(f"Error getting chat history for user {user_id}: {e}", exc_info=True)
        return []


def get_chat_history_for_service(user_id: str, channel_name: str, limit: int = 5):
    """
    Gets chat history for background services (WhatsApp, Discord, Messenger).

    Uses a write-through in-process cache to avoid a DB round-trip on every
    message during active conversations. Cache is bounded (MAX_ENTRIES=500)
    and TTL-expiring (CACHE_TTL_SECONDS=120) so RAM usage is fixed and small.

    Falls back to Supabase on cache miss (cold start or TTL expiry).
    """
    # --- Cache read ---
    cached = _cache_get(user_id, channel_name, limit)
    if cached is not None:
        logging.debug(f"[HISTORY_CACHE] HIT for {user_id}::{channel_name}")
        return cached

    # --- Cache miss: read from DB ---
    logging.debug(f"[HISTORY_CACHE] MISS for {user_id}::{channel_name} — fetching from DB")
    try:
        supabase = get_supabase_admin_client()
        response = supabase.table('chat_history').select('question, answer') \
            .eq('user_id', user_id) \
            .eq('channel_name', channel_name) \
            .order('created_at', desc=True) \
            .limit(limit) \
            .execute()

        history = list(reversed(response.data)) if response.data else []

        # Prime the cache with what we just fetched from DB
        _cache_set(user_id, channel_name, history)
        return history

    except Exception as e:
        logging.error(f"Error getting service chat history for user {user_id}: {e}")
        return []


def append_service_history(user_id: str, channel_name: str, question: str, answer: str):
    """
    Call this AFTER saving a new Q&A to the DB via save_chat_history().
    Appends the new turn to the in-process cache so the next message in this
    conversation reads from cache (no DB round-trip needed).

    This is the write-through step — keeping cache + DB in sync.
    """
    _cache_append(user_id, channel_name, question, answer)