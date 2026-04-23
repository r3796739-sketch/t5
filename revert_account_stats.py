import os
from dotenv import load_dotenv

load_dotenv()
from utils.supabase_client import get_supabase_admin_client

def revert_stats(email):
    supabase = get_supabase_admin_client()
    print(f"Reverting stats. Cleaning up dummy data...")
    
    # 1. Clean up Chat History
    print("Deleting dummy chat history...")
    supabase.table('chat_history').delete().eq('answer', 'Dummy answer to look busy').execute()
    
    # 2. Find all dummy profiles
    print("Finding dummy users to delete...")
    dummy_profiles = supabase.table('profiles').select('id, email').like('email', 'dummy%@example.com').execute()
    
    deleted_count = 0
    if dummy_profiles.data:
        dummy_ids = [p['id'] for p in dummy_profiles.data]
        
        # 3. Clean up Creator Earnings linked to dummy referrals
        print("Cleaning up fake affiliate earnings...")
        # PostgREST allows deleting by 'in_' filter
        if dummy_ids:
            supabase.table('creator_earnings').delete().in_('referred_user_id', dummy_ids).execute()
        
        # 4. Clean up Marketplace Transfers and Earnings linked to dummy buyers
        print("Cleaning up fake marketplace transfers and earnings...")
        if dummy_ids:
            # First get transfer IDs to delete their earnings
            transfers = supabase.table('chatbot_transfers').select('id').in_('buyer_id', dummy_ids).execute()
            if transfers.data:
                transfer_ids = [t['id'] for t in transfers.data]
                supabase.table('creator_marketplace_earnings').delete().in_('transfer_id', transfer_ids).execute()
                supabase.table('chatbot_transfers').delete().in_('id', transfer_ids).execute()
        
        # 5. Delete Dummy Auth Users (will cascade to profiles)
        print(f"Deleting {len(dummy_ids)} dummy internal user accounts...")
        for uid in dummy_ids:
            try:
                supabase.auth.admin.delete_user(uid)
                deleted_count += 1
            except Exception as e:
                print(f"Skipped deleting auth user {uid}: {e}")
                
    # 6. Reset usage stats for target user to 0 to be safe
    print("Resetting target user usage stats...")
    user_res = supabase.table('profiles').select('id').eq('email', email).maybe_single().execute()
    if user_res.data:
        user_id = user_res.data['id']
        supabase.table('usage_stats').update({
            'queries_this_month': 0, 
            'channels_processed': 0
        }).eq('user_id', user_id).execute()
    
    # 7. Clean up isolated Dummy Channels we might have created
    print("Cleaning up dummy channels...")
    supabase.table('channels').delete().like('channel_name', 'Dummy Channel%').execute()

    print(f"\n✅ Revert complete! Deleted {deleted_count} dummy accounts/stats.")
    print("Please refresh your Creator Dashboard page to see your real numbers restored (usage queries will be reset to 0 for peace of mind).")

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="Revert user stats to real numbers")
    parser.add_argument('--email', required=True, type=str, help="Email of your target user account")
    args = parser.parse_args()
    revert_stats(args.email)
