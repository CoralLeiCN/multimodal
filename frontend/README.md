# Collection Explorer frontend

React, TypeScript, Vite, and TanStack Query provide text search, image uploads,
browsing, attribution, and similar-image search. The layout follows the
[Full Stack FastAPI Template](https://github.com/fastapi/full-stack-fastapi-template).
Reusable components use Radix and the shadcn/ui component pattern.

Year-range inputs and place/category dropdowns filter browsing and every search
mode. Dropdowns use `GET /api/v1/filters`; applying filters repeats the current
search or refreshes the browse grid. Clearing filters keeps the current query.
Date ranges match overlapping creation years. The detail panel displays source
place and category labels alongside dates and attribution.

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
