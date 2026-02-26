"""
Multi-Source Task Handlers for YoppyChat
Handles processing of WhatsApp and Website data sources
"""

import os
import logging
from utils.whatsapp_parser import WhatsAppParser
from utils.website_scraper import WebsiteScraper
from utils.supabase_client import get_supabase_admin_client
from utils.qa_utils import extract_speaking_style
import time

logger = logging.getLogger(__name__)


def process_whatsapp_source(source_id: int, file_path: str, task_id: str = None, preferred_agent: str = None):
    """
    Process a WhatsApp chat export file as a data source.
    
    Args:
        source_id: ID of the data source record
        file_path: Path to the uploaded WhatsApp chat file
        task_id: Optional task ID for progress tracking
        preferred_agent: Optional name of the support agent/receptionist to learn from
    """
    supabase = get_supabase_admin_client()
    chatbot_id = None
    
    try:
        # Update status to processing
        supabase.table('data_sources').update({
            'status': 'processing',
            'progress': 10
        }).eq('id', source_id).execute()
        
        logger.info(f"Starting WhatsApp processing for source {source_id}, preferred_agent={preferred_agent}")
        
        # Get source details
        source_resp = supabase.table('data_sources').select('*').eq('id', source_id).single().execute()
        source = source_resp.data
        chatbot_id = source['chatbot_id']
        
        # Parse WhatsApp chat file
        logger.info(f"Parsing WhatsApp chat file: {file_path}")
        parser = WhatsAppParser()
        result = parser.parse_file(file_path, preferred_user=preferred_agent)
       
        messages = result['messages']
        primary_user = result['primary_user']
        stats = result['stats']
        
        if not messages:
            raise ValueError("No messages found in WhatsApp chat file")
        
        logger.info(f"Parsed {len(messages)} messages, identified agent: {primary_user}")
        
        # Update progress
        supabase.table('data_sources').update({'progress': 30}).eq('id', source_id).execute()
        
        # Create conversation chunks
        chunks = parser.chunk_messages(messages, chunk_size=30, overlap=5)
        logger.info(f"Created {len(chunks)} conversation chunks")
        
        # Update progress
        supabase.table('data_sources').update({'progress': 50}).eq('id', source_id).execute()
        
        # Extract speaking style from primary user messages
        primary_messages = parser.extract_primary_user_messages(messages, primary_user)
        speaking_style_text = " ".join([msg['text'] for msg in primary_messages[:50]])  # First 50 messages
        speaking_style = extract_speaking_style(speaking_style_text, source_type='whatsapp') if speaking_style_text else None
        
        # Create embeddings for each chunk
        transcripts_format = []
        for i, chunk in enumerate(chunks):
            formatted_text = parser.format_chunk_for_embedding(chunk)
            
            transcripts_format.append({
                'video_id': f"whatsapp_chunk_{i}",  # Using video_id field for compatibility
                'title': f"WhatsApp Chat - {chunk['date_range']}",
                'transcript': formatted_text,
                'url': f"whatsapp://chunk/{i}",
                'upload_date': chunk['messages'][0]['timestamp'],
                'uploader': primary_user or 'WhatsApp User'
            })
            
            # Update progress incrementally
            progress = 50 + int((i / len(chunks)) * 40)
            supabase.table('data_sources').update({'progress': progress}).eq('id', source_id).execute()
        
        # Store embeddings
        logger.info(f"Creating embeddings for {len(transcripts_format)} chunks")
        
        # Get chatbot owner (use creator_id, not user_id)
        chatbot_resp = supabase.table('channels').select('creator_id').eq('id', chatbot_id).maybe_single().execute()
        if not chatbot_resp or not chatbot_resp.data:
            raise ValueError(f"Chatbot {chatbot_id} not found - may have been deleted")
        user_id = chatbot_resp.data['creator_id']
        
        # Create embeddings using batch-friendly approach
        logger.info(f"Creating embeddings for {len(transcripts_format)} WhatsApp chunks")
        from utils.multi_source_embed import chunk_and_embed_text
        
        total_embedded = 0
        for transcript in transcripts_format:
            chunks_created = chunk_and_embed_text(
                text=transcript['transcript'],
                video_id=transcript['video_id'],
                channel_id=chatbot_id,
                source_id=source_id,
                user_id=user_id,
                source_type='whatsapp',
                additional_metadata={
                    'title': transcript['title'],
                    'primary_user': primary_user,
                    'date': transcript['upload_date']
                }
            )
            total_embedded += chunks_created
            
            # Update progress
            progress = 50 + int((total_embedded / len(transcripts_format)) * 40)
            supabase.table('data_sources').update({'progress': min(progress, 90)}).eq('id', source_id).execute()
        
        logger.info(f"Created {total_embedded} embeddings for WhatsApp source")
        
        # Mark as ready
        supabase.table('data_sources').update({
            'status': 'ready',
            'progress': 100,
            'metadata': {
                'message_count': stats['total_messages'],
                'primary_user': primary_user,
                'unique_senders': stats['unique_senders'],
                'conversation_blocks': len(chunks),
                'date_range': stats['date_range'],
                'speaking_style': speaking_style
            }
        }).eq('id', source_id).execute()
        
        # Update parent chatbot
        update_chatbot_readiness(chatbot_id)
        
        # Clean up the uploaded file
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Deleted temporary file: {file_path}")
        except Exception as e:
            logger.warning(f"Could not delete temporary file {file_path}: {e}")
        
        logger.info(f"WhatsApp source {source_id} processed successfully")
        return f"Successfully processed WhatsApp chat with {stats['total_messages']} messages"
        
    except Exception as e:
        logger.error(f"WhatsApp source {source_id} failed: {e}", exc_info=True)
        supabase.table('data_sources').update({
            'status': 'failed',
            'metadata': {'error': str(e)}
        }).eq('id', source_id).execute()
        
        if chatbot_id:
            update_chatbot_readiness(chatbot_id)
            
        raise


