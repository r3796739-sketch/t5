import os
import uuid
import logging
from .supabase_client import get_supabase_admin_client

log = logging.getLogger(__name__)

def generate_transfer_code():
    """Generates a unique, URL-safe transfer code."""
    return uuid.uuid4().hex[:12]

def create_transfer_record(creator_id: str, chatbot_id: int, query_limit: int, platform_fee_paise: int, creator_price_paise: int, google_review_id: int = None) -> str:
    """
    Creates a new pending transfer record for a chatbot or a google review business.
    Returns the unique transfer code.
    """
    supabase = get_supabase_admin_client()
    transfer_code = generate_transfer_code()
    
    try:
        supabase.table('chatbot_transfers').insert({
            'creator_id': creator_id,
            'chatbot_id': chatbot_id,
            'google_review_id': google_review_id,
            'transfer_code': transfer_code,
            'status': 'pending',
            'query_limit_monthly': query_limit,
            'platform_fee_monthly': platform_fee_paise,
            'creator_price_monthly': creator_price_paise,
            'queries_used_this_month': 0
        }).execute()
        return transfer_code
    except Exception as e:
        log.error(f"Error creating transfer record for creator {creator_id}, chatbot {chatbot_id}: {e}")
        return None

def get_transfer_by_code(transfer_code: str):
    """Fetches a transfer record by its unique code."""
    supabase = get_supabase_admin_client()
    try:
        res = supabase.table('chatbot_transfers').select('*').eq('transfer_code', transfer_code).single().execute()
        data = res.data
        if data:
            if data.get('chatbot_id'):
                ch_res = supabase.table('channels').select('*').eq('id', data['chatbot_id']).maybe_single().execute()
                data['channels'] = ch_res.data
            elif data.get('google_review_id'):
                gr_res = supabase.table('google_review_settings').select('*').eq('id', data['google_review_id']).maybe_single().execute()
                data['google_review_settings'] = gr_res.data
        return data
    except Exception as e:
        log.error(f"Error fetching transfer by code {transfer_code}: {e}")
        return None

def _transfer_ownership(supabase, db_utils_mod, creator_id: str, buyer_id: str,
                        item_type: str, item_id) -> None:
    """
    Generic helper that transfers marketplace item ownership from seller to buyer.
    Works for ALL product types:
      - 'chatbot'        → updates channels.creator_id, removes seller from user_channels
      - 'google_review'  → updates google_review_settings.user_id
      - Any future type  → extend the if/elif chain below

    After transfer:
    - Item disappears from seller's sidebar and dashboard
    - Buyer becomes the new owner with full edit access
    - Seller can still access the item via the Earnings > Marketplace "Edit Settings" link
    """
    if item_type == 'chatbot':
        # Transfer creator_id on the channels record to buyer
        supabase.table('channels').update({
            'creator_id': buyer_id
        }).eq('id', item_id).execute()

        # Give buyer a user_channels link (for quota/usage tracking)
        db_utils_mod.link_user_to_channel(buyer_id, item_id)

        # Remove seller from user_channels → sold chatbot disappears from their sidebar
        try:
            supabase.table('user_channels').delete() \
                .eq('user_id', creator_id).eq('channel_id', item_id).execute()
            log.info(f"[Transfer] Removed seller {creator_id} from user_channels for chatbot {item_id}.")
        except Exception as e:
            log.warning(f"[Transfer] Could not remove seller from user_channels: {e}")

        # Invalidate in-memory creator channel cache
        db_utils_mod._creator_channels_cache.pop(f"creator_channels:{creator_id}", None)
        db_utils_mod._creator_channels_cache.pop(f"creator_channels:{buyer_id}", None)

        log.info(f"[Transfer] Chatbot {item_id}: creator_id → {buyer_id} (was {creator_id}).")

    elif item_type == 'google_review':
        # Transfer user_id on google_review_settings to buyer
        supabase.table('google_review_settings').update({
            'user_id': buyer_id
        }).eq('id', item_id).execute()

        # Delete feedback history associated with this business so the buyer starts fresh and seller's test data is cleared
        try:
            supabase.table('google_reviews_feedback').delete().eq('settings_id', item_id).execute()
        except Exception as e:
            log.warning(f"[Transfer] Could not delete feedback history: {e}")

        log.info(f"[Transfer] Google Review {item_id}: user_id → {buyer_id} (was {creator_id}).")

    else:
        # Placeholder for future item types
        log.warning(f"[Transfer] Unknown item_type '{item_type}' — no ownership transfer performed for item {item_id}.")

    # --- Invalidate Redis sidebar/channel caches for both parties ---
    try:
        from .subscription_utils import redis_client
        if redis_client:
            redis_client.delete(f"user_visible_channels:{buyer_id}:community:none")
            redis_client.delete(f"user_visible_channels:{creator_id}:community:none")
    except Exception as e:
        log.warning(f"[Transfer] Could not invalidate Redis cache: {e}")


