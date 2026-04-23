"""
Stratified Soul & Style Extraction — analyzes as many videos as possible.

WHY the old approach was wrong:
  - limit(30) fetched 30 embedding ROWS, not 30 videos
  - Those 30 rows could all be from a single video (whichever was indexed first)
  - The creator's true identity is built across their ENTIRE body of work,
    not one early video

THIS approach:
  1. Loads ALL video IDs from the channel record (free — already in DB)
  2. Randomly samples up to MAX_VIDEOS for diversity
  3. For each video: fetches the opening chunks (chunk_index 0-2)
     — Openings are the most personality-dense: intros, catchphrases, energy
  4. Shuffles video order so the LLM sees voice across different topics/moods
  5. Uses an 80K char budget (8x larger than before) — modern LLMs handle this fine

Result: a soul/style profile that represents the creator's TRUE identity,
not just one video's content.
"""
import os
import sys
import random
from dotenv import load_dotenv
load_dotenv()

from utils.supabase_client import get_supabase_admin_client
from utils.qa_utils import extract_speaking_style, extract_creator_soul

# ── CONFIG ────────────────────────────────────────────────────────────────────
CHANNEL_ID = 2  # Change this to the channel you want to regenerate

# Max videos to sample. More = richer profile, but more DB queries + LLM tokens.
# 60 videos × ~3 chunks × ~800 chars ≈ 144K chars. We trim to MAX_CHARS anyway.
MAX_VIDEOS = 60

# Char budget for the LLM extraction prompt.
# 80K chars ≈ ~20K tokens. Well within Gemini/Claude/GPT-4 context windows.
# Groq fast models handle 32K tokens — still 6x larger than the old 10K chars.
MAX_CHARS = 80_000

# How many chunks to fetch per video (first N by chunk_index)
CHUNKS_PER_VIDEO = 3
# ─────────────────────────────────────────────────────────────────────────────

supabase = get_supabase_admin_client()

# ── Step 1: Get full video list from channel (single query) ───────────────────
print(f"\n{'='*60}")
print(f"Loading video list for channel_id={CHANNEL_ID}...")

channel_resp = supabase.table('channels') \
    .select('channel_name, videos') \
    .eq('id', CHANNEL_ID) \
    .single() \
    .execute()

if not channel_resp.data:
    print(f"ERROR: Channel {CHANNEL_ID} not found in database.")
    sys.exit(1)

channel_name = channel_resp.data.get('channel_name', f'Channel {CHANNEL_ID}')
all_videos   = channel_resp.data.get('videos') or []
all_video_ids = [v['video_id'] for v in all_videos if v.get('video_id')]

print(f"Channel    : {channel_name}")
print(f"Total videos in channel: {len(all_video_ids)}")

if not all_video_ids:
    print("ERROR: No videos found in channel record. Has the channel been processed?")
    sys.exit(1)

# ── Step 2: Stratified random sampling ───────────────────────────────────────
if len(all_video_ids) > MAX_VIDEOS:
    sampled_ids = random.sample(all_video_ids, MAX_VIDEOS)
    print(f"Randomly sampled {MAX_VIDEOS}/{len(all_video_ids)} videos for representativeness.")
    print("(Random sample ensures no single era or topic dominates the profile)")
else:
    sampled_ids = list(all_video_ids)
    print(f"Using all {len(sampled_ids)} videos (fewer than MAX_VIDEOS={MAX_VIDEOS}).")

# ── Step 3: Fetch opening chunks per video ────────────────────────────────────
# Opening chunks (chunk_index 0-2) are the most personality-dense:
#   - Intros and catchphrases appear here
#   - The creator's energy and speaking rhythm is set in the opening
#   - How they frame topics reveals their worldview
print(f"\nFetching opening chunks from {len(sampled_ids)} videos...")
print(f"Strategy: {CHUNKS_PER_VIDEO} chunks/video (openings) → shuffled → trimmed to {MAX_CHARS:,} chars\n")

text_parts = []
failed     = 0

