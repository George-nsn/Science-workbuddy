# Feature Specification: Brainstorm, Controlled Research, and Memory

**Feature Branch**: `documentation/005-brainstorm-research-memory`

**Created**: 2026-08-24

**Status**: Draft

**Input**: User description: "Document the implemented brainstorm sessions, plan discovery, controlled dynamic research, bounded agent workflow, memory context, audits, and lifecycle behavior."

## User Scenarios & Testing

### User Story 1 - Explore a Research Topic (Priority: P1)

A researcher starts a brainstorm session, receives bounded question,
experiment, and critic outputs, and can inspect the route and evidence
supporting the proposal.

**Why this priority**: Topic exploration turns an open research idea into a
falsifiable, reviewable direction.

**Independent Test**: Create a session through `/brainstorm/sessions`, submit
one message, and verify persisted messages, agent runs, evidence scope, and
bounded output.

**Acceptance Scenarios**:

1. **Given** an exploration session with an authorized project scope, **When**
   a turn runs, **Then** the orchestrator records each agent result and a
   terminal coordinator state.
2. **Given** a high-risk topic, **When** `safety_mode()` classifies it, **Then**
   required ethics/biosafety gates are surfaced and hazardous optimization is
   not generated.

### User Story 2 - Refine and Freeze a Research Plan (Priority: P1)

A researcher uploads an original topic document, answers preference questions,
reviews generated directions, and confirms a derived final version without
overwriting the original.

**Why this priority**: Versioned refinement preserves the user's intent and
creates an auditable transition from idea to plan.

**Independent Test**: Upload a text/Markdown original, generate plan
directions, submit feedback, confirm a version, and verify parent links,
change log, and frozen state.

**Acceptance Scenarios**:

1. **Given** an immutable original version, **When** feedback is applied,
   **Then** a new child version is created and the original remains unchanged.
2. **Given** a plan with missing non-critical preferences, **When** plan
   discovery normalizes the output, **Then** safe defaults or a bounded
   preference question set is returned.

### User Story 3 - Run Bounded Dynamic Research (Priority: P1)

A researcher authorizes a controlled research run that plans claims, chooses
allowed actions, retrieves evidence, judges sufficiency, and stops at its
budget or terminal condition.

**Why this priority**: Research expansion must improve recall without creating
an unbounded autonomous agent.

**Independent Test**: Execute a run with a small round/action budget and verify
plan snapshots, action audits, sufficiency assessment, and terminal status.

**Acceptance Scenarios**:

1. **Given** a retryable provider failure, **When** the action policy evaluates
   it, **Then** retry/backoff is recorded with a bounded retry count.
2. **Given** sufficient verified evidence or an exhausted budget, **When** the
   round completes, **Then** no further action is scheduled.

### User Story 4 - Recall Governed Project Memory (Priority: P2)

A brainstorm, research planner, or synthesis workflow recalls only the memory
types allowed for that consumer, without treating memory as current evidence.

**Why this priority**: Memory provides continuity while preserving evidence
boundaries.

**Independent Test**: Seed project facts, verified claims, steps, and failed
routes; call the memory context endpoint for each consumer; verify filtering
and `evidence_eligible=false`.

**Acceptance Scenarios**:

1. **Given** a brainstorm consumer, **When** context recall runs, **Then**
   only project facts, research steps, and failed routes are returned.
2. **Given** an expired trash record, **When** maintenance runs, **Then** it is
   purged only after the configured retention deadline.

### Edge Cases

- A model returns fenced JSON, a restricted YAML subset, Python dictionaries,
  or truncated structured output; parsing retries or falls back explicitly.
- A model call fails after a scientific draft is produced; the draft remains
  available rather than being replaced by a generic template.
- Evidence from another session, collection, or historical run is cited; it
  is rejected as outside the current candidate set.
- A research action is unauthorized, invalid, or has an identical empty
  query; it is not blindly retried.
- Deleting a project-scoped memory item affects recycle-bin and retention
  state without deleting independent workbench tasks.

## Requirements

### Functional Requirements

- **FR-001**: Brainstorm sessions MUST persist messages, versions, agent runs,
  literature links, and statuses.
- **FR-002**: Exploration and refinement MUST use typed agent roles and
  bounded draft-review-refine-organize rounds.
- **FR-003**: Original uploaded topic documents MUST be immutable; derived
  versions MUST include parent and change-log relationships.
- **FR-004**: High-risk topics MUST trigger explicit safety/ethics gates and
  MUST NOT receive hazardous operational optimization.
- **FR-005**: Dynamic research MUST persist a plan snapshot, round state,
  proposed actions, policy decisions, audits, claims, and sufficiency result.
- **FR-006**: Every dynamic research run MUST terminate on sufficiency,
  budget, round, authorization, or unrecoverable failure.
- **FR-007**: Memory context MUST enforce consumer-specific allowed memory
  types and mark all recalled items as not evidence-eligible.
- **FR-008**: Retryable and non-retryable failures MUST be classified and
  retained with their retry reason.
- **FR-009**: Retention, trash, restore, and permanent deletion MUST follow
  project policy and preserve independent task lifecycle.

### Key Entities

- **BrainstormSession/Message/Version**: A session conversation and immutable
  refinement history.
- **BrainstormAgentRun**: Prompt/model/input hash, output, status, and error.
- **ResearchRun/PlanSnapshot/Round**: A bounded controlled investigation.
- **ResearchActionAudit**: Requested action, authorization, result, and retry
  decision.
- **ProjectFact/ResearchStepMemory/RetrievalRouteMemory**: Governed durable
  project context and route experience.
- **MemoryDerivedIndex**: Replaceable semantic index over allowed memory types.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Every brainstorm and research run has a terminal status and an
  auditable sequence of agent/action records.
- **SC-002**: No refinement operation changes the persisted original version.
- **SC-003**: No recalled memory item is accepted by the current-workflow
  evidence verifier without fresh retrieval evidence.
- **SC-004**: Unauthorized, invalid, and non-retryable actions produce no
  repeated external call.

## Assumptions

- Users explicitly authorize project/collection scope and optional external
  search for each relevant workflow.
- The model provider is replaceable behind `domain/providers.py`.
- Retention and safety policy are project-scoped and may be configured by the
  user within repository constraints.

## Implementation References

- `apps/api/src/science_buddy/api/brainstorm.py`
- `apps/api/src/science_buddy/api/research.py`
- `apps/api/src/science_buddy/api/memory.py`
- `apps/api/src/science_buddy/services/brainstorm.py`
- `apps/api/src/science_buddy/services/brainstorm_plan.py`
- `apps/api/src/science_buddy/services/brainstorm_sessions.py`
- `apps/api/src/science_buddy/services/dynamic_research.py`
- `apps/api/src/science_buddy/services/controlled_research.py`
- `apps/api/src/science_buddy/services/research_audit.py`
- `apps/api/src/science_buddy/services/memory_context.py`
- `apps/api/src/science_buddy/services/memory_retrieval.py`
- `apps/api/src/science_buddy/services/memory_lifecycle.py`

