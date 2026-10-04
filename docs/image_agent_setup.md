# Image Studio setup

This guide describes the existing Image Studio prototype. Both the public HF
explorer and the first web release exclude image generation. The web
collection agent uses Codex; see the [version decision](product_versions.md).
In this prototype, the OpenAI client
handles conversation, planning, and evaluation through
a configured compatible endpoint. Nano Banana generates images,
and child processes execute compiled tasks. Pydantic Logfire traces execution.

The harness handles conversation outside the task process; only compiled design tasks
enter the task process. Explicit user choices override brand defaults for that task.
Conversation APIs and a separate local development environment are documented in
[Brand chat agent backend](chat_agent_backend.md). The `/create` page contains
the six-field brand editor; the collection sidebar handles conversation, generation,
results, and editing. See [brand prompts](brand_prompts.md).

## Prepare the local application

Install the locked dependencies with `uv sync --locked --all-packages --extra agent`, then run
`make build-studio`. Run the combined application with
`uv run --package multimodal-backend --extra agent uvicorn app.studio:app --host 127.0.0.1 --port 8000`;
the studio is available at `/create`. To run
only creation services, without opening the search database or contacting Qdrant:

```sh
make agent-api PORT=8001
```

Creation is disabled by default. To enable profile preparation on your own machine,
set these values in the ignored root `.env`:

```dotenv
AGENT_ENABLED=true
AGENT_ENVIRONMENT=development
AGENT_ACCESS_TOKEN=<a random secret of at least 32 characters>
AGENT_WORKSPACE=default
AGENT_DATABASE_URL=sqlite:///data/agent/agent.sqlite3
AGENT_STORAGE=local
```

Open `/create` and sign in with the workspace access key. It is exchanged for an
HTTP-only, SameSite=Strict session cookie lasting eight hours; it is not stored in
browser local storage. API clients can send the same key as a bearer token. The
agent uses one operator credential bound to one server-configured workspace.
It does not include user registration, membership administration, or company SSO.

Brand profiles and immutable versions work before model access
is configured. Generation requires the harness API key, model, Gemini image key,
and loopback gateway origin. Creating runs does not start a worker automatically.
A status of `queued` means a worker has not claimed the run yet; it is not proof
that model access has been validated.

## Run the prototype container

The `EDITION=studio` Docker image runs `python -m app.space`. This starts the API, waits for
its listening socket, then starts a polling worker when `AGENT_ENABLED=true`.
The supervisor sets `AGENT_GATEWAY_URL=http://127.0.0.1:7860` for both processes.
The worker and API exit together if either process fails. Container shutdown
signals both and waits up to ten seconds before killing remaining processes.
The HF search release has its own search-only artifact and entry point.
The default image excludes both model-provider SDKs. For a local prototype image,
build with `EDITION=studio` as a Docker build argument before setting
`AGENT_ENABLED=true`. Locally:

```sh
docker build --build-arg EDITION=studio -t multimodal-space:agent .
```

The `agent` dependency extra installs `openai` and `google-genai`. Search uses
neither SDK. Disabled servers expose only the agent status endpoint; chat and
Image Studio frontend modules load on demand.

When enabling this prototype, inject public configuration and credentials through
the container environment and the host's secret manager:

| Variable | Value |
| --- | --- |
| `AGENT_ENABLED` | `true` |
| `AGENT_ACCESS_TOKEN` | Secret operator credential, at least 32 characters |
| `AGENT_WORKSPACE` | Workspace identifier; default `default` |
| `AGENT_DATABASE_URL` | Secret PostgreSQL URL for a database dedicated to the agent |
| `AGENT_STORAGE` | `hf` |
| `AGENT_HF_BUCKET` | Private bucket ID, separate from collection images |
| `AGENT_HF_TOKEN` | Secret HF token with upload, download, list, and delete access to the agent bucket |
| `AGENT_OPENAI_API_KEY` | Secret key for the harness endpoint; `OPENAI_API_KEY` is also accepted |
| `AGENT_OPENAI_BASE_URL` | API base URL, default `https://api.openai.com/v1`; `OPENAI_BASE_URL` is also accepted |
| `AGENT_MODEL` | Required planning model; default for chat and evaluation |
| `GEMINI_API_KEY` | Secret key for Gemini image generation only |
| `LOGFIRE_TOKEN` | Secret write token for the Logfire project |
| `AGENT_LOGFIRE_API_URL` | Project-region API origin; default `https://logfire-api.pydantic.dev` |

