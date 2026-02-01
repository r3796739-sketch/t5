"""
Quick script to get your user ID from the database
"""
import os
from supabase import create_client

# Initialize Supabase
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_KEY')
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# Get all users with their email and ID
result = supabase.table('profiles').select('id, email, plan').execute()

print("\n" + "=" * 80)
print("Available Users:")
print("=" * 80)

for user in result.data:
    print(f"\nEmail: {user.get('email', 'N/A')}")
    print(f"User ID: {user['id']}")
    print(f"Plan: {user.get('plan', 'N/A')}")
    print("-" * 80)

print("\nCopy one of the User IDs above to test the monthly reset.\n")
