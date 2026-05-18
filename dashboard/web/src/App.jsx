import { useReducer } from 'react'
import MapView from './components/MapView'
import Sidebar from './components/Sidebar'
import StatsBar from './components/StatsBar'
import { useMapData } from './hooks/useMapData'

const initialLayers = {
  aurora: true,
  pepco:  true,
  tedi:   true,
  kik:    true,
  action: true,
}

const initialOverlays = {
  whitespace:     false,
  futureOpenings: false,
  heatmap:        false,
  proximity:      false,
}

const initialFilters = {
  region:        null,
  minBrands:     1,
  minConfidence: 0,
}

function reducer(state, action) {
  switch (action.type) {
    case 'LAYER':
      return { ...state, layers: { ...state.layers, [action.key]: action.value } }
    case 'OVERLAY':
      return { ...state, overlays: { ...state.overlays, [action.key]: action.value } }
    case 'FILTER':
      return { ...state, filters: { ...state.filters, [action.key]: action.value } }
    default:
      return state
  }
}

export default function App() {
  const [state, dispatch] = useReducer(reducer, {
    layers:   initialLayers,
    overlays: initialOverlays,
    filters:  initialFilters,
  })

  const { data, loading, error, lastRefresh, reload } = useMapData()

  return (
    <div style={styles.root}>
      <StatsBar
        stats={data.stats}
        lastRefresh={lastRefresh}
        loading={loading}
        onReload={reload}
      />

      {error && (
        <div style={styles.error}>
          ⚠️ API error: {error} — is the backend running?{' '}
          <code>uvicorn dashboard.api:app --reload --port 8000</code>
        </div>
      )}

      <div style={styles.body}>
        <Sidebar
          layers={state.layers}
          overlays={state.overlays}
          filters={state.filters}
          data={data}
          onLayer={(key, v) => dispatch({ type: 'LAYER', key, value: v })}
          onOverlay={(key, v) => dispatch({ type: 'OVERLAY', key, value: v })}
          onFilter={(key, v) => dispatch({ type: 'FILTER', key, value: v })}
        />
        <MapView
          layers={state.layers}
          overlays={state.overlays}
          filters={state.filters}
          data={data}
        />
      </div>

      {loading && !data.aurora && <Spinner />}
    </div>
  )
}

function Spinner() {
  return (
    <div style={styles.spinner}>
      <div style={styles.spinnerInner}>⟳ Завантаження даних…</div>
    </div>
  )
}

const styles = {
  root: {
    display: 'flex', flexDirection: 'column',
    width: '100vw', height: '100vh',
    background: '#111', overflow: 'hidden',
  },
  body: {
    display: 'flex', flex: 1, overflow: 'hidden',
  },
  error: {
    background: '#4a0000', color: '#ffcdd2',
    padding: '8px 16px', fontSize: 13,
    borderBottom: '1px solid #b71c1c',
  },
  spinner: {
    position: 'absolute', inset: 0,
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    background: 'rgba(0,0,0,.7)', zIndex: 9999,
  },
  spinnerInner: {
    background: '#1a1a2e', color: '#fff',
    padding: '16px 28px', borderRadius: 8,
    fontSize: 16,
  },
}