The Docker image sets `AGENT_ENVIRONMENT=production`. Production startup requires
PostgreSQL, HF bucket storage, Logfire, and both model-provider configurations. Provider and
bucket credentials stay in the API/worker environment. Each task process receives
only its gateway address, scoped task token, trace context, parent PID, deadline,
and temporary-directory settings. It uses the same installed dependencies as the
API and runs only the checked-in runtime; model output is data, never executable code.

The task processes share the container's filesystem, user, network, CPU, and memory.
They are not security sandboxes and have no separate network allowlist or resource
allocation. Size the container for the API, the worker, and one active task.
Use one container replica and one worker for each workspace. A second worker cannot
reconnect to child handles owned by the first and could mark its runs interrupted.

For local agent development, configure the settings above with development storage
as described earlier, then run both services together:

```sh
uv run --package multimodal-backend --extra agent python -m app.space --agent-only --host 127.0.0.1 --port 8001
```

This command uses the loopback gateway on port 8001. For profile preparation
without model credentials, use `make agent-api PORT=8001`. When starting the API
and `make agent-worker` manually, set `AGENT_GATEWAY_URL=http://127.0.0.1:8001`
for both. A public tunnel is unnecessary. Keep this prototype separate from the
public search deployment described in the [HF guide](huggingface_spaces.md).

## Models, limits, and recovery

| Setting | Default |
| --- | --- |
| `AGENT_CHAT_MODEL` | Uses `AGENT_MODEL` when empty |
| `AGENT_MODEL` | Required; choose a model available at the configured endpoint |
| `AGENT_EVALUATION_MODEL` | Uses `AGENT_MODEL` when empty |
| `AGENT_IMAGE_MODEL` | `gemini-3.1-flash-image` |
| `AGENT_RUN_TIMEOUT` | 600 seconds |
| `AGENT_STARTUP_TIMEOUT` | 180 seconds |
| `AGENT_MAX_QUEUED_RUNS` | 10 queued, active, or waiting runs per workspace |

