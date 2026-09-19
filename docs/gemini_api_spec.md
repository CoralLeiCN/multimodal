# Gemini API setup specification

This specification defines authentication for Python scripts and notebooks that
use the Gemini Developer API in this repository. The
[multimodal search service specification](multimodal_search_spec.md) describes
the frontend, search API, and image embedding workflow that use this setup.
The standard client is:

```python
from google import genai

client = genai.Client()
```

## API key and configuration

Create a Gemini API key in [Google AI Studio](https://aistudio.google.com/apikey),
selecting the Google Cloud project intended for this work. Store the key in
`GEMINI_API_KEY` in the Python process environment.

| Setting | Requirement |
| --- | --- |
| Python | Use Python 3.12 or newer, as declared in `pyproject.toml`. |
| SDK | Use the existing `google-genai` dependency and locked environment. |
| `GEMINI_API_KEY` | Set to the project's Gemini API key. |
| `GOOGLE_API_KEY` | Leave unset for this setup; the SDK prioritizes it when both key variables are set. |
| Backend selection | Leave `GOOGLE_GENAI_USE_VERTEXAI` and `GOOGLE_GENAI_USE_ENTERPRISE` unset for this setup. |

The SDK reads the key when constructing the client. Creating a client alone does
not confirm that the remote API accepts the key.
[Sources: API key configuration](https://ai.google.dev/gemini-api/docs/api-key)
and [SDK client setup](https://googleapis.github.io/python-genai/#create-a-client).

## Local setup

Run from the repository root:

```sh
uv sync --locked
```

Export the key before launching Python:

```sh
export GEMINI_API_KEY="your-key-here"
uv run --locked python
```

For a local `.env` file, use this format with the real value stored only locally:

```dotenv
GEMINI_API_KEY=your-key-here
```

Load the file explicitly when launching Python:

```sh
uv run --locked --env-file .env python
```

`genai.Client()` reads environment variables; it does not load `.env` itself.
The `--env-file` option makes `uv` load the file before Python starts. An existing
environment value can override the corresponding value in the file, so update
or unset an old exported key when switching credentials.

Before storing credentials in `.env`, add these rules to the root `.gitignore`:

```gitignore
.env
.env.*
!.env.example
```

The repository's `.gitignore` needs these additions as of 19 September 2026.
An optional `.env.example` must contain placeholders only. Keep key values out
of source files, notebook cells, saved outputs, and logs.

## Notebook use

Select the repository's `.venv` Python kernel. The kernel must receive
`GEMINI_API_KEY` before creating the client. Launch the notebook application from
the configured environment, or use its supported environment-file loading
setting. Restart the kernel after changing its launch environment.

For an interactive session, a hidden prompt can supply the key without saving it
in the notebook source:

```python
import os
from getpass import getpass
from google import genai

os.environ["GEMINI_API_KEY"] = getpass("Gemini API key: ")
client = genai.Client()
```

## Authentication check

Run this check after loading the environment. It sends a model-list request to
Google and prints a success message only after the request completes:

```python
from google import genai

with genai.Client() as client:
    client.models.list(config={"page_size": 1})
    print("Gemini API model listing succeeded.")
```

This confirms access to the listing endpoint. Access to a chosen model and its
quota must be checked when implementing the operation that uses it.
[Source: SDK model listing](https://googleapis.github.io/python-genai/#list-base-models).

## Acceptance criteria

- The locked environment imports `genai` from `google` successfully.
- A script and a notebook can construct the client with the configured key.
- The model-list check completes using the intended project's credentials.
- Missing or rejected credentials produce an error rather than a success message.
- `git check-ignore .env` reports `.env` after the ignore rules are added.
- Committed examples and saved notebook outputs contain no key values.

Verified against Google's documentation and the local SDK on 19 September 2026.
