# In utils/youtube_utils.py

import os
import re
import logging
import time
from datetime import datetime
from urllib.parse import urlparse, urlunparse
import concurrent.futures
import isodate
from typing import List, Dict, Optional, Union, Tuple
from googleapiclient.discovery import build
from dotenv import load_dotenv
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound
import yt_dlp

# --- Setup ---
load_dotenv()
log = logging.getLogger(__name__)
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
youtube_api = None
if YOUTUBE_API_KEY:
    try:
        youtube_api = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
        print("Successfully initialized YouTube Data API client.")
    except Exception as e:
        log.error(f"Failed to initialize YouTube API client: {e}")
else:
    log.warning("YOUTUBE_API_KEY not found. API-related functions will not work.")

# ==================================================================
# SECTION 1: CORE HELPER FUNCTIONS
# ==================================================================

def _get_transcript_with_fallback_clients(video_id: str) -> Optional[str]:
    """
    Uses yt-dlp with alternative clients (Android, iOS) to fetch transcripts.
    This often bypasses YouTube's PO token requirement for the web client.
    """
    import json
    import urllib.request
    
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    
    # Try different client configurations
    # Android and iOS clients often don't enforce the same PO token checks as web
    client_configs = [
        {'player_client': ['android', 'web']},
        {'player_client': ['ios', 'web']},
        {'player_client': ['mweb', 'web']},
        {'player_client': ['tv']},
    ]
    
    for config in client_configs:
        try:
            ydl_opts = {
                'skip_download': True,
                'writesubtitles': True,
                'writeautomaticsub': True,
                'subtitleslangs': ['en', 'en-US', 'hi', 'all'],
                'quiet': True,
                'no_warnings': True,
                'extractor_args': {'youtube': config},
            }
            
            client_name = config.get('player_client', ['unknown'])[0]
            print(f"[{video_id}] Trying yt-dlp with {client_name} client...")
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                
                subtitles = info.get('subtitles', {})
                automatic_captions = info.get('automatic_captions', {})
                
                if subtitles or automatic_captions:
                    print(f"[{video_id}] Found captions using {client_name} client!")
                    
                    # Prefer English, then any language
                    preferred_langs = ['en', 'en-US', 'en-GB', 'hi', 'hi-IN']
                    
                    # Try manual subtitles first
                    for lang in preferred_langs:
                        if lang in subtitles:
                            sub_data = subtitles[lang]
                            text = _extract_subtitle_text_simple(sub_data)
                            if text:
                                print(f"[{video_id}] ✅ Got manual subtitles ({lang}) via {client_name}")
                                return text
                    
                    # Try auto-generated
                    for lang in preferred_langs:
                        if lang in automatic_captions:
                            auto_data = automatic_captions[lang]
                            text = _extract_subtitle_text_simple(auto_data)
                            if text:
                                print(f"[{video_id}] ✅ Got auto-captions ({lang}) via {client_name}")
                                return text
                    
                    # Try any available
                    for lang, sub_data in subtitles.items():
                        text = _extract_subtitle_text_simple(sub_data)
                        if text:
                            print(f"[{video_id}] ✅ Got subtitles ({lang}) via {client_name}")
                            return text
                    
                    for lang, auto_data in automatic_captions.items():
                        text = _extract_subtitle_text_simple(auto_data)
                        if text:
                            print(f"[{video_id}] ✅ Got auto-captions ({lang}) via {client_name}")
                            return text
                            
        except Exception as e:
            log.debug(f"[{video_id}] {client_name} client failed: {e}")
            continue
    
    return None


