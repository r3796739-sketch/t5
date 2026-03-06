-- ============================================================================
-- YoppyChat AI — Performance Index for Embedding Search
-- 
-- Run this in the Supabase SQL Editor.
-- This creates an HNSW index on the embedding column, which makes
-- vector similarity search (match_embeddings) dramatically faster.
--
-- Expected improvement: 22s → <1s for typical queries.
-- ============================================================================

-- 1. Create HNSW index for cosine distance on the embedding column.
--    m=16, ef_construction=64 are solid defaults for production.
--    This index supports the <=> (cosine distance) operator used in match_embeddings.
CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw 
ON public.embeddings 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- 2. Create a composite B-tree index for the video_id filter used in match_embeddings.
--    This helps Postgres quickly filter by video_id before doing the vector search.
CREATE INDEX IF NOT EXISTS idx_embeddings_video_id 
ON public.embeddings (video_id);

-- 3. After creating indexes, analyze the table so the query planner uses them.
ANALYZE public.embeddings;