def process_website_source(source_id: int, task_id: str = None):
    """
    Crawl and process a website as a data source.
    
    Args:
        source_id: ID of the data source record
        task_id: Optional task ID for progress tracking
    """
    supabase = get_supabase_admin_client()
    chatbot_id = None
    
    try:
        # Update status to processing
        supabase.table('data_sources').update({
            'status': 'processing',
            'progress': 10
        }).eq('id', source_id).execute()
        
        logger.info(f"Starting website processing for source {source_id}")
        
        # Get source details
        source_resp = supabase.table('data_sources').select('*').eq('id', source_id).single().execute()
        source = source_resp.data
        chatbot_id = source['chatbot_id']
        website_url = source['source_url']
        
        if not website_url:
            raise ValueError("No website URL provided")
        
        # Check crawl mode from source metadata
        source_metadata = source.get('metadata') or {}
        crawl_mode = source_metadata.get('crawl_mode', 'auto')  # 'auto', 'single_page', 'full_crawl'
        
        # Determine if we should force single page or full crawl
        if crawl_mode == 'single_page':
            force_single = True
            max_pages = 1
        elif crawl_mode == 'full_crawl':
            force_single = False
            max_pages = 50
        else:
            # Auto mode: scraper will auto-detect from URL path
            force_single = False  # Let the scraper's _is_specific_page decide
            max_pages = 50
        
        # Scrape website
        logger.info(f"Scraping website: {website_url} (crawl_mode={crawl_mode})")
        scraper = WebsiteScraper(max_pages=max_pages, timeout=10)
        result = scraper.scrape_website(website_url, single_page=force_single)
        
        pages = result['pages']
        stats = result['stats']
        method = result['method']
        
        if not pages:
            raise ValueError("No pages could be scraped from the website")
        
        logger.info(f"Scraped {len(pages)} pages using method: {method}")
        
        # Update progress
        supabase.table('data_sources').update({'progress': 50}).eq('id', source_id).execute()
        
        # Get chatbot owner (use creator_id, not user_id)
        chatbot_resp = supabase.table('channels').select('creator_id').eq('id', chatbot_id).maybe_single().execute()
        if not chatbot_resp or not chatbot_resp.data:
            raise ValueError(f"Chatbot {chatbot_id} not found - may have been deleted")
        user_id = chatbot_resp.data['creator_id']
        
        # Create embeddings using batch-friendly approach
        logger.info(f"Creating embeddings for {len(pages)} website pages")
        from utils.multi_source_embed import chunk_and_embed_text
        
        total_chunks = 0
        for page_idx, page in enumerate(pages):
            if page.get('error'):
                logger.warning(f"Skipping failed page: {page['url']}")
                continue
            
            # Create embeddings for this page
            chunks_created = chunk_and_embed_text(
                text=page['text'],
                video_id=f"website_page_{page_idx}",
                channel_id=chatbot_id,
                source_id=source_id,
                user_id=user_id,
                source_type='website',
                additional_metadata={
                    'title': page['title'] or f"Page {page_idx + 1}",
                    'url': page['url'],
                    'page_index': page_idx
                }
            )
            total_chunks += chunks_created
            
            # Update progress incrementally
            progress = 50 + int(((page_idx + 1) / len(pages)) * 45)
            supabase.table('data_sources').update({'progress': min(progress, 95)}).eq('id', source_id).execute()
        
        logger.info(f"Created {total_chunks} total chunks from website")
        
        # Build list of scraped page details for display (limit to 50 to avoid huge payloads)
        scraped_pages = []
        for page in pages[:50]:
            page_info = {
                'url': page.get('url', ''),
                'title': page.get('title', 'Untitled'),
                'word_count': page.get('word_count', 0),
                'status': 'failed' if page.get('error') else 'success'
            }
            scraped_pages.append(page_info)
        
        # Mark as ready — set progress first as a safety measure, then mark ready
        try:
            supabase.table('data_sources').update({
                'status': 'ready',
                'progress': 100,
                'metadata': {
                    'page_count': len(pages),
                    'total_words': stats['total_words'],
                    'failed_pages': stats['failed_pages'],
                    'scraping_method': method,
                    'website_url': website_url,
                    'total_chunks': total_chunks,
                    'scraped_pages': scraped_pages
                }
            }).eq('id', source_id).execute()
        except Exception as update_err:
            logger.warning(f"Full metadata update failed ({update_err}), retrying without scraped_pages...")
            # Retry with minimal metadata to ensure status is marked ready
            supabase.table('data_sources').update({
                'status': 'ready',
                'progress': 100,
                'metadata': {
                    'page_count': len(pages),
                    'total_words': stats['total_words'],
                    'failed_pages': stats['failed_pages'],
                    'scraping_method': method,
                    'website_url': website_url,
                    'total_chunks': total_chunks
                }
            }).eq('id', source_id).execute()
        
        # Update parent chatbot
        update_chatbot_readiness(chatbot_id)
        
        logger.info(f"Website source {source_id} processed successfully")
        return f"Successfully scraped {len(pages)} pages from website"
        
    except Exception as e:
        logger.error(f"Website source {source_id} failed: {e}", exc_info=True)
        supabase.table('data_sources').update({
            'status': 'failed',
            'metadata': {'error': str(e)}
        }).eq('id', source_id).execute()
        
        if chatbot_id:
            update_chatbot_readiness(chatbot_id)
            
        raise


