# Tasks: Local Embeddings (007)

**Input**: `specs/007-local-embeddings/spec.md`, `specs/007-local-embeddings/plan.md`
**Depends on**: `006-dashboard-web-ui` (complete)

## Autonomous Execution Notes

- Fix bugs as you find them
- Greenfield pre-1.0 — modify existing code directly
- All 439 existing tests MUST continue to pass
- New dependency: fastembed — add to pyproject.toml
- mypy --strict must pass (fastembed may need type: ignore)

---

## Phase 1: Dependencies + Config + Migration

- [x] T001 Add `fastembed` to `pyproject.toml` dependencies. Run `uv sync` to install.
- [x] T002 Update `EmbeddingConfig` in `src/arb_scanner/models/config.py`: add `provider: str` field (default `"local"`), change `model` default to `"BAAI/bge-small-en-v1.5"`, change `dimensions` default to `384`. Keep `api_key` field (only used when provider=voyage).
- [x] T003 Create `src/arb_scanner/storage/migrations/011_add_embedding_384.sql`: `ALTER TABLE markets ADD COLUMN IF NOT EXISTS title_embedding_384 vector(384)` with HNSW index.
- [x] T004 Update `config.example.yaml`: change embedding section to show `provider: local`, `model: BAAI/bge-small-en-v1.5`, `dimensions: 384`. Add commented-out Voyage alternative.

**Quality gate**: All 5 gates. Existing tests must pass. `uv sync` succeeds.

---

## Phase 2: Local Embedder

- [x] T005 Refactor `src/arb_scanner/matching/embedding.py`: extract existing Voyage AI logic into a `_generate_voyage()` async function. Keep `generate_embeddings()` as the public entry point that dispatches based on `config.provider`.
- [x] T006 Add `_generate_local()` async function in `embedding.py`: uses `fastembed.TextEmbedding` with the configured model name. Lazy-loads the model on first call (module-level singleton). Batches input texts and returns `dict[str, list[float]]`.
- [x] T007 Wire dispatch in `generate_embeddings()`: if `config.provider == "local"` call `_generate_local()`, elif `config.provider == "voyage"` call `_generate_voyage()` (existing logic), else raise ValueError.

**Quality gate**: All 5 gates.

---

## Phase 3: Embedding Cache (Read Path)

- [x] T008 Add `GET_MARKET_EMBEDDINGS` query to `src/arb_scanner/storage/_queries.py`: `SELECT venue, event_id, title_embedding_384 FROM markets WHERE (venue, event_id) IN ...` (or batched approach). Return rows where the embedding column is not NULL.
- [x] T009 Add `get_market_embeddings(pairs: list[tuple[str, str]], dimensions: int) -> dict[str, list[float]]` method to `Repository`. Reads the correct embedding column based on dimensions (384 → `title_embedding_384`, 512 → `title_embedding`). Returns `dict[str, list[float]]` keyed by `"venue:event_id"`.
- [x] T010 Add `UPDATE_MARKET_EMBEDDING_384` query and `update_market_embedding_384()` method to `Repository` for persisting 384-dim embeddings.
- [x] T011 Update `_run_embedding_rerank` in `orchestrator.py`: before calling `generate_embeddings()`, load cached embeddings from DB via `repo.get_market_embeddings()`. Pass only uncached markets to the embedder. Merge cached + newly generated embeddings for the reranker.

**Quality gate**: All 5 gates.

---

## Phase 4: Tests

- [x] T012 Update `tests/unit/test_embedding.py`: add tests for local provider dispatch (mock fastembed), Voyage provider dispatch, unknown provider raises ValueError, config defaults changed.
- [x] T013 Add tests for cache-read path: mock `get_market_embeddings()` returning cached embeddings, verify `generate_embeddings()` is only called for uncached markets.
- [x] T014 Update existing embedding tests to work with new config defaults (provider=local, dimensions=384).
- [x] T015 Run full quality gate suite. Fix any failures. Verify coverage >=70%.

**Quality gate**: All 5 gates green.

---

## Phase 5: Polish + Docs

- [x] T016 Update CLAUDE.md: note local embedding default, mention fastembed dependency, document provider config option.
- [x] T017 Remove `VOYAGE_API_KEY` from `.env.example` (move to commented-out section). Update docker-compose.yml to remove `VOYAGE_API_KEY` from env_file passthrough (it's already in .env if needed).
- [x] T018 Verify `uv run arb-scanner scan --dry-run` works with zero API keys set for embeddings.

**Quality gate**: All 5 gates green. Final verification.

---

## Total: 18 tasks across 5 phases