def _extract_subtitle_text_simple(subtitle_data: List[Dict]) -> Optional[str]:
    """
    Simple helper to extract text from yt-dlp subtitle data.
    Uses requests and handles errors robustly.
    """
    import requests
    import json
    
    for sub in subtitle_data:
        try:
            sub_url = sub.get('url')
            if not sub_url:
                continue
                
            # Prefer json3 format
            if 'json3' in sub_url or sub.get('ext') == 'json3':
                try:
                    response = requests.get(sub_url, timeout=10)
                    response.raise_for_status()
                    
                    content = response.json()
                    events = content.get('events', [])
                    text_parts = []
                    for event in events:
                        if 'segs' in event:
                            for seg in event['segs']:
                                text = seg.get('utf8', '').strip()
                                if text and text != '\n':
                                    text_parts.append(text)
                    if text_parts:
                        return ' '.join(text_parts)
                except Exception as e:
                    log.debug(f"Failed to fetch/parse content from {sub_url}: {e}")
                    continue
        except Exception as e:
            log.debug(f"Failed to extract subtitle: {e}")
            continue
    
    return None


def get_transcript(video_id: str) -> Optional[str]:
    """
    Fetches a transcript for a given video_id using a three-step process:
    1. youtube_transcript_api (fast, primary)
    2. yt-dlp subtitle extraction (slower, fallback)
    3. yt-dlp with Android/iOS clients (bypasses PO token & IP blocks)
    
    Includes retry logic with exponential backoff for rate limiting.
    """
    # Expanded language list to cover all common variants
    PREFERRED_LANGUAGES = [
        'en', 'en-US', 'en-GB', 'en-AU', 'en-CA', 'en-IN',  # English variants
        'hi', 'hi-IN',  # Hindi variants
        'a.en', 'a.hi',  # Auto-generated prefixes sometimes used
    ]
    
    def _fetch_with_retry(transcript_obj, video_id, max_retries=3):
        """Helper to fetch transcript with exponential backoff retry."""
        for attempt in range(max_retries):
            try:
                fetched = transcript_obj.fetch()
                return "\n".join([segment['text'] if isinstance(segment, dict) else segment.text for segment in fetched])
            except Exception as e:
                error_str = str(e).lower()
                if 'too many requests' in error_str or 'blocked' in error_str or '429' in error_str:
                    wait_time = (2 ** attempt) * 2  # 2, 4, 8 seconds
                    log.warning(f"[{video_id}] Rate limited on attempt {attempt + 1}, waiting {wait_time}s...")
                    time.sleep(wait_time)
                    if attempt == max_retries - 1:
                        raise  # Re-raise on final attempt
                else:
                    raise  # Non-rate-limit errors should fail immediately
        return None
    
    # --- METHOD 1: YOUTUBE TRANSCRIPT API (Primary) ---
    try:
        # Instantiate the API client (required for newer versions)
        ytt_api = YouTubeTranscriptApi()
        transcript_list = ytt_api.list(video_id)
        
        # First, list all available transcripts for debugging
        available_transcripts = []
        try:
            for transcript in transcript_list:
                available_transcripts.append({
                    'language': transcript.language,
                    'language_code': transcript.language_code,
                    'is_generated': transcript.is_generated,
                    'is_translatable': transcript.is_translatable
                })
            print(f"[{video_id}] Available transcripts: {available_transcripts}")
        except Exception:
            pass
        
        # Try to get manual transcripts first (more accurate)
        try:
            transcript = transcript_list.find_manually_created_transcript(PREFERRED_LANGUAGES)
            result = _fetch_with_retry(transcript, video_id)
            if result:
                print(f"[{video_id}] ✅ Got manual transcript ({transcript.language_code})")
                return result
        except Exception as e:
            log.debug(f"[{video_id}] No manual transcript in preferred languages: {e}")
        
        # Fall back to auto-generated transcripts
        try:
            transcript = transcript_list.find_generated_transcript(PREFERRED_LANGUAGES)
            result = _fetch_with_retry(transcript, video_id)
            if result:
                print(f"[{video_id}] ✅ Got auto-generated transcript ({transcript.language_code})")
                return result
        except Exception as e:
            log.debug(f"[{video_id}] No generated transcript in preferred languages: {e}")
        
        # Last resort: try to get ANY available transcript and translate if possible
        try:
            for transcript in transcript_list:
                # If it's translatable, translate to English
                if transcript.is_translatable:
                    try:
                        translated = transcript.translate('en')
                        result = _fetch_with_retry(translated, video_id)
                        if result:
                            print(f"[{video_id}] ✅ Got translated transcript ({transcript.language_code} -> en)")
                            return result
                    except Exception:
                        pass
                # Otherwise just use whatever is available
                try:
                    result = _fetch_with_retry(transcript, video_id)
                    if result:
                        print(f"[{video_id}] ✅ Got transcript in {transcript.language_code}")
                        return result
                except Exception:
                    continue
        except Exception as e:
            log.debug(f"[{video_id}] Could not get any transcript: {e}")
                
    except (TranscriptsDisabled, NoTranscriptFound) as e:
        log.debug(f"[{video_id}] No transcript available via API: {e}")
    except Exception as e:
        log.debug(f"[{video_id}] YouTubeTranscriptApi method failed: {e}")
    
    # --- METHOD 2: YT-DLP (Fallback with rate limiting protection) ---
    print(f'Using method 2 yt-dlp for {video_id}')
    try:
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        
        # Expanded language list for yt-dlp - request all language variants
        subtitle_langs = ['en', 'en-US', 'en-GB', 'en-AU', 'en-CA', 'en-IN', 'hi', 'hi-IN', 'all']
        
        ydl_opts = {
            'skip_download': True,
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': subtitle_langs,
            'quiet': True,
            'no_warnings': True,
            'sleep_interval': 1,  # Add delay between requests
            'max_sleep_interval': 3,  # Maximum delay
            'extractor_retries': 3,  # Retry on failures
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            
            # Try to get subtitles from the info
            subtitles = info.get('subtitles', {})
            automatic_captions = info.get('automatic_captions', {})
            
            # Debug: Log what captions are available
            if subtitles:
                log.debug(f"[{video_id}] Manual subtitles available in: {list(subtitles.keys())}")
            if automatic_captions:
                log.debug(f"[{video_id}] Auto-captions available in: {list(automatic_captions.keys())}")
            
            if not subtitles and not automatic_captions:
                print(f"[{video_id}] yt-dlp found NO subtitles or auto-captions at all")
            
            # Expanded preferred language list
            preferred_langs = ['en', 'en-US', 'en-GB', 'en-AU', 'en-CA', 'en-IN', 'en-orig', 'hi', 'hi-IN']
            
            # Prefer manual subtitles over automatic
            transcript_lines = []
            
            # First try: manual subtitles in preferred languages
            for lang in preferred_langs:
                if lang in subtitles:
                    sub_data = subtitles[lang]
                    transcript_lines = _extract_subtitle_text(sub_data)
                    if transcript_lines:
                        print(f"[{video_id}] Found manual subtitles in: {lang}")
                        return '\n'.join(transcript_lines)
            
            # Second try: automatic captions in preferred languages
            for lang in preferred_langs:
                if lang in automatic_captions:
                    auto_data = automatic_captions[lang]
                    transcript_lines = _extract_subtitle_text(auto_data)
                    if transcript_lines:
                        print(f"[{video_id}] Found auto-captions in: {lang}")
                        return '\n'.join(transcript_lines)
            
            # Third try: ANY available manual subtitle
            for lang, sub_data in subtitles.items():
                transcript_lines = _extract_subtitle_text(sub_data)
                if transcript_lines:
                    print(f"[{video_id}] Using manual subtitles in: {lang}")
                    return '\n'.join(transcript_lines)
            
            # Fourth try: ANY available auto-caption
            for lang, auto_data in automatic_captions.items():
                transcript_lines = _extract_subtitle_text(auto_data)
                if transcript_lines:
                    print(f"[{video_id}] Using auto-captions in: {lang}")
                    return '\n'.join(transcript_lines)
            
            raise ValueError("No subtitles found via yt-dlp (checked all available languages)")
                
    except Exception as e:
        log.warning(f"[{video_id}] Methods 1 & 2 failed: {e}")
    
    # --- METHOD 3: CLIENT FALLBACK (Android/iOS bypasses PO token) ---
    print(f"[{video_id}] Using method 3: yt-dlp with alternative clients...")
    try:
        fallback_result = _get_transcript_with_fallback_clients(video_id)
        if fallback_result:
            return fallback_result
    except Exception as e:
        log.error(f"[{video_id}] Client fallback method also failed: {e}")
    
    log.error(f"[{video_id}] All transcript methods failed")
    return None

def _extract_subtitle_text(subtitle_data: List[Dict]) -> List[str]:
    """
    Helper function to extract text from yt-dlp subtitle data.
    Handles different subtitle formats safely.
    """
    import json
    import urllib.request
    
    lines = []
    for sub in subtitle_data:
        try:
            # Prefer json3 format
            if sub.get('ext') == 'json3':
                sub_url = sub.get('url')
                if sub_url:
                    # Add timeout and error handling for rate limiting
                    time.sleep(0.5)  # Small delay to avoid rate limiting
                    request = urllib.request.Request(
                        sub_url,
                        headers={'User-Agent': 'Mozilla/5.0'}
                    )
                    with urllib.request.urlopen(request, timeout=10) as response:
                        sub_content = json.loads(response.read().decode('utf-8'))
                        events = sub_content.get('events', [])
                        for event in events:
                            if 'segs' in event:
                                text = ''.join([seg.get('utf8', '') for seg in event['segs']])
                                if text.strip():
                                    lines.append(text.strip())
                    if lines:
                        break
            # Try other formats like srv3, vtt, etc.
            elif sub.get('ext') in ['srv3', 'vtt', 'ttml']:
                sub_url = sub.get('url')
                if sub_url:
                    time.sleep(0.5)
                    request = urllib.request.Request(
                        sub_url,
                        headers={'User-Agent': 'Mozilla/5.0'}
                    )
                    with urllib.request.urlopen(request, timeout=10) as response:
                        content = response.read().decode('utf-8')
                        # Simple text extraction (you might want to use a proper parser)
                        for line in content.split('\n'):
                            line = line.strip()
                            if line and not line.startswith('<') and '-->' not in line and not line.isdigit():
                                lines.append(line)
                    if lines:
                        break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                log.warning(f"Rate limited (429) while fetching subtitles. Backing off...")
                time.sleep(5)  # Wait longer on rate limit
            continue
        except Exception as e:
            log.debug(f"Error extracting subtitle format {sub.get('ext')}: {e}")
            continue
    
    return lines

def _fetch_transcript_worker(info_dict: Dict) -> Optional[Dict]:
    """
    Internal worker function for fetching a single transcript in a parallel thread.
    """
    video_id = info_dict.get('id')
    snippet = info_dict.get('snippet', {})
    content_details = info_dict.get('contentDetails', {})
    try:
        transcript_text = get_transcript(video_id)
        if not transcript_text:
            return None # Skip videos where no transcript could be found
        duration_iso = content_details.get('duration', 'PT0S')
        duration_seconds = isodate.parse_duration(duration_iso).total_seconds()
        video_data = {
            'video_id': video_id,
            'url': f"https://www.youtube.com/watch?v={video_id}",
            'title': snippet.get('title'),
            'uploader': snippet.get('channelTitle'),
            'description': snippet.get('description'),
            'duration': duration_seconds,
            'upload_date': snippet.get('publishedAt', '').split('T')[0],
            'transcript': transcript_text
        }
        print(f"✅ Successfully processed transcript for: {video_data['title'][:50]}...")
        return video_data
    except Exception as e:
        log.error(f"❌ Worker failed for video ID {video_id}: {e}", exc_info=False)
        return None

# ==================================================================
# SECTION 2: ROBUST CHANNEL & URL PROCESSING FUNCTIONS
# ==================================================================

def get_transcripts_from_channel(
    youtube_api_client,
    channel_url: str,
    target_video_count: int = 50,
    min_duration_seconds: int = 61,
    max_videos_to_scan: int = 500,
    progress_callback = None
) -> Tuple[List[Dict], str, int, List[Dict]]:
    """
    Intelligently finds and processes videos from a channel.
    NOW RETURNS: (successful_transcripts, thumbnail, subs_count, failed_long_form_videos)
    """
    start_time = time.perf_counter()

    # --- Step 1: Find Channel ID and Details ---
    try:
        if progress_callback: progress_callback("Locating channel details...")
        print(f"--- Step 1: Getting channel details for {channel_url} ---")
        match = re.search(r'(?:channel/|c/|@|user/)([^/?\s]+)', channel_url)
        if not match: raise ValueError("Could not parse a channel identifier from URL.")
        identifier = match.group(1)

        search_response = youtube_api_client.search().list(q=identifier, type='channel', part='id', maxResults=1).execute()
        if not search_response.get('items'): raise ValueError(f"Search API could not find a channel for identifier: {identifier}")
        channel_id = search_response['items'][0]['id']['channelId']

        channel_response = youtube_api_client.channels().list(part="snippet,contentDetails,statistics", id=channel_id).execute()
        if not channel_response.get('items'): raise ValueError(f"Could not find channel details for ID: {channel_id}")

        channel_item = channel_response['items'][0]
        uploads_playlist_id = channel_item['contentDetails']['relatedPlaylists']['uploads']
        subscriber_count = int(channel_item['statistics'].get('subscriberCount', 0))
        channel_thumbnail = channel_item['snippet']['thumbnails']['high']['url']
        print(f"Found Channel: {channel_item['snippet']['title']} ({subscriber_count} subscribers)")
    except Exception as e:
        log.error(f"Failed to get channel details: {e}", exc_info=True)
        return [], '', 0, []

    # --- Step 2: Scan for long-form videos ---
    long_form_metadata = []
    next_page_token = None
    videos_scanned = 0
    print(f"\n--- Step 2: Scanning for {target_video_count} videos longer than {min_duration_seconds}s (max scan: {max_videos_to_scan}) ---")
    
    if progress_callback: progress_callback("Scanning channel for long-form videos...")

    while len(long_form_metadata) < target_video_count and videos_scanned < max_videos_to_scan:
        playlist_response = youtube_api_client.playlistItems().list(
            part="contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=50,
            pageToken=next_page_token
        ).execute()

        video_ids_chunk = [item['contentDetails']['videoId'] for item in playlist_response.get('items', [])]
        if not video_ids_chunk: break

        videos_scanned += len(video_ids_chunk)

        video_details_response = youtube_api_client.videos().list(
            part="snippet,contentDetails",
            id=",".join(video_ids_chunk)
        ).execute()
        
        scanned_items = video_details_response.get('items', [])

        for video_meta in scanned_items:
            duration_iso = video_meta.get('contentDetails', {}).get('duration', 'PT0S')
            try:
                duration_seconds = isodate.parse_duration(duration_iso).total_seconds()
                if duration_seconds > min_duration_seconds:
                    long_form_metadata.append(video_meta)
                    if len(long_form_metadata) >= target_video_count:
                        break
            except isodate.ISO8601Error:
                continue

        print(f"\rScanned: {videos_scanned} videos | Found: {len(long_form_metadata)} long-form videos", end="")
        if progress_callback:
             progress_callback(f"Scanning... ({len(long_form_metadata)} videos found so far)")
             
        if len(long_form_metadata) >= target_video_count: break

        next_page_token = playlist_response.get('nextPageToken')
        if not next_page_token: break

    print(f"\nScan complete. Found {len(long_form_metadata)} long-form videos to process.")

    if not long_form_metadata:
        return [], channel_thumbnail, subscriber_count, []

    # --- Step 3: Fetch transcripts sequentially with delays to avoid rate limiting ---
    print(f"\n--- Step 3: Fetching transcripts for {len(long_form_metadata)} videos (staggered to avoid rate limits) ---")
    if progress_callback: progress_callback(f"Downloading transcripts for {len(long_form_metadata)} videos...")
    
    final_results = []
    # Process in smaller batches with delays to avoid YouTube rate limiting
    batch_size = 2  # Process 2 videos at a time
    for batch_start in range(0, len(long_form_metadata), batch_size):
        batch = long_form_metadata[batch_start:batch_start + batch_size]
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future_to_video = {executor.submit(_fetch_transcript_worker, meta): meta for meta in batch}
            for future in concurrent.futures.as_completed(future_to_video):
                result = future.result()
                if result:
                    final_results.append(result)
        
        completed_count = batch_start + len(batch)
        if progress_callback:
            progress_callback(f"Downloading transcripts: {completed_count}/{len(long_form_metadata)} videos processed")
        
        # Add delay between batches to avoid rate limiting
        if batch_start + batch_size < len(long_form_metadata):
            time.sleep(1.5)  # Wait 1.5 seconds between batches

    # --- Accurately calculate which long-form videos failed transcription ---
    successful_video_ids = {res['video_id'] for res in final_results}
    failed_long_form_videos = []
    for video_meta in long_form_metadata:
        if video_meta.get('id') not in successful_video_ids:
            failed_long_form_videos.append({
                'title': video_meta.get('snippet', {}).get('title', 'Unknown Title')
            })

    duration = time.perf_counter() - start_time
    print(f"\n[PERFORMANCE] Completed entire process in {duration:.2f} seconds.")

    return final_results, channel_thumbnail, subscriber_count, failed_long_form_videos

def get_transcripts_from_urls(
    youtube_api_client,
    video_urls: List[str],
    min_duration_seconds: int = 61
) -> List[Dict]:
    """
    Processes a specific list of video URLs, filters them for long-form content,
    and fetches their transcripts. Ideal for sync tasks.
    """
    print(f"--- Processing {len(video_urls)} URLs to find long-form content ---")
    video_ids = [match.group(1) for url in video_urls if (match := re.search(r"v=([a-zA-Z0-9_-]+)", url))]
    if not video_ids:
        return []

    # --- Step 1: Get metadata for all videos at once ---
    video_metadata_list = []
    for i in range(0, len(video_ids), 50):
        chunk_ids = video_ids[i:i + 50]
        try:
            response = youtube_api_client.videos().list(
                part="snippet,contentDetails",
                id=",".join(chunk_ids)
            ).execute()
            video_metadata_list.extend(response.get('items', []))
        except Exception as e:
            log.error(f"API error fetching video details for a chunk: {e}")

    # --- Step 2: Filter for long-form content ---
    long_form_metadata = []
    for video_meta in video_metadata_list:
        duration_iso = video_meta.get('contentDetails', {}).get('duration', 'PT0S')
        try:
            if isodate.parse_duration(duration_iso).total_seconds() > min_duration_seconds:
                long_form_metadata.append(video_meta)
        except isodate.ISO8601Error:
            continue
    
    print(f"Found {len(long_form_metadata)} new long-form videos to process.")
    if not long_form_metadata:
        return []

    # --- Step 3: Fetch transcripts in parallel (reduced workers) ---
    final_results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        future_to_video = {executor.submit(_fetch_transcript_worker, meta): meta for meta in long_form_metadata}
        for future in concurrent.futures.as_completed(future_to_video):
            result = future.result()
            if result:
                final_results.append(result)
            time.sleep(0.2)
    
    return final_results

# ==================================================================
# SECTION 3: OTHER UTILITY FUNCTIONS
# ==================================================================
def is_youtube_video_url(url: str) -> bool:
    """Checks if a URL is a valid YouTube video URL."""
    video_pattern = r'(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]+)'
    return re.match(video_pattern, url) is not None

def clean_youtube_url(url):
    """Removes tracking parameters from a YouTube URL for consistency."""
    parsed = urlparse(url)
    clean_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '',''))
    return clean_url

