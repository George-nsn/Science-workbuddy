# Science Buddy

Science Buddy is a local-first, privacy-preserving research assistant and workbench for universal academic and scientific literature. It uses SQLite (WAL mode) for durable literature/vector/graph/memory storage, Redis for optional cross-process cache and asynchronous jobs, and deterministic contracts for cryptographically traceable RAG workflows across natural sciences, computing & AI, engineering, physical sciences, and interdisciplinary research.

> Academic and research assistance only. It is not intended to replace professional peer review, domain experts, statisticians, or formal laboratory/computational standard operating procedures (SOPs).

## Documentation

- [docs/project-introduction.md](docs/project-introduction.md) — detailed project introduction (Chinese) with architecture and directory-structure diagrams.
- [docs/project-walkthrough.md](docs/project-walkthrough.md) — end-to-end code walkthrough (Chinese): data layer, ingestion, RAG, routing, memory, tool use, context management, shortcomings and feasibility.
- [docs/structure.md](docs/structure.md) — current directory layout and maintenance conventions.
- [docs/architecture.md](docs/architecture.md) — English architecture notes (trustworthy RAG path, memory context, agent workflow).
- [docs/architecture-zh.md](docs/architecture-zh.md) — Chinese architecture/mechanism deep dive (2026-08-06 snapshot, formerly `struct.md`).
- [docs/project-memory.md](docs/project-memory.md) — durable engineering memory and change log.
- [temp/README.md](temp/README.md) — staging area for one-off scripts and regenerable artifacts.

## Current scope

Implemented:

