# Feature Specification: Operations and Validation Gates

**Feature Branch**: `documentation/008-operations-validation`

**Created**: 2026-08-24

**Status**: Draft

**Input**: User description: "Document the Windows launcher, local service topology, migration safety, validation commands, runtime state, and operational recovery behavior."

## User Scenarios & Testing

### User Story 1 - Start and Stop the Full Local Stack (Priority: P1)

A researcher uses the stable Windows command files to start, inspect, and stop
the web, API, worker, and optional Redis services.

**Why this priority**: Local reproducibility depends on one stable operational
entry point rather than manual process orchestration.

**Independent Test**: Run `Science Buddy.cmd`, inspect `Science Buddy Status.cmd`,
then run `Science Buddy Stop.cmd` and verify process, port, health, and state
cleanup behavior.

**Acceptance Scenarios**:

1. **Given** no conflicting process owns the configured ports, **When** the
   launcher starts, **Then** it discovers Python/npm, applies migrations,
   starts services, and waits for health checks.
2. **Given** the stack is already running, **When** the launcher is invoked
   again, **Then** startup is idempotent and does not create duplicate service
   processes.

### User Story 2 - Apply a Safe Database Migration (Priority: P1)

A maintainer backs up the local SQLite database, applies an Alembic migration,
tests downgrade and upgrade, and can recover from failure.

**Why this priority**: The database contains the durable research record and
cannot be changed irreversibly by a convenience script.

**Independent Test**: Copy a fixture database to `apps/api/data/backups/`,
execute upgrade/downgrade/upgrade, and compare schema and migration state.

**Acceptance Scenarios**:

1. **Given** a pending migration, **When** the migration workflow runs, **Then**
   a backup exists before upgrade and all three migration phases complete.
2. **Given** a migration fails, **When** validation reports the failure, **Then**
   startup does not claim the service is healthy and the cause is preserved.

### User Story 3 - Verify Release Readiness (Priority: P1)

A maintainer runs focused checks followed by API and web validation before
claiming a change is complete.

**Why this priority**: The repository's contracts span Python, TypeScript,
database, worker, and retrieval quality.

**Independent Test**: Run the documented ruff, mypy, pytest, npm lint,
typecheck, build, and relevant evaluation commands in their documented
working directories.

**Acceptance Scenarios**:

1. **Given** focused tests pass, **When** the full validation suite runs,
   **Then** failures are surfaced rather than suppressed.
2. **Given** a retrieval quality dataset is available, **When** evaluation
   runs, **Then** metrics are emitted from the deterministic evaluation CLI.

### Edge Cases

- A port is occupied; the launcher reports the conflict and does not attach to
  an unrelated process.
- Docker is unavailable; local process mode remains explicit and Compose
  configuration is not falsely reported as running.
- Redis is unavailable; API degraded reads and worker/queue unavailability
  are reported separately.
- A stale runtime PID or log file exists; status checks validate the process
  before treating it as active.
- A migration backup path is unavailable; migration stops before schema change.

## Requirements

### Functional Requirements

- **FR-001**: Stable Windows command files MUST delegate to the versioned
  launcher and must not duplicate orchestration logic.
- **FR-002**: The launcher MUST discover configured Python/npm tools, protect
  ports, run Alembic migrations, and perform API/web health checks.
- **FR-003**: Runtime PID, state, and logs MUST be stored under the ignored
  `.runtime/launcher` directory and MUST not contain secrets.
- **FR-004**: Repeated startup and explicit stop MUST be idempotent for managed
  processes.
- **FR-005**: Database migration workflows MUST back up SQLite before upgrade
  and verify upgrade, downgrade, and upgrade.
- **FR-006**: Focused checks MUST run before full validation, and unresolved
  failures MUST block a completion claim.
- **FR-007**: API validation MUST include ruff, mypy, and pytest from the
  `apps/api` working directory.
- **FR-008**: Web validation MUST include lint, typecheck, and build from the
  repository root.
- **FR-009**: Retrieval evaluation MUST use the existing
  `science_buddy.evaluation` command when its dataset is available.

### Key Entities

- **Launcher manifest**: Version selection for the current operational script.
- **Runtime state**: Managed process IDs, ports, health, and log locations.
- **Migration backup**: A pre-change SQLite copy used for recovery.
- **Validation result**: Command, working directory, exit status, and captured
  failure or success outcome.

## Success Criteria

### Measurable Outcomes

- **SC-001**: A clean machine can start the documented local stack through one
  stable command and receive passing health checks.
- **SC-002**: Duplicate startup produces no unmanaged duplicate API, web, or
  worker process.
- **SC-003**: Every migration attempt has a pre-upgrade backup and a recorded
  three-phase verification result.
- **SC-004**: A completion report names every validation command and does not
  report success when a required command fails.

## Assumptions

- Windows is the primary local operation environment.
- The launcher version manifest remains the stable compatibility seam.
- Docker Compose is supported as an alternative topology but may be
  unavailable on a developer machine.

## Implementation References

- `Science Buddy.cmd`
- `Science Buddy Stop.cmd`
- `Science Buddy Status.cmd`
- `launcher.config.json`
- `scripts/launcher/current.ps1`
- `scripts/launcher/v1/launcher.ps1`
- `docs/launcher.md`
- `apps/api/alembic/`
- `apps/api/pyproject.toml`
- `package.json`
- `Makefile`

