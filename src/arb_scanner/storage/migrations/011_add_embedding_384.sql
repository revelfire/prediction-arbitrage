ALTER TABLE markets ADD COLUMN IF NOT EXISTS title_embedding_384 vector(384);
CREATE INDEX IF NOT EXISTS idx_markets_embedding_384
    ON markets USING hnsw (title_embedding_384 vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
