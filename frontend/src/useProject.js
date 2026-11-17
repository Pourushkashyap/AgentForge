import { useCallback, useEffect, useRef, useState } from 'react'
import { api, DONE } from './api'

// Polls one project: job status/steps/events/preview + (after build) the HITL chat.
export default function useProject(id) {
  const [job, setJob] = useState(null)
  const [chat, setChat] = useState([])
  const [error, setError] = useState('')
  const cur = useRef(id)
  cur.current = id

  const load = useCallback(async () => {
    if (!id) return null
    try {
      const j = await api.get(id)
      const c = DONE.includes(j.status) ? await api.chat(id) : null
      if (cur.current !== id) return null
      setJob(j)
      if (c) setChat(c)
      setError('')
      return { j, c }
    } catch (e) {
      if (cur.current === id) setError(e.message)
      return null
    }
  }, [id])

  useEffect(() => {
    setJob(null); setChat([]); setError('')
    if (!id) return
    let stop = false, timer
    const loop = async () => {
      const r = await load()
      if (stop) return
      const busy = r && (['queued', 'running'].includes(r.j.status)
        || ['installing', 'starting'].includes(r.j.preview?.status)
        || (r.c || []).some((m) => m.status === 'working'))
      timer = setTimeout(loop, !r ? 3000 : busy ? 1200 : 4000)
    }
    loop()
    return () => { stop = true; clearTimeout(timer) }
  }, [id, load])

  return { job, chat, setChat, refresh: load, error }
}
