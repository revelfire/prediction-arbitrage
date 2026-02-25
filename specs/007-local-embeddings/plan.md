# Plan: Local Embeddings (007)

**Input**: `specs/007-local-embeddings/spec.md`

## Architecture

The change replaces the Voyage AI HTTP client with a local ONNX model while preserving the existing pipeline interface. The `generate_embeddings()` function signature stays the same — it returns `dict[str, list[float]]` — but the implementation dispatches to either a local model or the Voyage API based on config.

```
EmbeddingConfig.provider
        │
        ├── "local" (default)
        │       │
        │       ▼
        │   LocalEmbedder (fastembed, BAAI/bge-small-en-v1.5)
        │       │
        │       ▼
        │   384-dim vectors
        │
        └── "voyage"
                │
                ▼
            VoyageEmbedder (existing httpx client)
                │
                ▼
            512-dim vectors

Both paths:
    ▼
Cache check (pgvector) ──→ skip if hit
    ▼
Generate only new ──→ persist to pgvector
    ▼
Return dict[str, list[float]] to reranker
```

## Key Decisions

1. **fastembed over sentence-transformers**: fastembed uses ONNX runtime (~100MB) instead of PyTorch (~500MB+). The `BAAI/bge-small-en-v1.5` model is 130MB and produces 384-dim embeddings. Total added footprint ~230MB.

2. **Cache-first architecture**: Before generating any embeddings, query pgvector for all `(venue, event_id)` pairs in the current batch. Only generate embeddings for cache misses. This is the main scan-cycle optimization.

3. **Separate embedding column**: Add `title_embedding_384 vector(384)` column rather than altering the existing `title_embedding vector(512)`. This avoids breaking anything if someone switches between providers. The query code checks the column matching the configured dimensions.

4. **Lazy model loading**: The fastembed model is loaded once on first use (singleton pattern) and reused across scan cycles. Model download happens on first run (~130MB, cached by fastembed in `~/.cache/fastembed/`).

## Dependencies

- Add `fastembed` to `pyproject.toml` (brings in `onnxruntime`, `tokenizers`, `huggingface-hub`)
- Remove: nothing (Voyage AI code stays as fallback)

## Files Changed

| File | Change |
|------|--------|
| `pyproject.toml` | Add `fastembed` dependency |
| `src/arb_scanner/models/config.py` | Add `provider` field to `EmbeddingConfig`, change defaults |
| `src/arb_scanner/matching/embedding.py` | Refactor: extract `VoyageEmbedder`, add `LocalEmbedder`, add cache-read logic |
| `src/arb_scanner/storage/_queries.py` or `_analytics_queries.py` | Add `SELECT title_embedding_384` query |
| `src/arb_scanner/storage/repository.py` | Add `get_market_embeddings()` and `update_market_embedding_384()` methods |
| `src/arb_scanner/storage/migrations/011_add_embedding_384.sql` | Add `title_embedding_384 vector(384)` column + HNSW index |
| `src/arb_scanner/cli/orchestrator.py` | Wire cache-read into `_run_embedding_rerank` |
| `config.example.yaml` | Update embedding section with `provider: local` |
| `Dockerfile` | No change needed (fastembed downloads model to cache on first run) |
| `tests/unit/test_embedding.py` | Update tests for new provider dispatch + cache read |

## Migration Strategy

- New column `title_embedding_384` added alongside existing `title_embedding` (512d)
- Orchestrator reads from whichever column matches `config.embedding.dimensions`
- Old 512d embeddings remain usable if someone sets `provider: voyage`
- No data migration needed — embeddings regenerate naturally on next scan

## Risk Assessment

- **Low risk**: fastembed is a well-maintained library (Qdrant team), ONNX runtime is stable
- **Medium risk**: First-run model download adds ~30s to initial startup. Mitigated by Docker layer caching or pre-downloading in Dockerfile.
- **Low risk**: 384d vs 512d — both are standard dimensions, cosine similarity works the same way
