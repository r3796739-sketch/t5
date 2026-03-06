"""
One-off script to regenerate speaking_style and creator_soul for a channel.
Uses the STYLE_LLM_PROVIDER/STYLE_MODEL_NAME from .env (Groq).
"""
import os
import sys
from dotenv import load_dotenv
load_dotenv()

from utils.supabase_client import get_supabase_admin_client
from utils.qa_utils import extract_speaking_style, extract_creator_soul

# --- CONFIG ---
CHANNEL_ID = 2  # Dan Martell's channel

supabase = get_supabase_admin_client()

# 1. Get text sample from existing embeddings
print(f"Fetching embeddings for channel {CHANNEL_ID}...")
resp = supabase.table('embeddings').select('metadata').eq('channel_id', CHANNEL_ID).limit(30).execute()

if not resp.data:
    print("ERROR: No embeddings found for this channel. Has it been processed?")
    sys.exit(1)

text_sample = " ".join([
    row['metadata'].get('chunk_text', '') 
    for row in resp.data 
    if row.get('metadata') and row['metadata'].get('chunk_text')
])

if not text_sample.strip():
    print("ERROR: No text content found in embeddings metadata.")
    sys.exit(1)

# Trim to ~10000 chars (same as tasks.py does)
text_sample = text_sample[:10000]
print(f"Got {len(text_sample)} chars of text sample.")

# 2. Extract speaking style
print(f"\n{'='*60}")
print(f"Using provider: {os.environ.get('STYLE_LLM_PROVIDER', os.environ.get('LLM_PROVIDER'))}")
print(f"Using model: {os.environ.get('STYLE_MODEL_NAME', os.environ.get('MODEL_NAME'))}")
print(f"{'='*60}\n")

print("--- Extracting SPEAKING STYLE ---")
speaking_style = extract_speaking_style(text_sample)
if speaking_style:
    print(f"\n✅ Speaking style extracted ({len(speaking_style)} chars)")
    print("-" * 40)
    print(speaking_style[:500] + "..." if len(speaking_style) > 500 else speaking_style)
    print("-" * 40)
else:
    print("❌ Failed to extract speaking style")

# 3. Extract creator soul
print("\n--- Extracting CREATOR SOUL ---")
creator_soul = extract_creator_soul(text_sample)
if creator_soul:
    print(f"\n✅ Creator soul extracted ({len(creator_soul)} chars)")
    print("-" * 40)
    print(creator_soul[:500] + "..." if len(creator_soul) > 500 else creator_soul)
    print("-" * 40)
else:
    print("❌ Failed to extract creator soul")

# 4. Update database
if speaking_style or creator_soul:
    update_data = {}
    if speaking_style:
        update_data['speaking_style'] = speaking_style
    if creator_soul:
        update_data['creator_soul'] = creator_soul
    
    supabase.table('channels').update(update_data).eq('id', CHANNEL_ID).execute()
    print(f"\n🎉 Database updated for channel {CHANNEL_ID}!")
    print(f"   - speaking_style: {'Updated' if speaking_style else 'Skipped'}")
    print(f"   - creator_soul: {'Updated' if creator_soul else 'Skipped'}")
else:
    print("\n❌ Nothing to update — both extractions failed.")
