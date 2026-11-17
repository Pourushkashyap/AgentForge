import { useMemo, useState } from 'react'
import { USE_MOCK } from '../api'
import { DOT, Icon, IconBtn, Logo, ago, projectTitle } from './ui'

const FILTERS = [['all', 'All'], ['running', 'Running'], ['done', 'Done'], ['failed', 'Failed']]
const bucket = (s) => (['queued', 'running'].includes(s) ? 'running' : s === 'failed' ? 'failed' : 'done')
const label = (s) => ({ queued: 'Queued', running: 'Building', completed: 'Ready', completed_with_warnings: 'Warnings', failed: 'Failed' }[s] || s.replace(/_/g, ' '))

export default function Sidebar({ open, projects, activeId, online, onSelect, onNew, onRebuild, onRetry, onClose }) {
  const [q, setQ] = useState('')
  const [filter, setFilter] = useState('all')

  const shown = useMemo(() => projects.filter((p) =>
    (filter === 'all' || bucket(p.status) === filter) &&
    projectTitle(p).toLowerCase().includes(q.trim().toLowerCase())), [projects, q, filter])

  return (
    <aside aria-label="Projects" className={`absolute z-30 h-full shrink-0 overflow-hidden bg-steel transition-all duration-300 lg:relative ${open ? 'w-64 border-r border-seam' : 'w-0'}`}>
      <div className="relative flex h-full w-64 flex-col">
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-64 bg-[radial-gradient(circle_at_0%_100%,rgba(139,92,246,0.38),transparent_65%)]" aria-hidden="true" />

        <div className="relative flex items-center gap-2.5 px-4 pb-4 pt-5">
          <Logo className="h-8 w-8" />
          <span className="font-display text-lg font-semibold tracking-tight">AgentForge</span>
          <IconBtn icon="left" title="Close sidebar (Ctrl+B)" className="ml-auto" onClick={onClose} />
        </div>

        <div className="relative px-3">
          <button onClick={onNew} aria-current={!activeId ? 'page' : undefined}
            className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition ${!activeId ? 'bg-gradient-to-r from-brand to-brand-deep text-white shadow-glow' : 'text-mist hover:bg-plate hover:text-fog'}`}>
            <Icon n="plus" className="h-5 w-5" />New project
          </button>

          <p className="mb-2 mt-5 px-1 text-sm font-medium text-fog">Projects</p>
          <div className="relative">
            <Icon n="search" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-mist" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search projects" aria-label="Search projects"
              className="w-full rounded-xl border border-seam bg-ink/70 py-2 pl-9 pr-3 text-sm placeholder:text-mist/70 focus:border-brand/70" />
          </div>
          <div className="mt-3 flex gap-1" role="tablist" aria-label="Filter projects">
            {FILTERS.map(([k, l]) => (
              <button key={k} role="tab" aria-selected={filter === k} onClick={() => setFilter(k)}
                className={`rounded-full px-2.5 py-1 text-xs font-medium transition ${filter === k ? 'bg-brand/20 text-brand-light' : 'text-mist hover:text-fog'}`}>{l}</button>
            ))}
          </div>
        </div>

        <nav className="relative mt-3 flex-1 space-y-0.5 overflow-y-auto px-2 pb-4">
          {projects.length === 0 && <p className="px-3 py-2 text-sm text-mist">No projects yet. Describe an idea in Agent Chat to start your first build.</p>}
          {projects.length > 0 && shown.length === 0 && <p className="px-3 py-2 text-sm text-mist">No projects match.</p>}
          {shown.map((p) => (
            <div key={p.id} className="group relative">
              <button onClick={() => onSelect(p.id)} aria-current={p.id === activeId ? 'page' : undefined}
                className={`w-full rounded-xl px-3 py-2 text-left transition ${p.id === activeId ? 'bg-plate shadow-[inset_2px_0_0_#8B5CF6]' : 'hover:bg-plate/60'}`}>
                <span className="flex items-center gap-2">
                  <span className={`h-2 w-2 shrink-0 rounded-full ${DOT[p.status] || 'bg-mist'}`} />
                  <span className="truncate pr-8 text-sm">{projectTitle(p)}</span>
                </span>
                <span className="mt-0.5 flex gap-2 pl-4 text-xs text-mist">
                  <span>{label(p.status)}</span><span className="text-mist/60">{ago(p.created_at)}</span>
                </span>
              </button>
              <IconBtn icon="refresh" title="Rebuild with the same idea" onClick={() => onRebuild(p)}
                className="absolute right-1.5 top-1.5 h-7 w-7 opacity-0 transition focus:opacity-100 group-hover:opacity-100 [@media(hover:none)]:opacity-100" />
            </div>
          ))}
        </nav>

        <div className="relative flex items-center gap-2 border-t border-seam/70 px-4 py-3 text-xs text-mist">
          <span className={`h-2 w-2 rounded-full ${USE_MOCK ? 'bg-warn' : online ? 'bg-ok' : 'bg-bad'}`} />
          <span>{USE_MOCK ? 'Demo mode, no backend' : online ? 'Backend connected' : 'Backend offline'}</span>
          {!USE_MOCK && !online && <button onClick={onRetry} className="ml-auto rounded px-1.5 py-0.5 text-brand-light hover:bg-plate">Retry</button>}
          {(USE_MOCK || online) && <span className="ml-auto">{projects.length} {projects.length === 1 ? 'project' : 'projects'}</span>}
        </div>
      </div>
    </aside>
  )
}
