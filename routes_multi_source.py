# Updated Multi-Source Channel Creation Routes
# Add these to app.py

# Required imports at the top of app.py (add if not present):
from werkzeug.utils import secure_filename
import os
from tasks_multi_source import process_whatsapp_source_task, process_website_source_task

# Configuration for file uploads
UPLOAD_FOLDER = 'uploads/whatsapp_chats'
ALLOWED_EXTENSIONS = {'txt'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Ensure upload directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    """Check if uploaded file has allowed extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/chatbot/create', methods=['POST'])
@login_required
def create_multi_source_chatbot():
    """
    Create a new chatbot with multiple data sources.
    Accepts YouTube URLs, WhatsApp file upload, and Website URLs.
    """
    try:
        if 'user' not in session:
            return jsonify({'status': 'error', 'message': 'Authentication required.'}), 401
        
        user_id = session['user']['id']
        active_community_id = session.get('active_community_id')
        user_status = get_user_status(user_id, active_community_id)
        
        if not user_status:
            return jsonify({'status': 'error', 'message': 'Could not verify user status.'}), 500
        
        # Get form data
        youtube_urls = request.form.getlist('youtube_urls[]')  # Multiple YouTube URLs
        website_url = request.form.get('website_url', '').strip()
        whatsapp_file = request.files.get('whatsapp_file')
        whatsapp_agent_name = request.form.get('whatsapp_agent_name', '').strip()  # Optional agent name
        chatbot_name = request.form.get('chatbot_name', '').strip()
        
        # Validate at least one source
        has_youtube = bool(youtube_urls and any(url.strip() for url in youtube_urls))
        has_website = bool(website_url)
        has_whatsapp = bool(whatsapp_file and whatsapp_file.filename)
        
        if not (has_youtube or has_website or has_whatsapp):
            return jsonify({
                'status': 'error',
                'message': 'Please provide at least one data source (YouTube, WhatsApp, or Website).'
            }), 400
        
        # Check plan limits (count data sources instead of channels)
        # For now, keep using channel limits - can update subscription_utils.py later
        if not user_status.get('is_active_community_owner'):
            max_channels = user_status['limits'].get('max_channels', 0)
            current_channels = user_status['usage'].get('channels_processed', 0)
            if max_channels != float('inf') and current_channels >= max_channels:
                message = f"You have reached the maximum of {int(max_channels)} chatbots for your plan."
                return jsonify({'status': 'limit_reached', 'message': message}), 403
        
        supabase = get_supabase_admin_client()
        
        # Step 1: Create the parent chatbot/channel record
        community_id_for_chatbot = active_community_id if user_status.get('is_active_community_owner') else None
        
        # Use first YouTube URL for initial channel record (backward compatibility)
        initial_youtube_url = None
        if has_youtube:
            initial_youtube_url = youtube_urls[0].strip()
            if is_youtube_video_url(initial_youtube_url):
                initial_youtube_url = get_channel_url_from_video_url(initial_youtube_url)
            initial_youtube_url = clean_youtube_url(initial_youtube_url)
        
        # Auto-detect bot type based on sources
        # YouTube-only = YouTuber bot, WhatsApp/Website = Business bot, Mixed = General
        if has_youtube and not has_whatsapp and not has_website:
            bot_type = 'youtuber'
        elif (has_whatsapp or has_website):
            bot_type = 'business'  # Business support bot for customer service
        else:
            bot_type = 'general'
        
        # Create chatbot record
        chatbot_data = {
            'creator_id': user_id,  # FIXED: Use creator_id for ownership check
            'channel_url': initial_youtube_url,  # Can be null if only WhatsApp/Website
            'status': 'processing',
            'is_shared': False,
            'community_id': community_id_for_chatbot,
            'channel_name': chatbot_name or 'New Chatbot',
            'has_youtube': has_youtube,
            'has_whatsapp': has_whatsapp,
            'has_website': has_website,
            'is_ready': False,
            'bot_type': bot_type  # Auto-detected persona type
        }
        
        chatbot_resp = supabase.table('channels').insert(chatbot_data).execute()
        chatbot = chatbot_resp.data[0]
        chatbot_id = chatbot['id']
        
        # Link chatbot to user
        db_utils.link_user_to_channel(user_id, chatbot_id)
        db_utils.increment_channels_processed(user_id)
        
        # Invalidate cache
        if redis_client:
            cache_key = f"user_visible_channels:{user_id}:community:{active_community_id or 'none'}"
            redis_client.delete(cache_key)
        
        task_ids = []
        
        # Step 2: Create data_sources and schedule tasks for each source
        
        # Process YouTube sources
        if has_youtube:
            for youtube_url in youtube_urls:
                youtube_url = youtube_url.strip()
                if not youtube_url:
                    continue
                
                # Handle video URLs
                if is_youtube_video_url(youtube_url):
                    youtube_url = get_channel_url_from_video_url(youtube_url)
                
                youtube_url = clean_youtube_url(youtube_url)
                
                # Create data source record
                source_resp = supabase.table('data_sources').insert({
                    'chatbot_id': chatbot_id,
                    'source_type': 'youtube',
                    'source_url': youtube_url,
                    'status': 'pending'
                }).execute()
                
                source_id = source_resp.data[0]['id']
                
                # Schedule YouTube processing task (use existing process_channel_task)
                # But we need to update it to work with data_sources
                # For now, use the existing task
                from tasks import process_channel_task
                task = process_channel_task.schedule(args=(chatbot_id,), delay=1)
                task_ids.append({'type': 'youtube', 'task_id': task.id, 'source_id': source_id})
        
        # Process Website source
        if has_website:
            # Create data source record
            source_resp = supabase.table('data_sources').insert({
                'chatbot_id': chatbot_id,
                'source_type': 'website',
                'source_url': website_url,
                'status': 'pending'
            }).execute()
            
            source_id = source_resp.data[0]['id']
            
            # Schedule website processing task
            task = process_website_source_task.schedule(args=(source_id,), delay=1)
            task_ids.append({'type': 'website', 'task_id': task.id, 'source_id': source_id})
        
        # Process WhatsApp source
        if has_whatsapp:
            # Validate file
            if not allowed_file(whatsapp_file.filename):
                return jsonify({
                    'status': 'error',
                    'message': 'Invalid file type. Please upload a .txt file.'
                }), 400
            
            # Save file with unique name
            filename = secure_filename(whatsapp_file.filename)
            unique_filename = f"{user_id}_{chatbot_id}_{int(time.time())}_{filename}"
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            whatsapp_file.save(file_path)
            
            # Create data source record with optional agent name
            source_metadata = {'original_filename': filename}
            if whatsapp_agent_name:
                source_metadata['preferred_agent'] = whatsapp_agent_name
            
            source_resp = supabase.table('data_sources').insert({
                'chatbot_id': chatbot_id,
                'source_type': 'whatsapp',
                'source_url': f"file://{unique_filename}",
                'status': 'pending',
                'metadata': source_metadata
            }).execute()
            
            source_id = source_resp.data[0]['id']
            
            # Schedule WhatsApp processing task (pass agent name)
            task = process_whatsapp_source_task.schedule(
                args=(source_id, file_path, whatsapp_agent_name), 
                delay=1
            )
            task_ids.append({'type': 'whatsapp', 'task_id': task.id, 'source_id': source_id})
        
        return jsonify({
            'status': 'processing',
            'chatbot_id': chatbot_id,
            'task_ids': task_ids,
            'message': f'Processing {len(task_ids)} data source(s)...'
        })
        
    except Exception as e:
        logger.error(f"Failed to create multi-source chatbot: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/chatbot/<int:chatbot_id>/sources', methods=['GET'])
@login_required
def get_chatbot_sources(chatbot_id):
    """
    Get all data sources for a chatbot with their processing status.
    """
    try:
        user_id = session['user']['id']
        supabase = get_supabase_admin_client()
        
        # Verify user has access to this chatbot
        access_check = supabase.table('user_channels').select('channel_id').eq(
            'user_id', user_id
        ).eq('channel_id', chatbot_id).execute()
        
        if not access_check.data:
            return jsonify({'status': 'error', 'message': 'Access denied'}), 403
        
        # Get all sources
        sources_resp = supabase.table('data_sources').select('*').eq(
            'chatbot_id', chatbot_id
        ).execute()
        
        return jsonify({
            'status': 'success',
            'sources': sources_resp.data
        })
        
    except Exception as e:
        logger.error(f"Failed to get chatbot sources: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/source/<int:source_id>/status', methods=['GET'])
@login_required
def get_source_status(source_id):
    """
    Get the processing status of a specific data source.
    """
    try:
        supabase = get_supabase_admin_client()
        
        source_resp = supabase.table('data_sources').select('*').eq('id', source_id).single().execute()
        
        if not source_resp.data:
            return jsonify({'status': 'error', 'message': 'Source not found'}), 404
        
        return jsonify({
            'status': 'success',
            'source': source_resp.data
        })
        
    except Exception as e:
        logger.error(f"Failed to get source status: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500