- Next.js research workbench with literature search, library management, single-PDF or whole-folder PDF upload, and evidence retrieval pages.
- Local, source-preserving PDF enrichment: extractive article summaries, explicit/domain keyword tags, project-level rebuild, and content-hash deduplication without sending PDF text to a cloud model.
- Model and privacy settings page with provider/model selection, connection testing, and encrypted local API Key storage.
- FastAPI liveness and readiness endpoints.
- Live PubMed ESearch/EFetch, Europe PMC core search, OpenAlex Works, and Crossref Works adapters with per-source rate limiting, fair interleaving, and partial-success fallback.
- Cross-source PMID/DOI deduplication, reproducible search snapshots, and idempotent library ingestion.
- Automatic tags from MeSH, publication type, source, and evidence depth; manual replacement and one-click reset to automatic tags.
- Library search plus multi-tag AND filtering, visible-result selection, and named or automatically named RAG collections.
- Collection-scoped vector indexing, deterministic knowledge graphs, evidence retrieval, and model-assisted answers.
- Human-in-the-loop topic brainstorming with exploration/refinement modes, selectable RAG scope, audited specialist-agent outputs, immutable uploaded originals, derived versions, Mermaid routes, and evidence-gated material candidates.
- Explicitly authorized brainstorm exploration automatically supplements from four scholarly sources toward 30 deduplicated session papers; narrow queries or unavailable providers may legitimately yield fewer.
- OpenAlex journal metrics shown beside journal names as `OA 2-year impact` and same-topic `OA-Q1..Q4`; these are not Clarivate JIF/JCR, CAS, or SCImago SJR quartiles and contribute at most a 10% retrieval prior.
- Europe PMC open-full-text XML import and text-based PDF parsing with explicit OCR rejection.
- Structure-aware section/chunk creation with source offsets, page locations, hashes, and adjacency.
- Bilingual query planning plus exact ID, SQLite FTS5, metadata, MeSH, E5-vector, and bounded graph retrieval.
- Weighted RRF, content-hash deduplication, per-paper diversity, adjacent evidence blocks, and tamper-evident evidence IDs.
- Local `multilingual-e5-base` semantic recall with normalized 768-dimensional float32 BLOB vectors and NumPy exact Top-K ranking.
- Deterministic full GraphRAG v1 with grounded entities/mentions/relations, hierarchical communities, community reports, and Local/Global/DRIFT/Path retrieval.
- Three-level retrieval cache: in-process LRU/TTL → Redis → durable SQLite.
- ARQ worker tasks for health checks and project embedding indexing.
- Protocols for chunking, embedding, hybrid retrieval, model providers, and evidence verification.
- Controlled multi-agent state contracts and deterministic state machine tests.
- Brainstorm prompts assume ordinary institutional compliance for standard lawful experiments and do not repeatedly spend tokens on generic safety/ethics boilerplate. A short non-operational guard remains only for genuinely high-risk topics such as BSL-3/4, gain-of-function, select agents, toxins, germline editing, or directly identifiable human data.
- Every brainstorm design is requested as an end-to-end research workflow—from falsifiable question and pilot validation through controlled main experiments, orthogonal confirmation, statistics, decision gates, negative-result interpretation, and reproducibility. Missing non-critical context is filled with explicit provisional assumptions and pilot-calibration gates instead of stopping the design.
- Brainstorm prompts are goal-driven rather than checklist-driven: the model chooses the number of stages, sections, work packages, Mermaid nodes, and appropriate scientific detail. Experiment, method, critic, and coordinator Agents receive different output budgets; long DeepSeek outputs get a larger length-aware retry before deterministic fallback. New results disclose whether every Agent completed with model output or which Agents required fallback assistance.
- Experiment and method generation now use a bounded draft-review-refine-organize pipeline. The scientific draft is genuinely free Markdown—providers do not receive `response_format`, a JSON schema, or schema-field instructions during drafting. A concise reviewer may request targeted additions; the model refines at most once in Balanced/Deep or twice in Max; only the final organizer maps the preserved draft into application fields. Reviewer approval, repeated feedback, no material growth, sub-call failure, or the round limit stops the loop. Coordinator uses free drafting followed by one final organization step. Every draft/review/refine step is audited, and a failed organizer preserves the scientific draft instead of replacing it with a generic template. This follows the bounded-state/termination principles used by LangGraph, AutoGen, and OpenAI Agents without adding those frameworks as runtime dependencies.
- Mermaid technical routes use a consistent publication-style visual theme with scientific colors, curved links, decision nodes, stage spacing, responsive scrolling, and an end-to-end workflow card header while retaining strict Mermaid security mode.
- Structured model responses prefer JSON but safely recognize fenced/wrapped JSON, a restricted YAML subset, Python dictionaries, TOML, and XML before enforcing the same Pydantic/evidence/safety contracts; truncated objects still retry or fall back safely.
- Per-session model depth and bounded context budgets are selectable; new brainstorm sessions default to deep reasoning with a 128K total context budget, while evidence answers default to balanced/64K.
- Durable memory governance: project-scoped recycle bin, configurable retention, hourly cache/trash maintenance, deterministic brainstorm summaries, and source-grounded project facts with confidence/importance/status controls.
- Controlled Tavily web search configuration with encrypted keys, clipped HTTPS-only results, explicit per-session authorization, and no model-side search tools.
- Brainstorm Plan workflow: RAG/project-memory/recent-research direction discovery, generated preference questions, direction selection, persisted experiment preferences, and one-click bounded technical-route/proposal generation. Direction discovery and final Plan generation are persisted background jobs, so navigating to another module or refreshing the browser does not cancel them; reopening the session resumes status polling. The Plan-specific normalizer safely accepts a single direction, a top-level direction array, approved field aliases, or missing preference questions; it preserves only directions containing a model-supplied title and rationale, fills the non-scientific constraint questionnaire from a fixed backend template, and keeps Evidence ID and controlled HTTPS-source validation strict.
- Evidence IDs are signed for one retrieval workflow and are not copied from Plan discovery into final proposal generation. If an Agent nevertheless emits a stale or unknown ID, the Evidence Guard removes it recursively from structured lists and free text, marks the affected statement as unsupported, records a dedicated guard audit, and re-runs strict validation. Unknown IDs never become accepted citations, but one bad reference no longer discards the rest of an otherwise usable multi-Agent result.
- Workbench Plot Agent: parse GFM Markdown tables, optionally use the configured LLM for one declarative intent/style plan, render publication-style PNG locally with Matplotlib, show a debounced no-write local preview, place a resizable plot card directly below and independently from its source table, hide/show that table without deleting it, drag the card with a grab cursor to reorder it in the Markdown document, and double-click the card to edit typography, palette, line/point styling, size, DPI, grid, and legend placement. Plot text is not edited directly on the PNG.
- Replicate-aware plots accept long observations, wide Rep1/Rep2/Rep3 columns, or pre-aggregated tables; they distinguish biological from technical replicates, support independent or explicitly identified paired measurements, show raw observations and per-group n, and calculate sample SD, SEM, or a 95% small-sample t interval without inventing significance tests.
- Plot replicate auto-detection is deterministic: long layout requires repeated condition rows with one response column and no error column; wide layout requires at least two numeric columns whose names match replicate patterns such as Rep1/R1/重复1/复孔2; SD/SE/SEM/CI/error-labelled numeric columns select the pre-aggregated path. Biological versus technical units and paired versus independent designs remain explicit user choices. The plot editor shows the detected layout and per-group n after rendering.
- Enabling LLM plot planning now triggers planning even when the intent box is empty, using an automatic table/replicate-aware intent. Live preview remains local and is explicitly labelled as not representing the LLM result; final generate/rerender calls the model once. DeepSeek planning uses quick/low reasoning with an 8192-token output budget and one 16384-token retry, then reports whether model planning was applied or deterministic fallback was used. Consent, planning source, and rationale persist with the plot and reappear when editing.
- Persistent thesis/review synthesis workbench: aggregate structural template distillation, all-workbench-note snapshotting, multi-file PDF/DOCX/TXT/Markdown supplements, Document Understanding/Writer/Reviewer stages, chapter-level drafts and summaries, bounded global review with selective rewrites, controlled online literature, signed citations, local table-to-figure generation, Markdown/DOCX export, and durable background-job status. Chapter context options range from 32K to 1M subject to the configured model's actual window. The distillation method is documented in [docs/thesis-distillation.md](docs/thesis-distillation.md).
- Unified Memory Context API for brainstorm, evidence-question planning, and synthesis with consumer-specific allowlists. Project facts, verified Claim—Evidence matrices, append-only research steps, and failed-route experience share one recall contract while remaining non-Evidence planning context. Historical StepMemory cannot support conclusions; synthesis uses verified priors to prioritize current retrieval. Failed routes persist query, tool, scope, result count, reason, and a conservative retry recommendation.

