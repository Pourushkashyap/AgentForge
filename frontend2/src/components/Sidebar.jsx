import { Btn, DOT, ago, projectTitle } from './ui'

export default function Sidebar({ open, projects, activeId, onSelect, onNew }) {
  return (
    <aside className={`absolute z-30 h-full shrink-0 overflow-hidden bg-zinc-900 transition-all duration-300 lg:relative ${open ? 'w-72 border-r border-zinc-800' : 'w-0'}`}>
      <div className="flex h-full w-72 flex-col">
        <div className="p-3">
          <Btn variant="primary" icon="plus" className="w-full" onClick={onNew}>New project</Btn>
        </div>
        <p className="px-4 pb-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">History</p>
        <nav className="flex-1 space-y-1 overflow-y-auto px-2 pb-4">
          {projects.length === 0 && <p className="px-2 text-sm text-zinc-500">No projects yet.</p>}
          {projects.map((p) => (
            <button key={p.id} onClick={() => onSelect(p.id)}
              className={`w-full rounded-lg px-3 py-2 text-left transition ${p.id === activeId ? 'bg-zinc-800' : 'hover:bg-zinc-800/60'}`}>
              <div className="flex items-center gap-2">
                <span className={`h-2 w-2 shrink-0 rounded-full ${DOT[p.status] || 'bg-zinc-600'}`} />
                <span className="truncate text-sm">{projectTitle(p)}</span>
              </div>
              <p className="mt-0.5 pl-4 text-xs text-zinc-500">{p.status.replace(/_/g, ' ')} · {ago(p.created_at)}</p>
            </button>
          ))}
        </nav>
      </div>
    </aside>
  )
}
