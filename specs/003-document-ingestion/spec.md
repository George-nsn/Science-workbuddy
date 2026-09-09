# Feature Specification: Local Document Ingestion and Enrichment

**Feature Branch**: `documentation/003-document-ingestion`

**Created**: 2026-08-24

**Status**: Draft

**Input**: User description: "Document the implemented local upload, parsing, section/chunk creation, PDF enrichment, and OCR behavior."

## User Scenarios & Testing

### User Story 1 - Import a Research Document Locally (Priority: P1)

A researcher uploads one PDF or a batch of PDFs, and the system creates
source-preserving document assets associated with the project.

**Why this priority**: Local source preservation is the foundation for
traceable retrieval.

**Independent Test**: Upload a text PDF through `/documents/upload`, then
verify the asset hash, parser status, source path, and project association.

**Acceptance Scenarios**:

1. **Given** a supported PDF, **When** upload completes, **Then** a
   `DocumentAsset` records content hash, source, parser version, parse status,
   and extraction metadata.
2. **Given** a batch with duplicate files, **When** batch ingestion runs,
   **Then** content-hash deduplication avoids duplicate assets while reporting
   each input result.

### User Story 2 - Preserve Structure for Retrieval (Priority: P1)

A researcher opens an imported document and can trace chunks back to section,
page, character offsets, and neighboring context.

**Why this priority**: Retrieval must cite the original location rather than
an opaque text fragment.

**Independent Test**: Parse a document with headings and multiple pages, then
assert `Section` and `Chunk` records retain paths, offsets, hashes, and
adjacency.

**Acceptance Scenarios**:

1. **Given** a structured document, **When chunking runs, **Then** sections
   and chunks preserve source order and section paths.
2. **Given** an oversized text unit, **When chunking applies its token limit,
   **Then** it splits deterministically without losing source offsets.

### User Story 3 - Enrich PDFs Without Cloud Upload (Priority: P2)

A researcher requests extractive summary and keyword enrichment for local PDFs,
and the result remains derived local data.

**Why this priority**: Enrichment improves organization without exposing
private documents to a cloud model.

**Independent Test**: Run `extract_pdf_enrichment_from_text()` on fixture text
and verify bounded summary and local keyword output.

**Acceptance Scenarios**:

1. **Given** a parsed PDF, **When enrichment runs, **Then summary and keywords
   are deterministic, bounded, and linked to the source asset.
2. **Given** OCR is unavailable, **When an image-only document is submitted,
   **Then** the API reports the unavailable local OCR path explicitly.

### Edge Cases

- A PDF is malformed or has no extractable text; parsing returns a typed
  `DocumentParseError` or an explicit rejected status.
- An optional Docling or GROBID backend is not installed; the fallback path
  does not pretend that richer structure was extracted.
- The same content appears at different file paths; the content hash remains
  the deduplication key.
- A document contains very long sentences or empty headings; chunking retains
  valid bounded units and does not create empty chunks.
- A user uploads unsupported media; the API rejects it without persisting
  misleading document metadata.

## Requirements

### Functional Requirements

- **FR-001**: Upload routes MUST support single and batch document imports and
  return per-file outcomes.
- **FR-002**: Parser selection MUST be explicit among Europe PMC XML, text PDF,
  optional Docling/GROBID, and fallback implementations.
- **FR-003**: Every persisted document asset MUST include content hash, source,
  parse status, parser version, and extraction metadata.
- **FR-004**: Section and chunk records MUST preserve page/section locations,
  character offsets, content hashes, and adjacency where available.
- **FR-005**: Original uploaded assets MUST be immutable; enrichment and parsed
  output MUST be derived records.
- **FR-006**: Local PDF enrichment MUST produce bounded extractive summaries
  and keywords without sending PDF text to a cloud model.
- **FR-007**: OCR MUST use the local service when configured and report
  `OcrUnavailableError` when it cannot run.
- **FR-008**: Chunk creation MUST enforce finite token limits and deterministic
  source ordering.

### Key Entities

- **DocumentAsset**: An immutable uploaded or imported source file and its
  provenance.
- **Section**: A structured document section with source location.
- **Chunk**: A bounded retrieval unit with offsets, hash, and adjacency.
- **ChunkEmbedding**: A later vector representation of a chunk and model.
- **PdfEnrichment**: Derived extractive summary and keyword data.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Re-importing identical content creates no duplicate asset,
  section, or chunk lineage.
- **SC-002**: Every retrieved chunk can identify its source asset and a
  non-empty location or offset.
- **SC-003**: Enrichment completes without any outbound model request
  containing uploaded PDF text.
- **SC-004**: Unsupported, malformed, and OCR-unavailable inputs return
  explicit typed failures rather than success-shaped empty data.

## Assumptions

- Uploaded files are stored under the configured local data directory.
- PDF text extraction is preferred; OCR is opt-in and local.
- The existing chunk-token target and parser implementations are the current
  behavior contract.

## Implementation References

- `apps/api/src/science_buddy/api/documents.py`
- `apps/api/src/science_buddy/services/documents.py`
- `apps/api/src/science_buddy/services/document_ingestion.py`
- `apps/api/src/science_buddy/services/chunking.py`
- `apps/api/src/science_buddy/services/pdf_enrichment.py`
- `apps/api/src/science_buddy/services/ocr.py`
- `apps/api/src/science_buddy/infrastructure/models.py`

