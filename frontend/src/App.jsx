import { useCallback, useEffect, useState } from 'react'
import { api, getReq, saveReq, USE_MOCK } from './api'
import useProject from './useProject'
import Sidebar from './components/Sidebar'
import Home from './components/Home'
import Workspace from './components/Workspace'
import Preview from './components/Preview'
import { Btn, Icon, projectTitle } from './components/ui'

const wide = () => window.innerWidth >= 1024

export default function App() {
  const [projects, setProjects] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [sidebar, setSidebar] = useState(wide())
  const [showPreview, setShowPreview] = useState(true)
  const [online, setOnline] = useState(true)
  const [creating, setCreating] = useState(false)
  const [resuming, setResuming] = useState(false)
  const [notice, setNotice] = useState('')
  const [reload, setReload] = useState(0)
  const { job, chat, setChat, refresh, error } = useProject(activeId)

  const loadList = useCallback(() => api.list().then((l) => { setProjects(l); setOnline(true) }).catch(() => setOnline(false)), [])
  useEffect(() => { loadList() }, [loadList])

  // keep sidebar entry in sync with the live job
  useEffect(() => {
    if (job) setProjects((ps) => (ps.some((p) => p.id === job.id) ? ps.map((p) => (p.id === job.id ? { ...p, ...job } : p)) : [job, ...ps]))
  }, [job])

  // reload the iframe whenever a chat edit finishes
  const lastStatus = chat.at(-1)?.status
  useEffect(() => { if (lastStatus === 'done') setReload((r) => r + 1) }, [lastStatus, chat.length])

  const open = (id) => { setActiveId(id); setNotice(''); setShowPreview(wide()); if (!wide()) setSidebar(false) }

  async function generate(requirements, options) {
    setCreating(true); setNotice('')
    try {
      const j = await api.create(requirements, options)
      saveReq(j.id, requirements)
      setProjects((ps) => [j, ...ps])
      open(j.id)
      setSidebar(false)           // sidebar closes when generation starts
      setShowPreview(wide())
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

  return (
    <div className="relative flex h-full overflow-hidden">
      <Sidebar open={sidebar} projects={projects} activeId={activeId} onSelect={open} onNew={() => { setActiveId(null); setNotice(''); if (!wide()) setSidebar(false) }} />
      <main className="flex min-w-0 flex-1">
        <section className={`min-w-0 flex-1 flex-col ${activeId && showPreview ? 'hidden lg:flex' : 'flex'}`}>
          <header className="flex items-center gap-2 border-b border-zinc-800 px-3 py-2">
            <Btn title={sidebar ? 'Close sidebar' : 'Open sidebar'} onClick={() => setSidebar(!sidebar)}><Icon n="sidebar" className="h-5 w-5" /></Btn>
            <span className="truncate text-sm font-semibold">{job && activeId ? projectTitle(job) : 'AgentForge'}</span>
            {USE_MOCK && <span className="rounded bg-amber-500/20 px-1.5 text-[11px] text-amber-300">DEMO</span>}
            {activeId && !showPreview && <Btn className="ml-auto" icon="eye" onClick={() => setShowPreview(true)}>Preview</Btn>}
          </header>
          {activeId
            ? <Workspace id={activeId} job={job} chat={chat} error={notice || error} resuming={resuming} onResume={resume} onSend={send} />
            : <Home onGenerate={generate} busy={creating} error={notice} online={online} />}
        </section>
        {activeId && showPreview && <Preview id={activeId} job={job} reload={reload} refresh={refresh} onClose={() => setShowPreview(false)} />}
      </main>
    </div>
  )
}
