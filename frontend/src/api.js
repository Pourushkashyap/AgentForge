// Single place that talks to the AgentForge Flask backend (backend2).
// VITE_MOCK=1 (npm run demo) swaps in an in-browser fake backend.
import * as mock from './mock.js'

export const USE_MOCK = import.meta.env.VITE_MOCK === '1'
const BASE = (import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

async function req(path, { body, ...opts } = {}) {
  const res = await fetch(BASE + path, {
    ...opts,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const isJson = (res.headers.get('content-type') || '').includes('json')
  const data = isJson ? await res.json() : await res.text()
  if (!res.ok) {
    const err = new Error((isJson && data.error) || `HTTP ${res.status}`)
    err.status = res.status
    throw err
  }
  return data
}

const real = {
  health: () => req('/health'),
  list: async () => (await req('/api/projects')).projects,
  create: (requirements, options) => req('/api/projects', { method: 'POST', body: { requirements, options } }),
  get: (id) => req(`/api/projects/${id}`),
  resume: (id) => req(`/api/projects/${id}/resume`, { method: 'POST' }), // needs backend_patch
  startPreview: (id) => req(`/api/projects/${id}/preview`, { method: 'POST' }),
  stopPreview: (id) => req(`/api/projects/${id}/preview`, { method: 'DELETE' }),
  chat: async (id) => (await req(`/api/projects/${id}/chat`)).messages,
  send: (id, message) => req(`/api/projects/${id}/chat`, { method: 'POST', body: { message } }),
  files: async (id) => (await req(`/api/projects/${id}/files`)).files,
  file: (id, path) => req(`/api/projects/${id}/files/${path.split('/').map(encodeURIComponent).join('/')}`),
  downloadUrl: (id) => `${BASE}/api/projects/${id}/download`,
}

export const api = USE_MOCK ? mock.api : real

// Full idea text is not returned by the list API (only 200 chars) -> keep it locally for "restart".
export const saveReq = (id, text) => localStorage.setItem('af:req:' + id, text)
export const getReq = (id) => localStorage.getItem('af:req:' + id)
export const DONE = ['completed', 'completed_with_warnings']
