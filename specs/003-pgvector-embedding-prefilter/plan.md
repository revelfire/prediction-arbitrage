# Implementation Plan: pgvector Embedding Pre-Filter

**Branch**: `003-pgvector-embedding-prefilter` | **Date**: 2026-02-24 | **Spec**: [spec.md](spec.md)

## Summary

Add Voyage AI vector embeddings as a second pre-filter stage in the matching pipeline. BM25 runs first for broad recall, then cosine similarity filters down to high-confidence pairs before Claude evaluation. Embeddings stored in pgvector via the existing `markets` table.

## Technical Context

**New Dependency**: `voyageai` (Voyage AI Python SDK, async-compatible)
**Embedding Model**: `voyage-3-lite` (512 dimensions, ~$0.000002/token)
**Storage**: `vector(512)` column on existing `markets` table with HNSW index
**API Key**: Reuses `ANTHROPIC_API_KEY` or separate `VOYAGE_API_KEY`

## Constitution Check

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. Human-in-the-Loop | PASS | Pre-filter only — no trading logic |
| II. Pydantic at Every Boundary | PASS | EmbeddingConfig added to Settings |
| III. Async-First I/O | PASS | Voyage API calls via async httpx |
| IV. Structured Logging | PASS | Embedding generation and filtering logged |
| V. Two-Pass Matching | PASS | Now three-pass: BM25 → Embedding → Claude |
| VI. Configuration Over Code | PASS | Model, threshold, dimensions all in config.yaml |

## Project Structure (new/modified files)

```text
src/arb_scanner/
├── models/
│   └── config.py              # EXTEND: add EmbeddingConfig, add to Settings
├── matching/
│   ├── embedding.py           # NEW: Voyage API client, batch embedding generation
│   ├── embedding_prefilter.py # NEW: cosine similarity filtering of BM25 candidates
│   ├── prefilter.py           # MODIFY: increase default top_k from 10 to 20
│   └── semantic.py            # MODIFY: rename "BM25 Score" to "Similarity Score"
├── cli/
│   └── orchestrator.py        # MODIFY: wire embedding into pipeline
├── storage/
│   └── migrations/
│       └── 009_add_embedding_column.sql  # NEW: ALTER TABLE + HNSW index
├── config/
│   └── loader.py              # No change (env var interpolation already works)

config.example.yaml            # EXTEND: add embedding section

tests/
├── unit/
│   ├── test_embedding.py          # NEW: embedding client tests
│   └── test_embedding_prefilter.py # NEW: cosine filter tests
├── integration/
│   └── test_embedding_pipeline.py  # NEW: pipeline integration tests
```

## Key Technical Decisions

### 1. Voyage AI via httpx (not voyageai SDK)
Use direct httpx calls to the Voyage API instead of the `voyageai` SDK. This avoids a new dependency — we already have httpx. The Voyage API is a simple POST to `https://api.voyageai.com/v1/embeddings` with an API key header. Keeps the dependency footprint minimal.

### 2. BM25 top_k increased to 20
Currently top_k=10. With the embedding filter as a second stage, we widen BM25's net to top_k=20 for better recall, knowing the embedding filter will tighten precision. Net effect: more true positives reach Claude, fewer false positives.

### 3. Embeddings computed during orchestration, not ingestion
Compute embeddings in `_match_candidates()` right before the cosine filter, not during market fetch. This keeps the ingestion clients simple and avoids embedding markets that will never be compared (e.g., venue-specific markets with no cross-venue equivalent). Embeddings are optionally cached in the DB for subsequent scans.

### 4. In-memory cosine similarity (not pgvector ANN)
For the initial implementation, compute cosine similarity in Python using numpy on the BM25 candidate pairs. The pair count is small (50-300 pairs) so ANN search overhead isn't justified yet. The DB stores embeddings for cache/persistence, but the actual similarity computation is in-memory. pgvector ANN can replace BM25 entirely in a future feature.

## Pipeline Flow (updated)

```
fetch_markets()
  → poly_markets, kalshi_markets
  → generate_embeddings(all_markets, config)     # batch Voyage API call
  → store embeddings in markets table (optional, when DB available)
  ↓
prefilter_candidates(top_k=20)                    # BM25 broad pass
  → bm25_pairs: list[(Market, Market, float)]
  ↓
embedding_rerank(bm25_pairs, config)              # cosine similarity filter
  → filtered_pairs: list[(Market, Market, float)] # float = cosine sim
  ↓
_filter_cached(filtered_pairs)                    # cache lookup
  ↓
evaluate_pairs(uncached)                          # Claude API
```

## SQL Design

### Migration 009: Add embedding column
```sql
ALTER TABLE markets ADD COLUMN IF NOT EXISTS
    title_embedding vector(512);

CREATE INDEX IF NOT EXISTS idx_markets_embedding
    ON markets USING hnsw (title_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
```
