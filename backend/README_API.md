# AgentForge backend2 – graph + API

## Run
```
pip install -r requirements.txt
cp .env.example .env     # add GROQ_API_KEY
python app.py            # http://127.0.0.1:8000
pytest
```

## Graph (`workflow/graph.py`)
```
research -> master_plan -> frontend_plan -> frontend_build -> api_analysis
  -> backend_plan -> backend_build -> validate
validate -> finalize (no errors) | debug -> validate (loop, max_debug_attempts) | fail
any node error -> fail
```
`GET /api/graph` returns the nodes, edges and a Mermaid diagram.

## Validation (`workflow/validators.py`)
Backend syntax/requirements/health/seed JSON, frontend package + imports,
leaked-secret scan, frontend<->backend API contract, live backend smoke test
(optional Playwright).

## Debugging (`workflow/debugger.py`)
Zero-token deterministic fixes first, then an LLM patch of only the failing
files (syntax-checked, truncation-checked, never writes unoffered files, never
sends secrets).

## API
| Method | Path | Purpose |
|---|---|---|
| POST | /api/projects | `{"requirements": "...", "options": {...}}` -> 202 + job |
| GET | /api/projects | list jobs |
| GET | /api/projects/<id> | status, current node, issues, fixes |
| GET | /api/projects/<id>/events?after=N | poll progress |
| GET | /api/projects/<id>/files[/<path>] | browse generated files |
| GET | /api/projects/<id>/download | zip |
| GET | /api/graph, /health | graph info, liveness |

Options: research, market_scope, seed_data, validate_connected,
validate_browser, max_debug_attempts (0-5), strict_frontend_validation.

## Live preview, chat (human in the loop), streaming
| Method | Path | Purpose |
|---|---|---|
| POST/GET/DELETE | /api/projects/<id>/preview | start / status / stop the running site (`url` when `status` is `ready`) |
| POST | /api/projects/<id>/chat | `{"message": "make the header dark"}` -> 202, edits the project |
| GET | /api/projects/<id>/chat | conversation (`status`: working/done/failed, `changed` files) |
| GET | /api/projects/<id>/stream | Server-Sent Events: progress, ends with `event: end` |

`GET /api/projects/<id>` also returns `steps` (planning, generating, validating,
installing, preview) with `pending/active/done/failed` for the progress UI.
A chat edit is verified (build + backend checks) and rolled back if it fails.
Option `auto_preview` (default true) starts the preview when the build finishes.