def move_chatbot_to_buyer(transfer_id: str, buyer_id: str, subscription_id: str) -> bool:
    """
    Executes a marketplace transfer: determines the item type, calls the generic
    _transfer_ownership helper, marks the transfer as active, and notifies the seller.
    Applies consistently to ALL product types (chatbots, Google Reviews, future items).
    """
    from . import db_utils
    supabase = get_supabase_admin_client()
    
    try:
        transfer_res = supabase.table('chatbot_transfers').select('*').eq('id', transfer_id).single().execute()
        if not transfer_res.data:
            log.error(f"Transfer {transfer_id} not found.")
            return False
            
        transfer = transfer_res.data
        chatbot_id = transfer.get('chatbot_id')
        google_review_id = transfer.get('google_review_id')
        creator_id = transfer['creator_id']

        # --- Determine item type and delegate to generic helper ---
        if chatbot_id:
            _transfer_ownership(supabase, db_utils, creator_id, buyer_id, 'chatbot', chatbot_id)
        elif google_review_id:
            _transfer_ownership(supabase, db_utils, creator_id, buyer_id, 'google_review', google_review_id)
        else:
            log.warning(f"Transfer {transfer_id} has no chatbot_id or google_review_id — skipping ownership transfer.")

        # --- Mark transfer as active ---
        supabase.table('chatbot_transfers').update({
            'status': 'active',
            'buyer_id': buyer_id,
            'razorpay_subscription_id': subscription_id
        }).eq('id', transfer_id).execute()

        # --- Notify the seller ---
        try:
            price_inr = transfer['creator_price_monthly'] / 100
            seller_msg = (
                f"Your item has been purchased! Payment of ₹{price_inr:.2f} received. "
                f"The buyer now manages the item. You can still edit settings via Earnings → Marketplace."
            )
            db_utils.create_notification(creator_id, seller_msg, type='sale')
            log.info(f"Seller {creator_id} notified of sale (transfer {transfer_id}).")
        except Exception as notify_err:
            log.warning(f"Failed to create seller notification: {notify_err}")
        
        return True

    except Exception as e:
        log.error(f"Error executing marketplace transfer {transfer_id}: {e}")
        return False


def record_creator_marketplace_earning(subscription_id: str, gross_amount_paise: int):
    """
    Records an earning for the creator when a marketplace subscription is paid.
    Calculates the exact platform fee at the time of the transaction.
    """
    supabase = get_supabase_admin_client()
    
    try:
        # Find the transfer associated with this subscription
        transfer_res = supabase.table('chatbot_transfers').select('*').eq('razorpay_subscription_id', subscription_id).single().execute()
        if not transfer_res.data:
            log.warning(f"No marketplace transfer found for subscription {subscription_id}.")
            return False
            
        transfer = transfer_res.data
        
        gross_amount = gross_amount_paise
        platform_fee = transfer['platform_fee_monthly']
        creator_amount = gross_amount - platform_fee
        
        # Insert the earning record
        supabase.table('creator_marketplace_earnings').insert({
            'transfer_id': transfer['id'],
            'creator_id': transfer['creator_id'],
            'gross_amount': gross_amount,
            'platform_fee': platform_fee,
            'creator_amount': creator_amount,
            'status': 'credited'
        }).execute()
        
        log.info(f"Recorded marketplace earning: gross {gross_amount}p, platform fee {platform_fee}p, net {creator_amount}p for creator {transfer['creator_id']}.")
        return True
        
    except Exception as e:
        log.error(f"Error recording marketplace earning for subscription {subscription_id}: {e}")
        return False

