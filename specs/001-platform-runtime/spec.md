# Feature Specification: Platform Runtime and Persistence

**Feature Branch**: `documentation/001-platform-runtime`

**Created**: 2026-08-24

**Status**: Draft

**Input**: User description: "Document the implemented runtime topology, persistence boundary, background jobs, and operational recovery behavior."

## User Scenarios & Testing

### User Story 1 - Start a Local Research Workspace (Priority: P1)

A user starts the API and web application through the stable Windows entry
point and receives a usable local workspace with database connectivity and
health endpoints.

**Why this priority**: All research features depend on a predictable local
runtime.

**Independent Test**: Start the application with the documented launcher and
verify the API live/readiness responses and the web page without exposing the
services beyond localhost.

**Acceptance Scenarios**:

1. **Given** valid local settings, **When** the API lifespan starts, **Then**
   interrupted jobs are recovered, Redis/cache state is initialized, and the
   API mounts its router under the configured prefix.
2. **Given** the API is running, **When** a client requests `/health/live` and
   `/health/ready`, **Then** each response reports the appropriate health
   state without leaking secrets.

### User Story 2 - Run Bounded Background Work (Priority: P1)

A user requests project or collection embedding and the worker performs the
job without concurrent SQLite writes.

**Why this priority**: Indexing and maintenance must not make the local
database corrupt or permanently locked.

**Independent Test**: Enqueue one project-index job and one collection-index
job, then verify serialized execution, result counts, retry behavior, and
durable job status.

**Acceptance Scenarios**:

1. **Given** an initialized embedding service, **When** the worker executes an
   indexing job, **Then** it persists the indexed count and returns the
   project or collection identifier.
2. **Given** a worker restart during a durable job, **When** the API starts
   again, **Then** the job is marked failed and can be explicitly retried.

### User Story 3 - Maintain Local Data Lifecycle (Priority: P2)

A scheduled maintenance task removes expired cache entries and purges
expired recycle-bin records according to project retention settings.

**Why this priority**: Retention and cleanup are required for predictable local
storage behavior.

**Independent Test**: Seed expired cache/trash records and run
`memory_maintenance_task`, then verify only eligible records are removed.

**Acceptance Scenarios**:

1. **Given** records past their retention deadline, **When** maintenance runs,
   **Then** the result reports separate cache and trash deletion counts.

### Edge Cases

- Redis is unavailable during API startup; reads may degrade to local cache
  layers, while queue-dependent work remains explicitly retryable.
- A job receives an invalid UUID or an uninitialized embedding service; the
  worker fails visibly instead of returning a success-shaped result.
- SQLite is locked; serialized jobs retry within their configured limit and
  retain the failure reason.
- Shutdown occurs while requests or jobs are active; resources are closed
  through the API lifespan and worker shutdown hooks.

## Requirements

### Functional Requirements

- **FR-001**: The API MUST initialize Redis, `InProcessTaskManager`, and
  `ThreeLevelCache` in its lifespan and dispose them on shutdown.
- **FR-002**: The API MUST expose live and ready health routes through
  `api/health.py`.
- **FR-003**: The worker MUST expose baseline health, project embedding,
  collection embedding, and memory maintenance jobs.
- **FR-004**: SQLite-writing worker jobs MUST be serialized with a maximum of
  one concurrent job.
- **FR-005**: Durable background jobs MUST record status and make interrupted
  work visible for explicit retry.
- **FR-006**: Database schema changes MUST use Alembic and require
  upgrade/downgrade/upgrade verification.
- **FR-007**: Runtime addresses MUST default to local interfaces and must not
  expose Redis or database files as network services.

### Key Entities

- **Project**: A user's research scope, retrieval revision, and retention
  policy.
- **Job**: Durable asynchronous work with status, attempts, timestamps, and
  error information.
- **CacheEntry**: Durable L3 cache data governed by a namespace and expiry.
- **ModelConfiguration**: Encrypted local model-provider settings.

## Success Criteria

### Measurable Outcomes

- **SC-001**: A clean local startup reaches API readiness without manual
  database DDL or service-specific setup.
- **SC-002**: No two SQLite-writing worker jobs overlap in production
  configuration.
- **SC-003**: Every interrupted job is visible as failed after the next API
  startup and is never silently discarded.
- **SC-004**: Health, job, and maintenance responses contain typed status and
  counts, with no credential values.

## Assumptions

- The deployment is single-user and local-first.
- Redis is optional for degraded reads but required for ARQ queue execution.
- The existing Alembic history and SQLAlchemy models remain the persistence
  contract.

## Implementation References

- `apps/api/src/science_buddy/main.py`
- `apps/api/src/science_buddy/worker.py`
- `apps/api/src/science_buddy/services/background_tasks.py`
- `apps/api/src/science_buddy/services/cache.py`
- `apps/api/src/science_buddy/services/memory_lifecycle.py`
- `apps/api/src/science_buddy/infrastructure/database.py`
- `apps/api/src/science_buddy/infrastructure/models.py`

