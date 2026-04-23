"""
Simplified embedding creation for multi-source data
Processes in small batches to avoid database timeouts
Uses Gemini embeddings to match existing configuration
"""

import logging
import os
import google.generativeai as genai
from utils.supabase_client import get_supabase_admin_client
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

def create_embeddings_batch(texts, channel_id, source_id, user_id, metadata_list, batch_size=10):
    """
    Create embeddings in small batches using Gemini.
    Uses the same embedding configuration as the main app.
    
    Args:
        texts: List of text chunks to embed
        channel_id: Channel/chatbot ID
        source_id: Data source ID
        user_id: User/creator ID
        metadata_list: List of metadata dicts for each text
        batch_size: Number of embeddings to process at once
    """
    supabase = get_supabase_admin_client()
    
    # Get Gemini configuration
    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        logger.error("GEMINI_API_KEY not found in environment")
        return
    
    genai.configure(api_key=api_key)
    model = os.environ.get('EMBED_MODEL', 'models/text-embedding-004')
    if not model.startswith('models/'):
        model = f"models/{model}"
    
    output_dimensions = int(os.environ.get('GEMINI_EMBED_DIMENSIONS', '1536'))
    
    total = len(texts)
    logger.info(f"Creating {total} embeddings in batches of {batch_size} using Gemini ({output_dimensions} dimensions)")
    
    for i in range(0, total, batch_size):
        batch_texts = texts[i:i+batch_size]
        batch_metadata = metadata_list[i:i+batch_size]
        
        # Generate embeddings using Gemini with retry logic for rate limits
        max_retries = 5
        base_delay = 5  # seconds
        
        for attempt in range(max_retries):
            try:
                result = genai.embed_content(
                    model=model, 
                    content=batch_texts, 
                    task_type="retrieval_document",
                    output_dimensionality=output_dimensions
                )
                
                # Extract embeddings from result
                embeddings = []
                if isinstance(result, dict) and 'embedding' in result:
                    emb_data = result['embedding']
                    # Single text case
                    if isinstance(emb_data, (list, tuple)) and all(isinstance(x, (float, int)) for x in emb_data):
                        if len(batch_texts) == 1:
                            embeddings = [emb_data]
                    # Multiple texts case
                    elif isinstance(emb_data, list) and len(emb_data) > 0:
                        embeddings = emb_data
                
                if len(embeddings) != len(batch_texts):
                    logger.error(f"Embedding count mismatch: got {len(embeddings)}, expected {len(batch_texts)}")
                    break # Break retry loop, this is a logic/formatting error
                
                break # Success! Break out of the retry loop
                    
            except Exception as e:
                error_msg = str(e).lower()
                is_rate_limit = "429" in error_msg or "quota" in error_msg or "rate limit" in error_msg
                
                if is_rate_limit and attempt < max_retries - 1:
                    sleep_time = base_delay * (2 ** attempt) # Exponential backoff: 5s, 10s, 20s, 40s
                    logger.warning(f"Rate limit hit for batch {i}. Retrying in {sleep_time} seconds (Attempt {attempt+1}/{max_retries})...")
                    import time
                    time.sleep(sleep_time)
                else:
                    logger.error(f"Failed to generate embeddings for batch {i} after {attempt + 1} attempts: {e}")
                    embeddings = [] # Ensure embeddings is empty so we don't insert garbage
                    break
        
        if not embeddings:
            continue # Skip inserting if we never successfully got embeddings
        
        # Insert each embedding
        for j, (text, embedding, metadata) in enumerate(zip(batch_texts, embeddings, batch_metadata)):
            try:
                supabase.table('embeddings').insert({
                    'channel_id': channel_id,
                    'source_id': source_id,
                    'user_id': user_id,
                    'video_id': metadata.get('video_id', f'chunk_{i+j}'),
                    'embedding': embedding,
                    'metadata': {
                        **metadata,
                        'chunk_text': text  # FIXED: Store full chunk (already sized by splitter)
                    }
                }).execute()
                logger.info(f"Successfully inserted embedding {i+j}")
            except Exception as e:
                logger.error(f"Failed to insert embedding {i+j}: {e}")
                # Continue with next embedding instead of failing completely
        
        logger.info(f"Processed {min(i+batch_size, total)}/{total} embeddings")


def chunk_and_embed_text(text, video_id, channel_id, source_id, user_id, source_type, additional_metadata=None):
    """
    Split text into chunks and create embeddings.
    
    Args:
        text: Text to chunk and embed
        video_id: Unique ID for this content
        channel_id: Channel/chatbot ID
        source_id: Data source ID
        user_id: User/creator ID
        source_type: Type of source (whatsapp, website, youtube)
        additional_metadata: Extra metadata to include
    """
    # Split text into chunks
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=50,
        length_function=len
    )
    
    chunks = text_splitter.split_text(text)
    logger.info(f"Split text into {len(chunks)} chunks")
    
    # Prepare metadata for each chunk
    metadata_list = []
    for idx, chunk in enumerate(chunks):
        metadata = {
            'source_type': source_type,
            'video_id': video_id,
            'chunk_index': idx,
            'total_chunks': len(chunks),
            **(additional_metadata or {})
        }
        metadata_list.append(metadata)
    
    # Create embeddings in batches
    create_embeddings_batch(chunks, channel_id, source_id, user_id, metadata_list, batch_size=5)
    
    return len(chunks)
