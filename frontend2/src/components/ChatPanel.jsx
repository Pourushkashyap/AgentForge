import { useEffect, useRef, useState } from 'react'
import { api, DONE, getReq } from '../api'
import { Btn, BotAvatar, Icon, IconBtn, LinkBtn, Spinner, UserAvatar, progressOf } from './ui'

const EXAMPLES = [
  { label: 'Yoga studio', text: 'A booking website for a yoga studio with class schedule, gallery and contact form' },
  { label: 'Candle store', text: 'An online store for handmade candles with cart and order tracking' },
  { label: 'Fitness coaching', text: 'A fitness coaching platform with workouts, diet plans and progress dashboard' },
  { label: 'Restaurant', text: 'A restaurant website with menu, table reservations and customer reviews' },
  { label: 'Photographer', text: 'A photographer portfolio with project galleries, pricing and an enquiry form' },
  { label: 'Dental clinic', text: 'A dental clinic website with services, doctor profiles and appointment booking' },
]
const SUGGESTIONS = ['Make the header dark', 'Change the accent color to green', 'Add a testimonials section', 'Improve the layout on mobile', 'Add a contact form']

const Bot = ({ children }) => <div className="flex gap-2.5"><BotAvatar /><div className="min-w-0 flex-1 pt-0.5 text-sm leading-relaxed">{children}</div></div>
const Me = ({ children, onCopy }) => (
  <div className="group flex gap-2.5">
    <UserAvatar />
    <div className="min-w-0 rounded-xl border border-seam bg-plate px-3 py-2 text-sm leading-relaxed"><p className="whitespace-pre-wrap">{children}</p></div>
    {onCopy && <IconBtn icon="copy" title="Copy idea" className="h-7 w-7 opacity-0 transition focus:opacity-100 group-hover:opacity-100 [@media(hover:none)]:opacity-100" onClick={onCopy} />}
  </div>
)

function BuildStatus({ id, job, resuming, onResume, onRebuild, onShowPreview }) {
  if (!job) return <span className="flex items-center gap-2 text-mist"><Spinner /> Connecting…</span>
  if (['queued', 'running'].includes(job.status)) {
    const cur = job.steps.find((s) => s.status === 'active')
    return (
      <div>
        <p className="flex items-center gap-2"><Spinner />Building your website</p>
        <p className="mt-1 text-xs text-mist">{cur ? cur.label : 'Getting ready'}, {progressOf(job)}% done. Watch each agent on the Flow tab.</p>
      </div>
    )
  }
  if (job.status === 'failed') {
    return (
      <div>
        <p className="font-medium text-bad">The build stopped before it finished.</p>
        {job.error && <p className="mt-1 max-h-20 overflow-auto whitespace-pre-wrap font-mono text-xs text-bad/90">{job.error}</p>}
        <div className="mt-3 flex flex-wrap gap-2">
          <Btn size="sm" variant="primary" icon="play" disabled={resuming} onClick={onResume}>{resuming ? 'Resuming…' : 'Resume from here'}</Btn>
          <Btn size="sm" variant="outline" icon="refresh" onClick={onRebuild}>Start over</Btn>
        </div>
      </div>
    )
  }
  const n = (k) => job.result?.[k]?.length ?? 0
  return (
    <div>
      <p className="font-medium">{job.result?.business_name || 'Your project'} is ready.</p>
      <p className="mt-1 text-xs text-mist">{n('api_contract')} API endpoints, {n('fixes')} auto-fixes, {n('issues')} warnings.</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Btn size="sm" variant="primary" icon="eye" onClick={onShowPreview}>Open preview</Btn>
        <LinkBtn size="sm" icon="download" href={api.downloadUrl(id)}>Download zip</LinkBtn>
        <Btn size="sm" variant="outline" icon="refresh" onClick={onRebuild}>Rebuild</Btn>
      </div>
    </div>
  )
}

