# Feature Specification: Retrieval, Evidence, Vectors, and Graph

**Feature Branch**: `documentation/004-retrieval-evidence-graph`

**Created**: 2026-08-24

**Status**: Draft

**Input**: User description: "Document the implemented query planning, hybrid retrieval, local vector store, evidence verification, cache revision, and graph retrieval behavior."

## User Scenarios & Testing

### User Story 1 - Retrieve a Scoped Evidence Set (Priority: P1)

A researcher submits a question and receives ranked chunks from exact,
full-text, metadata, MeSH, vector, and bounded graph routes.

**Why this priority**: The evidence set is the contract consumed by analysis
and synthesis.

**Independent Test**: Seed papers, chunks, metadata, vectors, and graph edges,
execute `/retrieval/search`, and verify route traces, fused ranks, deduplication,
paper caps, and collection scope.

**Acceptance Scenarios**:

1. **Given** the same query and data, **When** hybrid retrieval runs twice,
   **Then** route ordering, Weighted RRF scores, and selected evidence blocks
   are identical.
2. **Given** a collection query, **When** graph and vector routes run, **Then**
   every candidate belongs to the collection's validated paper set.

### User Story 2 - Cite Evidence Mechanically (Priority: P1)

A model-generated analysis references opaque evidence IDs, and the verifier
accepts only current candidate IDs with matching excerpts and locations.

**Why this priority**: Mechanical citation checks prevent unsupported claims.

**Independent Test**: Issue tokens for fixture candidates, submit valid and
invalid citations, and assert accepted IDs, rejected IDs, and explicit errors.

**Acceptance Scenarios**:

1. **Given** a current candidate and matching excerpt, **When verification
   runs, **Then** the citation is accepted with its source locator.
2. **Given** a historical or unknown evidence ID, **When verification runs,
   **Then** the citation is rejected and cannot support a final claim.

### User Story 3 - Use Local Vector and Graph Indexes (Priority: P2)

A worker embeds chunks into SQLite and a researcher uses deterministic graph
routes without requiring a separate vector database or Neo4j.

**Why this priority**: The local deployment must provide semantic and graph
recall with bounded infrastructure.

**Independent Test**: Encode/decode fixture vectors, index them, query Top-K,
rebuild graph nodes, and run local/global/path retrieval.

**Acceptance Scenarios**:

1. **Given** a finite 768-dimensional vector, **When it is stored and loaded,
   **Then** byte length, dtype, normalization, and dimension checks pass.
2. **Given** a graph rebuild request, **When deterministic sources are
   unchanged, **Then** node/edge identifiers and scoped results are stable.

### Edge Cases

- A query has no exact, FTS, vector, or graph matches; the response reports an
  empty evidence set rather than inventing a result.
- A vector contains NaN, infinity, wrong dimension, or unknown model; storage
  rejects it explicitly.
- A retrieval revision changes after indexing or tagging; stale cache entries
  are bypassed.
- A citation excerpt differs from the signed candidate span; mechanical
  verification rejects it.
- Redis is unavailable; L1/L3 behavior remains explicit and no stale result
  crosses a revision boundary.

## Requirements

### Functional Requirements

- **FR-001**: Query planning MUST classify, normalize, and expand queries
  deterministically, including bilingual and MeSH terms where applicable.
- **FR-002**: Hybrid retrieval MUST support exact, FTS5, metadata, MeSH,
  dense-vector, and bounded graph routes.
- **FR-003**: Weighted Reciprocal Rank Fusion MUST record route traces and
  produce deterministic fused rankings.
- **FR-004**: Retrieval MUST deduplicate content, cap chunks per paper, and
  attach adjacent source blocks when available.
- **FR-005**: Vector storage MUST use finite normalized float32 BLOBs with
  explicit model and expected-dimension checks.
- **FR-006**: Cache keys MUST include project retrieval revision and applicable
  collection scope.
- **FR-007**: Evidence tokens MUST be opaque and valid only for the current
  workflow candidate set.
- **FR-008**: Mechanical verification MUST validate evidence ID, excerpt,
  paper, section, and source location before synthesis.
- **FR-009**: Graph nodes and edges MUST preserve source spans, deterministic
  identifiers, and `collection:{id}` isolation.

### Key Entities

- **QueryPlan**: Normalized query type, expansions, and route strategy.
- **RetrievalCandidate/FusedChunk**: A ranked source chunk and its fused score.
- **ChunkEmbedding**: A model-specific normalized vector BLOB.
- **Evidence token**: An opaque ID mapped to a current source span.
- **GraphNode/GraphEdge**: Deterministic scoped literature relationships.
- **CacheEntry**: Revision-aware durable cache value.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Identical inputs produce identical route traces, fused ranks, and
  evidence IDs within one workflow.
- **SC-002**: Zero citations with unknown, stale, or mismatched evidence IDs
  reach a persisted final answer.
- **SC-003**: Vector indexing rejects every invalid dimension or non-finite
  value before persistence.
- **SC-004**: Collection retrieval returns no paper outside collection
  membership, including graph expansion.

## Assumptions

- The default semantic model is local `multilingual-e5-base` with 768
  dimensions.
- SQLite is the durable vector and graph store for the current deployment.
- Current-workflow evidence IDs are the only citation authority for generated
  claims.

## Implementation References

- `apps/api/src/science_buddy/api/literature.py`
- `apps/api/src/science_buddy/api/graph.py`
- `apps/api/src/science_buddy/services/query_planning.py`
- `apps/api/src/science_buddy/services/retrieval.py`
- `apps/api/src/science_buddy/services/vector_store.py`
- `apps/api/src/science_buddy/services/evidence.py`
- `apps/api/src/science_buddy/services/structured_evidence.py`
- `apps/api/src/science_buddy/services/knowledge_graph.py`
- `apps/api/src/science_buddy/services/graphrag.py`
- `apps/api/src/science_buddy/services/retrieval_cache.py`

