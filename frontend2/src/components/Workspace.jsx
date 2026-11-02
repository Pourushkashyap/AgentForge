import { useEffect, useRef, useState } from 'react'
import { DONE, getReq } from '../api'
import { Btn, Icon, Spinner } from './ui'

const LEVEL = { error: 'text-rose-400', warning: 'text-amber-400', info: 'text-zinc-400' }

function StepIcon({ s }) {
  if (s === 'done') return <span className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500/20 text-emerald-400"><Icon n="check" className="h-3 w-3" /></span>
  if (s === 'failed') return <span className="flex h-5 w-5 items-center justify-center rounded-full bg-rose-500/20 text-rose-400"><Icon n="x" className="h-3 w-3" /></span>
  if (s === 'active') return <Spinner className="h-5 w-5" />
  return <span className="h-5 w-5 rounded-full border-2 border-zinc-700" />
}

function Progress({ job, onResume, resuming }) {
  const failed = job.status === 'failed'
  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900 p-4">
      <ul className="space-y-3">
        {job.steps.map((s) => (
          <li key={s.key} className="flex items-center gap-3">
            <StepIcon s={s.status} />
            <span className={`text-sm ${s.status === 'pending' ? 'text-zinc-500' : 'text-zinc-100'}`}>{s.label}</span>
          </li>
        ))}
      </ul>
      <div className="mt-4 max-h-40 space-y-0.5 overflow-y-auto rounded-lg bg-zinc-950 p-3 font-mono text-xs">
        {(job.events || []).slice(-10).map((e, i) => (
          <p key={i} className={LEVEL[e.level] || LEVEL.info}><span className="text-zinc-600">[{e.node}]</span> {e.message}</p>
        ))}
      </div>
      {failed && (
        <div className="mt-4 rounded-lg border border-rose-500/30 bg-rose-500/10 p-3">
          <p className="text-sm font-medium text-rose-300">Build paused / failed</p>
          {job.error && <p className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap text-xs text-rose-200/80">{job.error}</p>}
          <Btn variant="primary" icon="play" className="mt-3" disabled={resuming} onClick={onResume}>
            {resuming ? 'Resuming…' : 'Resume from here'}
          </Btn>
        </div>
      )}
    </div>
  )
}

function Bubble({ m }) {
  if (m.role === 'user') return <div className="ml-auto max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-violet-600 px-4 py-2 text-sm">{m.content}</div>
  return (
    <div className="max-w-[85%] rounded-2xl rounded-bl-sm bg-zinc-800 px-4 py-2 text-sm">
      {m.status === 'working' ? <span className="flex items-center gap-2 text-zinc-300"><Spinner /> Applying your change…</span>
        : <p className={m.status === 'failed' ? 'text-rose-300' : ''}>{m.content}</p>}
      {m.changed?.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {m.changed.map((f) => <span key={f} className="rounded bg-zinc-900 px-1.5 py-0.5 font-mono text-[11px] text-zinc-400">{f}</span>)}
        </div>
      )}
    </div>
  )
}

export default function Workspace({ id, job, chat, error, resuming, onResume, onSend }) {
  const [text, setText] = useState('')
  const end = useRef(null)
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth' }) }, [job?.events?.length, job?.status, chat.length, chat.at(-1)?.status])

  const done = job && DONE.includes(job.status)
  const working = chat.some((m) => m.status === 'working')
  const can = done && !working && text.trim().length > 0
  const send = () => { if (can) { onSend(text.trim()); setText('') } }
  const hint = done ? 'Ask for a change, e.g. "make the header dark"' : job?.status === 'failed' ? 'Resume the build to continue' : 'Chat unlocks when the build finishes'

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex-1 space-y-4 overflow-y-auto px-4 py-6">
        <div className="mx-auto max-w-2xl space-y-4">
          <div className="ml-auto max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-zinc-800 px-4 py-2 text-sm">{getReq(id) || job?.requirements_preview || '…'}</div>
          {!job ? <div className="flex items-center gap-2 text-sm text-zinc-500"><Spinner /> Connecting…</div> : <Progress job={job} onResume={onResume} resuming={resuming} />}
          {done && (
            <div className="rounded-2xl border border-emerald-500/30 bg-emerald-500/10 p-4 text-sm">
              <p className="font-medium text-emerald-300">{job.result?.business_name || 'Your project'} is ready</p>
              <p className="mt-1 text-xs text-zinc-400">{job.result?.api_contract?.length ?? 0} API endpoints · {job.result?.fixes?.length ?? 0} auto-fixes · {job.result?.issues?.length ?? 0} warnings. Tell me what to change below.</p>
            </div>
          )}
          {chat.map((m) => <Bubble key={m.id} m={m} />)}
          {error && <p className="rounded-lg bg-rose-500/10 p-3 text-sm text-rose-300">{error}</p>}
          <div ref={end} />
        </div>
      </div>
      <div className="border-t border-zinc-800 p-3">
        <div className="mx-auto flex max-w-2xl items-end gap-2 rounded-2xl border border-zinc-700 bg-zinc-900 p-2 focus-within:border-violet-500">
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={1} disabled={!done || working} placeholder={hint}
            onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && (e.preventDefault(), send())}
            className="max-h-32 flex-1 resize-none bg-transparent px-2 py-1.5 text-sm outline-none placeholder:text-zinc-500 disabled:opacity-50" />
          <Btn variant="primary" disabled={!can} onClick={send}><Icon n="send" /></Btn>
        </div>
      </div>
    </div>
  )
}