def update_chatbot_readiness(chatbot_id: int):
    """
    Update the parent chatbot's readiness based on data sources.
    This is called after any source completes processing.
    Also merges speaking_style from sources into the parent chatbot.
    
    Args:
        chatbot_id: ID of the chatbot/channel
    """
    supabase = get_supabase_admin_client()
    
    try:
        # Get all data sources for this chatbot (include metadata for speaking_style)
        sources_resp = supabase.table('data_sources').select('source_type, status, metadata').eq('chatbot_id', chatbot_id).execute()
        
        if not sources_resp.data:
            logger.warning(f"No data sources found for chatbot {chatbot_id}")
            return
        
        sources = sources_resp.data
        
        # Count ready sources
        ready_count = sum(1 for s in sources if s['status'] == 'ready')
        pending_count = sum(1 for s in sources if s['status'] in ['pending', 'processing'])
        
        # Check what source types are ready
        has_youtube = any(s['source_type'] == 'youtube' and s['status'] == 'ready' for s in sources)
        has_whatsapp = any(s['source_type'] == 'whatsapp' and s['status'] == 'ready' for s in sources)
        has_website = any(s['source_type'] == 'website' and s['status'] == 'ready' for s in sources)
        
        # Extract speaking_style from WhatsApp sources (primary user's communication style)
        speaking_style = None
        for source in sources:
            if source['source_type'] == 'whatsapp' and source['status'] == 'ready':
                metadata = source.get('metadata') or {}
                if metadata.get('speaking_style'):
                    speaking_style = metadata['speaking_style']
                    logger.info(f"Extracted speaking style from WhatsApp source for chatbot {chatbot_id}")
                    break
        
        # Update chatbot
        is_ready = ready_count > 0
        if pending_count > 0:
            status = 'processing'
        elif ready_count > 0:
            status = 'ready'
        else:
            status = 'failed'
        
        update_data = {
            'is_ready': is_ready,
            'status': status,
            'has_youtube': has_youtube,
            'has_whatsapp': has_whatsapp,
            'has_website': has_website
        }
        
        # Only update speaking_style if we extracted one from WhatsApp
        if speaking_style:
            update_data['speaking_style'] = speaking_style
        
        supabase.table('channels').update(update_data).eq('id', chatbot_id).execute()
        
        logger.info(f"Updated chatbot {chatbot_id}: ready={is_ready}, YouTube={has_youtube}, WhatsApp={has_whatsapp}, Website={has_website}, style={'extracted' if speaking_style else 'none'}")
        
    except Exception as e:
        logger.error(f"Failed to update chatbot readiness for {chatbot_id}: {e}", exc_info=True)


