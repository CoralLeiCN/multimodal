# Image Studio setup

[Design](image_agent_design.md) · [设计中文版](image_agent_design.CN.md)

Image Studio runs the Python agent inside a Modal Sandbox. Gemini plans and evaluates
images, Nano Banana generates them, and Pydantic Logfire traces execution. Uploaded
references work independently of the collection catalogue and Qdrant.

Gemini Flash handles conversation outside the sandbox; only compiled design tasks
enter the sandbox. Explicit user choices override brand defaults for that task.
The conversation backend and isolated-worktree setup are documented in
[Brand chat agent backend](chat_agent_backend.md). The existing Image Studio UI
continues to use single-run generation; the collection sidebar connects to the conversation endpoints.

## Prepare the local application

Install the locked dependencies with `uv sync --locked --all-packages`, then run
`make build`. The studio is available at `/create` through the normal app. To run
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
first release uses one operator credential bound to one server-configured workspace.
It does not include user registration, membership administration, or company SSO.

Brand profiles, immutable versions, and reference uploads work before model access
is configured. Generation remains unavailable until the Gemini key and public HTTPS
gateway origin are configured. Creating runs does not start a worker automatically.
A status of `queued` means a worker has not claimed the run yet; it is not proof
that Modal credentials or model access have been validated.

## Deploy the cloud services

Use a separate Modal environment for development and production. Before deployment,
create a private object storage bucket, a PostgreSQL database dedicated to the agent,
and a Pydantic Logfire project. Configure a Modal Secret named
`multimodal-agent-services` with:

| Variable | Value |
| --- | --- |
| `AGENT_ACCESS_TOKEN` | Random operator credential, at least 32 characters |
| `AGENT_WORKSPACE` | Workspace identifier; default `default` |
| `AGENT_DATABASE_URL` | `postgresql+psycopg://...` connection URL; use the database provider's TLS settings |
| `AGENT_STORAGE` | `s3` |
| `AGENT_S3_BUCKET` | Private bucket name |
| `AGENT_S3_REGION` | Bucket region; default `eu-west-2` |
| `AGENT_S3_ENDPOINT` | Optional HTTPS origin for S3-compatible storage |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Scoped object storage credentials, unless an alternate AWS credential chain is configured |
| `GEMINI_API_KEY` | Key with access to the selected models |
| `AGENT_GATEWAY_URL` | Public HTTPS origin of this deployment, without a path or trailing query |
| `LOGFIRE_TOKEN` | Write token for the Logfire project |
| `AGENT_LOGFIRE_API_URL` | Project-region API origin; default `https://logfire-api.pydantic.dev` |

The deployment sets `AGENT_ENABLED=true` and `AGENT_ENVIRONMENT=production`.
Production startup rejects SQLite, local file storage, missing Logfire credentials,
and missing Gemini/gateway configuration. Object storage needs read, write, list,
and delete permissions within the application's prefix for orphan cleanup.

Authenticate the Modal CLI, select the intended environment, and run:

```sh
make agent-deploy
```

The app is named `multimodal-agent`. The deployment defines a FastAPI web endpoint
and a worker scheduled every 30 seconds, with at most one worker container. Set
`AGENT_GATEWAY_URL` to the origin shown for the `web` endpoint. On the first deploy,
update that Secret field once the endpoint URL is known, then redeploy before
submitting work. The frontend is served by the same origin at `/create`; `/` redirects
there for the standalone creation deployment.

Do not run `make agent-worker` against the same environment as an additional scheduler
unless needed for debugging. It runs a polling worker every five seconds. Database
claims and attempt fencing protect ownership, but one scheduled worker is sufficient.

The worker provisions a fresh Modal Sandbox for each attempt. Its image contains
only the portable runtime and locked dependencies, and it has 1 CPU and a 1 GiB
memory limit. It receives a short-lived task token and the public gateway origin;
Gemini, database, bucket, Logfire, and Modal provisioning credentials stay in the
trusted services. Only the gateway hostname is on the sandbox's outbound allowlist.
A local `127.0.0.1` backend cannot serve this remote sandbox.

## Models, limits, and recovery