The endpoint must support Chat Completions, strict JSON Schema structured outputs,
`store=false`, and `max_completion_tokens`. Planning and evaluation models must
also accept base64 image inputs. There is no automatic model or provider fallback.
The client sends one request per step, with a 120-second timeout and SDK retries
disabled. Confirm compatibility with the chosen endpoint before enabling testers.
See [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
Logfire records operation, model, token counts, timing, and sanitized failures;
automatic provider instrumentation is disabled to keep prompts and images out of traces.

Each conversation permits up to 20 user turns and one unfinished turn at a time.
The trusted chat service compiles generation requests into bounded tasks. Each
execution can generate one initial image and one revision; successful results
become the subject of follow-up edits. The workspace allows one active task process.
Evaluation scores support review and do not guarantee brand or subject fidelity.
See [the conversation contract](chat_agent_backend.md#api-contract) for API limits.

Paid calls are recorded before submission. Identical completed steps return their
saved results, and an ambiguous submission is never automatically repeated. The
provider SDK uses one attempt per call. A timeout may leave `outcome_unknown` even
when Google generated and charged for an image. Start a new run only when you want
another paid attempt. Different requests with the same submission key return 409.

Checkpoints and asset records persist in PostgreSQL and the HF bucket. Each task
process exits when its worker parent disappears or its deadline expires, even if
the polling loop stops. Worker shutdown terminates and reaps its children. After a
worker or container restart, interrupted attempts fail and preserve completed candidates;
they are never automatically replayed. Persisted `sandbox_id` and `sandbox_name`
fields now identify local process attempts, not remote resources.
Chat clarification returns an ordinary reply without starting a task process.
Cancellation revokes tool access immediately and the next worker sweep terminates
the task process.
Already submitted provider calls may still incur charges. Unreferenced task files
older than 24 hours are eligible for cleanup; referenced inputs and outputs are retained.
The HF adapter uses the bucket's server upload time, skips entries with unknown
timestamps, and examines at most 1,000 entries per cleanup pass.

Actual provider usage is recorded when returned. Currency estimates, daily spend
quotas, and currency reconciliation are not implemented; provider dashboards
remain the source for charges. Count, concurrency, queue, and runtime limits are enforced.

## Tracing

Logfire records explicit metadata-only spans around trusted model calls. The task process creates explicit spans
and sends them through an authenticated OTLP relay using its task token. The relay
checks the attempt and trace ID, removes arbitrary attributes, exception messages,
events, and content, and persists sanitized batches for forwarding to Logfire.
Automatic provider instrumentation is disabled; raw prompt and image capture is
not exposed as an application option.

The run API returns the trace ID for searching in Logfire. Trace delivery uses
a bounded backlog of 500 batches with a 24-hour retry window. The task process buffers
128 spans and exports batches of at most 64 spans. A hard task process exit may
lose final spans; persisted run events remain the source of execution status. Tracing
failure does not erase results. The authenticated status endpoint reports an absent
Logfire configuration or relay backlog. It does not claim verified delivery.

## Validation

```sh
make agent-test
make lint
make build
# Run against an already started local server:
PLAYWRIGHT_BASE_URL=http://127.0.0.1:8001 bun run --cwd frontend test
```

Backend tests use isolated SQLite, image fixtures, real local task processes,
and fake model services.
They cover the actual portable agent loop, revision budgets, stale attempts,
idempotency, uncertain submissions, lifecycle recovery, trace redaction, and API
access. Browser tests exercise sign-in, profiles, creation, edits, cancellation,
and upload errors with mocked API responses. Neither test suite makes paid calls.

Local validation on 26 September 2026 passed all 233 Python tests, Ruff, Biome,
and the frontend build. The rebuilt Linux amd64 image contains no Modal package.
A real task process completed through the HTTP gateway with a fake image provider,
persisted its result, and was reaped. The Space supervisor started the API and
worker together and shut down both cleanly. The tests also cover cancellation,
deadline and parent loss, interrupted attempts, and child startup failures.

HF asset operations are covered with SDK fakes; a real private bucket still needs
verification. Before real use, perform a cloud smoke test with a configured development environment:
verify a recorded process attempt, one real generated
image persisted in the bucket, Logfire trace delivery, cancellation, and PostgreSQL
claims across worker restarts. Those checks require cloud credentials and are not
substituted by the fake-service tests.

After changing dependencies, update `uv.lock` and rebuild/redeploy. The Docker
build exports its requirements directly from that lockfile.
The collection search client can be regenerated separately with `make generate-client`.

## Collection source configuration

The agent uses `DATABASE_URL` to resolve image UUIDs in the shared PostgreSQL
catalogue by default. Source images use `IMAGE_ROOT`, overridden by
`AGENT_COLLECTION_IMAGE_ROOT`. Use a read-only HF mount in hosted deployments.
Missing files return an error; available bytes must match the catalogue checksum. Set `AGENT_COLLECTION_API_URL`
to use a trusted HTTPS collection API instead. Record IDs such as `co25823` need
`DATABASE_URL` even when the image bytes come from the online API.
The agent reads `.env` and `.env.local`, with `.env.local` taking precedence.
Uploaded-asset generation remains independent of search.
