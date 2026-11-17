// Shared UI primitives for AgentForge: icons, buttons, status pills, toast, helpers.
import { useEffect, useState } from 'react'

const PATHS = {
  sidebar: 'M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM9 3v18',
  plus: 'M12 5v14M5 12h14',
  minus: 'M5 12h14',
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
  search: 'M21 21l-4.3-4.3M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14z',
  copy: 'M10 8h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H10a2 2 0 0 1-2-2V10a2 2 0 0 1 2-2zM4 16a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2',
  monitor: 'M4 3h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM8 21h8M12 17v4',
  tablet: 'M6 2h12a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zM12 18h.01',
  phone: 'M8 2h8a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zM12 18h.01',
  folder: 'M3 6a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6z',
  file: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6',
  shuffle: 'M16 3h5v5M4 20 21 3M21 16v5h-5M15 15l6 6M4 4l5 5',
  chevron: 'M6 9l6 6 6-6',
  alert: 'M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z',
  terminal: 'M4 17l6-6-6-6M12 19h8',
  clock: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 6v6l4 2',
  layers: 'M12 2 2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5',
  bug: 'M12 20a6 6 0 0 1-6-6v-3a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v3a6 6 0 0 1-6 6zM12 20v-9M8 2l1.9 1.9M16 2l-1.9 1.9M6 13H2M22 13h-4',
  globe: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM2 12h20M12 2a15 15 0 0 1 0 20 15 15 0 0 1 0-20z',
  shield: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',
  bolt: 'M13 2 3 14h9l-1 8 10-12h-9l1-8z',
  sliders: 'M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6',
  maximize: 'M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3',
  database: 'M12 8c4.4 0 8-1.3 8-3s-3.6-3-8-3-8 1.3-8 3 3.6 3 8 3zM4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
  info: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 16v-4M12 8h.01',
  user: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  chat: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z',
  left: 'M15 18l-6-6 6-6',
  right: 'M9 18l6-6-6-6',
  flow: 'M6 3a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM18 15a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM9 6h3a3 3 0 0 1 3 3v6',
  pencil: 'M17 3a2.8 2.8 0 0 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z',
}

export const Icon = ({ n, className = 'h-4 w-4' }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={PATHS[n]} />
  </svg>
)

// Brand mark: an "A" with a spark.
export const Logo = ({ className = 'h-8 w-8' }) => (
  <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
    <defs><linearGradient id="afg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#A78BFA" /><stop offset="1" stopColor="#6D28D9" /></linearGradient></defs>
    <rect width="32" height="32" rx="10" fill="url(#afg)" />
    <path d="M9 23 16 8l7 15M11.5 18.5h9" fill="none" stroke="#FFFFFF" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
    <circle cx="24" cy="8" r="2" fill="#FFFFFF" />
  </svg>
)

export const BotAvatar = ({ className = 'h-7 w-7' }) => <Logo className={`shrink-0 ${className}`} />
export const UserAvatar = ({ className = 'h-7 w-7' }) => (
  <span className={`flex shrink-0 items-center justify-center rounded-full bg-gradient-to-b from-brand/50 to-brand-deep/60 text-fog ${className}`}><Icon n="user" className="h-4 w-4" /></span>
)

export const Spinner = ({ className = 'h-4 w-4', tone = 'brand' }) => (
  <span className={`inline-block animate-spin rounded-full border-2 ${tone === 'dark' ? 'border-white/30 border-t-white' : 'border-seam border-t-brand-light'} ${className}`} role="progressbar" aria-label="Loading" />
)

const VARIANT = {
  primary: 'bg-gradient-to-b from-brand to-brand-deep font-semibold text-white shadow-[0_0_16px_rgba(139,92,246,0.35)] hover:brightness-110',
  white: 'bg-white font-semibold text-[#1B1033] hover:bg-brand-light',
  soft: 'bg-brand/15 text-brand-light hover:bg-brand/25',
  ghost: 'text-mist hover:bg-plate hover:text-fog',
  outline: 'border border-seam text-fog hover:border-brand/60 hover:bg-plate',
  danger: 'bg-bad/15 text-bad hover:bg-bad/25',
}
const SIZE = { sm: 'px-2.5 py-1 text-xs', md: 'px-3.5 py-2 text-sm' }
export const btnClass = (variant = 'ghost', size = 'md', extra = '') =>
  `inline-flex items-center justify-center gap-1.5 whitespace-nowrap ${/rounded-/.test(extra) ? '' : 'rounded-xl'} font-medium transition disabled:cursor-not-allowed disabled:opacity-40 ${VARIANT[variant]} ${SIZE[size]} ${extra}`