def get_channel_details_by_url(channel_url: str):
    """
    [CORRECTED] Fetches channel details using a URL, with a robust fallback.
    This version correctly identifies the URL type and uses the appropriate API parameter.
    """
    if not youtube_api:
        raise ConnectionError("YouTube API client is not initialized. Check your API key.")

    channel_identifier = None
    api_param = {}

    if '/channel/' in channel_url:
        match = re.search(r'/channel/([^/?&]+)', channel_url)
        if match:
            channel_identifier = match.group(1)
            api_param = {'id': channel_identifier}
            print(f"Identified Channel ID: {channel_identifier}")
    elif '/@' in channel_url:
        match = re.search(r'/@([^/?&]+)', channel_url)
        if match:
            channel_identifier = match.group(1)
            api_param = {'forHandle': channel_identifier}
            print(f"Identified Channel Handle: {channel_identifier}")
    elif '/c/' in channel_url or '/user/' in channel_url:
        match = re.search(r'/(?:c|user)/([^/?&]+)', channel_url)
        if match:
            channel_identifier = match.group(1)
            api_param = {'forUsername': channel_identifier}
            print(f"Identified legacy custom URL: {channel_identifier}")

    if not channel_identifier:
        raise ValueError("Could not extract a valid channel ID or name from the URL.")

    response = None
    try:
        print(f"Attempting direct API lookup with parameter: {api_param}")
        request = youtube_api.channels().list(
            part="snippet,contentDetails,statistics",
            **api_param
        )
        response = request.execute()
    except Exception as e:
        log.warning(f"Direct API lookup failed with an exception: {e}")

    if response and response.get('items'):
        print("Direct API lookup successful.")
        return response['items'][0]

    log.warning(f"Direct lookup for '{channel_identifier}' failed. Trying search as a fallback...")
    search_request = youtube_api.search().list(
        part="snippet",
        q=channel_identifier,
        type="channel",
        maxResults=1
    )
    search_response = search_request.execute()
    if not search_response.get('items'):
        raise ValueError(f"YouTube channel not found for identifier via search: {channel_identifier}")

    channel_id_from_search = search_response['items'][0]['snippet']['channelId']
    details_request = youtube_api.channels().list(
        part="snippet,contentDetails,statistics",
        id=channel_id_from_search
    )
    details_response = details_request.execute()
    return details_response['items'][0]

