import { useEffect, useRef, useState } from 'react'
import { Btn, Icon, IconBtn, Spinner, fmtTime, progressOf, useNow } from './ui'

// The build pipeline drawn as agent -> task nodes, driven by the live job steps.
const NODES = [
  { agent: 'Planner', role: 'Product architect who turns your idea into pages, layout and an API list.', task: 'Plan the pages, layout and API endpoints', who: 'Site Architect' },
  { agent: 'Code Generator', role: 'Full-stack engineer who writes the frontend and backend files.', task: 'Generate the frontend and backend files', who: 'Full-stack Engineer' },
  { agent: 'Validator and Debugger', role: 'QA engineer who checks the code and fixes errors automatically.', task: 'Validate the code and fix any errors', who: 'QA Engineer' },
  { agent: 'Installer', role: 'Build engineer who installs every dependency the project needs.', task: 'Install the project dependencies', who: 'Build Engineer' },
  { agent: 'Preview Runner', role: 'Release engineer who starts the servers and opens the live preview.', task: 'Launch the live preview', who: 'Release Engineer' },
]
const LEVEL = { error: 'text-bad', warning: 'text-warn', info: 'text-mist' }
const STATE = { pending: 'Waiting', active: 'Working', done: 'Done', failed: 'Failed' }

function Badge({ s }) {
  if (s === 'done') return <span className="flex h-5 w-5 items-center justify-center rounded-full bg-brand text-white"><Icon n="check" className="h-3 w-3" /></span>
  if (s === 'failed') return <span className="flex h-5 w-5 items-center justify-center rounded-full bg-bad/20 text-bad"><Icon n="x" className="h-3 w-3" /></span>
  if (s === 'active') return <Spinner className="h-5 w-5" />
  return <span className="block h-5 w-5 rounded-full border-2 border-seam" />
}

function Dot() { return <span className="h-2 w-2 shrink-0 rounded-full bg-brand ring-2 ring-ink" /> }

function Row({ n, last, onLogs }) {
  const s = n.status
  const ring = s === 'active' ? 'border-brand shadow-glow' : s === 'done' ? 'border-brand/50' : s === 'failed' ? 'border-bad/70' : 'border-seam'
  const card = `rounded-2xl border bg-plate p-4 transition ${ring} ${s === 'pending' ? 'opacity-70' : ''}`
  const lit = s === 'done' || s === 'active'
  return (
    <div>
      <div className="grid gap-3 sm:grid-cols-[1fr_56px_1fr] sm:gap-0">
        <div className={card}>
          <div className="flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-brand/20 text-brand-light"><Icon n="spark" className="h-3.5 w-3.5" /></span>
            <span className="text-xs text-mist">Agent</span>
            <span className="ml-auto"><Badge s={s} /></span>
          </div>
          <p className="mt-2 font-medium">{n.agent}</p>
          <p className="mt-2 rounded-lg border border-seam bg-ink/70 p-2 text-xs leading-relaxed text-mist">{n.role}</p>
          <button onClick={onLogs} className="mt-2 text-xs font-medium text-brand-light hover:underline">View logs</button>
        </div>

        <div className="hidden items-center sm:flex" aria-hidden="true">
          <Dot />
          <span className={`relative h-px flex-1 overflow-hidden ${lit ? 'bg-brand/60' : 'bg-seam'}`}>
            {s === 'active' && <span className="absolute top-0 h-px w-6 animate-forge bg-white" />}
          </span>
          <Dot />
        </div>

        <div className={card}>
          <div className="flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-brand/20 text-brand-light"><Icon n="layers" className="h-3.5 w-3.5" /></span>
            <span className="text-xs text-mist">Task</span>
          </div>
          <p className="mt-2 text-sm leading-relaxed">{n.task}</p>
          <span className="mt-3 inline-flex items-center gap-2 rounded-lg border border-seam bg-ink/70 px-2.5 py-1 text-xs">
            <span className="text-fog">{n.who}</span><span className={s === 'failed' ? 'text-bad' : s === 'pending' ? 'text-mist' : 'text-brand-light'}>{STATE[s]}</span>
          </span>
        </div>
      </div>
      {!last && <div className={`ml-8 h-5 w-px ${lit ? 'bg-brand/60' : 'bg-seam'}`} aria-hidden="true" />}
    </div>
  )
}

