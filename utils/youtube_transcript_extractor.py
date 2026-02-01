"""
YouTube Transcript Extraction using yt-dlp (FASTER & MORE RELIABLE)
"""

import yt_dlp
import json
import re
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os

app = Flask(__name__, static_folder='.')
CORS(app)

# ============================================================
# Method 1: Using yt-dlp to extract subtitles
# ============================================================

def get_transcript_ytdlp(video_url):
    """
    Extract transcript using yt-dlp (most reliable method)
    """
    ydl_opts = {
        'skip_download': True,  # Don't download video
        'writesubtitles': True,  # Get subtitles
        'writeautomaticsub': True,  # Get auto-generated subs
        'subtitleslangs': ['en'],  # Preferred languages
        'quiet': True,
        'no_warnings': True,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            
            # Get subtitles
            subtitles = info.get('subtitles', {})
            automatic_captions = info.get('automatic_captions', {})
            
            # Prefer manual subtitles, fallback to auto-generated
            all_subs = subtitles or automatic_captions
            
            if not all_subs:
                return {
                    'success': False,
                    'error': 'No subtitles available for this video'
                }
            
            # Get English subtitles (or first available language)
            lang = 'en' if 'en' in all_subs else list(all_subs.keys())[0]
            sub_list = all_subs[lang]
            
            # Find json3 format (contains text and timestamps)
            json3_sub = next((s for s in sub_list if s.get('ext') == 'json3'), None)
            
            if json3_sub:
                # Download subtitle content
                sub_url = json3_sub['url']
                with yt_dlp.YoutubeDL({'quiet': True}) as ydl2:
                    sub_data = ydl2.urlopen(sub_url).read().decode('utf-8')
                    sub_json = json.loads(sub_data)
                    
                    # Parse json3 format
                    transcript = []
                    for event in sub_json.get('events', []):
                        if 'segs' in event:
                            text = ''.join(seg.get('utf8', '') for seg in event['segs'])
                            if text.strip():
                                transcript.append({
                                    'text': text.strip(),
                                    'start': event.get('tStartMs', 0) / 1000,
                                    'duration': event.get('dDurationMs', 0) / 1000
                                })
                    
                    return {
                        'success': True,
                        'transcript': transcript,
                        'video_id': info.get('id'),
                        'title': info.get('title'),
                        'duration': info.get('duration'),
                        'language': lang
                    }
            
            return {
                'success': False,
                'error': 'Subtitle format not supported'
            }
            
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


# ============================================================
# Method 2: Simpler - Get plain text transcript
# ============================================================

def get_transcript_simple(video_url):
    """
    Simpler method - just get the text without precise timestamps
    """
    ydl_opts = {
        'skip_download': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitlesformat': 'srv3',  # Get SRV3 format (easier to parse)
        'subtitleslangs': ['en'],
        'quiet': True,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            
            # Get requested subtitles
            requested_subs = info.get('requested_subtitles', {})
            
            if not requested_subs:
                return None
            
            # Get English subtitle
            en_sub = requested_subs.get('en')
            if en_sub:
                sub_url = en_sub['url']
                with yt_dlp.YoutubeDL({'quiet': True}) as ydl2:
                    sub_data = ydl2.urlopen(sub_url).read().decode('utf-8')
                    
                    # Parse XML/SRV3 format
                    import xml.etree.ElementTree as ET
                    root = ET.fromstring(sub_data)
                    
                    transcript = []
                    for text_elem in root.findall('.//text'):
                        start = float(text_elem.get('start', 0))
                        dur = float(text_elem.get('dur', 0))
                        text = text_elem.text or ''
                        
                        if text.strip():
                            transcript.append({
                                'text': text.strip(),
                                'start': start,
                                'duration': dur
                            })
                    
                    return transcript
            
            return None
            
    except Exception as e:
        print(f"Error: {e}")
        return None


# ============================================================
# Flask API Routes
# ============================================================

def extract_video_id(url):
    patterns = [
        r'(?:youtube\.com\/watch\?v=|youtu\.be\/)([^&\n?#]+)',
        r'youtube\.com\/embed\/([^&\n?#]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return url.strip()

@app.route('/')
def index():
    if os.path.exists('index.html'):
        return send_from_directory('.', 'index.html')
    return '<h1>yt-dlp Transcript API</h1>'

@app.route('/api/transcript', methods=['GET'])
def get_transcript():
    video_url = request.args.get('video_url')
    method = request.args.get('method', 'ytdlp')  # 'ytdlp' or 'simple'
    format_type = request.args.get('format', 'json')
    include_timestamps = request.args.get('include_timestamps', 'true').lower() == 'true'
    
    if not video_url:
        return jsonify({'error': 'video_url parameter required', 'success': False}), 400
    
    # Use yt-dlp method
    result = get_transcript_ytdlp(video_url)
    
    if not result['success']:
        return jsonify(result), 404
    
    transcript = result['transcript']
    
    if format_type == 'text':
        if include_timestamps:
            text = '\n'.join([f"[{entry['start']:.2f}s] {entry['text']}" for entry in transcript])
        else:
            text = ' '.join([entry['text'] for entry in transcript])
        return text, 200, {'Content-Type': 'text/plain'}
    else:
        return jsonify(result)

@app.route('/api/video-info', methods=['GET'])
def get_video_info():
    """Get video metadata"""
    video_url = request.args.get('video_url')
    
    if not video_url:
        return jsonify({'error': 'video_url required'}), 400
    
    try:
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(video_url, download=False)
            
            return jsonify({
                'success': True,
                'title': info.get('title'),
                'duration': info.get('duration'),
                'views': info.get('view_count'),
                'uploader': info.get('uploader'),
                'upload_date': info.get('upload_date'),
                'description': info.get('description', '')[:500],  # First 500 chars
                'thumbnail': info.get('thumbnail'),
                'available_languages': list(info.get('subtitles', {}).keys()) + 
                                      list(info.get('automatic_captions', {}).keys())
            })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'method': 'yt-dlp'})

if __name__ == '__main__':
    app.run(debug=True, port=5000)


# ============================================================
# Standalone Usage Examples
# ============================================================

if __name__ == "__main__":
    # Example 1: Get transcript with yt-dlp
    print("Testing yt-dlp method...")
    result = get_transcript_ytdlp('https://www.youtube.com/watch?v=dQw4w9WgXcQ')
    
    if result['success']:
        print(f"✅ Success! Found {len(result['transcript'])} segments")
        print(f"Title: {result['title']}")
        print(f"First 3 lines:")
        for entry in result['transcript'][:3]:
            print(f"  [{entry['start']:.2f}s] {entry['text']}")
    else:
        print(f"❌ Error: {result['error']}")