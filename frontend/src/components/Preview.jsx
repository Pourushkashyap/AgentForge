import { useEffect, useState } from 'react'
import { api, DONE } from '../api'
import { Btn, Icon, Spinner } from './ui'

function CodeView({ id, reload }) {
  const [files, setFiles] = useState(null)
  const [sel, setSel] = useState(null)
  const [src, setSrc] = useState('')
  useEffect(() => { api.files(id).then(setFiles).catch(() => setFiles([])) }, [id, reload])
  useEffect(() => { if (sel) { setSrc('Loading…'); api.file(id, sel).then(setSrc).catch((e) => setSrc(e.message)) } }, [id, sel, reload])
  if (!files) return <div className="p-6"><Spinner /></div>
  if (!files.length) return <p className="p-6 text-sm text-zinc-500">No files yet.</p>
  return (
    <div className="flex h-full min-h-0">
      <ul className="w-56 shrink-0 overflow-y-auto border-r border-zinc-800 p-2 text-xs">
        {files.map((f) => (
          <li key={f}><button onClick={() => setSel(f)} className={`w-full truncate rounded px-2 py-1 text-left font-mono ${sel === f ? 'bg-zinc-800 text-white' : 'text-zinc-400 hover:bg-zinc-900'}`}>{f}</button></li>
        ))}
      </ul>
      <pre className="min-w-0 flex-1 overflow-auto p-4 text-xs text-zinc-300">{sel ? src : 'Select a file to view it.'}</pre>
    </div>
  )
}

export default function Preview({ id, job, reload, refresh, onClose }) {
  const [tab, setTab] = useState('preview')
  const [k, setK] = useState(0)
  const [busy, setBusy] = useState(false)
  const pv = job?.preview, st = pv?.status || 'idle'
  const done = job && DONE.includes(job.status)
  const act = async (fn) => { setBusy(true); try { await fn(id) } catch { /* shown via preview.error */ } await refresh(); setBusy(false) }

  const center = (icon, title, sub, action) => (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
      {icon}<p className="text-sm font-medium text-zinc-200">{title}</p>{sub && <p className="max-w-xs text-xs text-zinc-500">{sub}</p>}{action}
    </div>
  )
  let body
  if (!job || ['queued', 'running'].includes(job.status)) body = center(<Spinner className="h-6 w-6" />, 'Building your website…', 'The live preview appears here as soon as the build finishes.')
  else if (!done) body = center(null, 'Build stopped before the preview', 'Resume the build from the left panel.')
  else if (st === 'ready') body = <iframe key={`${reload}-${k}`} src={pv.url} title="preview" className="h-full w-full bg-white" />
  else if (['installing', 'starting'].includes(st)) body = center(<Spinner className="h-6 w-6" />, st === 'installing' ? 'Installing dependencies…' : 'Starting servers…', 'First run can take a few minutes.')
  else body = center(<Icon n="eye" className="h-8 w-8 text-zinc-600" />, st === 'failed' ? 'Preview failed' : 'Preview is not running', pv?.error?.slice(0, 200), <Btn variant="primary" icon="play" disabled={busy} onClick={() => act(api.startPreview)}>Start preview</Btn>)

  const tabBtn = (key, icon, label) => (
    <button onClick={() => setTab(key)} className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 text-sm ${tab === key ? 'bg-zinc-800 text-white' : 'text-zinc-400 hover:text-zinc-200'}`}><Icon n={icon} />{label}</button>
  )
  return (
    <section className="flex w-full min-w-0 flex-col border-l border-zinc-800 bg-zinc-900/40 lg:w-[55%] lg:shrink-0">
      <div className="flex items-center gap-1 border-b border-zinc-800 px-3 py-2">
        {tabBtn('preview', 'eye', 'Preview')}{tabBtn('code', 'code', 'Code')}
        <div className="ml-auto flex items-center gap-1">
          {st === 'ready' && tab === 'preview' && <>
            <Btn title="Reload" onClick={() => setK(k + 1)}><Icon n="refresh" /></Btn>
            <a title="Open in new tab" href={pv.url} target="_blank" rel="noreferrer" className="rounded-lg px-3 py-1.5 text-zinc-300 hover:bg-zinc-800"><Icon n="ext" /></a>
            <Btn title="Stop preview" disabled={busy} onClick={() => act(api.stopPreview)}><Icon n="stop" /></Btn>
          </>}
          {done && <a title="Download project (zip)" href={api.downloadUrl(id)} className="rounded-lg px-3 py-1.5 text-zinc-300 hover:bg-zinc-800"><Icon n="download" /></a>}
          <Btn title="Close panel" onClick={onClose}><Icon n="x" /></Btn>
        </div>
      </div>
      <div className="min-h-0 flex-1">
        {tab === 'preview' ? body : job?.result?.project_root ? <CodeView id={id} reload={reload} /> : center(null, 'No files yet')}
      </div>
    </section>
  )
}
