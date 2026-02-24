ALTER TABLE markets ADD COLUMN IF NOT EXISTS title_embedding vector(512);
CREATE INDEX IF NOT EXISTS idx_markets_embedding
    ON markets USING hnsw (title_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
