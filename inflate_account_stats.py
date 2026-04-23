import os
import uuid
import random
import argparse
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load env before importing supabase to ensure config is set up
load_dotenv()
from utils.supabase_client import get_supabase_admin_client

def create_fake_user(supabase, email):
    # Try to create a user in auth via admin SDK to satisfy FK constraints
    try:
        user_res = supabase.auth.admin.create_user({
            'email': email,
            'password': 'FakePassword123!',
            'email_confirm': True
        })
        user_id = user_res.user.id
        
        # also create profile
        supabase.table('profiles').upsert({'id': user_id, 'email': email, 'full_name': 'Fake User'}).execute()
        return user_id
    except Exception as e:
        print(f"Error creating fake user: {e}")
        return None

def inflate_stats(email):
    supabase = get_supabase_admin_client()
    
    print(f"Looking up user with email: {email}...")
    res = supabase.table('profiles').select('id, full_name, email').eq('email', email).maybe_single().execute()
    if not res or not hasattr(res, 'data') or not res.data:
        print(f"Error: User with email '{email}' not found. Make sure this user has signed up.")
        return
        
    user_id = res.data['id']
    user_email = res.data['email']
    print(f"Found user: {res.data.get('full_name')} ({user_id})")
    
    # 1. Get or create a channel
    ch_res = supabase.table('channels').select('id, channel_name').eq('creator_id', user_id).limit(1).execute()
    if ch_res.data:
        channel_id = ch_res.data[0]['id']
        channel_name = ch_res.data[0]['channel_name'] or "Dummy Channel"
        print(f"Using existing channel ID: {channel_id} ({channel_name})")
    else:
        print("Creating dummy channel...")
        channel_name = f'Dummy Channel {uuid.uuid4().hex[:4]}'
        ch_ins = supabase.table('channels').insert({
            'channel_url': f"https://youtube.com/channel/dummy_{uuid.uuid4().hex[:8]}",
            'creator_id': user_id,
            'status': 'active',
            'is_shared': False,
            'has_youtube': True,
            'channel_name': channel_name
        }).execute()
        channel_id = ch_ins.data[0]['id']

    print("Generating fake data... this may take a few seconds.")
    
    now = datetime.utcnow()
    
    # Generate exactly 'num_items' dates distributed in a perfect exponential growth curve
    # so the graph strictly goes UP every single month.
    def generate_growth_dates(num_items):
        dates = []
        for i in range(num_items):
            # i / (num_items - 1) goes from 0 to 1
            # raise to a high power (like 3) so most points cluster near 1 (today)
            # and very few points cluster near 0 (180 days ago)
            # and because it's uniformly sampled, the sorting guarantees a smooth curve!
            progress = (i / max(1, num_items - 1)) ** 2.5
            days_ago = int((1.0 - progress) * 160) # distribute over last 160 days
            dates.append((now - timedelta(days=days_ago)).isoformat())
        return dates

    # Helper pools for the data
    referral_dates = []
    mp_sales_dates = []
    chat_dates = []

    # 2. Inflate Referrals (which also inflates Affiliate MRR)
    # The paid referrals count towards the display of paying subscribers and MRR
    num_referrals = random.randint(10, 15)
    
    # Initialize pool of dates
    referral_dates = generate_growth_dates(num_referrals)
    
    print(f"Creating {num_referrals} fake referrals...")
    for _ in range(num_referrals):
        fake_email = f"dummy_ref_{uuid.uuid4().hex[:8]}@example.com"
        fake_uid = create_fake_user(supabase, fake_email)
        if fake_uid:
            plan = random.choice(['creator_inr', 'personal_inr'])
            created_at = referral_dates.pop()
            
            supabase.table('profiles').update({
                'referred_by_channel_id': channel_id,
                'direct_subscription_plan': plan
            }).eq('id', fake_uid).execute()
            
            # create earnings to give history
            supabase.table('creator_earnings').insert({
                'creator_id': user_id,
                'referred_user_id': fake_uid,
                'channel_id': channel_id,
                'amount_usd': random.uniform(10.0, 45.0),
                'plan_id': plan,
                'created_at': created_at
            }).execute()

    # 3. Inflate Marketplace Sales and MRR
    num_mp_sales = random.randint(15, 20)
    mp_sales_dates = generate_growth_dates(num_mp_sales)
    
    print(f"Creating {num_mp_sales} marketplace dummy transactions...")
    for _ in range(num_mp_sales):
        fake_email = f"dummy_mp_{uuid.uuid4().hex[:8]}@example.com"
        buyer_uid = create_fake_user(supabase, fake_email)
        if buyer_uid:
            creator_price_paise = random.randint(23500, 25500) # e.g. Rs.500 to Rs.2500 MRR
            platform_fee_paise = int(creator_price_paise * 0.2)
            created_at = mp_sales_dates.pop()
            
            t_res = supabase.table('chatbot_transfers').insert({
                'creator_id': user_id,
                'chatbot_id': channel_id,
                'buyer_id': buyer_uid,
                'status': 'active',
                'transfer_code': uuid.uuid4().hex[:12],
                'query_limit_monthly': 100,
                'platform_fee_monthly': platform_fee_paise,
                'creator_price_monthly': creator_price_paise,
                'queries_used_this_month': 0,
                'created_at': created_at
            }).execute()
            
            if t_res.data:
                transfer_id = t_res.data[0]['id']
                supabase.table('creator_marketplace_earnings').insert({
                    'transfer_id': transfer_id,
                    'creator_id': user_id,
                    'gross_amount': creator_price_paise + platform_fee_paise,
                    'platform_fee': platform_fee_paise,
                    'creator_amount': creator_price_paise,
                    'status': 'credited',
                    'payment_date': created_at
                }).execute()

    # 4. Inflate Total Conversations
    num_convos = random.randint(4, 50)
    chat_dates = generate_growth_dates(num_convos)
    
    print(f"Creating {num_convos} fake conversations...")
    for _ in range(num_convos):
        supabase.table('chat_history').insert({
            'channel_name': channel_name,
            'user_id': user_id,
            'question': 'Dummy question?',
            'answer': 'Dummy answer to look busy',
            'created_at': chat_dates.pop()
        }).execute()

    # 5. Inflate Usage Stats
    print("Inflating query usage...")
    usage_res = supabase.table('usage_stats').select('queries_this_month, channels_processed').eq('user_id', user_id).maybe_single().execute()
    new_queries = 80 + random.randint(150, 5500) # add some queries
    if usage_res.data:
        curr_queries = usage_res.data.get('queries_this_month') or 0
        supabase.table('usage_stats').update({
            'queries_this_month': curr_queries + new_queries,
            'channels_processed': 6
        }).eq('user_id', user_id).execute()
    else:
        supabase.table('usage_stats').insert({
            'user_id': user_id,
            'queries_this_month': new_queries,
            'channels_processed': 6
        }).execute()

    # Give user a subscription if they are free to show large limits
    user_status_res = supabase.table('profiles').select('direct_subscription_plan').eq('id', user_id).execute()
    if not user_status_res.data or not user_status_res.data[0].get('direct_subscription_plan') or user_status_res.data[0].get('direct_subscription_plan') == 'free':
        supabase.table('profiles').update({'direct_subscription_plan': 'creator_inr'}).eq('id', user_id).execute()
        print("Updated user plan to 'creator_inr' so limits look large.")
        
    print("\n✅ Successfully inflated account stats!")
    print("Please refresh your Creator Dashboard page to see the new numbers.")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Inflate user stats to test dashboard rendering")
    parser.add_argument('--email', required=True, type=str, help="Email of the user account to inflate. Example: --email test@example.com")
    args = parser.parse_args()
    inflate_stats(args.email)