def process_pdf_source(source_id: int, file_path: str, task_id: str = None):
    """
    Process a PDF file as a data source.

    Args:
        source_id: ID of the data source record
        file_path: Path to the uploaded PDF file
        task_id: Optional task ID for progress tracking
    """
    from utils.pdf_parser import extract_text_from_pdf, chunk_pdf_pages
    from utils.multi_source_embed import chunk_and_embed_text

    supabase = get_supabase_admin_client()
    chatbot_id = None

    try:
        supabase.table('data_sources').update({
            'status': 'processing',
            'progress': 10
        }).eq('id', source_id).execute()

        logger.info(f"Starting PDF processing for source {source_id}")

        source_resp = supabase.table('data_sources').select('*').eq('id', source_id).single().execute()
        source = source_resp.data
        chatbot_id = source['chatbot_id']

        # --- Extract text ---
        logger.info(f"Extracting text from PDF: {file_path}")
        result = extract_text_from_pdf(file_path)
        pages = result['pages']
        doc_title = result['title']
        total_words = result['total_words']
        total_pages = result['total_pages']

        if not pages:
            raise ValueError("No readable text found in the PDF. It may be image-only or password-protected.")

        logger.info(f"Extracted {len(pages)} pages ({total_words} words) from '{doc_title}'")
        supabase.table('data_sources').update({'progress': 30}).eq('id', source_id).execute()

        # --- Chunk pages ---
        chunks = chunk_pdf_pages(pages, chunk_size=5, overlap=1)
        logger.info(f"Created {len(chunks)} page-groups for embedding")
        supabase.table('data_sources').update({'progress': 50}).eq('id', source_id).execute()

        # --- Get chatbot owner ---
        chatbot_resp = supabase.table('channels').select('creator_id').eq('id', chatbot_id).maybe_single().execute()
        if not chatbot_resp or not chatbot_resp.data:
            raise ValueError(f"Chatbot {chatbot_id} not found")
        user_id = chatbot_resp.data['creator_id']

        # --- Create embeddings ---
        logger.info(f"Creating embeddings for {len(chunks)} PDF chunks")
        total_embedded = 0
        for i, chunk in enumerate(chunks):
            chunks_created = chunk_and_embed_text(
                text=chunk['text'],
                video_id=f"pdf_chunk_{i}",
                channel_id=chatbot_id,
                source_id=source_id,
                user_id=user_id,
                source_type='pdf',
                additional_metadata={
                    'title': doc_title,
                    'page_range': chunk['page_range'],
                    'chunk_index': i,
                }
            )
            total_embedded += chunks_created
            progress = 50 + int(((i + 1) / len(chunks)) * 45)
            supabase.table('data_sources').update({'progress': min(progress, 95)}).eq('id', source_id).execute()

        logger.info(f"Created {total_embedded} embeddings for PDF source {source_id}")

        # --- Mark ready ---
        supabase.table('data_sources').update({
            'status': 'ready',
            'progress': 100,
            'metadata': {
                'pdf_title': doc_title,
                'page_count': total_pages,
                'total_words': total_words,
                'chunks': len(chunks),
            }
        }).eq('id', source_id).execute()

        # --- Update parent chatbot ---
        update_chatbot_readiness(chatbot_id)

        # --- Clean up file ---
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Deleted temporary PDF file: {file_path}")
        except Exception as del_err:
            logger.warning(f"Could not delete temporary file {file_path}: {del_err}")

        logger.info(f"PDF source {source_id} processed successfully")
        return f"Successfully processed PDF '{doc_title}' ({total_pages} pages, {total_words} words)"

    except Exception as e:
        logger.error(f"PDF source {source_id} failed: {e}", exc_info=True)
        supabase.table('data_sources').update({
            'status': 'failed',
            'metadata': {'error': str(e)}
        }).eq('id', source_id).execute()
        
        if chatbot_id:
            update_chatbot_readiness(chatbot_id)
            
        raise