for i, vid_id in enumerate(sampled_ids):
    try:
        resp = supabase.table('embeddings') \
            .select('metadata') \
            .eq('channel_id', CHANNEL_ID) \
            .eq('video_id', vid_id) \
            .order('metadata->>chunk_index') \
            .limit(CHUNKS_PER_VIDEO) \
            .execute()

        if not resp.data:
            failed += 1
            continue

        title       = resp.data[0]['metadata'].get('video_title', f'Video {i+1}')
        chunks_text = "\n".join(
            row['metadata'].get('chunk_text', '')
            for row in resp.data
            if row.get('metadata', {}).get('chunk_text')
        )

        if chunks_text.strip():
            text_parts.append(f"\n=== {title} ===\n{chunks_text}")

        if (i + 1) % 10 == 0 or (i + 1) == len(sampled_ids):
            print(f"  ✓ {i+1}/{len(sampled_ids)} videos processed...")

    except Exception as e:
        failed += 1
        print(f"  ✗ Failed for video {vid_id}: {e}")

print(f"\nSuccessfully retrieved text from {len(text_parts)} videos. ({failed} failed)")

if not text_parts:
    print("ERROR: No text content retrieved. Check embeddings table.")
    sys.exit(1)

# ── Step 4: Shuffle + combine + trim ─────────────────────────────────────────
# Shuffle so the LLM sees the creator across different topics/moods —
# not just the first N videos in chronological order.
random.shuffle(text_parts)

full_text    = "\n".join(text_parts)
original_len = len(full_text)

if original_len > MAX_CHARS:
    full_text = full_text[:MAX_CHARS]
    print(f"Trimmed: {original_len:,} → {MAX_CHARS:,} chars (budget cap)")
else:
    print(f"Total text: {original_len:,} chars (within budget, no trimming needed)")

print(f"Est. tokens: ~{len(full_text) // 4:,} tokens")

# ── Step 5: Run extractions ───────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"LLM Provider : {os.environ.get('STYLE_LLM_PROVIDER', os.environ.get('LLM_PROVIDER', 'Not Set'))}")
print(f"LLM Model    : {os.environ.get('STYLE_MODEL_NAME',  os.environ.get('MODEL_NAME',   'Not Set'))}")
print(f"{'='*60}\n")

print("── Extracting SPEAKING STYLE ───────────────────────────────")
speaking_style = extract_speaking_style(full_text)
if speaking_style:
    print(f"✅ Done ({len(speaking_style)} chars)")
    print(speaking_style[:600] + "\n..." if len(speaking_style) > 600 else speaking_style)
else:
    print("❌ Failed to extract speaking style")

print("\n── Extracting CREATOR SOUL ─────────────────────────────────")
creator_soul = extract_creator_soul(full_text)
if creator_soul:
    print(f"✅ Done ({len(creator_soul)} chars)")
    print(creator_soul[:600] + "\n..." if len(creator_soul) > 600 else creator_soul)
else:
    print("❌ Failed to extract creator soul")

# ── Step 6: Update database ───────────────────────────────────────────────────
if speaking_style or creator_soul:
    update_data = {}
    if speaking_style:
        update_data['speaking_style'] = speaking_style
    if creator_soul:
        update_data['creator_soul'] = creator_soul

    supabase.table('channels').update(update_data).eq('id', CHANNEL_ID).execute()

    print(f"\n{'='*60}")
    print(f"🎉 Database updated — Channel: {channel_name} (id={CHANNEL_ID})")
    print(f"   speaking_style : {'Updated ✅' if speaking_style else 'Skipped ❌'}")
    print(f"   creator_soul   : {'Updated ✅' if creator_soul else 'Skipped ❌'}")
    print(f"\n📊 Extraction stats:")
    print(f"   Videos in channel : {len(all_video_ids)}")
    print(f"   Videos sampled    : {len(text_parts)}")
    print(f"   Text analyzed     : {len(full_text):,} chars (~{len(full_text)//4:,} tokens)")
    print(f"{'='*60}")
else:
    print("\n❌ Nothing to update — both extractions failed.")
    print("   Check your LLM provider config in .env")
