# Spec: Local Embeddings (007)

**Depends on**: `003-pgvector-embedding-prefilter` (complete), `006-dashboard-web-ui` (complete)

## Problem

The embedding pre-filter currently calls the Voyage AI API every scan cycle to regenerate embeddings for all markets — even though:
1. Most market titles don't change between scans
2. Embeddings are already persisted to pgvector but never read back
3. The Voyage AI dependency adds API cost, latency, and a required API key for a step that could run locally

## Solution

Replace Voyage AI with a local ONNX-based model (`BAAI/bge-small-en-v1.5` via `fastembed`) and add embedding caching so previously-seen markets skip regeneration.

## User Stories

- **US-01**: As an operator, I want the scanner to generate embeddings locally so I don't need a Voyage AI API key or pay per-scan embedding costs.
- **US-02**: As an operator, I want embeddings cached in PostgreSQL so repeat markets skip regeneration across scan cycles.
- **US-03**: As an operator, I want backward compatibility so I can still use Voyage AI if I prefer (API-based embedding as a fallback).

## Functional Requirements

- **FR-01**: Replace `voyage-3-lite` as the default embedding model with `BAAI/bge-small-en-v1.5` running locally via `fastembed` (ONNX runtime, no PyTorch dependency).
- **FR-02**: Before calling the embedding model, check pgvector for existing embeddings by `(venue, event_id)`. Only generate embeddings for markets not already in the database.
- **FR-03**: Support a `provider` field in `EmbeddingConfig`: `"local"` (default, fastembed) or `"voyage"` (existing HTTP API). When `provider=voyage`, behavior is unchanged from current implementation.
- **FR-04**: The local model must produce embeddings compatible with the existing cosine similarity reranker (`embedding_prefilter.py`). No changes to the reranking logic.
- **FR-05**: Remove `api_key` as a required field — it should only be needed when `provider=voyage`.
- **FR-06**: The `dimensions` config field should default to 384 (bge-small output) when provider is local.
- **FR-07**: Update migration to handle dimension change: add a new `title_embedding_384` column (vector(384)) alongside the existing `title_embedding` (vector(512)) column, or alter the column if no production data exists yet.

## Non-Functional Requirements

- **NFR-01**: Local embedding generation for 500 markets must complete in <10 seconds on a 4-core machine.
- **NFR-02**: The `fastembed` + `onnxruntime` dependency must add <200MB to the Docker image (vs ~500MB+ for PyTorch).
- **NFR-03**: All 439+ existing tests must continue to pass.

## Success Criteria

- **SC-01**: `uv run arb-scanner scan --dry-run` works without any API keys set for embeddings.
- **SC-02**: Second scan cycle for the same markets produces zero embedding API/model calls (all cache hits from pgvector).
- **SC-03**: Voyage AI remains functional when `provider: voyage` and `api_key` is set in config.

## Edge Cases

- **EC-01**: First scan on empty database — all embeddings generated locally, persisted to pgvector.
- **EC-02**: Market title changes between scans — stale embedding used until TTL or manual refresh. Acceptable for a pre-filter.
- **EC-03**: Mixed dimensions — if switching from Voyage (512d) to local (384d) mid-deployment, old 512d embeddings in pgvector should be ignored (dimension mismatch) and regenerated.
