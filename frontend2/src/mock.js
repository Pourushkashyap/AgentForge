// In-browser fake backend for demos (npm run demo). Same shape as the real API.
// Tip: put the word "fail" in your idea to see a paused build and the Resume button.
const KEY = 'af:mock'
const read = () => { try { return JSON.parse(localStorage.getItem(KEY)) || [] } catch { return [] } }
const write = (jobs) => localStorage.setItem(KEY, JSON.stringify(jobs))
const find = (id) => read().find((j) => j.id === id)
const patch = (id, fn) => { const jobs = read(); const j = jobs.find((x) => x.id === id); if (j) fn(j); write(jobs); return j }

const TIMELINE = [
  [0, 'research', 'Research disabled; skipped'], [1, 'master_plan', 'Planned project'],
  [2.5, 'frontend_plan', '5 pages, 8 planned API calls'], [4, 'frontend_build', 'Frontend generated'],
  [5, 'api_analysis', '8 endpoints required'], [6.5, 'backend_plan', 'Backend planned'],
  [8, 'backend_build', 'Backend generated with 8 endpoints'], [9.5, 'validate', '0 errors, 1 warnings'],
  [10.5, 'finalize', 'Project ready (completed)'],
]
const FAIL_AT = 6.2
const STEP_LABELS = [['planning', 'Planning'], ['generating', 'Generating Files'], ['validating', 'Validating & Debugging'], ['installing', 'Installing Dependencies'], ['preview', 'Launching Preview']]
const titleOf = (t) => t.replace(/[^a-z0-9 ]/gi, '').split(/\s+/).slice(0, 4).map((w) => w[0]?.toUpperCase() + w.slice(1)).join(' ') || 'Demo Project'

function siteHtml(j) {
  const { accent = '#7c3aed', dark = false } = j.theme || {}
  return `<html><body style="margin:0;font-family:system-ui;background:#f8fafc;color:#0f172a">
<div style="padding:16px 32px;background:${dark ? '#0f172a' : accent};color:#fff;display:flex;justify-content:space-between"><b>${titleOf(j.requirements)}</b><span>Home &nbsp; Services &nbsp; Contact</span></div>
<div style="padding:64px 32px"><h1 style="font-size:42px;margin:0 0 12px">${titleOf(j.requirements)}</h1>
<p style="max-width:520px;color:#475569">${j.requirements.slice(0, 160)}</p>
<button style="background:${accent};color:#fff;border:0;padding:12px 22px;border-radius:10px;margin-top:16px">Get started</button></div>
<div style="display:flex;gap:16px;padding:0 32px">${[1, 2, 3].map((n) => `<div style="flex:1;background:#fff;border-radius:14px;padding:20px;box-shadow:0 1px 4px #0002"><b>Feature ${n}</b><p style="color:#64748b">Generated demo card.</p></div>`).join('')}</div></body></html>`
}

function view(j) {
  const now = Date.now()
  const t = j.offset + (now - j.started) / 1000
  const failed = j.failFlag && !j.resumed && t >= FAIL_AT
  const T = failed ? FAIL_AT : t
  const done = !failed && T >= 10.5
  const status = failed ? 'failed' : done ? 'completed' : 'running'
  const events = TIMELINE.filter((e) => e[0] <= T).map(([at, node, message], i) => ({ index: i, ts: j.started / 1000 + at - j.offset, node, level: 'info', message }))
  if (failed) events.push({ index: events.length, ts: now / 1000, node: 'backend_plan', level: 'error', message: 'backend_plan failed: Groq rate limit (demo)' })

  const pv0 = j.pv === undefined ? j.started + (10.5 - j.offset) * 1000 : j.pv
  let pv = 'idle'
  if (done && pv0 === null) pv = 'stopped'
  else if (done) { const d = (now - pv0) / 1000; pv = d < 0 ? 'installing' : d < 1.5 ? 'installing' : d < 3 ? 'starting' : 'ready' }

  const gen = T >= 9.5 ? 'done' : T < 4 ? 'pending' : failed ? 'failed' : 'active'
  const stepStatus = [
    T >= 4 ? 'done' : failed ? 'failed' : 'active', gen,
    T >= 10.5 ? 'done' : T < 9.5 ? 'pending' : 'active',
    pv === 'installing' ? 'active' : ['starting', 'ready'].includes(pv) ? 'done' : 'pending',
    pv === 'starting' ? 'active' : pv === 'ready' ? 'done' : 'pending',
  ]
  return {
    id: j.id, status, requirements_preview: j.requirements.slice(0, 200), created_at: j.created, options: j.options,
    current_node: events.at(-1)?.node, error: failed ? 'backend_plan: Groq rate limit (demo)' : null,
    result: done ? { project_name: titleOf(j.requirements).toLowerCase().replace(/ /g, '-'), business_name: titleOf(j.requirements), project_root: '/demo', api_contract: new Array(8).fill(0), issues: [{}], fixes: [{}] } : null,
    preview: { status: pv, url: pv === 'ready' ? 'data:text/html;charset=utf-8,' + encodeURIComponent(siteHtml(j)) : null },
    steps: STEP_LABELS.map(([key, label], i) => ({ key, label, status: stepStatus[i] })), events: events.slice(-50),
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const needJob = (id) => { const j = find(id); if (!j) throw Object.assign(new Error('Project not found.'), { status: 404 }); return j }

export const api = {
  health: async () => ({ status: 'ok' }),
  list: async () => read().map(view).sort((a, b) => b.created_at - a.created_at),
  create: async (requirements, options) => {
    await sleep(250)
    const j = { id: Math.random().toString(16).slice(2, 14).padEnd(12, '0'), requirements, options, created: Date.now() / 1000, started: Date.now(), offset: 0, failFlag: /fail/i.test(requirements), chat: [], theme: {} }
    write([...read(), j]); return view(j)
  },
  get: async (id) => view(needJob(id)),
  resume: async (id) => { needJob(id); return view(patch(id, (j) => { j.resumed = true; j.offset = FAIL_AT; j.started = Date.now() })) },
  startPreview: async (id) => { needJob(id); return view(patch(id, (j) => { j.pv = Date.now() })).preview },
  stopPreview: async (id) => { needJob(id); return view(patch(id, (j) => { j.pv = null })).preview },
  chat: async (id) => {
    const j = patch(id, (job) => (job.chat || []).forEach((m) => {
      if (m.status === 'working' && Date.now() >= m.doneAt) {
        m.status = 'done'; m.changed = ['frontend/src/App.jsx', 'frontend/src/styles.css']
        const colors = { red: '#dc2626', blue: '#2563eb', green: '#16a34a', purple: '#7c3aed', orange: '#ea580c', pink: '#db2777' }
        const text = m.ask.toLowerCase(); job.theme = job.theme || {}
        Object.keys(colors).forEach((c) => { if (text.includes(c)) job.theme.accent = colors[c] })
        if (text.includes('dark')) job.theme.dark = true
        m.content = 'Done — I updated the site and verified the build still passes.'
      }
    }))
    return j.chat
  },
  send: async (id, message) => {
    needJob(id)
    const j = patch(id, (job) => { job.chat.push({ id: 'u' + job.chat.length, role: 'user', content: message }, { id: 'a' + job.chat.length, role: 'assistant', content: '', status: 'working', changed: [], ask: message, doneAt: Date.now() + 2500 }) })
    return j.chat.at(-1)
  },
  files: async () => ['README.md', 'backend/main.py', 'frontend/package.json', 'frontend/src/App.jsx'],
  file: async (_id, path) => `// ${path}\n// (demo file — real content comes from your backend)\n`,
  downloadUrl: () => '#',
}
