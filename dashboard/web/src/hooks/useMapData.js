import { useCallback, useEffect, useState } from 'react'

async function fetchJSON(url) {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${r.status} ${url}`)
  return r.json()
}

export function useMapData() {
  const [data, setData] = useState({
    aurora:         null,
    competitors:    null,
    whitespace:     null,
    futureOpenings: null,
    allFlat:        null,
    stats:          null,
  })
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState(null)
  const [lastRefresh, setLastRefresh] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const base = import.meta.env.BASE_URL
      const [aurora, competitors, whitespace, futureOpenings, allFlat, stats] =
        await Promise.all([
          fetchJSON(`${base}data/stores-aurora.json`),
          fetchJSON(`${base}data/stores-competitors.json`),
          fetchJSON(`${base}data/whitespace.json`),
          fetchJSON(`${base}data/future-openings.json`),
          fetchJSON(`${base}data/stores-all.json`),
          fetchJSON(`${base}data/stats.json`),
        ])
      setData({ aurora, competitors, whitespace, futureOpenings, allFlat, stats })
      setLastRefresh(new Date())
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  return { data, loading, error, lastRefresh, reload: load }
}