export const Btn = ({ icon, children, className = '', variant = 'ghost', size = 'md', type = 'button', ...p }) => (
  <button type={type} {...p} className={btnClass(variant, size, className)}>
    {icon && <Icon n={icon} />}{children}
  </button>
)

// Same look as Btn, but renders a link (downloads, open in new tab).
export const LinkBtn = ({ icon, children, className = '', variant = 'outline', size = 'md', ...p }) => (
  <a {...p} className={btnClass(variant, size, className)}>
    {icon && <Icon n={icon} />}{children}
  </a>
)

export const IconBtn = ({ icon, title, active = false, className = '', type = 'button', ...p }) => (
  <button type={type} title={title} aria-label={title} {...p}
    className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-xl transition disabled:cursor-not-allowed disabled:opacity-40 ${active ? 'bg-plate text-fog' : 'text-mist hover:bg-plate hover:text-fog'} ${className}`}>
    <Icon n={icon} />
  </button>
)

export const IconLink = ({ icon, title, className = '', ...p }) => (
  <a title={title} aria-label={title} {...p}
    className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-xl text-mist transition hover:bg-plate hover:text-fog ${className}`}>
    <Icon n={icon} />
  </a>
)

export const Switch = ({ checked, onChange, label }) => (
  <button type="button" role="switch" aria-checked={checked} aria-label={label} onClick={() => onChange(!checked)}
    className={`relative h-5 w-9 shrink-0 rounded-full transition ${checked ? 'bg-brand' : 'bg-seam'}`}>
    <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${checked ? 'left-[18px]' : 'left-0.5'}`} />
  </button>
)

export const DOT = {
  completed: 'bg-ok',
  completed_with_warnings: 'bg-warn',
  running: 'bg-brand animate-pulse',
  queued: 'bg-brand animate-pulse',
  failed: 'bg-bad',
}

export const STATUS = {
  queued: { label: 'Queued', tone: 'arc' },
  running: { label: 'Building', tone: 'arc' },
  completed: { label: 'Ready', tone: 'ok' },
  completed_with_warnings: { label: 'Ready with warnings', tone: 'warn' },
  failed: { label: 'Failed', tone: 'bad' },
}
const TONE = {
  arc: 'border-arc/30 bg-arc/10 text-arc',
  ok: 'border-ok/30 bg-ok/10 text-ok',
  warn: 'border-warn/30 bg-warn/10 text-warn',
  bad: 'border-bad/30 bg-bad/10 text-bad',
  mute: 'border-seam bg-plate text-mist',
}
export const StatusPill = ({ status }) => {
  const s = STATUS[status] || { label: (status || 'Unknown').replace(/_/g, ' '), tone: 'mute' }
  return (
    <span className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${TONE[s.tone]}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${DOT[status] || 'bg-mist'}`} />{s.label}
    </span>
  )
}

export const Toast = ({ msg }) => msg ? (
  <div role="status" className="pointer-events-none fixed bottom-5 left-1/2 z-50 animate-rise rounded-lg border border-brand/50 bg-plate px-4 py-2 text-sm text-fog shadow-glow">{msg}</div>
) : null

export const DEFAULT_OPTS = { research: false, seed_data: true, validate_browser: true, max_debug_attempts: 2 }

export const projectTitle = (p) => p.result?.business_name || p.requirements_preview || 'Untitled'
export const ago = (sec) => {
  const s = Math.max(0, Date.now() / 1000 - sec)
  return s < 60 ? 'just now' : s < 3600 ? `${Math.floor(s / 60)}m ago` : s < 86400 ? `${Math.floor(s / 3600)}h ago` : `${Math.floor(s / 86400)}d ago`
}
export const fmtTime = (sec) => {
  const s = Math.max(0, Math.floor(sec))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true } catch {
    try {
      const t = document.createElement('textarea')
      t.value = text; t.style.position = 'fixed'; t.style.opacity = '0'
      document.body.appendChild(t); t.select()
      const ok = document.execCommand('copy'); t.remove(); return ok
    } catch { return false }
  }
}

export const progressOf = (job) => {
  const steps = job?.steps || []
  const done = steps.filter((x) => x.status === 'done').length
  const active = steps.some((x) => x.status === 'active')
  return Math.min(100, Math.round(((done + (active ? 0.5 : 0)) / (steps.length || 1)) * 100))
}

export function useNow(active) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [active])
  return now
}
