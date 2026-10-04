# Collection Explorer frontend

React, TypeScript, Vite, and TanStack Query provide text search, image uploads,
browsing, attribution, and similar-image search. The layout follows the
[Full Stack FastAPI Template](https://github.com/fastapi/full-stack-fastapi-template).
Reusable components use Radix and the shadcn/ui component pattern.

The default build produces `dist/search` with only the collection explorer.
`build:web` produces `dist/web` with the Codex collection companion, and
`build:studio` produces `dist/studio` for the deferred Image Studio prototype.
Each has a separate route tree; search does not compile agent routes or chunks. The upload notice reports local server processing for SigLIP
search.

## Development

From the repository root, start the backend as described in
[the backend guide](../backend/README.md), then run:

```sh
bun install --frozen-lockfile
bun run dev
```

Open `http://127.0.0.1:5173`. Vite forwards `/api` requests to the backend on port
8000, preserving the browser's Host header so workspace sign-in passes the backend's
same-origin check.
For a single local application, run `bun run build` and restart the backend;
FastAPI serves that build at `http://127.0.0.1:8000`.

If Bun is installed locally under `data/tools/node_modules/.bin/`, add that
directory to PATH for direct shell commands:

```sh
export PATH="$PWD/data/tools/node_modules/.bin:$PATH"
```

The root Makefile includes this directory automatically for Make commands.

The frontend consumes the generated client in `src/client/generated/`. After
changing backend contracts, run `bash scripts/generate-client.sh` from the root.
This exports OpenAPI locally and regenerates the client without starting services.
Keep API keys in the backend environment.

Install Playwright's Chromium browser before running tests. If it is stored in a
custom directory, use the same `PLAYWRIGHT_BROWSERS_PATH` for installation and tests.
The default test URL is `http://127.0.0.1:8000`; set `PLAYWRIGHT_BASE_URL` for
another port.

```sh
bun run lint
bun run build
bun run test
```

The Playwright checks use isolated API responses to verify frontend interactions,
request formats, error states, attribution, and mobile layout. Run the local
backend to serve the built UI before these checks. A real indexed collection is
needed to verify search relevance against the configured embedding model and the Qdrant server.

## Search interface

The homepage introduces multimodal search across Science Museum Group datasets
and displays the total indexed image count, prefixed with “Sample” when the API
marks the index as a sample. Browsing and filters can load while status reports
that search verification is still running. Collection Explorer is an independent
project with no affiliation to the Science Museum Group.

The text search field randomly starts with “a brass microscope” or “an early
computer” in grey, ready to submit with Explore. Focusing the field clears the
example so users can type their own query. Later focus changes preserve typed
queries. Returning to the collection selects a fresh random example.

The image search tab offers three selectable thumbnails from `examples/images`:
Coke Cola, Modal, and Tech: Europe. Selecting an example fills the image preview;
Explore submits it through the same image search endpoint and filters as an upload.
Users can switch examples, remove the selection, or replace it with their own image.
Vite bundles the example images with the frontend for development and production.

Year-range inputs and place/category dropdowns filter browsing and every search
mode. Dropdowns use `GET /api/v1/filters`; applying filters repeats the current
search or refreshes the browse grid. Clearing filters keeps the current query.
Date ranges match overlapping creation years. The detail panel displays source
place and category labels alongside dates and attribution.
Each supplied collection ID has a copy button and, when the agent is enabled,
a button to open chat with that ID in the composer. The chat action closes the
detail panel, replaces the draft, and focuses the composer without sending a message. Existing chat history is kept.
Copy success or failure is announced in the detail panel.

## Optional chat and Image Studio

Enable these features with `AGENT_ENABLED=true` and follow
[Image Studio setup](../docs/image_agent_setup.md) for credentials and services.
Enter the workspace access key configured as `AGENT_ACCESS_TOKEN` to sign in.

The `/create` page contains only brand configuration: Brand name, Brand description,
Brand colors palette, Personality, Typography, and Illustration style. Personality
is free text (for example, premium, calm, technical, optimistic). Name and description
are required. The page supports workspace sign-in, saving new brands, and editing
saved brands as new immutable versions.
Generation, task progress, results, and edits are available in collection chat.
The page has no reference uploads or standalone generation controls.

The API stores `name`, `description`, `colors`, `personality`, `typography`, and
`illustration_style`. Prompt templates are maintained in
[`image_agent.yaml`](../backend/app/prompts/image_agent.yaml); see the
[designer guide](../docs/brand_prompts.md). Browser tests cover all six fields,
version editing, failed-save recovery, and desktop/mobile layout.

The left-edge Chat tab toggles a collection companion sidebar. Opening it makes
room beside the collection, so users can browse, filter, and search images while
chatting. The collection and conversation scroll independently, and interacting
with the collection leaves the sidebar open. On screens up to 900px wide, the
collection and chat share the screen vertically with chat below the collection.

Chat uses the authenticated `/api/v1/agent/conversations` endpoints. Sign in with
the workspace access key, select a saved brand version, and send a message.
Create or update brands at `/create`. Each search card's **Use in chat** button
inserts its exact image UUID into the draft; it never submits automatically.

The sidebar polls durable history every two seconds while open, displays task
status, supports cancellation, and renders generated images with download and edit
controls. Use the Conversation selector to restore history after a page reload.
Closing the panel preserves its draft; the draft itself is not persisted on reload.
New chat starts a conversation bound to the selected brand on the next send.
A lost submission response is retried with the same body and idempotency key.
API keys remain on the server, and workspace authentication uses the existing
HTTP-only cookie. An unavailable backend displays an error instead of a preview reply.

Enter sends a message; Shift + Enter adds a line. Escape inside the chat closes it
and restores focus to the tab. Run `make agent-worker` alongside the API to process
turns. Generation runs through the prototype worker and its loopback gateway.
Collection images resolve through `DATABASE_URL` by default. Set
`AGENT_COLLECTION_API_URL` to use a trusted HTTPS collection API instead.
See [chat backend configuration](../docs/chat_agent_backend.md).

This sidebar belongs to the separate Studio edition. The Codex collection conversation
UI is implemented in the web edition; the first web release excludes generation. See the
[version decision](../docs/product_versions.md).

Chat is loaded only when the backend enables it. Image Studio uses a separate
chunk loaded on visiting `/create`. Search downloads neither feature module on
its initial page load.


## Collection companion edition

Use `make web` for the built app or `make web-backend` and `make web-dev` in two
terminals. Set the HF OAuth callback to the frontend origin; see
[web configuration](../docs/product_versions.md#run-the-web-edition).
`src/components/explorer-panel.tsx` implements account login, private history,
text/image questions, progress events, verified collection cards, follow-up
questions, cancellation and idempotent retries. Image and record actions populate
the composer. Attachments accept JPEG and PNG, matching the image upload API.
Image generation is deferred to a later release.

A rejected message (HTTP 4xx) keeps the draft editable and allows switching or
starting a conversation. A network failure or server error retains the same
submission and idempotency key for retry. A failed history refresh after an
accepted submission also preserves that key.

Playwright selects tests using `APP_EDITION=search` (default), `web`, or `studio`.
Serve the corresponding build and set `PLAYWRIGHT_BASE_URL` when using a port
other than 8000. For example:

```sh
APP_EDITION=web PLAYWRIGHT_BASE_URL=http://127.0.0.1:8000 bun run test
```

The generated client covers public search, companion and Studio contracts in
`openapi.json`. Companion JSON responses are validated by Pydantic and consumed
through generated request functions and types; SSE uses the browser's EventSource.
Schema generation is a development command using the `agent` and `web` extras.
Each deployed edition retains its own route set. Private collection-tool routes
are excluded from the public schema.

Companion result cards include each source association's credit, copyright notice,
source link and linked licence, using the same licence renderer as image details.
