const PATHS = {
  sidebar: 'M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM9 3v18',
  plus: 'M12 5v14M5 12h14',
  send: 'M22 2 11 13M22 2l-7 20-4-9-9-4 20-7z',
  play: 'M5 3l14 9-14 9V3z',
  refresh: 'M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5',
  ext: 'M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6',
  download: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3',
  stop: 'M6 6h12v12H6z',
  check: 'M20 6 9 17l-5-5',
  x: 'M18 6 6 18M6 6l12 12',
  code: 'M16 18l6-6-6-6M8 6l-6 6 6 6',
  eye: 'M2 12s4-8 10-8 10 8 10 8-4 8-10 8S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  spark: 'M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z',
}

export const Icon = ({ n, className = 'h-4 w-4' }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d={PATHS[n]} />
  </svg>
)

export const Spinner = ({ className = 'h-4 w-4' }) => (
  <span className={`inline-block animate-spin rounded-full border-2 border-zinc-600 border-t-violet-400 ${className}`} />
)

export const Btn = ({ icon, children, className = '', variant = 'ghost', ...p }) => {
  const styles = {
    primary: 'bg-violet-600 text-white hover:bg-violet-500',
    ghost: 'text-zinc-300 hover:bg-zinc-800',
    outline: 'border border-zinc-700 text-zinc-200 hover:bg-zinc-800',
  }
  return (
    <button {...p} className={`inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-40 ${styles[variant]} ${className}`}>
      {icon && <Icon n={icon} />}{children}
    </button>
  )
}

export const DOT = { completed: 'bg-emerald-400', completed_with_warnings: 'bg-amber-400', running: 'bg-sky-400 animate-pulse', queued: 'bg-sky-400 animate-pulse', failed: 'bg-rose-500' }
export const projectTitle = (p) => p.result?.business_name || p.requirements_preview || 'Untitled'
export const ago = (sec) => {
  const s = Math.max(0, Date.now() / 1000 - sec)
  return s < 60 ? 'just now' : s < 3600 ? `${Math.floor(s / 60)}m ago` : s < 86400 ? `${Math.floor(s / 3600)}h ago` : `${Math.floor(s / 86400)}d ago`
}