| Setting | Default |
| --- | --- |
| `AGENT_MODEL` | `gemini-3.8-flash` |
| `AGENT_EVALUATION_MODEL` | `gemini-3.8-flash` |
| `AGENT_IMAGE_MODEL` | `gemini-3.1-flash-image` |
| `AGENT_RUN_TIMEOUT` | 600 seconds |
| `AGENT_STARTUP_TIMEOUT` | 180 seconds |
| `AGENT_MAX_QUEUED_RUNS` | 10 queued, active, or waiting runs per workspace |

The UI accepts one subject and up to three style references, PNG/JPEG/WebP,
10 MiB and 20 million pixels each. A revision adds the previous candidate as a
fifth model input. Animated and invalid images are rejected. Candidate count is
one or two; each run allows one additional revision, at most eight combined
planning/evaluation calls, and one active sandbox per workspace. The controls for
subject preservation and brand influence guide prompts rather than numerical model
parameters. Generated image aspect ratio is checked within a 10% tolerance.

The sandbox plans, generates candidates, evaluates them, and revises the weakest
candidate once when requested by its structured evaluation. Brand profiles are
written by the user; the planning model interprets descriptions and references at
run time. Scores guide review and are not guarantees of brand or subject fidelity.
A follow-up edit starts a new run using the selected output as its subject.

Paid calls are recorded before submission. Identical completed steps return their
saved results, and an ambiguous submission is never automatically repeated. The
provider SDK uses one attempt per call. A timeout may leave `outcome_unknown` even
when Google generated and charged for an image. Start a new run only when you want
another paid attempt. Different requests with the same submission key return 409.

Checkpoints and asset records live outside the sandbox. Worker restarts reconnect
to a known sandbox or reconcile its deterministic name; they do not replay an
uncertain creation. Unexpected exits fail the run and preserve completed candidates.
A clarification releases the sandbox; answering creates a new attempt. Cancellation
revokes tool access immediately and the next worker sweep terminates the sandbox.
Already submitted provider calls may still incur charges. Unreferenced task files
older than 24 hours are eligible for cleanup; referenced inputs and outputs are retained.

Actual provider usage is recorded when returned. Currency estimates, daily spend
quotas, and Modal billing reconciliation are not implemented; provider dashboards
remain the source for charges. Count, concurrency, queue, and runtime limits are enforced.

## Tracing

Logfire instruments trusted Google Gen AI calls. The sandbox creates explicit spans
and sends them through an authenticated OTLP relay using its task token. The relay
checks the attempt and trace ID, removes arbitrary attributes, exception messages,
events, and content, and persists sanitized batches for forwarding to Logfire.
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT` is forced on the
trusted process; raw prompt and image capture is not exposed as an application option.

The UI's run details show the trace ID for searching in Logfire. Trace delivery uses
a bounded backlog of 500 batches with a 24-hour retry window. The sandbox buffers
128 spans and exports batches of at most 64 spans. A hard sandbox exit may
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

Backend tests use isolated SQLite, image fixtures, and fake Modal/Gemini services.
They cover the actual portable agent loop, revision budgets, stale attempts,
idempotency, uncertain submissions, lifecycle recovery, trace redaction, and API
access. Browser tests exercise sign-in, profiles, creation, edits, cancellation,
and upload errors with mocked API responses. Neither test suite makes paid calls.

Before real use, perform a cloud smoke test with a configured development environment:
verify a recorded Modal sandbox ID, gateway-only network access, one real generated
image persisted in the bucket, Logfire trace delivery, cancellation, and PostgreSQL
claims across worker restarts. Those checks require cloud credentials and are not
substituted by the fake-service tests.

After changing dependencies, run `make agent-lock` and rebuild/redeploy. The two
requirements files in `deploy/` are exports from `uv.lock`; do not edit them by hand.
The collection search client can be regenerated separately with `make generate-client`.
The chat backend can resolve collection image IDs from a read-only catalogue and
image root accessible to the gateway. Use the collection cards’ **Use in chat** control to insert image IDs into chat;
see the chat guide for configuration and source handling.


The shared search catalogue now uses Neon PostgreSQL. Configure
`AGENT_COLLECTION_API_URL` with the trusted HTTPS collection API origin to use
that catalogue from the agent. `AGENT_COLLECTION_DATABASE` reads a legacy local
SQLite snapshot only; it does not connect directly to Neon. Without either an
available snapshot or the API configuration, collection-ID generation returns
`collection_unavailable`. Uploaded-asset generation remains independent of search.
