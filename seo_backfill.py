"""
seo_backfill.py
---------------
One-off script to generate SEO metadata for all existing channels that
were created before the SEO automation was added.

Run from the project root:
    python seo_backfill.py [--dry-run]

Options:
    --dry-run   Print what would be written without touching the database.
    --force     Re-generate even for channels that already have seo_title set.
"""
import sys
import os
import time
from dotenv import load_dotenv

load_dotenv()

from utils.supabase_client import get_supabase_admin_client
from utils.seo_utils import generate_seo_metadata

DRY_RUN = '--dry-run' in sys.argv
FORCE   = '--force'   in sys.argv

def main():
    supabase = get_supabase_admin_client()

    query = supabase.table('channels') \
        .select('id, channel_name') \
        .eq('status', 'ready') \
        .not_.is_('channel_name', 'null')

    if not FORCE:
        # Only backfill channels that don't already have SEO metadata
        query = query.is_('seo_title', 'null')

    result = query.execute()
    channels = result.data or []

    if not channels:
        print("✅ All channels already have SEO metadata. Nothing to do.")
        return

    print(f"{'[DRY RUN] ' if DRY_RUN else ''}Found {len(channels)} channel(s) to backfill.\n")

    success = 0
    failed  = 0

    for ch in channels:
        channel_id   = ch['id']
        channel_name = ch['channel_name'].strip()

        print(f"  → [{channel_id}] {channel_name!r} ... ", end='', flush=True)
        try:
            seo = generate_seo_metadata(channel_name)

            if DRY_RUN:
                print("(dry-run)")
                print(f"       title: {seo['seo_title']!r}")
                print(f"       desc:  {seo['seo_meta_description']!r}")
                print(f"       h1:    {seo['seo_h1']!r}")
            else:
                supabase.table('channels').update({
                    'seo_title':            seo['seo_title'],
                    'seo_meta_description': seo['seo_meta_description'],
                    'seo_h1':               seo['seo_h1'],
                }).eq('id', channel_id).execute()
                print(f"✅  {seo['seo_title']!r}")

            success += 1

        except Exception as exc:
            print(f"❌  Error: {exc}")
            failed += 1

        # Polite delay — avoids hammering Google Autocomplete or the LLM API
        time.sleep(1.5)

    print(f"\nDone. {success} succeeded, {failed} failed.")

if __name__ == '__main__':
    main()
