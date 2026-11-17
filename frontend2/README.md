# AgentForge Frontend (React + Tailwind + JavaScript)

```bash
npm install
npm run demo   # fake in-browser backend, no Python needed (put the word "fail" in your idea to see Resume)
npm run dev    # real backend: set VITE_API_URL in .env (default http://127.0.0.1:8000), run `python app.py` in backend2
npm run build  # production build
```

## Layout (inspired by a flow-studio UI)
- **Sidebar**: New project, project search, status filters, per-project Rebuild, backend status. `Ctrl+B` toggles it.
- **Studio (center)**: title + status, `Preview & Run`, `Clear`, `Rebuild`. Build option chips (market research, sample data, browser test, auto-fix attempts) and three tabs:
  - **Flow**: the build pipeline as agent -> task nodes driven by live job steps, progress bar with timer, activity log (filter, copy).
  - **Preview**: desktop / tablet / mobile sizes, reload, open in new tab, stop, download, copy URL.
  - **Code**: folder tree, file filter, line numbers, copy file.
- **Agent Chat (right)**: describe your idea, example prompts and Surprise me, build status with Resume / Start over / Download, then chat to edit the site. Collapsible on desktop; on phones switch with the bottom bar.

## Theme
Only black, purple and white. Colors live in `tailwind.config.js` (ink, steel, plate, seam, fog, mist, brand, arc, ok, warn, bad). Fonts: Bricolage Grotesque (headings), Hanken Grotesk (text), JetBrains Mono (code).

## Files
`src/api.js` (backend calls) · `src/useProject.js` (polling) · `src/mock.js` (demo backend) · `src/components/*`

## CORS
backend2 already allows http://localhost:5173. For another origin set `CORS_ORIGINS` in backend2/.env.
