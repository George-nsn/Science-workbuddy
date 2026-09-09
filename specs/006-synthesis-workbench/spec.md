# Feature Specification: Synthesis and Research Workbench

**Feature Branch**: `documentation/006-synthesis-workbench`

**Created**: 2026-08-24

**Status**: Draft

**Input**: User description: "Document the implemented chapter synthesis, source intake, review pipeline, workbench notes/tasks/attachments, local OCR, plotting, and export behavior."

## User Scenarios & Testing

### User Story 1 - Build a Source-Grounded Synthesis Session (Priority: P1)

A researcher creates a synthesis session, imports local sources and workbench
notes, and receives a durable outline and chapter sections grounded in current
retrieval evidence.

**Why this priority**: The workbench turns source material into a reviewable
research document without losing provenance.

**Independent Test**: Create a session, upload two supported source formats,
run understanding and writing, and verify durable sources, sections, hashes,
and current evidence markers.

**Acceptance Scenarios**:

1. **Given** duplicate source content in different files, **When** sources are
   ingested, **Then** SHA-256 deduplication retains one source lineage.
2. **Given** a section with current evidence candidates, **When** writing is
   persisted, **Then** only current evidence IDs or local source markers
   remain in the generated prose.

### User Story 2 - Review and Selectively Rewrite Sections (Priority: P1)

A researcher reviews chapter output, receives per-section and global review
feedback, and selectively rewrites affected sections without deleting the
prior draft.

**Why this priority**: Reviewability is necessary for trustworthy long-form
research output.

**Independent Test**: Run one section review and one global review, rewrite
one section, and verify review rounds, prior draft, and updated section state.

**Acceptance Scenarios**:

1. **Given** a draft with a review finding, **When** a selective rewrite runs,
   **Then** only the selected section changes and the review/audit records
   retain the reason.
2. **Given** an organizer failure, **When** the pipeline ends, **Then** the
   scientific draft remains available and is not replaced by an empty template.

### User Story 3 - Maintain Daily Research Notes and Tasks (Priority: P2)

A researcher edits one Markdown note per project day, attaches local images,
creates tasks, and later sends relevant notes into synthesis.

**Why this priority**: Daily work is the durable input to longer synthesis and
research planning.

**Independent Test**: Create a day note, add an attachment and task, reload
the date route, and verify independent task and attachment lifecycle.

**Acceptance Scenarios**:

1. **Given** a project date, **When** the note is saved twice, **Then** the
   `(project_id, entry_date)` uniqueness rule returns one note.
2. **Given** a deleted note with attachments, **When** recycle-bin deletion
   occurs, **Then** the note and attachment enter trash while the task remains.

### User Story 4 - Render a Local Data Figure and Export (Priority: P3)

A researcher selects a Markdown table, previews a deterministic Matplotlib
figure, and exports a synthesis or evidence report.

**Why this priority**: Reproducible figures and exports make analysis usable
outside the application.

**Independent Test**: Submit a valid table to plot preview, render the PNG,
and export a fixture session; verify source hash, render specification, and
file output.

**Acceptance Scenarios**:

1. **Given** a supported Markdown data table, **When** plotting runs, **Then**
   the figure stores source table hash, source ID, table index, and render
   specification.
2. **Given** an unsupported table or failed renderer, **When** preview runs,
   **Then** the API returns a typed error and preserves the source data.

### Edge Cases

- A source is malformed, empty, or unsupported; extraction reports an explicit
  error without creating a misleading source.
- A synthesis job is interrupted by an API restart; it is marked failed and
  remains explicitly retryable.
- A source contains an unknown citation marker; it is removed or rejected
  before persistence rather than becoming a fabricated reference.
- An attachment has duplicate content or an unavailable local OCR backend;
  hash identity and OCR error state remain visible.
- A note is deleted while a task points to its date; the task remains
  independent of note lifecycle.

## Requirements

### Functional Requirements

- **FR-001**: Synthesis sessions MUST accept PDF, DOCX, TXT, Markdown, and
  pasted text through the documented API routes.
- **FR-002**: Source extraction MUST run locally and deduplicate by SHA-256
  content hash.
- **FR-003**: The synthesis pipeline MUST persist understanding, section
  drafts, review rounds, global review, selective rewrites, and agent runs.
- **FR-004**: Each section MUST receive only relevant source modules, allowed
  memory context, current retrieval evidence, and its prior draft.
- **FR-005**: Persisted prose MUST retain only current evidence IDs or approved
  local source markers; unknown references MUST be rejected or removed.
- **FR-006**: Durable synthesis jobs MUST survive browser navigation and
  report interrupted state after API restart.
- **FR-007**: A project MUST have at most one workbench note per entry date.
- **FR-008**: Workbench attachments MUST persist local path, hash, size,
  dimensions, OCR status/text/error, and note relationship.
- **FR-009**: Plot output MUST retain source table hash and render metadata.
- **FR-010**: Exports MUST be generated from validated durable session data.

### Key Entities

- **SynthesisSession/Source**: A synthesis job and its local source lineage.
- **SynthesisSection**: A chapter section with draft, status, and stable key.
- **SynthesisReviewRound/AgentRun**: Review feedback and model execution
  audit.
- **WorkbenchNote/Task/Attachment**: Daily research records and local media.
- **Figure manifest**: Source table, hash, render specification, and output.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Reopening a synthesis session restores its sources, sections,
  review rounds, jobs, and prior drafts.
- **SC-002**: No persisted synthesis citation points to an unknown or
  cross-workflow evidence ID.
- **SC-003**: Repeated plotting of identical table content and specification
  produces the same source hash and deterministic metadata.
- **SC-004**: Deleting a note never deletes an independent task before its
  retention or explicit deletion rule applies.

## Assumptions

- Source text and uploaded attachments remain on the local processing path.
- Model context budgets are selected from the existing configured window
  options.
- Markdown table rendering is deterministic and local.

## Implementation References

- `apps/api/src/science_buddy/api/synthesis.py`
- `apps/api/src/science_buddy/api/workbench.py`
- `apps/api/src/science_buddy/services/synthesis.py`
- `apps/api/src/science_buddy/services/synthesis_template.py`
- `apps/api/src/science_buddy/services/plot_planning.py`
- `apps/api/src/science_buddy/services/plot_agent.py`
- `apps/api/src/science_buddy/services/ocr.py`
- `apps/web/app/synthesis/page.tsx`
- `apps/web/app/workbench/page.tsx`
- `apps/web/app/workbench/[date]/page.tsx`

