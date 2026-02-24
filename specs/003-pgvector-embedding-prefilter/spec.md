# Feature Specification: pgvector Embedding Pre-Filter

**Feature**: `003-pgvector-embedding-prefilter` | **Date**: 2026-02-24 | **Status**: Draft
**Depends on**: `001-arb-scanner-core` (complete), `002-arb-history-analytics` (complete)

## Problem Statement

The BM25 pre-filter operates purely on token overlap in market titles. It misses semantically equivalent contracts with different wording — e.g. "Will the Fed cut rates in Q3?" vs "Federal Reserve rate reduction by September?" scores poorly despite being the same market. This means Claude receives false positives (unrelated pairs that share tokens) and misses true positives (related pairs with no token overlap), wasting LLM budget and reducing recall.

## Solution

Add a vector embedding pre-filter stage using Voyage AI embeddings stored in pgvector. The embedding captures semantic meaning of `title + resolution_criteria`, enabling cosine similarity matching that BM25 misses. The pipeline becomes: BM25 (cheap, broad recall) → Embedding cosine filter (semantic precision) → Cache lookup → Claude (final arbiter).

## User Stories

### US1: Embedding-Enhanced Matching (P1)
**As a** market operator, **I want** the scanner to catch semantically equivalent contracts even when titles use different wording, **so that** I don't miss arbitrage opportunities.

### US2: Reduced LLM Costs (P1)
**As a** system operator, **I want** the embedding pre-filter to eliminate false positive pairs before Claude evaluation, **so that** I spend less on LLM API calls per scan cycle.

## Functional Requirements

### FR-001: Embedding Generation
The system MUST generate 512-dimensional vector embeddings for each market's `title + ". " + resolution_criteria` using Voyage AI (`voyage-3-lite` model via `voyageai` SDK). Embeddings are computed during market ingestion and stored in the `markets` table.

### FR-002: Embedding Storage
The system MUST store embeddings in a `title_embedding vector(512)` column on the `markets` table (migration 009). An HNSW index with cosine distance MUST be created for ANN search.

### FR-003: Two-Stage Pre-Filter Pipeline
The pipeline MUST run BM25 first (broad recall, top_k=20), then filter BM25 candidates by cosine similarity (threshold configurable, default 0.60). Pairs below the cosine threshold are dropped before Claude evaluation.

### FR-004: Embedding Config
The system MUST add `EmbeddingConfig` to `config.yaml` with fields: `enabled` (bool, default true), `model` (str, default "voyage-3-lite"), `api_key` (str, `${VOYAGE_API_KEY}` or fallback to `${ANTHROPIC_API_KEY}`), `cosine_threshold` (float, default 0.60), `dimensions` (int, default 512).

### FR-005: Graceful Degradation
When embeddings are unavailable (no API key, DB column missing, or `enabled: false`), the pipeline MUST fall back to BM25-only matching with no errors. The embedding stage is an enhancement, not a hard requirement.

### FR-006: Embedding Batch API
The system MUST batch embedding requests (up to 128 texts per Voyage API call) to minimize round trips during ingestion of ~1000 markets.

### FR-007: Score Passthrough
The cosine similarity score MUST replace the BM25 score in the `(Market, Market, float)` tuple passed to the semantic matcher. The Claude prompt label MUST be updated from "BM25 Score" to "Similarity Score".

## Success Criteria

- SC-001: Markets with `title_embedding IS NOT NULL` after a live scan cycle
- SC-002: Cosine similarity correctly ranks semantically similar pairs higher than BM25 alone
- SC-003: Claude API calls per scan cycle reduced by ≥30% compared to BM25-only
- SC-004: Scan still works with `embedding.enabled: false` (BM25-only fallback)
- SC-005: All quality gates pass (ruff, mypy --strict, 70% coverage)

## Out of Scope

- Replacing BM25 entirely — it remains as the cheap first stage
- Embedding-based search CLI commands — future feature
- Fine-tuning or custom embedding models