Implemented model-assisted path: backend-only OpenAI-compatible or Anthropic configuration; controlled Evidence Analyst → semantic Verifier → Synthesizer calls; mechanical Evidence ID validation; persisted research runs; Markdown and DOCX evidence reports. Missing or invalid model configuration returns an explicit error rather than a fabricated answer.

Not implemented yet: generalized historical PDF table/figure backfill, systematic-review screening, meta-analysis, or free-running autonomous agents. Workbench OCR and table-to-plot visualization are implemented locally; structured PDF table/cell evidence remains deliberately narrower.

## Prerequisites

- Node.js 22 or newer
- Python 3.12 or newer
- Docker Desktop with Docker Compose (recommended full stack)

## Configure

Copy `.env.example` to `.env`. Cloud model keys are always handled by the backend and are never stored in browser storage.

The Plot Agent does not require a separate API key. Local table parsing and PNG rendering always work without a model. If the user explicitly enables model planning for a table, it reuses the model configured on the settings page and sends only that selected table and plotting intent; the model returns a validated declarative plan and never executes plotting code.

The live plot preview is always deterministic and local, even when model planning is enabled, so changing a slider does not repeatedly call the model. A configured model is called at most once when the user generates or rerenders the final plot. Technical repeats are labeled separately and must not be interpreted as independent biological sample size.

ClinicalTrials.gov, BLAST, OncoKB, bio.tools, and generic MCP currently appear only as disabled registry entries. They are not connected or executable, so no API key is currently requested for them. Future OncoKB execution would normally require an authorized token; the requirements of a future MCP server would depend on that server.

Set `NCBI_EMAIL` to the registered developer email used with the configured `NCBI_TOOL`. Without an NCBI API key the adapter stays at 3 requests/second; with a key it uses at most 10 requests/second. Change `EVIDENCE_SIGNING_KEY` before any non-local deployment.

For model-assisted answers, open **模型与隐私** in the Web sidebar, select a manufacturer and model, enter the API Key, optionally test the connection, then save. Presets are included for OpenAI, Anthropic, DeepSeek, Google Gemini, Alibaba Qwen, Kimi, Zhipu GLM, SiliconFlow, OpenRouter, the official GitHub Copilot SDK, Ollama, LM Studio, and custom OpenAI-compatible endpoints. Each preset supplies the matching protocol, Base URL, and model IDs; custom model IDs remain available. The Key is encrypted before being persisted in local SQLite and is never returned to the browser. Set `MODEL_CONFIG_ENCRYPTION_KEY` for a dedicated encryption secret; otherwise `EVIDENCE_SIGNING_KEY` is used. Changing either encryption secret requires entering the API Key again.

GitHub Models inference was retired on July 30, 2026, so this project does not use its obsolete endpoint. The GitHub Copilot option uses GitHub's official Copilot SDK in `empty` mode with an empty tool allowlist, no host file/Git access, no skills, no session store, and telemetry disabled. It requires a Copilot subscription and uses the locally signed-in Copilot CLI account by default; an eligible GitHub OAuth token can be supplied instead. Available models are subscription-dependent, so the UI uses Copilot's `auto` route.

