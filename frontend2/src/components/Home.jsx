import { useState } from 'react'
import { Btn, Icon, Spinner } from './ui'

const EXAMPLES = [
  'A booking website for a yoga studio with class schedule, gallery and contact form',
  'An online store for handmade candles with cart and order tracking',
  'A fitness coaching platform with workouts, diet plans and progress dashboard',
]

export default function Home({ onGenerate, busy, error, online }) {
  const [text, setText] = useState('')
  const [opts, setOpts] = useState({ research: false, seed_data: true, validate_browser: true, max_debug_attempts: 2 })
  const ok = text.trim().length >= 10 && !busy
  const submit = () => ok && onGenerate(text.trim(), opts)
  const toggle = (k, label) => (
    <button onClick={() => setOpts({ ...opts, [k]: !opts[k] })}
      className={`rounded-full border px-3 py-1 text-xs transition ${opts[k] ? 'border-violet-500 bg-violet-500/15 text-violet-200' : 'border-zinc-700 text-zinc-400 hover:bg-zinc-800'}`}>
      {opts[k] ? '✓ ' : ''}{label}
    </button>
  )
  return (
    <div className="flex flex-1 flex-col items-center justify-center overflow-y-auto px-4 pb-16">
      <div className="w-full max-w-2xl">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-violet-600"><Icon n="spark" className="h-6 w-6" /></div>
          <h1 className="text-3xl font-semibold">What do you want to build?</h1>
          <p className="mt-2 text-zinc-400">Describe your idea — AgentForge plans, generates, validates and previews the full website.</p>
        </div>

        <div className="rounded-2xl border border-zinc-700 bg-zinc-900 p-3 shadow-xl focus-within:border-violet-500">
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={4} autoFocus
            onKeyDown={(e) => e.key === 'Enter' && (e.ctrlKey || e.metaKey) && submit()}
            placeholder="e.g. A website for my dog grooming business with services, booking and gallery…"
            className="w-full resize-none bg-transparent p-2 text-base outline-none placeholder:text-zinc-500" />
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-zinc-800 pt-3">
            <div className="flex flex-wrap items-center gap-2">
              {toggle('research', 'Market research')}
              {toggle('seed_data', 'Sample data')}
              {toggle('validate_browser', 'Browser test')}
              <label className="flex items-center gap-1 text-xs text-zinc-400">Auto-fix
                <select value={opts.max_debug_attempts} onChange={(e) => setOpts({ ...opts, max_debug_attempts: +e.target.value })}
                  className="rounded-md border border-zinc-700 bg-zinc-900 px-1 py-0.5">
                  {[0, 1, 2, 3, 4, 5].map((n) => <option key={n}>{n}</option>)}
                </select>
              </label>
            </div>
            <Btn variant="primary" disabled={!ok} onClick={submit}>
              {busy ? <Spinner /> : <Icon n="send" />} Generate
            </Btn>
          </div>
        </div>

        {error && <p className="mt-3 rounded-lg bg-rose-500/10 p-3 text-sm text-rose-300">{error}</p>}
        {!online && <p className="mt-3 rounded-lg bg-amber-500/10 p-3 text-sm text-amber-300">Backend not reachable. Start it with <code>python app.py</code> in backend2, or run <code>npm run demo</code>.</p>}

        <div className="mt-6 grid gap-2 sm:grid-cols-3">
          {EXAMPLES.map((ex) => (
            <button key={ex} onClick={() => setText(ex)} className="rounded-xl border border-zinc-800 p-3 text-left text-xs text-zinc-400 transition hover:border-zinc-600 hover:text-zinc-200">{ex}</button>
          ))}
        </div>
        <p className="mt-4 text-center text-xs text-zinc-600">Ctrl + Enter to generate</p>
      </div>
    </div>
  )
}
