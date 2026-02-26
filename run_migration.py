import os
import sys
from dotenv import load_dotenv

# Add the project root to the python path so we can import utils
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

load_dotenv()

from utils.supabase_client import get_supabase_admin_client
from supabase import create_client

def run_migration():
    try:
        print("Reading migration file...")
        with open('marketplace_migration.sql', 'r') as f:
            sql = f.read()

        # The python supabase client doesn't have a direct way to execute raw SQL easily
        # However, we can use the underlying postgrest client if we're careful,
        # or we can just ask the user to run it in the Supabase SQL editor.
        print("Since executing raw SQL via the postgrest API is restricted, please copy the contents of `marketplace_migration.sql`")
        print("and run it directly in your Supabase SQL Editor.")
        print("This ensures all triggers, RPCs, and RLS policies are created with the correct permissions.")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    run_migration()