Select the dedicated **DeepSeek** manufacturer rather than combining the Anthropic/OpenAI preset with a DeepSeek URL. The current DeepSeek preset uses `https://api.deepseek.com` with `deepseek-v4-pro` or `deepseek-v4-flash`. Per the official V4 API guide, these models expose a 1,000,000-token context window; the backend sends OpenAI-format `thinking={"type":"enabled"}` plus `reasoning_effort=low|high|max`, JSON Output, and `max_tokens` only for the dedicated DeepSeek provider. New brainstorm sessions default to `max` and 1M. Ollama and LM Studio use loopback HTTP endpoints and do not require a stored API Key.

Deployment operators can instead configure one of:

- `LLM_PROVIDER=openai_compatible`, plus `LLM_MODEL`, `LLM_API_KEY`, and optionally `LLM_BASE_URL`.
- `LLM_PROVIDER=anthropic`, plus `LLM_MODEL`, `LLM_API_KEY`, and optionally `LLM_BASE_URL`.

`LLM_PROVIDER` also accepts the preset IDs `openai`, `deepseek`, `google_gemini`, `qwen`, `kimi`, `zhipu`, `siliconflow`, `openrouter`, `ollama`, and `lmstudio`. Local `ollama` and `lmstudio` configurations may omit `LLM_API_KEY`.

Complete environment configuration takes precedence over the local UI and is read-only there. Secrets are read by the API only and are never returned by model status or settings endpoints. Plain HTTP model endpoints are accepted only on localhost; remote endpoints must use HTTPS. Changing the provider or Base URL requires entering the API Key again.

## Run with Docker Compose

Docker Desktop is not bundled with the project. Compose starts Redis, API, Worker, and Web; SQLite remains a shared local file under `data/`. The API can run without Redis and falls back to L1 memory plus L3 SQLite cache, while Worker jobs and L2 cache require Redis.

```text
docker compose up --build
```

Open the Web application at `http://127.0.0.1:3000`. The API is available locally at `http://127.0.0.1:8000/api/v1`.

## Windows one-click launcher

Double-click **Science Buddy.cmd** in the repository root. The stable launcher will:

1. locate the configured Python and npm runtimes;
2. apply `alembic upgrade head` before starting the API;
3. start FastAPI and Next.js as launcher-managed background process trees;
4. wait for API/Web health checks;
5. open `http://127.0.0.1:3000` in the default browser;
6. degrade to API/Web without Redis when Redis is unavailable.

Use **Science Buddy Stop.cmd** to stop launcher-managed processes and **Science Buddy Status.cmd** to inspect PIDs, ports, health and log paths.

The click targets never contain application-specific startup commands. They call `scripts/launcher/current.ps1`, which reads `launcher.config.json` and dispatches to a versioned implementation such as `scripts/launcher/v1/launcher.ps1`. Future API/Web command or framework changes should update the manifest or add a new launcher version; the root click entry remains unchanged.

Runtime PID, state and logs are written under `.runtime/launcher/` and are ignored by Git. Useful terminal commands:

```text
powershell -ExecutionPolicy Bypass -File scripts/launcher/current.ps1 start -NoBrowser
powershell -ExecutionPolicy Bypass -File scripts/launcher/current.ps1 start -WithWorker
powershell -ExecutionPolicy Bypass -File scripts/launcher/current.ps1 status
powershell -ExecutionPolicy Bypass -File scripts/launcher/current.ps1 logs
powershell -ExecutionPolicy Bypass -File scripts/launcher/current.ps1 stop
powershell -ExecutionPolicy Bypass -File scripts/launcher/current.ps1 doctor
```

Worker startup is optional and requires Redis. Set `SCIENCE_BUDDY_PYTHON` when the project interpreter is not discoverable automatically. See [docs/launcher.md](docs/launcher.md) for the launcher contract and troubleshooting.

## Local development

Backend:

```text
cd apps/api
pip install -e ".[dev]"
alembic upgrade head
uvicorn science_buddy.main:app --reload
```

Worker:

```text
cd apps/api
arq science_buddy.worker.WorkerSettings
```

Frontend:

```text
npm install
npm run dev:web
```

## Quality checks

```text
cd apps/api
ruff check src tests
mypy src
pytest

cd ../..
npm run lint:web
npm run typecheck:web
npm run build:web
```

On Windows, `scripts/dev.ps1` wraps common Compose and validation operations.

## RAG conventions

