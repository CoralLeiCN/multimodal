# Collection Explorer frontend

React, TypeScript, Vite, and TanStack Query provide text search, image uploads,
browsing, attribution, and similar-image search. The layout follows the
[Full Stack FastAPI Template](https://github.com/fastapi/full-stack-fastapi-template).
Reusable components use Radix and the shadcn/ui component pattern.

Image Studio at `/create` provides workspace sign-in, versioned brand profiles,
reference uploads, generation progress, clarification, cancellation, downloads,
and follow-up edits. Its components and API helpers live in `src/components/creation/`.
The same build works with the standalone agent API; see
[Image Studio setup](../docs/image_agent_setup.md). Creation tests in
`tests/creation.spec.ts` mock cloud responses and do not call paid models.

Year-range inputs and place/category dropdowns filter browsing and every search
mode. Dropdowns use `GET /api/v1/filters`; applying filters repeats the current
search or refreshes the browse grid. Clearing filters keeps the current query.
Date ranges match overlapping creation years. The detail panel displays source
place and category labels alongside dates and attribution.

The left-edge Chat tab toggles a collection companion sidebar. Opening it makes
room beside the collection, so users can browse, filter, and search images while
chatting. The collection and conversation scroll independently, and interacting
with the collection leaves the sidebar open. On screens up to 900px wide, the
collection and chat share the screen vertically with chat below the collection.

Chat includes suggested prompts, a multiline composer, message history, and a
New chat control. Enter sends a message; Shift + Enter adds a line. Closing the
sidebar preserves the conversation and draft until the page reloads. Escape
while focus is inside the chat closes it and restores focus to the tab.

Chat currently runs as an explicitly labelled local preview. Messages are held
in React state, and the preview returns a fixed explanatory reply without making
network requests. To connect an agent, pass an `onSend` handler to `ChatPanel` in
`src/routes/index.tsx`. The exported `ChatReplyHandler` in
`src/components/chat-panel.tsx` accepts the complete message history (including
the latest user message) and an `AbortSignal`, and resolves to the assistant's
reply string. Providing this handler removes the preview labels. The component
handles pending replies, retry after failure, and cancellation when starting a
new chat or unmounting. Closing the sidebar lets a pending reply finish. Keep
credentials and agent execution in the backend; no chat endpoint is assumed here.

From the repository root, start the backend as described in
[the backend guide](../backend/README.md), then run:

```sh
bun install --frozen-lockfile
bun run dev
```

Open `http://127.0.0.1:5173`. Vite forwards `/api` requests to the backend on port
8000. For a single local application, run `bun run build` and restart the backend;
FastAPI serves that build at `http://127.0.0.1:8000`.

Bun is installed in `data/tools/node_modules/.bin/` in this workspace. To use that
copy when Bun is absent from your normal PATH, direct shell commands need:

```sh
export PATH="$PWD/data/tools/node_modules/.bin:$PATH"
```

The root Makefile includes this directory automatically for Make commands.

The frontend consumes the generated client in `src/client/generated/`. After
changing backend contracts, run `bash scripts/generate-client.sh` from the root.
This exports OpenAPI locally and regenerates the client without starting services.
Keep API keys in the backend environment.

```sh
bun run lint
bun run build
PLAYWRIGHT_BROWSERS_PATH="$PWD/data/tools/browsers" \
  frontend/node_modules/.bin/playwright test --config frontend/playwright.config.ts
```

The Playwright checks use isolated API responses to verify frontend interactions,
request formats, error states, attribution, and mobile layout. Run the local
backend to serve the built UI before these checks. A real indexed collection is
needed to verify search relevance against Gemini and the Qdrant server.
