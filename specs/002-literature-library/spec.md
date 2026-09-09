# Feature Specification: Literature Discovery and Library

**Feature Branch**: `documentation/002-literature-library`

**Created**: 2026-08-24

**Status**: Draft

**Input**: User description: "Document the implemented scholarly source discovery, normalization, import, library, archive, tag, and RAG collection behavior."

## User Scenarios & Testing

### User Story 1 - Search Multiple Scholarly Sources (Priority: P1)

A researcher submits one query and receives a reproducible, fairly interleaved
result set from PubMed, Europe PMC, OpenAlex, and Crossref.

**Why this priority**: Source breadth and provenance are the first step in
evidence-based research.

**Independent Test**: Stub each provider, execute `/literature/search`, and
verify normalized records, source references, a search-run identifier, and
partial-failure reporting.

**Acceptance Scenarios**:

1. **Given** provider responses containing overlapping PMID/DOI records,
   **When** discovery merges them, **Then** one candidate retains all valid
   source references and normalized metadata.
2. **Given** one provider times out, **When** the search completes, **Then**
   available sources are returned with a visible route error.

### User Story 2 - Import Papers into a Project (Priority: P1)

A researcher selects literature results and imports them idempotently into the
project library.

**Why this priority**: Imported papers become the durable scope for retrieval,
tags, and collections.

**Independent Test**: Import the same normalized record twice and verify one
paper and one project association with preserved provenance.

**Acceptance Scenarios**:

1. **Given** a paper with PMID, DOI, or provider identity, **When** it is
   imported, **Then** the library persists normalized identifiers and metadata.
2. **Given** the same paper is imported again, **When** ingestion runs,
   **Then** no duplicate paper or project association is created.

### User Story 3 - Curate Tags, Archives, and Collections (Priority: P2)

A researcher filters papers by tags, creates a named archive, and builds a
deduplicated RAG collection for scoped retrieval.

**Why this priority**: Project organization controls what later workflows can
  retrieve.

**Independent Test**: Create automatic tags, edit one paper's tags, create an
archive and collection, then verify filtered results and collection scope.

**Acceptance Scenarios**:

1. **Given** manually curated tags, **When** automatic refresh runs, **Then**
   curated tags remain unchanged until the user resets them.
2. **Given** a collection containing repeated paper selections, **When** it is
   saved, **Then** the collection contains one project-validated paper entry
   per paper and reports vector/graph status.

### Edge Cases

- A provider returns malformed dates, empty titles, or invalid identifiers;
  parsers omit invalid records without corrupting valid results.
- Provider rate limits or HTTP 5xx failures are recorded and do not erase
  existing library data.
- A paper is retracted; the library preserves the signal and displays it.
- An archive name differs only by case or whitespace; it appends to the
  existing archive instead of duplicating the paper set.
- A collection has no vector backend; graph-only creation remains available
  and vector status remains pending.

## Requirements

### Functional Requirements

- **FR-001**: The discovery service MUST normalize DOI, provider identifiers,
  publication dates, authors, publication types, and MeSH headings.
- **FR-002**: The discovery service MUST merge duplicate records and retain
  every valid source reference.
- **FR-003**: Provider calls MUST use the shared asynchronous rate limiter.
- **FR-004**: Literature import MUST be idempotent for normalized paper
  identifiers and project associations.
- **FR-005**: Library responses MUST expose retraction, open-access,
  citation, metadata-provenance, and journal-metric signals when available.
- **FR-006**: Automatic tags MUST distinguish `auto`, `manual`, and
  `brainstorm` origins.
- **FR-007**: Manual tag curation MUST block automatic replacement until an
  explicit reset operation.
- **FR-008**: RAG collections MUST deduplicate papers and enforce project scope
  for vector and graph queries.
- **FR-009**: Hidden selections MUST be cleared when a library result set
  changes.

### Key Entities

- **Paper**: Normalized scholarly metadata and provenance.
- **PaperIdentifier**: A cross-source PMID, PMCID, DOI, or provider identity.
- **ProjectPaper**: The association between a project and a paper.
- **Tag/PaperTag**: Project-scoped automatic or curated classification.
- **LiteratureArchive**: A named project grouping with archive-paper links.
- **RagCollection/CollectionPaper**: A retrieval scope and its validated paper
  membership.
- **SearchRun**: A reproducible search request and provider-result summary.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Repeating the same import produces no duplicate paper or
  project-paper rows.
- **SC-002**: A search response identifies every contributing provider and
  every provider error without requiring log inspection.
- **SC-003**: A manually curated paper's tags remain stable across automatic
  refreshes until reset.
- **SC-004**: Every collection query can be proven to use only its project and
  collection membership.

## Assumptions

- Provider credentials and rate limits are configured by the backend.
- The project library is the source of truth for user organization.
- OpenAlex journal metrics are retrieval signals, not Clarivate JIF/JCR or
  SCImago rankings.

## Implementation References

- `apps/api/src/science_buddy/api/literature.py`
- `apps/api/src/science_buddy/services/literature/discovery.py`
- `apps/api/src/science_buddy/services/literature/pubmed.py`
- `apps/api/src/science_buddy/services/literature/europe_pmc.py`
- `apps/api/src/science_buddy/services/literature/openalex.py`
- `apps/api/src/science_buddy/services/literature/crossref.py`
- `apps/api/src/science_buddy/services/literature/ingestion.py`
- `apps/api/src/science_buddy/services/library_management.py`
- `apps/api/src/science_buddy/services/tags.py`
- `apps/api/src/science_buddy/services/collections.py`

