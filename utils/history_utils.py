import logging
import json
from datetime import datetime
from .supabase_client import get_supabase_client, get_supabase_admin_client
from supabase import Client as SupabaseClient

# --- FIX: Modified save_chat_history to accept a Supabase client ---
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
        # Attempt to insert, including the new integration_source column
        try:
            supabase_client.table('chat_history').insert(data).execute()
        except Exception as insert_e:
            # Fallback if the user hasn't added the integration_source column to the table yet
            if 'does not exist' in str(insert_e).lower() or 'not found' in str(insert_e).lower() or 'integration_source' in str(insert_e):
                del data['integration_source']
                supabase_client.table('chat_history').insert(data).execute()
                logging.warning(f"Saved chat history without integration_source for user {user_id}. Please add the column to Supabase.")
            else:
                raise insert_e

    except Exception as e:
        # Log the specific error if it occurs
        logging.error(f"Error saving chat history for user {user_id}: {e}", exc_info=True)

# --- FIX: Modified get_chat_history to use an authenticated client ---
def get_chat_history(user_id, channel_name, access_token: str, limit=None):
    """Get chat history from the database using an authenticated client."""
    # Always use the user-specific client to respect RLS policies
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
        
        # Deserialize sources if needed
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
    Gets chat history for background services (like the Discord bot) using the admin client.
    """
    try:
        supabase = get_supabase_admin_client()
        
        # --- THIS IS THE FIX ---
        response = supabase.table('chat_history').select('question, answer') \
            .eq('user_id', user_id) \
            .eq('channel_name', channel_name) \
            .order('created_at', desc=True) \
            .limit(limit) \
            .execute()
        # --- END FIX ---
            
        # The history is fetched in reverse chronological order, so we reverse it back
        return list(reversed(response.data)) if response.data else []
    except Exception as e:
        logging.error(f"Error getting service chat history for user {user_id}: {e}")
        return []