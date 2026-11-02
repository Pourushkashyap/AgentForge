# AgentForge Frontend (React + Tailwind + JavaScript)

```bash
npm install
npm run demo   # fake in-browser backend, no Python needed (tip: put the word "fail" in your idea to see Resume)
npm run dev    # real backend: set VITE_API_URL in .env (default http://127.0.0.1:8000), run `python app.py` in backend2
```

## Layout
- **Left sidebar**: project history (from `GET /api/projects`). Toggle button top-left; closes automatically when generation starts.
- **Center**: idea input (Home) -> live progress -> HITL chat (edit your site after it is built).
- **Right**: live preview iframe + Code tab (file browser) + reload / open / stop / download.
- **Resume**: failed builds show "Resume from here". Needs `backend_patch/APPLY_PATCH.md` (3 small edits); without it the frontend restarts with the same idea.

## Files
`src/api.js` (all backend calls) · `src/useProject.js` (polling) · `src/mock.js` (demo backend) · `src/components/*`

## CORS
backend2 already allows http://localhost:5173. For another origin set `CORS_ORIGINS` in backend2/.env.
