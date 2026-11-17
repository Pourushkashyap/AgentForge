import { useEffect, useMemo, useState } from 'react'
import { api, DONE } from '../api'
import { Btn, Icon, IconBtn, IconLink, Spinner } from './ui'

const DEVICES = [['desktop', 'monitor', 'Desktop', '100%'], ['tablet', 'tablet', 'Tablet (768px)', '768px'], ['mobile', 'phone', 'Mobile (390px)', '390px']]

function CodeView({ id, reload, onCopy }) {
  const [files, setFiles] = useState(null)
  const [sel, setSel] = useState(null)
  const [src, setSrc] = useState('')
  const [q, setQ] = useState('')
  const [closed, setClosed] = useState({})

  useEffect(() => { api.files(id).then(setFiles).catch(() => setFiles([])) }, [id, reload])
  useEffect(() => {
    if (files?.length && !sel) setSel(files.find((f) => /readme/i.test(f)) || files[0])
  }, [files, sel])
  useEffect(() => {
    if (!sel) return
    setSrc('Loading…')
    api.file(id, sel).then((t) => setSrc(typeof t === 'string' ? t : JSON.stringify(t, null, 2))).catch((e) => setSrc(e.message))
  }, [id, sel, reload])

  const groups = useMemo(() => {
    const m = {}
    ;(files || []).filter((f) => f.toLowerCase().includes(q.trim().toLowerCase())).forEach((f) => {
      const i = f.lastIndexOf('/')
      const d = i < 0 ? '(root)' : f.slice(0, i)
      if (!m[d]) m[d] = []
      m[d].push(f)
    })
    return Object.entries(m).sort(([a], [b]) => a.localeCompare(b))
  }, [files, q])

  if (!files) return <div className="p-6"><Spinner /></div>
  if (!files.length) return <p className="p-6 text-sm text-mist">No files yet.</p>

  const lines = src.split('\n')
  return (
    <div className="flex h-full min-h-0">
      <div className="flex w-60 shrink-0 flex-col border-r border-seam">
        <div className="relative p-2">
          <Icon n="search" className="pointer-events-none absolute left-4 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-mist" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter files" aria-label="Filter files"
            className="w-full rounded-md border border-seam bg-ink py-1.5 pl-8 pr-2 text-xs placeholder:text-mist/70 focus:border-brand/70" />
        </div>
        <div className="flex-1 overflow-y-auto px-2 pb-2 text-xs">
          {groups.length === 0 && <p className="px-2 py-1 text-mist">No files match.</p>}
          {groups.map(([dir, list]) => (
            <div key={dir} className="mb-1">
              <button onClick={() => setClosed({ ...closed, [dir]: !closed[dir] })} aria-expanded={!closed[dir]}
                className="flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-mist hover:text-fog">
                <Icon n="chevron" className={`h-3 w-3 shrink-0 transition ${closed[dir] ? '-rotate-90' : ''}`} />
                <Icon n="folder" className="h-3.5 w-3.5 shrink-0 text-brand" />
                <span className="truncate font-mono">{dir}</span><span className="ml-auto text-mist/60">{list.length}</span>
              </button>
              {!closed[dir] && list.map((f) => (
                <button key={f} onClick={() => setSel(f)} title={f}
                  className={`flex w-full items-center gap-1.5 truncate rounded py-1 pl-7 pr-2 text-left font-mono ${sel === f ? 'bg-plate text-fog' : 'text-mist hover:bg-plate/60 hover:text-fog'}`}>
                  <Icon n="file" className="h-3.5 w-3.5 shrink-0" /><span className="truncate">{f.split('/').pop()}</span>
                </button>
              ))}
            </div>
          ))}
        </div>
      </div>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b border-seam px-3 py-1.5">
          <span className="min-w-0 flex-1 truncate font-mono text-xs text-mist">{sel || 'Select a file'}</span>
          {sel && <span className="text-xs text-mist/60">{lines.length} lines</span>}
          {sel && <IconBtn icon="copy" title="Copy file" className="h-7 w-7" onClick={() => onCopy(src, 'File copied')} />}
        </div>
        <div className="min-h-0 flex-1 overflow-auto bg-ink p-3">
          {sel ? (
            <div className="flex font-mono text-xs leading-relaxed">
              <pre aria-hidden="true" className="select-none pr-4 text-right text-mist/40">{lines.map((_, i) => i + 1).join('\n')}</pre>
              <pre className="min-w-0 text-fog/90">{src}</pre>
            </div>
          ) : <p className="text-sm text-mist">Select a file to view it.</p>}
        </div>
      </div>
    </div>
  )
}

