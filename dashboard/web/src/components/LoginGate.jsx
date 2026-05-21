import { useEffect, useRef, useState } from 'react'

const CLIENT_ID = '889487330001-fvqqf163rng12f56bia0rjqs1g31oijr.apps.googleusercontent.com'

const ALLOWED = new Set([
  'a.mytrofanova@avrora.ua',
  'o.sabaniuk@avrora.ua',
  'o.sabaniuk.avrora@gmail.com',
  'mandryksofiya@gmail.com',
  'mytrofanova.alona@gmail.com',
])

function decodeJwt(token) {
  try {
    const b64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    return JSON.parse(atob(b64))
  } catch {
    return null
  }
}

export default function LoginGate({ children }) {
  const [user, setUser]     = useState(null)
  const [denied, setDenied] = useState(false)
  const btnRef              = useRef(null)

  useEffect(() => {
    const script   = document.createElement('script')
    script.src     = 'https://accounts.google.com/gsi/client'
    script.async   = true
    script.onload  = () => {
      window.google.accounts.id.initialize({
        client_id: CLIENT_ID,
        callback: ({ credential }) => {
          const payload = decodeJwt(credential)
          if (!payload) return
          const email = (payload.email || '').toLowerCase()
          if (ALLOWED.has(email)) {
            setUser({ email, name: payload.name, picture: payload.picture })
          } else {
            setDenied(true)
          }
        },
      })
      window.google.accounts.id.renderButton(btnRef.current, {
        theme: 'filled_black',
        size:  'large',
        text:  'signin_with',
        shape: 'rectangular',
        width: 280,
      })
    }
    document.head.appendChild(script)
  }, [])

  if (user) return children

  return (
    <div style={s.overlay}>
      <div style={s.card}>
        <div style={s.logo}>🗺️</div>
        <h1 style={s.title}>Aurora Romania</h1>
        <p style={s.sub}>Retail Intelligence Map</p>

        {denied ? (
          <p style={s.denied}>
            Access denied. Your Google account is not authorised.
          </p>
        ) : (
          <>
            <p style={s.hint}>Sign in with your authorised Google account to continue</p>
            <div ref={btnRef} style={s.btnWrap} />
          </>
        )}
      </div>
    </div>
  )
}

const s = {
  overlay:  { position: 'fixed', inset: 0, background: '#0d1117', display: 'flex', alignItems: 'center', justifyContent: 'center' },
  card:     { background: '#1a1a2e', border: '1px solid #2d2d4e', borderRadius: 12, padding: '40px 48px', textAlign: 'center', maxWidth: 380, width: '90%' },
  logo:     { fontSize: 48, marginBottom: 12 },
  title:    { color: '#fff', margin: '0 0 4px', fontSize: 22, fontWeight: 700, fontFamily: 'sans-serif' },
  sub:      { color: '#888', margin: '0 0 28px', fontSize: 14, fontFamily: 'sans-serif' },
  hint:     { color: '#aaa', fontSize: 13, margin: '0 0 16px', fontFamily: 'sans-serif' },
  btnWrap:  { display: 'flex', justifyContent: 'center' },
  denied:   { color: '#ef5350', fontSize: 14, margin: 0, fontFamily: 'sans-serif' },
}