def get_creator_marketplace_balance(creator_id: str):
    """
    Calculates the creator's marketplace total earnings, pending payouts, and withdrawable balance.
    Reuses the creator_payouts table for withdrawal requests.
    """
    supabase = get_supabase_admin_client()
    try:
        # 1. Calculate total earned from marketplace
        earnings_res = supabase.table('creator_marketplace_earnings').select('creator_amount').eq('creator_id', creator_id).execute()
        # Note: Amounts are stored in paise, so convert to INR (or preferred currency) if needed. 
        # Here we convert from paise to standard decimals for display (assuming INR)
        total_earned_paise = sum(item['creator_amount'] for item in earnings_res.data) if earnings_res.data else 0
        total_earned = total_earned_paise / 100.0

        # For marketplace payouts, we identify them by a 'payout_type' key in the 'payout_destination_details' JSONB column.
        history_res = supabase.table('creator_payouts').select('*').eq('creator_id', creator_id).order('requested_at', desc=True).execute()
        all_history = history_res.data or []
        
        # Filter for marketplace payouts only
        history = [p for p in all_history if p.get('payout_destination_details') and p['payout_destination_details'].get('payout_type') == 'marketplace']
        
        pending_payouts = sum(p['amount_usd'] for p in history if p.get('status') in ['pending', 'processing'])
        total_paid = sum(p['amount_usd'] for p in history if p.get('status') == 'paid')

        withdrawable_balance = total_earned - pending_payouts - total_paid
        
        return {
            'withdrawable_balance': round(withdrawable_balance, 2),
            'pending_payouts': round(pending_payouts, 2),
            'total_earned': round(total_earned, 2),
            'history': history,
            'currency': 'INR' # Hardcoded for Indian marketplace right now
        }

    except Exception as e:
        log.error(f"Error getting marketplace balance for {creator_id}: {e}")
        return {'withdrawable_balance': 0.0, 'pending_payouts': 0.0, 'total_earned': 0.0, 'history': [], 'currency': 'INR'}

def create_marketplace_payout_request(creator_id: str, amount: float, payout_details: dict):
    """
    Creates a new payout request for marketplace earnings.
    Tags the request via the JSONB details column to separate it from affiliate earnings.
    """
    supabase = get_supabase_admin_client()
    try:
        current_balances = get_creator_marketplace_balance(creator_id)
        withdrawable_balance = current_balances.get('withdrawable_balance', 0.0)

        if amount > withdrawable_balance:
            return None, "Withdrawal amount cannot exceed your available marketplace balance."

        # Add the type marker
        marked_details = dict(payout_details)
        marked_details['payout_type'] = 'marketplace'

        # Store using the existing column
        new_payout = supabase.table('creator_payouts').insert({
            'creator_id': creator_id,
            'amount_usd': amount, # Reusing the column, but it represents INR
            'status': 'pending',
            'payout_destination_details': marked_details 
        }).execute().data[0]

        return new_payout, "Marketplace payout requested successfully. It will be reviewed by an admin."
    except Exception as e:
        log.error(f"Error creating marketplace payout request for creator {creator_id}: {e}")
        return None, "An internal error occurred."

def reset_monthly_queries():
    """
    Cron job function to reset `queries_used_this_month` to 0 
    for all active transfers where a month has passed since last_query_reset.
    """
    # This would typically be run by huey or a system cron job
    supabase = get_supabase_admin_client()
    try:
        # Use a raw query or an RPC to do this efficiently:
        # UPDATE chatbot_transfers SET queries_used_this_month = 0, last_query_reset = CURRENT_DATE 
        # WHERE status = 'active' AND last_query_reset < CURRENT_DATE - INTERVAL '1 month';
        res = supabase.rpc('reset_marketplace_monthly_queries').execute()
        log.info("Successfully reset monthly queries for marketplace transfers.")
        return True
    except Exception as e:
        log.error(f"Error resetting monthly queries: {e}")
        return False