The default embedding contract is `intfloat/multilingual-e5-base` with 768 dimensions. Queries use the `query:` prefix and chunks use the `passage:` prefix. PubMedBERT/BioBERT CLS vectors are not used as retrieval embeddings without contrastive fine-tuning and evaluation.

Graph entities, relations, mentions, communities, reports, and path audits remain in SQLite. GraphRAG uses deterministic metadata and original-text provenance rather than free-form LLM relation extraction. Graph routes receive bounded RRF weights and must resolve back to source Chunks before becoming evidence.

Install the optional local embedding runtime with `pip install -e ".[dev,rag]"`. The first indexing task downloads the configured model into `HF_HOME`/the Hugging Face cache. On CPU this can take time. Until vectors exist, evidence retrieval reports `hybrid-sparse`; after successful indexing it reports `hybrid-dense`. Identifier lookups report `identifier`.

Retrieval results expose the bilingual query plan, every route's candidate count/latency/error, route-level raw rank and Weighted-RRF contribution, cache level, anchor/neighbor role, and retrieval version. RRF and cosine values are ranking signals, not medical confidence scores.

## Implemented API surface

- `POST /api/v1/literature/search`
- `POST /api/v1/literature/import`
- `GET /api/v1/library`
- `PUT /api/v1/library/papers/{paper_id}/tags`
- `POST /api/v1/library/papers/{paper_id}/tags/reset`
- `POST /api/v1/documents/upload`
- `POST /api/v1/documents/import-europe-pmc`
- `POST /api/v1/retrieval/index`
- `POST /api/v1/retrieval/search`
- `POST /api/v1/graph/rebuild`
- `POST /api/v1/graph/query`
- `GET /api/v1/graph/{project_id}`
- `POST /api/v1/rag/collections`
- `GET /api/v1/rag/collections`
- `POST /api/v1/rag/collections/{collection_id}/vector`
- `POST /api/v1/brainstorm/sessions`
- `GET /api/v1/brainstorm/sessions`
- `GET /api/v1/brainstorm/sessions/{session_id}`
- `POST /api/v1/brainstorm/sessions/{session_id}/source`
- `POST /api/v1/brainstorm/sessions/{session_id}/messages`
- `POST /api/v1/brainstorm/sessions/{session_id}/confirm`
- `DELETE /api/v1/brainstorm/sessions/{session_id}`
- `GET /api/v1/memory/projects/{project_id}/trash`
- `POST /api/v1/memory/projects/{project_id}/trash/{entity_type}/{entity_id}/restore`
- `DELETE /api/v1/memory/projects/{project_id}/trash/{entity_type}/{entity_id}`
- `GET/PUT /api/v1/memory/projects/{project_id}/retention`
- `GET /api/v1/memory/projects/{project_id}/facts`
- `PATCH /api/v1/memory/projects/{project_id}/facts/{fact_id}`
- `GET /api/v1/memory/brainstorm/sessions/{session_id}/summary`
- `GET/PUT/DELETE /api/v1/settings/web-search`
- `POST /api/v1/settings/web-search/test`
- `POST /api/v1/web-search/search`
- `POST /api/v1/brainstorm/sessions/{session_id}/plan/discover`
- `PUT /api/v1/brainstorm/sessions/{session_id}/plan/preferences`
- `POST /api/v1/brainstorm/sessions/{session_id}/plan/generate`
- `GET /api/v1/brainstorm/sessions/{session_id}/background-job`
- `GET /api/v1/research/model-status`
- `POST /api/v1/research/answer`
- `GET /api/v1/research/{run_id}`
- `GET /api/v1/research/{run_id}/export?format=markdown|docx`
- `POST /api/v1/memory/projects/{project_id}/context`
- `POST/GET /api/v1/synthesis/sessions`
- `GET /api/v1/synthesis/sessions/{session_id}`
- `POST /api/v1/synthesis/sessions/{session_id}/sources/upload`
- `POST /api/v1/synthesis/sessions/{session_id}/sources/text`
- `POST /api/v1/synthesis/sessions/{session_id}/run`
- `GET /api/v1/synthesis/sessions/{session_id}/job`
- `PATCH /api/v1/synthesis/sessions/{session_id}/sections/{section_id}`
- `GET /api/v1/synthesis/sessions/{session_id}/export?format=markdown|docx`

## Retrieval evaluation

Create a manually reviewed JSONL set following `data/eval/queries.example.jsonl`, then run `python -m science_buddy.evaluation ../../data/eval/queries.jsonl` from `apps/api`. Report sparse and dense runs separately.