export default function Canvas({ job, resuming, onResume, onRebuild, onCopy }) {
  const failed = job?.status === 'failed'
  const running = ['queued', 'running'].includes(job?.status)
  const now = useNow(running)
  const [logOpen, setLogOpen] = useState(true)
  const [filter, setFilter] = useState('all')
  const logRef = useRef(null)

  const steps = job?.steps?.length ? job.steps : NODES.map(() => ({ status: 'pending' }))
  const nodes = steps.map((s, i) => ({ ...(NODES[i] || { agent: s.label, role: 'Runs this stage of the build.', task: s.label, who: 'Agent' }), status: s.status }))
  const pct = progressOf(job)

  const all = job?.events || []
  const events = all.filter((e) => filter === 'all' || e.level === filter).slice(-40)
  const warnings = all.filter((e) => e.level === 'warning').length
  const errors = all.filter((e) => e.level === 'error').length
  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight }, [events.length, logOpen, filter])

  const chip = (k, l, c) => (
    <button key={k} onClick={() => setFilter(k)} aria-pressed={filter === k}
      className={`rounded-full px-2.5 py-0.5 text-xs transition ${filter === k ? 'bg-brand/20 text-brand-light' : 'text-mist hover:text-fog'}`}>{l}{c != null ? ` ${c}` : ''}</button>
  )

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="dot-grid min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl space-y-4 p-5">
          {!job ? (
            <div className="flex items-start gap-3 rounded-2xl border border-brand/40 bg-plate/90 p-4">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand/20 text-brand-light"><Icon n="spark" /></span>
              <div>
                <p className="font-display text-base font-semibold">Your agent flow appears here</p>
                <p className="mt-0.5 text-sm text-mist">Describe your website in Agent Chat. These five agents then plan, build, check and launch it, and you can watch each one work.</p>
              </div>
            </div>
          ) : (
            <div className="rounded-2xl border border-seam bg-steel/90 p-4">
              <div className="flex items-center gap-3">
                <h2 className="font-display text-base font-semibold">Build progress</h2>
                <span className="ml-auto flex items-center gap-3 text-xs text-mist">
                  {running && <span className="inline-flex items-center gap-1"><Icon n="clock" className="h-3.5 w-3.5" />{fmtTime(now / 1000 - job.created_at)}</span>}
                  <span className="font-medium tabular-nums text-fog">{pct}%</span>
                </span>
              </div>
              <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-seam" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Build progress">
                <div className={`h-full rounded-full transition-all duration-500 ${failed ? 'bg-bad' : 'bg-gradient-to-r from-brand-deep to-brand-light'}`} style={{ width: `${pct}%` }} />
              </div>
            </div>
          )}

          {failed && (
            <div role="alert" className="rounded-2xl border border-bad/50 bg-bad/10 p-4">
              <p className="flex items-center gap-1.5 text-sm font-medium text-bad"><Icon n="alert" />Build paused or failed</p>
              {job.error && <p className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap font-mono text-xs text-bad/90">{job.error}</p>}
              <div className="mt-3 flex flex-wrap gap-2">
                <Btn variant="primary" icon="play" disabled={resuming} onClick={onResume}>{resuming ? 'Resuming…' : 'Resume from here'}</Btn>
                <Btn variant="outline" icon="refresh" onClick={onRebuild}>Start over</Btn>
                {job.error && <Btn icon="copy" onClick={() => onCopy(job.error, 'Error copied')}>Copy error</Btn>}
              </div>
            </div>
          )}

          <div>{nodes.map((n, i) => <Row key={i} n={n} last={i === nodes.length - 1} onLogs={() => { setLogOpen(true); setFilter('all') }} />)}</div>
        </div>
      </div>

      <div className="border-t border-seam bg-steel">
        <div className="flex items-center gap-2 px-3 py-2">
          <button onClick={() => setLogOpen(!logOpen)} aria-expanded={logOpen} className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-sm font-medium hover:bg-plate">
            <Icon n="terminal" />Activity log<Icon n="chevron" className={`h-3.5 w-3.5 transition ${logOpen ? 'rotate-180' : ''}`} />
          </button>
          {logOpen && <div className="flex gap-0.5">{chip('all', 'All')}{chip('warning', 'Warnings', warnings)}{chip('error', 'Errors', errors)}</div>}
          <IconBtn icon="copy" title="Copy log" className="ml-auto" disabled={!all.length} onClick={() => onCopy(all.map((e) => `[${e.node}] ${e.level}: ${e.message}`).join('\n'), 'Log copied')} />
        </div>
        {logOpen && (
          <div ref={logRef} className="mx-3 mb-3 h-32 space-y-0.5 overflow-y-auto rounded-xl bg-ink p-3 font-mono text-xs">
            {events.length === 0 && <p className="text-mist">No messages yet. Logs appear here once a build starts.</p>}
            {events.map((e, i) => <p key={i} className={LEVEL[e.level] || LEVEL.info}><span className="text-mist/60">[{e.node}]</span> {e.message}</p>)}
          </div>
        )}
      </div>
    </div>
  )
}
