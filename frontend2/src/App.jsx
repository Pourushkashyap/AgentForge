import { useCallback, useEffect, useRef, useState } from 'react'
import { api, DONE, getReq, saveReq, USE_MOCK } from './api'
import useProject from './useProject'
import Sidebar from './components/Sidebar'
import Toolbar from './components/Toolbar'
import Canvas from './components/Canvas'
import Preview from './components/Preview'
import ChatPanel from './components/ChatPanel'
import { Btn, DEFAULT_OPTS, Icon, IconBtn, StatusPill, Toast, UserAvatar, copyText, projectTitle } from './components/ui'

const wide = () => window.innerWidth >= 1024

export default function App() {
  const [projects, setProjects] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [sidebar, setSidebar] = useState(wide())
  const [chatOpen, setChatOpen] = useState(true)
  const [mobileView, setMobileView] = useState('chat')   // phones: 'chat' | 'studio'
  const [tab, setTab] = useState('flow')                // 'flow' | 'preview' | 'code'
  const [opts, setOpts] = useState(DEFAULT_OPTS)
  const [online, setOnline] = useState(true)
  const [creating, setCreating] = useState(false)
  const [resuming, setResuming] = useState(false)
  const [notice, setNotice] = useState('')
  const [reload, setReload] = useState(0)
  const [toast, setToast] = useState('')
  const toastTimer = useRef(null)
  const { job, chat, setChat, refresh, error } = useProject(activeId)

  const flash = useCallback((msg) => {
    setToast(msg); clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToast(''), 2200)
  }, [])
  const copy = useCallback(async (text, label = 'Copied') => flash((await copyText(text)) ? label : 'Copy failed'), [flash])

  const loadList = useCallback(() => api.list().then((l) => { setProjects(l); setOnline(true) }).catch(() => setOnline(false)), [])
  useEffect(() => { loadList() }, [loadList])

  useEffect(() => {
    const h = (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'b') { e.preventDefault(); setSidebar((s) => !s) } }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [])

  useEffect(() => {
    if (job) setProjects((ps) => (ps.some((p) => p.id === job.id) ? ps.map((p) => (p.id === job.id ? { ...p, ...job } : p)) : [job, ...ps]))
  }, [job])

  const lastStatus = chat.at(-1)?.status
  useEffect(() => { if (lastStatus === 'done') setReload((r) => r + 1) }, [lastStatus, chat.length])

  const done = job && DONE.includes(job.status)
  const codeReady = !!job?.result?.project_root

  const open = (id) => { setActiveId(id); setNotice(''); setTab('flow'); setMobileView('studio'); if (!wide()) setSidebar(false) }
  const newProject = () => { setActiveId(null); setNotice(''); setTab('flow'); setMobileView('chat'); if (!wide()) setSidebar(false) }

  async function generate(requirements) {
    setCreating(true); setNotice('')
    try {
      const j = await api.create(requirements, opts)
      saveReq(j.id, requirements)
      setProjects((ps) => [j, ...ps])
      open(j.id)
    } catch (e) { setNotice(e.message) }
    setCreating(false)
  }

  async function rebuild(p) {
    const text = getReq(p.id) || p.requirements_preview
    if (!text) return flash('No idea text saved for this project')
    setCreating(true); setNotice('')
    try {
      const j = await api.create(text, p.options || opts)
      saveReq(j.id, text); setProjects((ps) => [j, ...ps]); open(j.id)
    } catch (e) { setNotice(e.message) }
    setCreating(false)
  }

  async function resume() {
    setResuming(true); setNotice('')
    try {
      await api.resume(activeId)
    } catch (e) {
      if ([404, 405, 409].includes(e.status)) {   // no /resume route or no checkpoint -> fresh run, same idea
        try {
          const text = getReq(activeId) || job.requirements_preview
          const j = await api.create(text, job.options)
          saveReq(j.id, text); setProjects((ps) => [j, ...ps]); setActiveId(j.id)
          setNotice(`${e.status === 409 ? e.message : 'Resume endpoint not installed (see backend_patch).'} Started a fresh run with the same idea.`)
        } catch (e2) { setNotice(e2.message) }
      } else setNotice(e.message)
    }
    await refresh(); setResuming(false)
  }

  async function send(text) {
    setNotice('')
    setChat((c) => [...c, { id: 'tmp-u', role: 'user', content: text }, { id: 'tmp-a', role: 'assistant', content: '', status: 'working', changed: [] }])
    try { await api.send(activeId, text) } catch (e) { setNotice(e.message); setChat((c) => c.filter((m) => !m.id.startsWith('tmp-'))) }
    refresh()
  }

  // Preview & Run: jump to the preview tab and start the servers if they are not running
  async function previewRun() {
    setTab('preview'); setMobileView('studio')
    if (done && !['ready', 'installing', 'starting'].includes(job.preview?.status || 'idle')) {
      try { await api.startPreview(activeId) } catch { /* shown in the preview pane */ }
      refresh()
    }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="relative flex min-h-0 flex-1">
        {sidebar && <div className="fixed inset-0 z-20 bg-black/70 lg:hidden" onClick={() => setSidebar(false)} aria-hidden="true" />}
        <Sidebar open={sidebar} projects={projects} activeId={activeId} online={online}
          onSelect={open} onNew={newProject} onRebuild={rebuild} onRetry={loadList} onClose={() => setSidebar(false)} />

        <main className="flex min-w-0 flex-1">
          <section className={`min-w-0 flex-1 flex-col ${mobileView === 'chat' ? 'hidden lg:flex' : 'flex'}`}>
            <header className="flex flex-wrap items-center gap-x-3 gap-y-2 px-5 pb-3 pt-4">
              {!sidebar && <IconBtn icon="sidebar" title="Open sidebar (Ctrl+B)" onClick={() => setSidebar(true)} />}
              <h1 className="min-w-0 truncate font-display text-xl font-semibold sm:text-2xl">{job && activeId ? projectTitle(job) : 'AgentForge Studio'}</h1>
              {job && activeId && <StatusPill status={job.status} />}
              {USE_MOCK && <span className="rounded-full border border-brand/40 bg-brand/10 px-2 py-0.5 text-xs font-medium text-brand-light">Demo</span>}
              <div className="ml-auto flex items-center gap-2">
                <Btn variant="primary" className="rounded-full" icon="play" disabled={!activeId} onClick={previewRun}>Preview &amp; Run</Btn>
                <Btn variant="outline" className="rounded-full" onClick={newProject}>Clear</Btn>
                <Btn variant="outline" className="hidden rounded-full sm:inline-flex" disabled={!activeId || creating} onClick={() => job && rebuild(job)}>Rebuild</Btn>
                {!chatOpen && <IconBtn icon="chat" title="Open Agent Chat" className="hidden lg:inline-flex" onClick={() => setChatOpen(true)} />}
                <UserAvatar className="hidden h-9 w-9 sm:flex" />
              </div>
            </header>
            <Toolbar opts={opts} setOpts={setOpts} tab={tab} setTab={setTab} codeReady={codeReady} />
            {tab === 'flow'
              ? <Canvas job={activeId ? job : null} resuming={resuming} onResume={resume} onRebuild={() => job && rebuild(job)} onCopy={copy} />
              : <Preview id={activeId} job={activeId ? job : null} view={tab} reload={reload} refresh={refresh} onCopy={copy} />}
          </section>

          <ChatPanel className={`${mobileView === 'chat' ? 'flex' : 'hidden'} ${chatOpen ? 'lg:flex' : 'lg:hidden'} w-full lg:w-[360px] lg:shrink-0`}
            id={activeId} job={activeId ? job : null} chat={chat} error={notice || error} creating={creating} resuming={resuming}
            online={online} onRetry={loadList} onGenerate={generate} onSend={send} onResume={resume} onRebuild={() => job && rebuild(job)}
            onShowPreview={previewRun} onCopy={copy} onCollapse={() => setChatOpen(false)} onMenu={() => setSidebar(true)} />
        </main>
      </div>

      <nav className="flex border-t border-seam bg-steel lg:hidden" aria-label="Switch view">
        {[['studio', 'flow', 'Studio'], ['chat', 'chat', 'Agent Chat']].map(([k, icon, l]) => (
          <button key={k} onClick={() => setMobileView(k)} aria-pressed={mobileView === k}
            className={`flex flex-1 items-center justify-center gap-2 py-3 text-sm font-medium ${mobileView === k ? 'text-brand-light' : 'text-mist'}`}><Icon n={icon} />{l}</button>
        ))}
      </nav>
      <Toast msg={toast} />
    </div>
  )
}
