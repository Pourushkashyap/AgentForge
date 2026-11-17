import { Icon, IconBtn } from './ui'

const OPTS = [['research', 'Market research'], ['seed_data', 'Sample data'], ['validate_browser', 'Browser test']]
const TABS = [['flow', 'flow', 'Flow'], ['preview', 'eye', 'Preview'], ['code', 'code', 'Code']]

// Build options as chips (like the LLM / Tools chips in a flow studio) + view tabs.
export default function Toolbar({ opts, setOpts, tab, setTab, codeReady }) {
  const n = opts.max_debug_attempts
  return (
    <div className="flex flex-wrap items-center gap-2 px-5 pb-3">
      {OPTS.map(([k, name]) => (
        <button key={k} aria-pressed={opts[k]} onClick={() => setOpts({ ...opts, [k]: !opts[k] })} title={`${name}: applies to the next build`}
          className={`inline-flex items-center gap-2 rounded-xl border px-3 py-1.5 text-sm transition ${opts[k] ? 'border-brand/60 bg-brand/15 text-fog' : 'border-seam bg-plate text-mist hover:text-fog'}`}>
          <span className={`h-2 w-2 rounded-full ${opts[k] ? 'bg-brand-light' : 'bg-seam'}`} />{name}
        </button>
      ))}
      <div className={`inline-flex items-center gap-1 rounded-xl border py-1 pl-3 pr-1 text-sm ${n > 0 ? 'border-brand/60 bg-brand/15 text-fog' : 'border-seam bg-plate text-mist'}`} title="Auto-fix attempts: applies to the next build">
        Auto-fix
        <IconBtn icon="minus" title="Fewer auto-fix attempts" className="h-6 w-6" disabled={n <= 0} onClick={() => setOpts({ ...opts, max_debug_attempts: n - 1 })} />
        <span className="w-4 text-center font-medium tabular-nums" aria-live="polite">{n}</span>
        <IconBtn icon="plus" title="More auto-fix attempts" className="h-6 w-6" disabled={n >= 5} onClick={() => setOpts({ ...opts, max_debug_attempts: n + 1 })} />
      </div>

      <div className="ml-auto flex rounded-xl border border-seam bg-plate p-0.5" role="tablist" aria-label="View">
        {TABS.map(([k, icon, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} disabled={k === 'code' && !codeReady && tab !== 'code'} onClick={() => setTab(k)}
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:opacity-40 ${tab === k ? 'bg-brand/25 text-fog' : 'text-mist hover:text-fog'}`}>
            <Icon n={icon} />{label}
          </button>
        ))}
      </div>
    </div>
  )
}
