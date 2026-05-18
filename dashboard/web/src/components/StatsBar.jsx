export default function StatsBar({ stats, lastRefresh, loading, onReload }) {
  const comp = stats?.competitor_brands ?? {}
  const total = Object.values(comp).reduce((s, b) => s + b.stores, 0)

  const fmt = (n) => (n ?? '…').toLocaleString()
  const ts  = lastRefresh
    ? lastRefresh.toLocaleTimeString('uk-UA', { hour: '2-digit', minute: '2-digit' })
    : '…'

  return (
    <div style={styles.bar}>
      <div style={styles.logo}>🗺️ <strong>Aurora Romania</strong> <span style={styles.sub}>Retail Intelligence Map</span></div>

      <div style={styles.stats}>
        <Stat label="Aurora магазини" value={fmt(stats?.aurora_stores)} color="#4CAF50" />
        <Stat label="Aurora міста"    value={fmt(stats?.aurora_cities)}  color="#81C784" />
        <Stat label="Pepco"           value={fmt(comp.Pepco?.stores)}    color="#EF5350" />
        <Stat label="KiK"             value={fmt(comp.KiK?.stores)}      color="#42A5F5" />
        <Stat label="TEDi"            value={fmt(comp.TEDi?.stores)}     color="#FF9800" />
        <Stat label="Конкуренти всього" value={fmt(total)}              color="#9E9E9E" />
        <Stat label="White-space міста" value={fmt(stats?.whitespace_cities)} color="#FFC107" />
        <Stat label="Overlap міста"   value={fmt(stats?.overlap_cities)} color="#CE93D8" />
      </div>

      <div style={styles.right}>
        <span style={styles.ts}>Оновлено: {ts}</span>
        <button style={styles.btn} onClick={onReload} disabled={loading}>
          {loading ? '⟳' : '↻'} Refresh
        </button>
      </div>
    </div>
  )
}

function Stat({ label, value, color }) {
  return (
    <div style={styles.stat}>
      <span style={{ ...styles.dot, background: color }} />
      <div>
        <div style={styles.val}>{value}</div>
        <div style={styles.lbl}>{label}</div>
      </div>
    </div>
  )
}

const styles = {
  bar: {
    display: 'flex', alignItems: 'center', gap: 16,
    padding: '0 16px', height: 56,
    background: '#1a1a2e', color: '#fff',
    borderBottom: '1px solid #333', flexShrink: 0, overflow: 'hidden',
  },
  logo: { fontSize: 14, whiteSpace: 'nowrap', marginRight: 8 },
  sub:  { color: '#aaa', fontWeight: 400, marginLeft: 4 },
  stats: { display: 'flex', gap: 12, flex: 1, overflow: 'hidden' },
  stat: { display: 'flex', alignItems: 'center', gap: 6, minWidth: 80 },
  dot:  { width: 8, height: 8, borderRadius: '50%', flexShrink: 0 },
  val:  { fontSize: 15, fontWeight: 700, lineHeight: 1 },
  lbl:  { fontSize: 10, color: '#aaa', lineHeight: 1.2, marginTop: 2 },
  right: { display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 },
  ts:   { fontSize: 11, color: '#888' },
  btn:  {
    padding: '4px 10px', fontSize: 12,
    background: '#2d2d4e', color: '#fff',
    border: '1px solid #555', borderRadius: 4, cursor: 'pointer',
  },
}
