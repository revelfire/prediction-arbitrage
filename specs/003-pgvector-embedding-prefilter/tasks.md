# Tasks: pgvector Embedding Pre-Filter

**Input**: Design documents from `/specs/003-pgvector-embedding-prefilter/`
**Depends on**: `002-arb-history-analytics` (complete)

## Format: `[ID] [P?] [Story] Description`

## Autonomous Execution Notes

- Do NOT pause between tasks — execute sequentially within phases, parallel where marked [P]
- Fix quality gate failures immediately
- Run all 5 quality gates after each phase completes
- All existing 295 tests MUST continue to pass after every phase
- This is a greenfield pre-1.0 app — modify existing code directly, no backward compat ceremony

---

## Phase 1: Foundation (Config + Migration + Embedding Client)

- [x] T001 [P] Add `EmbeddingConfig` to `src/arb_scanner/models/config.py`: fields `enabled` (bool, default True), `model` (str, default "voyage-3-lite"), `api_key` (str, default ""), `cosine_threshold` (float, default 0.60), `dimensions` (int, default 512). Add `embedding: EmbeddingConfig` to `Settings` with default. Re-export from `models/__init__.py`.
- [x] T002 [P] Create `src/arb_scanner/storage/migrations/009_add_embedding_column.sql`: ALTER TABLE markets ADD COLUMN IF NOT EXISTS title_embedding vector(512). CREATE HNSW index with vector_cosine_ops (m=16, ef_construction=64).
- [x] T003 Create `src/arb_scanner/matching/embedding.py`: async function `generate_embeddings(markets: list[Market], config: EmbeddingConfig) -> dict[str, list[float]]` that calls Voyage AI API via httpx POST to `https://api.voyageai.com/v1/embeddings`. Batch up to 128 texts per request. Input text = `f"{m.title}. {m.resolution_criteria}"`. Returns dict keyed by `f"{venue}:{event_id}"` → embedding vector. Handle API errors gracefully with structlog + empty dict fallback. Also add `_market_key(m: Market) -> str` helper.
- [x] T004 [P] Extend `config.example.yaml` with embedding section: enabled, model, api_key (`${VOYAGE_API_KEY}`), cosine_threshold, dimensions.
- [x] T005 [P] Create `tests/unit/test_embedding.py`: test EmbeddingConfig model validation, test `generate_embeddings` with mocked httpx response (mock Voyage API returning 512-dim vectors), test batch splitting for >128 markets, test graceful failure on API error, test `_market_key` helper. Target: ~12 tests.

**Quality gate**: All 5 gates.

---

## Phase 2: Embedding Pre-Filter + Pipeline Integration

- [x] T006 Create `src/arb_scanner/matching/embedding_prefilter.py`: async function `embedding_rerank(pairs: list[tuple[Market, Market, float]], embeddings: dict[str, list[float]], config: EmbeddingConfig) -> list[tuple[Market, Market, float]]`. Compute cosine similarity between each pair's embeddings. Drop pairs below `config.cosine_threshold`. Return filtered pairs sorted by cosine similarity descending, with cosine sim as the new float value. Use numpy for cosine computation. If either market has no embedding, keep the pair (don't penalize missing embeddings).
- [x] T007 Modify `src/arb_scanner/matching/prefilter.py`: change default `top_k` from 10 to 20 in `prefilter_candidates()`.
- [x] T008 Modify `src/arb_scanner/matching/semantic.py`: rename "BM25 Score" label in `_format_pair()` to "Similarity Score".
- [x] T009 Modify `src/arb_scanner/cli/orchestrator.py` `_match_candidates()`: after BM25 prefilter, call `generate_embeddings()` for the markets in the BM25 pairs, then call `embedding_rerank()` to filter. Only do this when `config.embedding.enabled` and `config.embedding.api_key` is non-empty. Otherwise skip (BM25-only fallback). Log pair counts before/after embedding filter.
- [x] T010 [P] Create `tests/unit/test_embedding_prefilter.py`: test cosine filtering with known vectors, test threshold behavior (pairs above/below), test missing embeddings are kept, test empty pairs input, test sorting by cosine similarity. Target: ~10 tests.

**Quality gate**: All 5 gates.

---

## Phase 3: DB Persistence + Tests + Polish

- [x] T011 Extend `src/arb_scanner/storage/_queries.py` or `_analytics_queries.py`: add `UPDATE_MARKET_EMBEDDING` query to set `title_embedding` on the markets table by (venue, event_id).
- [x] T012 Extend `src/arb_scanner/storage/repository.py`: add `update_market_embedding(venue: str, event_id: str, embedding: list[float]) -> None` method.
- [x] T013 Modify orchestrator: after generating embeddings, persist them to DB via `update_market_embedding()` when DB is available. This is a fire-and-forget optimization for subsequent scans.
- [x] T014 [P] Create `tests/integration/test_embedding_pipeline.py`: mock Voyage API, test full pipeline flow with embedding enabled vs disabled, test that BM25-only fallback works when embedding disabled. Target: ~8 tests.
- [x] T015 Run full quality gate suite. Fix any failures. Verify coverage ≥70%.
- [x] T016 Update `CLAUDE.md`: add embedding config note, update architecture to mention embedding layer.

**Quality gate**: All 5 gates green. Final verification.

---

## Total: 16 tasks across 3 phases
