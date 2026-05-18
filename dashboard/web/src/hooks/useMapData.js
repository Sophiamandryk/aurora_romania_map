import { useCallback, useEffect, useRef, useState } from 'react'
import { API_BASE } from '../config'

const REFRESH_MS = 5 * 60 * 1000 // 5 min auto-refresh

async function fetchJSON(url) {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${r.status} ${url}`)
  return r.json()
}

export function useMapData() {
  const [data, setData] = useState({
    aurora:         null,  // [store, ...]
    competitors:    null,  // { Pepco: [...], KiK: [...], ... }
    whitespace:     null,  // [city, ...]
    futureOpenings: null,  // [change, ...]
    allFlat:        null,  // for heatmap
    stats:          null,
  })
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState(null)
  const [lastRefresh, setLastRefresh] = useState(null)
  const timerRef = useRef(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [aurora, competitors, whitespace, futureOpenings, allFlat, stats] =
        await Promise.all([
          fetchJSON(`${API_BASE}/stores/aurora`),
          fetchJSON(`${API_BASE}/stores/competitors`),
          fetchJSON(`${API_BASE}/whitespace?min_brands=1&limit=300`),
          fetchJSON(`${API_BASE}/future-openings`),
          fetchJSON(`${API_BASE}/stores/all`),
          fetchJSON(`${API_BASE}/stats`),
        ])
      setData({ aurora, competitors, whitespace, futureOpenings, allFlat, stats })
      setLastRefresh(new Date())
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    timerRef.current = setInterval(load, REFRESH_MS)
    return () => clearInterval(timerRef.current)
  }, [load])

  return { data, loading, error, lastRefresh, reload: load }
}
