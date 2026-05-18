import { BRANDS, REGIONS } from '../config'

export default function Sidebar({ layers, overlays, filters, onLayer, onOverlay, onFilter, data }) {
  const wsCities = data?.whitespace?.length ?? 0
  const auroraStores = data?.aurora?.length ?? 0

  return (
    <div style={styles.sidebar}>
      <Section title="🏪 Бренди">
        {Object.values(BRANDS).map(b => (
          <Toggle
            key={b.key}
            label={b.name}
            checked={layers[b.key] ?? true}
            color={b.fill}
            count={b.key === 'aurora'
              ? auroraStores
              : (data?.competitors?.[capBrand(b.key)]?.length ?? 0)}
            onChange={v => onLayer(b.key, v)}
          />
        ))}
      </Section>

      <Section title="🔍 Аналітичні шари">
        <Toggle
          label="White-space міста"
          checked={overlays.whitespace}
          color="#FFC107"
          count={wsCities}
          onChange={v => onOverlay('whitespace', v)}
        />
        <Toggle
          label="Можливі відкриття Aurora"
          checked={overlays.futureOpenings}
          color="#E91E63"
          count={data?.futureOpenings?.length ?? 0}
          onChange={v => onOverlay('futureOpenings', v)}
        />
        <Toggle
          label="Heatmap конкурентів"
          checked={overlays.heatmap}
          color="#FF5722"
          onChange={v => onOverlay('heatmap', v)}
        />
        <Toggle
          label="Кола proximity (Aurora)"
          checked={overlays.proximity}
          color="#9C27B0"
          onChange={v => onOverlay('proximity', v)}
        />
      </Section>

      <Section title="🎛️ Фільтри">
        <FilterSelect
          label="Регіон"
          value={filters.region ?? ''}
          options={[['', 'Всі регіони'], ...REGIONS.map(r => [r, r])]}
          onChange={v => onFilter('region', v || null)}
        />
        <FilterRange
          label={`Мін. брендів у місті (white-space): ${filters.minBrands ?? 1}`}
          min={1} max={3}
          value={filters.minBrands ?? 1}
          onChange={v => onFilter('minBrands', +v)}
        />
        <FilterRange
          label={`Мін. оцінка впевненості: ${filters.minConfidence ?? 0}`}
          min={0} max={1} step={0.05}
          value={filters.minConfidence ?? 0}
          onChange={v => onFilter('minConfidence', +v)}
        />
      </Section>

      <Section title="📊 Легенда">
        <Legend />
      </Section>
    </div>
  )
}

function capBrand(key) {
  return { aurora: 'Aurora', pepco: 'Pepco', tedi: 'TEDi', kik: 'KiK', action: 'Action' }[key] ?? key
}

function Section({ title, children }) {
  return (
    <div style={styles.section}>
      <div style={styles.sectionTitle}>{title}</div>
      {children}
    </div>
  )
}

function Toggle({ label, checked, color, count, onChange }) {
  return (
    <label style={styles.toggle}>
      <input
        type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)}
        style={{ accentColor: color, marginRight: 6 }}
      />
      <span style={{ ...styles.dot, background: color }} />
      <span style={styles.toggleLabel}>{label}</span>
      {count !== undefined && <span style={styles.count}>{count}</span>}
    </label>
  )
}

function FilterSelect({ label, value, options, onChange }) {
  return (
    <div style={styles.filter}>
      <div style={styles.filterLabel}>{label}</div>
      <select value={value} onChange={e => onChange(e.target.value)} style={styles.select}>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </div>
  )
}

function FilterRange({ label, min, max, step = 1, value, onChange }) {
  return (
    <div style={styles.filter}>
      <div style={styles.filterLabel}>{label}</div>
      <input
        type="range" min={min} max={max} step={step} value={value}
        onChange={e => onChange(e.target.value)}
        style={{ width: '100%' }}
      />
    </div>
  )
}

function Legend() {
  const entries = [
    { color: '#4CAF50', label: 'Aurora (підтверджений)' },
    { color: '#EF5350', label: 'Pepco' },
    { color: '#FF9800', label: 'TEDi' },
    { color: '#42A5F5', label: 'KiK' },
    { color: '#AB47BC', label: 'Action' },
    { color: '#FFC107', label: 'White-space (немає Aurora)', shape: 'diamond' },
    { color: '#E91E63', label: 'Можливе відкриття Aurora', shape: 'star' },
  ]
  return (
    <div>
      {entries.map(e => (
        <div key={e.label} style={styles.legendRow}>
          <span style={e.shape === 'diamond'
            ? { ...styles.legendDot, background: e.color, transform: 'rotate(45deg)' }
            : e.shape === 'star'
            ? { ...styles.legendDot, background: e.color, borderRadius: 2 }
            : { ...styles.legendDot, background: e.color }
          } />
          <span style={styles.legendLabel}>{e.label}</span>
        </div>
      ))}
    </div>
  )
}

const styles = {
  sidebar: {
    width: 240, flexShrink: 0,
    background: '#1a1a2e', color: '#e0e0e0',
    overflowY: 'auto', borderRight: '1px solid #333',
    fontSize: 13,
  },
  section: {
    padding: '12px 14px',
    borderBottom: '1px solid #2d2d4e',
  },
  sectionTitle: {
    fontSize: 11, fontWeight: 700, color: '#aaa',
    textTransform: 'uppercase', letterSpacing: 1,
    marginBottom: 8,
  },
  toggle: {
    display: 'flex', alignItems: 'center',
    marginBottom: 6, cursor: 'pointer', userSelect: 'none',
  },
  dot: { width: 8, height: 8, borderRadius: '50%', marginRight: 6, flexShrink: 0 },
  toggleLabel: { flex: 1, fontSize: 12 },
  count: {
    fontSize: 11, color: '#888',
    background: '#2d2d4e', borderRadius: 8,
    padding: '1px 6px',
  },
  filter: { marginBottom: 10 },
  filterLabel: { fontSize: 11, color: '#aaa', marginBottom: 4 },
  select: {
    width: '100%', padding: '4px 6px', fontSize: 12,
    background: '#2d2d4e', color: '#e0e0e0',
    border: '1px solid #444', borderRadius: 4,
  },
  legendRow: { display: 'flex', alignItems: 'center', marginBottom: 5 },
  legendDot: { width: 10, height: 10, borderRadius: '50%', marginRight: 8, flexShrink: 0 },
  legendLabel: { fontSize: 11, color: '#ccc' },
}
