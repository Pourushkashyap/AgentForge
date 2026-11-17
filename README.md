# AgentForge

**Describe a website in plain English. AgentForge plans it, builds a full-stack project, tests it, fixes what breaks, and shows it running live — then lets you keep refining it by chatting.**

AgentForge is an AI app builder. You write an idea ("an online bookstore with a cart, login and order tracking"), and a multi-step agent pipeline produces a working **React + Tailwind frontend** and a **FastAPI backend** (JSON storage, JWT login), validates the result in a real browser, repairs problems automatically and starts a live preview. Afterwards you can ask for changes in a chat ("make the header dark", "add a sign-up page") and every change is verified before it is kept.

> **Status:** a local development tool. It generates code with an LLM, so always review generated projects before deploying them anywhere. See [Limitations](#limitations).

---

## Table of contents

- [Features](#features)
- [How it works](#how-it-works)
- [Tech stack](#tech-stack)
- [Prerequisites](#prerequisites)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Using AgentForge](#using-agentforge)
- [Chat editing](#chat-editing)
- [Command line](#command-line)
- [REST API](#rest-api)
- [What a generated project looks like](#what-a-generated-project-looks-like)
- [Project structure](#project-structure)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)
- [Contributing](#contributing)
- [License](#license)

---

## Features

**Generation**
- Idea → complete project: pages, components, routing, API client, backend routes and starter data.
- Login and Sign-up out of the box: registration, login, current-user endpoint, hashed passwords, JWT tokens.
- Frontend and backend are generated against one shared **API contract**, so field names and endpoints match.
- Optional market and competitor research before planning (needs a Tavily key).
- Starter records for catalog-style collections, with a **different, fitting placeholder picture for every item** (generated offline).

**Quality gates**
- Static checks: imports, provider wiring, login/route/context consistency, route parameters, links to missing pages.
- API contract check between what the frontend calls and what the backend serves.
- The generated backend is **started and called over HTTP**, including a real *register → login → me → wrong password* round-trip.
- A real **browser check** (Playwright) opens every page, including detail pages such as `/products/:id`, and fails on runtime errors.
- **Auto-fixer:** mechanical repairs that cost no LLM tokens first, then LLM repairs, in a bounded validate → debug loop.

**Workflow**
- Live preview of the generated site, a code browser, and a zip download.
- **Resume** a failed build from its last checkpoint instead of starting over.
- **Chat refinement (human-in-the-loop):** edits are applied, verified, and rolled back automatically if they break the build.
- Project history with live status, plus a demo mode that needs no Python backend.

## How it works

```
 Browser  (React UI, :5173)
    │  REST / JSON
    ▼
 Flask API (:8000) ──► JobManager ──► LangGraph pipeline ──► Groq LLM  (+ Tavily search)
    │                      │                  │
    │                      │                  └─► generated_projects_api/<job>/<project>/{frontend,backend}
    ▼                      ▼
 PreviewManager        Chat / revise engine
 (npm run dev + uvicorn on free local ports)
```

The generation graph (`backend2/workflow/graph.py`):

```
research → master_plan → frontend_plan → frontend_build → api_analysis
        → backend_plan → backend_build → validate
validate ── no errors ───────────────► finalize
validate ── errors, attempts left ───► debug ──► validate
validate ── errors, no attempts ─────► fail   (resume later from the checkpoint)
```

| Step in the UI | Graph nodes | What happens |
|---|---|---|
| Planning | `research`, `master_plan`, `frontend_plan` | Optional research, then business plan and page plan |
| Generating Files | `frontend_build`, `api_analysis`, `backend_plan`, `backend_build` | Frontend files, API contract, backend, seed data |
| Validating & Debugging | `validate`, `debug`, `finalize` | Static checks, API checks, browser check, auto-fix |
| Installing Dependencies | — | `npm install` for the generated frontend |
| Launching Preview | — | Backend and frontend are started and health-checked |

## Tech stack

| Layer | Technology |
|---|---|
| AgentForge UI | React 18, Vite 5, Tailwind CSS 3 (JavaScript) |
| AgentForge API | Python, Flask, Flask-CORS |
| Agent pipeline | LangGraph, Pydantic, Groq (`openai/gpt-oss-120b` by default), optional Tavily |
| Generated frontend | React, Vite, Tailwind CSS, React Router |
| Generated backend | FastAPI, Uvicorn, JSON-file storage, JWT auth |
| Browser validation | Playwright |
| Tests | pytest |

## Prerequisites

- **Python 3.11+** (developed on 3.13)
- **Node.js 18+** (20 LTS recommended) and npm
- A **Groq API key** — <https://console.groq.com>
- *Optional:* a Tavily API key for the research step
- *Optional but recommended:* Playwright's Chromium for the browser check

## Quick start

The repository has two parts: `backend2/` (API + agents) and `frontend/` (the UI). Use two terminals.

### 1. Backend

**Windows (PowerShell)**

```powershell
cd AgentForge
python -m venv agentenv
# If activation is blocked:  Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\agentenv\Scripts\Activate.ps1

cd backend2
pip install -r requirements.txt
copy .env.example .env          # then open .env and add your GROQ_API_KEY

npm install                      # Playwright
npx playwright install chromium # enables the real-browser check

python app.py                    # API on http://127.0.0.1:8000
```

**macOS / Linux**

```bash
cd AgentForge
python3 -m venv agentenv && source agentenv/bin/activate
cd backend2
pip install -r requirements.txt
cp .env.example .env            # add your GROQ_API_KEY
npm install && npx playwright install chromium
python app.py
```

Check it is up: open <http://127.0.0.1:8000/health> — you should see `{"status": "ok"}`.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev          # UI on http://localhost:5173
```

Open <http://localhost:5173>, type an idea and press **Generate**.

> **Try the UI without a backend:** `npm run demo` starts the interface with an in-browser fake backend. Put the word `fail` in your idea to see a paused build and the **Resume** button.

## Configuration

### Backend (`backend2/.env`)

| Variable | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` | — (required) | Key used for every LLM call |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Model name |
| `GROQ_TPM_LIMIT` | `12000` | Tokens-per-minute budget the client paces itself to. Set it to your plan's limit |
| `TAVILY_API_KEY` | — | Enables web search for the research step |
| `PIPELINE_WORKERS` | `1` | Number of builds that can run at the same time |
| `OUTPUT_DIR` | `generated_projects_api` | Where generated projects are stored |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:5174` | Origins allowed to call the API |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | API bind address |
| `FLASK_DEBUG` | `0` | Leave off while building: the reloader can restart the server mid-build |

You do **not** need to set `JWT_SECRET_KEY`. The preview and validation runs give every generated backend a project-specific dev secret automatically (stored in `backend2/.dev_secret`, never written into the generated project). Set `JWT_SECRET_KEY` yourself to override it.

### Frontend (`frontend/.env`)

| Variable | Default | Purpose |
|---|---|---|
| `VITE_API_URL` | `http://127.0.0.1:8000` | Backend base URL |
| `VITE_MOCK` | `0` | `1` = use the built-in fake backend (this is what `npm run demo` sets) |

### Build options (sent with each project)

| Option | Default | Meaning |
|---|---|---|
| `research` | `false` | Run market and competitor research first |
| `market_scope` | `Global` | Region used by the research step |
| `seed_data` | `true` | Create starter records and pictures for catalog collections |
| `validate_connected` | `true` | Start the generated backend and smoke-test the API |
| `validate_browser` | `true` | Open every page in a real browser (needs Playwright) |
| `max_debug_attempts` | `2` | How many validate → debug loops are allowed |
| `strict_frontend_validation` | `false` | Fail the build on any remaining frontend finding |
| `auto_preview` | `true` | Start the live preview when the build finishes |

## Using AgentForge

1. **Describe your idea** on the home screen and press **Generate** (`Ctrl + Enter`). The sidebar closes while the build runs.
2. **Watch progress** — five steps plus a live event log.
3. **Preview** the site on the right. Use **Code** to browse the files and **Download** for a zip.
4. **Refine with chat** once the build is done.
5. **Resume** if a build stops: failed builds show *Resume from here*.
6. Old projects stay in the sidebar history.

### Writing a good idea

The planner works best with concrete structure. Name the pages, the users' actions, the data and the look:

```text
Build a hotel website called "Seaside Retreat" for travellers.
Pages: Home, Rooms (filter by type and price), Room Details, Book a Room,
My Bookings, Gallery, Login, Signup.
Users can sign up and log in, open a room's detail page and book it for selected dates.
Data: rooms (name, type, price per night, capacity, description),
bookings (room, user, check-in, check-out, status), gallery (title, caption).
Design: calm and elegant, navy blue and gold, rounded cards.
```

Tips:

- Include at least one **detail page** (for example *Room Details*) so sub-pages are generated and tested.
- Write **"Users can sign up and log in"** if you want authentication.
- Keep it to roughly **5–8 pages and 2–3 data collections**. Larger briefs hit LLM token limits and fail more often.
- Add heavy features (payments, chat, maps, admin panel) **later**, one at a time, through the chat.

## Chat editing

After a build finishes you can ask for changes in plain language. For each request AgentForge:

1. Chooses the files that need to change and edits them (large files are patched, not rewritten).
2. Runs cheap, **mechanical fixes without the LLM** whenever the request matches a known problem.
3. **Verifies the result**: login/route/context consistency, API contract, frontend build and, if backend files changed, a real *register → login* round-trip against the started backend.
4. **Rolls everything back** if a check fails, and tells you why. The summary lists only the checks that actually ran.

Requests that are handled without the LLM when they match:

| You write | What happens |
|---|---|
| "add a sign-up page" | Sign-up page, route, link on the login page and backend endpoints are created |
| "login not working" / "`loginUser is not a function`" | Login/register call shapes, token reads and missing names are corrected |
| "page crashed: `query.toLowerCase is not a function`" | Event objects stored in string state and unsafe string calls are fixed |
| "all gallery images look the same" | Every item gets its own picture |

Good requests are **short and specific** ("make the header dark and blue"). Avoid asking the chat to run terminal commands — it edits files and runs its own checks, nothing more.

## Command line

You can also generate a project without the UI:

```bash
cd backend2
python run_pipeline.py --requirements "A gym website with classes, trainers and membership plans. Users can sign up and log in."
python run_pipeline.py --requirements-file project_requirements.txt
```

| Flag | Effect |
|---|---|
| `--requirements` / `--requirements-file` | The project brief (string or UTF-8 file) |
| `--output-dir` | Parent folder for generated projects (default `generated_projects`) |
| `--no-browser` | Skip the Playwright check |
| `--skip-connected` | Do not start the generated backend for validation |
| `--no-seed-data` | No starter records |
| `--relaxed-frontend-validation` | Do not fail on frontend findings |
| `--overwrite-frontend` | Allow replacing an existing generated frontend |

> The UI and REST API run the LangGraph workflow, which contains the newest validation, auto-fix, resume and chat features. Use them for the full experience.

## REST API

Base URL: `http://127.0.0.1:8000`

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `GET` | `/api/graph` | Pipeline topology and default options |
| `POST` | `/api/projects` | Start a build. Body: `{ "requirements": "...", "options": { ... } }` (10–8000 characters) |
| `GET` | `/api/projects` | List projects |
| `GET` | `/api/projects/<id>` | Status, steps, recent events, preview state, result |
| `POST` | `/api/projects/<id>/resume` | Continue a failed build from its checkpoint |
| `GET` | `/api/projects/<id>/events?after=N` | Events since index `N` |
| `GET` | `/api/projects/<id>/stream` | Server-sent events until the build finishes |
| `GET` | `/api/projects/<id>/files` | List generated files |
| `GET` | `/api/projects/<id>/files/<path>` | Read one text file (up to 500 KB) |
| `GET` | `/api/projects/<id>/download` | Project as a zip (without `.env` files) |
| `POST` / `GET` / `DELETE` | `/api/projects/<id>/preview` | Start / inspect / stop the live preview |
| `GET` | `/api/projects/<id>/chat` | Chat history |
| `POST` | `/api/projects/<id>/chat` | Ask for a change. Body: `{ "message": "..." }` (up to 2000 characters) |

Project `status` is one of `queued`, `running`, `completed`, `completed_with_warnings`, `failed`.

**PowerShell**

```powershell
$body = @{
  requirements = 'Build a bookstore called "PageTurner". Pages: Home, Shop, Book Details, Cart, Login, Signup. Users can sign up and log in and place orders.'
  options = @{ max_debug_attempts = 2; validate_browser = $true }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/projects -ContentType "application/json" -Body $body
Invoke-RestMethod http://127.0.0.1:8000/api/projects/<id>
```

**curl**

```bash
curl -X POST http://127.0.0.1:8000/api/projects \
  -H "Content-Type: application/json" \
  -d '{"requirements":"A pet adoption site with pets, detail pages, login and signup.","options":{"max_debug_attempts":2}}'
```

The API has **no authentication**. Run it locally only (see [Limitations](#limitations)).

## What a generated project looks like

```text
backend2/generated_projects_api/<job_id>/
├── job.json                 # status, events, options, result
├── checkpoint.json          # lets "Resume" continue where a build stopped
└── <project-name>/
    ├── backend/             # FastAPI app
    │   ├── main.py          # routes, auth, validation
    │   ├── requirements.txt
    │   ├── storage/         # JSON-file store
    │   └── data/            # starter records (*.json)
    ├── frontend/<app>/      # React + Vite + Tailwind
    │   ├── src/{pages,components,context,api}/
    │   └── public/images/catalog/   # generated placeholder pictures
    └── preview.log          # output of the preview processes
```

Run a generated project by hand:

```bash
# backend
cd backend && pip install -r requirements.txt
export JWT_SECRET_KEY="change-me"            # PowerShell: $env:JWT_SECRET_KEY = "change-me"
uvicorn main:app --port 8010

# frontend (second terminal)
cd frontend/<app> && npm install
VITE_API_PROXY_TARGET=http://127.0.0.1:8010 npm run dev
```

The generated backend refuses to start without `JWT_SECRET_KEY`, on purpose: a hard-coded default secret would be insecure.

## Project structure

```text
AgentForge/
├── frontend/                       # AgentForge UI (React + Vite + Tailwind)
│   ├── src/
│   │   ├── App.jsx                 # layout and state
│   │   ├── api.js                  # every backend call
│   │   ├── mock.js                 # fake backend for `npm run demo`
│   │   ├── useProject.js           # polls one project
│   │   └── components/             # Sidebar, Home, Workspace, Preview, ui
│   └── .env, .env.demo
└── backend2/                       # API and agents
    ├── app.py                      # Flask entry point
    ├── run_pipeline.py             # command-line generator
    ├── api/                        # routes, job manager, live preview
    ├── workflow/                   # LangGraph graph, nodes, validators, debugger,
    │                               #   checkpoint/resume, chat revise engine, flow checks
    ├── planning/                   # master, frontend and backend planners
    ├── generation/                 # frontend/backend generators, seed data, placeholder art,
    │                               #   API contract checks, runtime/browser validation
    ├── research/                   # market and competitor research (Tavily)
    ├── core/                       # Groq client with rate limiting, dev secrets, npm helpers
    ├── tests/                      # pytest suite (+ Playwright spec in tests/browser)
    └── generated_projects_api/     # output (created at run time)
```

## Testing

```bash
cd backend2
pytest                      # whole suite
pytest tests/test_hitl.py   # one file
```

- Some tests start servers or use optional packages and are skipped or fail if those packages are missing — install everything in `requirements.txt` first.
- The browser check uses `tests/browser/runtime.spec.js` and needs `npx playwright install chromium`.
- Frontend: there is no build step to run for the demo. `npm run build` produces a production bundle.

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| `Estimated request size (…) exceeds the configured TPM budget` or HTTP 429 | Groq tokens-per-minute limit | Wait a minute and press **Resume**. Set `GROQ_TPM_LIMIT` to your plan's limit. Shorten the idea |
| `Groq daily token limit (TPD) has been reached` | Daily quota used up | Wait for the reset or use another key/plan, then **Resume** |
| `Backend did not start: …` in the preview | The generated backend crashed on startup | Read the reason shown in the UI and the end of `<project>/preview.log`. Run the backend by hand (see above) to see the full traceback |
| Everything stays "skipped" / browser check never runs | Playwright is not installed | `cd backend2 && npm install && npx playwright install chromium` |
| `This page crashed` in the preview | A runtime error in a generated page | Paste the error into the chat, for example: `Page crashed: query.toLowerCase is not a function` |
| Changed a Python file but nothing changes | Flask does not hot-reload | Stop (`Ctrl + C`) and run `python app.py` again |
| **Resume** starts a brand-new build | The project has no checkpoint (built before resume existed) | Expected for old projects; new builds resume from where they stopped |
| Browser console shows a CORS error | UI origin not allowed | Add it to `CORS_ORIGINS` in `backend2/.env` and restart the backend |
| `running scripts is disabled` (PowerShell) | Execution policy | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned`, then activate the venv again |
| Port already in use | Another process uses 8000 or 5173 | Set `PORT` in `.env`, or `npm run dev -- --port 5174` and add that origin to `CORS_ORIGINS` |
| First preview takes minutes | The generated frontend installs its npm packages once | Wait; later previews start faster |

## Limitations

- **Local tool.** The API has no authentication and can start processes on your machine. Do not expose it to a network.
- **Generated code is LLM output.** The pipeline validates a lot, but review the code, secrets and dependencies before using a project in production.
- **Generated apps use JSON-file storage**, which suits demos and prototypes, not production traffic.
- **Placeholder pictures are offline illustrations** (emoji and colours), not photos. Put real images in `frontend/public/images/` to replace them.
- **The preview is a dev server**, not a production build.
- Quality depends on the LLM and on your Groq rate limits; very large briefs are more likely to fail.
- The browser check cannot judge visual design, only that pages load and run without errors.

## Contributing

1. Fork the repository and create a feature branch.
2. Add or update tests in `backend2/tests/` and run `pytest`.
3. Open a pull request that explains the problem and the fix.

Bug reports are most useful with the project's `job.json` events and the tail of `preview.log`.

## License

No license file is included yet. Add a `LICENSE` file (for example MIT) before sharing or publishing the project.
