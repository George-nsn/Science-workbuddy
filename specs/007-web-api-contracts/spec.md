# Feature Specification: Web and API Contracts

**Feature Branch**: `documentation/007-web-api-contracts`

**Created**: 2026-08-24

**Status**: Draft

**Input**: User description: "Document the implemented Next.js screens, typed API client contracts, route composition, settings, health, and user-visible error behavior."

## User Scenarios & Testing

### User Story 1 - Navigate the Research Workspace (Priority: P1)

A researcher opens the web application and navigates among dashboard, search,
library, evidence, brainstorm, memory, synthesis, workbench, and settings
screens while retaining the selected project context.

**Why this priority**: The web shell is the user's entry point to every domain
workflow.

**Independent Test**: Build the web app and visit each route with a running
API, verifying loading, empty, success, and typed error states.

**Acceptance Scenarios**:

1. **Given** the API base URL is configured or defaulted, **When** a page
   loads, **Then** requests use the shared client contract and local default
   address.
2. **Given** an API route returns a typed error, **When** the page renders,
   **Then** it shows an actionable error state without exposing credentials.

### User Story 2 - Use Typed Literature and Retrieval Data (Priority: P1)

A researcher searches literature, selects library papers, runs retrieval, and
views evidence using the TypeScript response types shared by the client.

**Why this priority**: Contract drift would make evidence workflows silently
incorrect.

**Independent Test**: Run API fixture responses through the client types and
exercise search/library/evidence pages against the corresponding routes.

**Acceptance Scenarios**:

1. **Given** a literature search response, **When** the client renders it,
   **Then** source references, identifiers, tags, and provenance fields remain
   available.
2. **Given** a retrieval response includes route errors or cache level,
   **When** evidence renders, **Then** the partial result and trace are visible.

### User Story 3 - Configure Models and Controlled Web Search (Priority: P2)

A researcher views model catalog, tests a provider, and configures optional web
search without placing a secret in the browser bundle.

**Why this priority**: External integrations must remain explicit and safe.

**Independent Test**: Exercise settings GET/PUT/test/delete routes and inspect
the network payloads and rendered configuration status.

**Acceptance Scenarios**:

1. **Given** an encrypted backend configuration, **When** the settings page
   loads, **Then** it receives status and provider metadata, not the secret.
2. **Given** web search is not authorized, **When** a search is requested,
   **Then** the API rejects it visibly and the UI does not retry silently.

### Edge Cases

- The API is unavailable during page load; the UI distinguishes connection
  failure from an empty result.
- A response adds optional fields or omits nullable fields; the client
  preserves compatible rendering and explicit null states.
- A long-running job continues after browser navigation; reopening the route
  resumes status polling from the durable job.
- A local API address is changed; `NEXT_PUBLIC_API_URL` controls the client
  without hardcoding a remote secret-bearing endpoint.

## Requirements

### Functional Requirements

- **FR-001**: The web app MUST expose the documented route pages and stable
  navigation for all implemented functional domains.
- **FR-002**: `apps/web/lib/api.ts` MUST remain the source of client response
  types and API base URL defaults.
- **FR-003**: API routers MUST be composed only through
  `api/science_buddy/api/router.py` and the configured API prefix.
- **FR-004**: Client views MUST distinguish loading, empty, partial, success,
  and typed error states.
- **FR-005**: Nullable, optional, provenance, route-error, and job-status
  fields MUST not be discarded before rendering.
- **FR-006**: Model and web-search secrets MUST never be sent to or stored in
  the browser bundle.
- **FR-007**: Long-running job views MUST poll durable status and must not
  treat browser navigation as cancellation.
- **FR-008**: API error responses MUST identify actionable input, authorization,
  availability, or scope problems without returning secret values.

### Key Entities

- **API response contract**: Typed JSON response consumed by web screens.
- **Route page**: An App Router screen for one user workflow.
- **Job status**: Durable progress/failure state used by polling screens.
- **Configuration status**: Provider metadata and configured/unconfigured
  state without secret material.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Every implemented API router has a corresponding typed client
  usage or explicit backend-only rationale.
- **SC-002**: Web type-check and build complete without contract errors.
- **SC-003**: User-visible API failures identify the next action in all primary
  workflows.
- **SC-004**: No browser network payload or built asset contains configured
  API-key/token values.

## Assumptions

- The web application is served locally on `127.0.0.1:3000`.
- The API is served locally on `127.0.0.1:8000/api/v1` by default.
- Authentication is outside the current single-user local scope.

## Implementation References

- `apps/web/lib/api.ts`
- `apps/web/app/page.tsx`
- `apps/web/app/dashboard/page.tsx`
- `apps/web/app/search/page.tsx`
- `apps/web/app/library/page.tsx`
- `apps/web/app/evidence/page.tsx`
- `apps/web/app/brainstorm/page.tsx`
- `apps/web/app/memory/page.tsx`
- `apps/web/app/synthesis/page.tsx`
- `apps/web/app/workbench/page.tsx`
- `apps/web/app/settings/page.tsx`
- `apps/api/src/science_buddy/api/router.py`
- `apps/api/src/science_buddy/api/settings.py`

