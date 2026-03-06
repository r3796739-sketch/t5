-- 1. Drop the old function to avoid overload issues
DROP FUNCTION IF EXISTS public.match_embeddings(vector, float, int, text[]);

-- 2. Create the updated function with p_channel_id
CREATE OR REPLACE FUNCTION public.match_embeddings (
  query_embedding vector(1536),
  match_threshold float,
  match_count int,
  p_video_ids text[] DEFAULT NULL,
  p_channel_id bigint DEFAULT NULL
)
RETURNS TABLE (
  id bigint,
  metadata jsonb,
  similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    embeddings.id,
    embeddings.metadata,
    1 - (embeddings.embedding <=> query_embedding) AS similarity
  FROM embeddings
  WHERE 
    (p_video_ids IS NULL OR embeddings.video_id = ANY(p_video_ids))
    AND (p_channel_id IS NULL OR embeddings.channel_id = p_channel_id)
    AND 1 - (embeddings.embedding <=> query_embedding) > match_threshold
  ORDER BY similarity DESC
  LIMIT match_count;
END;
$$;