export default function ChatPanel({ className = '', id, job, chat, error, creating, resuming, online, onRetry, onGenerate, onSend, onResume, onRebuild, onShowPreview, onCopy, onCollapse, onMenu }) {
  const [text, setText] = useState('')
  const end = useRef(null)
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth' }) }, [job?.status, chat.length, chat.at(-1)?.status, id])

  const done = job && DONE.includes(job.status)
  const working = chat.some((m) => m.status === 'working')
  const len = text.trim().length
  const disabled = creating || (id && !done) || working
  const can = !disabled && (id ? len > 0 : len >= 10)
  const placeholder = !id ? 'Describe your website idea' : done ? 'Ask for a change' : job?.status === 'failed' ? 'Resume the build to keep chatting' : 'Chat unlocks when the build finishes'

  const submit = (t = text) => {
    const v = t.trim()
    if (!v || disabled) return
    if (!id) { if (v.length < 10) return; onGenerate(v) } else onSend(v)
    setText('')
  }
  const surprise = () => {
    const pool = EXAMPLES.filter((e) => e.text !== text)
    setText(pool[Math.floor(Math.random() * pool.length)].text)
  }
  const idea = id ? (getReq(id) || job?.requirements_preview || '…') : ''

  return (
    <aside aria-label="Agent Chat" className={`min-h-0 flex-col border-seam bg-steel lg:m-3 lg:ml-0 lg:rounded-2xl lg:border ${className}`}>
      <div className="flex items-center gap-2 px-4 py-3.5">
        <IconBtn icon="sidebar" title="Open projects" className="h-7 w-7 lg:hidden" onClick={onMenu} />
        <IconBtn icon="right" title="Collapse chat" className="hidden h-7 w-7 lg:inline-flex" onClick={onCollapse} />
        <h2 className="font-display text-sm font-semibold">Agent Chat</h2>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 pb-4">
        <Bot>
          <p className="font-display text-lg font-medium">Describe your idea</p>
          {!id && <p className="mt-1 text-mist">Tell me what the website is for and what it needs. I'll hand it to the agents and build it.</p>}
        </Bot>

        {!id && (
          <div className="pl-9">
            <p className="mb-2 text-xs text-mist">Or start from an example</p>
            <div className="flex flex-wrap gap-2">
              {EXAMPLES.map((ex) => (
                <button key={ex.label} onClick={() => setText(ex.text)} title={ex.text}
                  className="rounded-full border border-seam px-3 py-1 text-xs text-mist transition hover:border-brand/60 hover:bg-plate hover:text-fog">{ex.label}</button>
              ))}
              <button onClick={surprise} className="inline-flex items-center gap-1.5 rounded-full border border-brand/50 bg-brand/10 px-3 py-1 text-xs text-brand-light transition hover:bg-brand/20"><Icon n="shuffle" className="h-3 w-3" />Surprise me</button>
            </div>
          </div>
        )}

        {!online && (
          <div role="alert" className="flex flex-wrap items-center gap-2 rounded-xl border border-seam bg-plate p-3 text-xs text-mist">
            <Icon n="alert" className="h-4 w-4 text-bad" />
            <p className="min-w-0 flex-1">Can't reach the backend. Start it with <code>python app.py</code> or run <code>npm run demo</code>.</p>
            <Btn size="sm" variant="outline" icon="refresh" onClick={onRetry}>Retry</Btn>
          </div>
        )}

        {id && <Me onCopy={() => onCopy(idea, 'Idea copied')}>{idea}</Me>}
        {id && <Bot><BuildStatus id={id} job={job} resuming={resuming} onResume={onResume} onRebuild={onRebuild} onShowPreview={onShowPreview} /></Bot>}

        {chat.map((m) => m.role === 'user' ? <Me key={m.id}>{m.content}</Me> : (
          <div key={m.id} className="group flex gap-2.5">
            <BotAvatar />
            <div className="min-w-0 flex-1 pt-0.5 text-sm leading-relaxed">
              {m.status === 'working'
                ? <span className="flex items-center gap-2 text-mist"><Spinner /> Applying your change…</span>
                : <p className={`whitespace-pre-wrap ${m.status === 'failed' ? 'text-bad' : ''}`}>{m.content}</p>}
              {m.changed?.length > 0 && <div className="mt-2 flex flex-wrap gap-1">{m.changed.map((f) => <span key={f} className="rounded bg-ink px-1.5 py-0.5 font-mono text-[11px] text-mist">{f}</span>)}</div>}
            </div>
            {m.status !== 'working' && m.content && <IconBtn icon="copy" title="Copy reply" className="h-7 w-7 opacity-0 transition focus:opacity-100 group-hover:opacity-100 [@media(hover:none)]:opacity-100" onClick={() => onCopy(m.content, 'Reply copied')} />}
          </div>
        ))}
        {error && <p role="alert" className="rounded-xl border border-bad/40 bg-bad/10 p-3 text-sm text-bad">{error}</p>}
        <div ref={end} />
      </div>

      <div className="px-3 pb-3">
        {done && !working && !text && (
          <div className="mb-2 flex gap-1.5 overflow-x-auto pb-1" aria-label="Suggested changes">
            {SUGGESTIONS.map((s) => <button key={s} onClick={() => submit(s)} className="shrink-0 rounded-full border border-seam px-3 py-1 text-xs text-mist transition hover:border-brand/60 hover:bg-plate hover:text-fog">{s}</button>)}
          </div>
        )}
        <div className={`flex items-end gap-2 rounded-2xl border-[1.5px] bg-ink px-3 py-2 transition ${disabled ? 'border-seam' : 'border-brand focus-within:shadow-glow'}`}>
          <textarea value={text} onChange={(e) => setText(e.target.value)} disabled={disabled} aria-label="Message"
            rows={Math.min(5, Math.max(1, text.split('\n').length))} placeholder={placeholder}
            onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && (e.preventDefault(), submit())}
            className="min-h-[28px] flex-1 resize-none bg-transparent py-1 text-sm placeholder:text-mist/70 disabled:opacity-60" />
          <Btn variant="white" disabled={!can} onClick={() => submit()} className="rounded-xl px-4 py-1.5">{creating ? <Spinner tone="dark" className="h-3.5 w-3.5" /> : 'Send'}</Btn>
        </div>
        <p className={`mt-1.5 px-1 text-xs ${!id && text && len < 10 ? 'text-warn' : 'text-mist/70'}`}>{!id && text && len < 10 ? 'Write at least 10 characters.' : 'Enter to send, Shift + Enter for a new line.'}</p>
      </div>
    </aside>
  )
}