export default function Preview({ id, job, view, reload, refresh, onCopy }) {
  const [dev, setDev] = useState('desktop')
  const [k, setK] = useState(0)
  const [busy, setBusy] = useState(false)
  const pv = job?.preview, st = pv?.status || 'idle'
  const done = job && DONE.includes(job.status)
  const act = async (fn) => { setBusy(true); try { await fn(id) } catch { /* shown via preview.error */ } await refresh(); setBusy(false) }
  const width = DEVICES.find((d) => d[0] === dev)[3]
  const framed = dev !== 'desktop'

  const center = (icon, title, sub, action) => (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
      {icon}<p className="font-display text-base font-semibold">{title}</p>{sub && <p className="max-w-xs text-sm text-mist">{sub}</p>}{action}
    </div>
  )

  if (view === 'code') {
    return <div className="min-h-0 flex-1 border-t border-seam bg-steel">{job?.result?.project_root ? <CodeView id={id} reload={reload} onCopy={onCopy} /> : center(<Icon n="folder" className="h-8 w-8 text-mist" />, 'No files yet', 'Files appear here once the build finishes.')}</div>
  }

  let body
  if (!id) body = center(<Icon n="eye" className="h-8 w-8 text-mist" />, 'No preview yet', 'Describe your idea in Agent Chat to start a build.')
  else if (!job || ['queued', 'running'].includes(job.status)) body = center(<Spinner className="h-6 w-6" />, 'Building your website', 'The live preview appears here as soon as the build finishes.')
  else if (!done) body = center(<Icon n="alert" className="h-8 w-8 text-bad" />, 'Build stopped before the preview', 'Resume the build from the Flow tab.')
  else if (st === 'ready') body = (
    <div className={`flex h-full justify-center overflow-auto ${framed ? 'bg-ink p-3' : ''}`}>
      <iframe key={`${reload}-${k}`} src={pv.url} title="Website preview" style={{ width }}
        className={`h-full max-w-full bg-white transition-[width] duration-300 ${framed ? 'rounded-xl border border-seam' : ''}`} />
    </div>
  )
  else if (['installing', 'starting'].includes(st)) body = center(<Spinner className="h-6 w-6" />, st === 'installing' ? 'Installing dependencies' : 'Starting servers', 'The first run can take a few minutes.')
  else body = center(<Icon n="eye" className="h-8 w-8 text-mist" />, st === 'failed' ? 'Preview failed' : 'Preview is not running', pv?.error?.slice(0, 200),
    <Btn variant="primary" icon="play" disabled={busy} onClick={() => act(api.startPreview)}>Start preview</Btn>)

  const ready = done && st === 'ready'
  return (
    <div className="flex min-h-0 flex-1 flex-col border-t border-seam bg-steel">
      {ready && (
        <div className="flex items-center gap-2 border-b border-seam px-3 py-2">
          <div className="flex rounded-xl bg-ink p-0.5" role="group" aria-label="Preview size">
            {DEVICES.map(([key, icon, title]) => (
              <button key={key} title={title} aria-label={title} aria-pressed={dev === key} onClick={() => setDev(key)}
                className={`rounded-lg p-1.5 transition ${dev === key ? 'bg-brand/25 text-fog' : 'text-mist hover:text-fog'}`}><Icon n={icon} /></button>
            ))}
          </div>
          <div className="flex min-w-0 flex-1 items-center gap-2 rounded-xl bg-ink px-3 py-1.5 font-mono text-xs text-mist">
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-brand-light" /><span className="truncate">{pv.url.startsWith('data:') ? 'Demo preview' : pv.url}</span>
          </div>
          {!pv.url.startsWith('data:') && <IconBtn icon="copy" title="Copy preview URL" onClick={() => onCopy(pv.url, 'URL copied')} />}
          <IconBtn icon="refresh" title="Reload preview" onClick={() => setK(k + 1)} />
          <IconLink icon="ext" title="Open in new tab" href={pv.url} target="_blank" rel="noreferrer" />
          <IconBtn icon="stop" title="Stop preview" disabled={busy} onClick={() => act(api.stopPreview)} />
          <IconLink icon="download" title="Download project (zip)" href={api.downloadUrl(id)} />
        </div>
      )}
      <div className="min-h-0 flex-1">{body}</div>
    </div>
  )
}