def get_channel_url_from_video_url(video_url: str) -> Optional[str]:
    """
    Finds the parent channel URL from a given YouTube video URL.
    """
    if not youtube_api:
        log.error("YouTube API client not initialized, cannot fetch video details.")
        return None

    video_id_match = (
        re.search(r"v=([a-zA-Z0-9_-]{11})", video_url) or
        re.search(r"youtu\.be/([a-zA-Z0-9_-]{11})", video_url)
    )
    
    if not video_id_match:
        return None

    video_id = video_id_match.group(1)
    
    try:
        request = youtube_api.videos().list(part="snippet", id=video_id)
        response = request.execute()

        if response.get("items"):
            channel_id = response["items"][0]["snippet"]["channelId"]
            log.info(f"Detected channel ID {channel_id} from video ID {video_id}.")
            return f"https://www.youtube.com/channel/{channel_id}"
        else:
            log.warning(f"Could not find video details for video ID: {video_id}")
            return None
            
    except Exception as e:
        log.error(f"API error while fetching channel from video {video_id}: {e}")
        return None

def is_youtube_channel_url(url: str) -> bool:
    """
    Validates if a URL is a valid YouTube channel URL.
    Handles /@handle, /channel/ID, /c/legacy, and /user/legacy formats.
    """
    if not isinstance(url, str):
        return False
    channel_pattern = r'^(https?://)?(www\.)?(youtube\.com|youtu\.be)/(channel/UC[\w-]{21}[AQgw]|@[\w.-]+|c/[\w.-]+|user/[\w.-]+)/?$'
    return re.match(channel_pattern, url) is not None