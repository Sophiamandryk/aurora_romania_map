import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import 'leaflet.markercluster'
import 'leaflet.markercluster/dist/MarkerCluster.css'
import 'leaflet.markercluster/dist/MarkerCluster.Default.css'
import { useEffect, useRef } from 'react'
import { BRANDS, ROMANIA_CENTER, ROMANIA_ZOOM } from '../config'

// ── Icon factories ────────────────────────────────────────────────────────────

function circleIcon(fill, border, size = 12) {
  return L.divIcon({
    className: '',
    html: `<div style="
      width:${size}px;height:${size}px;
      background:${fill};
      border:2px solid ${border};
      border-radius:50%;
      box-shadow:0 1px 4px rgba(0,0,0,.5);
    "></div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
    popupAnchor: [0, -size / 2 - 2],
  })
}

function diamondIcon(fill, border, size = 14) {
  return L.divIcon({
    className: '',
    html: `<div style="
      width:${size}px;height:${size}px;
      background:${fill};
      border:2px solid ${border};
      transform:rotate(45deg);
      box-shadow:0 1px 4px rgba(0,0,0,.5);
    "></div>`,
    iconSize: [size + 4, size + 4],
    iconAnchor: [(size + 4) / 2, (size + 4) / 2],
    popupAnchor: [0, -(size + 4) / 2 - 2],
  })
}

function pulseIcon(fill) {
  return L.divIcon({
    className: '',
    html: `<div style="position:relative;width:20px;height:20px">
      <div style="
        position:absolute;inset:0;
        background:${fill};border-radius:50%;
        animation:pulse 1.5s ease-in-out infinite;opacity:.6;
      "></div>
      <div style="
        position:absolute;top:4px;left:4px;
        width:12px;height:12px;
        background:${fill};border:2px solid white;border-radius:50%;
      "></div>
    </div>`,
    iconSize: [20, 20],
    iconAnchor: [10, 10],
    popupAnchor: [0, -12],
  })
}

// ── Nearest competitor helper ─────────────────────────────────────────────────

function haversine(lat1, lng1, lat2, lng2) {
  const R = 6371
  const d = (a, b) => (b - a) * Math.PI / 180
  const a = Math.sin(d(lat1, lat2) / 2) ** 2 +
    Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
    Math.sin(d(lng1, lng2) / 2) ** 2
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
}

function nearestCompetitor(store, competitors) {
  let best = null, bestDist = Infinity
  for (const [brand, stores] of Object.entries(competitors || {})) {
    for (const c of stores) {
      if (!c.latitude || !c.longitude) continue
      const d = haversine(store.latitude, store.longitude, c.latitude, c.longitude)
      if (d < bestDist) { bestDist = d; best = { brand, city: c.city, dist: d } }
    }
  }
  return best ? `${best.brand} — ${best.city}: ${best.dist.toFixed(2)} km` : null
}

// ── Popup builders ────────────────────────────────────────────────────────────

function auroraPopup(s, competitors) {
  const nearest = nearestCompetitor(s, competitors)
  return `
    <div style="font-family:sans-serif;font-size:12px;min-width:180px">
      <div style="font-weight:700;color:#2E7D32;margin-bottom:4px">🟢 Aurora Multimarket</div>
      <div><strong>${s.city}</strong></div>
      ${s.address ? `<div style="color:#555;margin-top:2px">${s.address}</div>` : ''}
      ${s.region   ? `<div style="margin-top:4px">📍 ${s.region}${s.county ? ', ' + s.county : ''}</div>` : ''}
      ${s.first_seen_date ? `<div style="color:#888;font-size:11px">Перший раз: ${s.first_seen_date}</div>` : ''}
      ${nearest ? `<div style="margin-top:6px;padding-top:6px;border-top:1px solid #eee;color:#c62828">Найближчий конкурент:<br>${nearest}</div>` : ''}
      ${s.store_id ? `<div style="color:#aaa;font-size:10px;margin-top:4px">ID: ${s.store_id}</div>` : ''}
    </div>`
}

function competitorPopup(s, brand, brandCfg) {
  return `
    <div style="font-family:sans-serif;font-size:12px;min-width:160px">
      <div style="font-weight:700;color:${brandCfg.color};margin-bottom:4px">
        ${brand}
      </div>
      <div><strong>${s.city ?? ''}</strong></div>
      ${s.address ? `<div style="color:#555;margin-top:2px">${s.address}</div>` : ''}
    </div>`
}

function whitespacePopup(ws) {
  return `
    <div style="font-family:sans-serif;font-size:12px;min-width:180px">
      <div style="font-weight:700;color:#F57F17;margin-bottom:4px">⬡ White-space</div>
      <div><strong>${ws.city}</strong></div>
      <div style="margin-top:4px">Конкуренти: <strong>${ws.brands?.replace(/,/g, ', ')}</strong></div>
      <div>Магазинів: ${ws.total_stores} | Брендів: ${ws.brand_count}</div>
      <div style="margin-top:4px;padding-top:4px;border-top:1px solid #eee;color:#1B5E20;font-weight:600">
        Aurora відсутня
      </div>
    </div>`
}

function futurePopup(fo) {
  const lvl = fo.confidence_level ?? '?'
  const score = fo.confidence_score?.toFixed(2) ?? '?'
  return `
    <div style="font-family:sans-serif;font-size:12px;min-width:180px">
      <div style="font-weight:700;color:#E91E63;margin-bottom:4px">📍 Можливе відкриття Aurora</div>
      <div><strong>${fo.city}</strong></div>
      <div style="margin-top:4px">Впевненість: <strong>${lvl}</strong> (${score})</div>
      <div style="color:#888;font-size:11px">${fo.detected_date ?? ''}</div>
    </div>`
}

// ── MapView component ─────────────────────────────────────────────────────────

export default function MapView({ layers, overlays, filters, data }) {
  const containerRef = useRef(null)
  const mapRef       = useRef(null)
  const brandLayers  = useRef({})   // key → MarkerClusterGroup
  const overlayLayers = useRef({})  // key → layer

  // ── Init map once ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (mapRef.current) return
    const map = L.map(containerRef.current, {
      center: ROMANIA_CENTER,
      zoom:   ROMANIA_ZOOM,
      zoomControl: true,
      preferCanvas: false,
    })

    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      attribution: '© OpenStreetMap © CARTO',
      subdomains: 'abcd',
      maxZoom: 19,
    }).addTo(map)

    // CSS for pulse animation
    const style = document.createElement('style')
    style.textContent = `
      @keyframes pulse {
        0%,100%{transform:scale(1);opacity:.6}
        50%{transform:scale(2);opacity:0}
      }
    `
    document.head.appendChild(style)

    mapRef.current = map
    return () => {
      map.remove()
      mapRef.current = null
    }
  }, [])

  // ── Rebuild brand + whitespace layers when data arrives ────────────────────
  useEffect(() => {
    const map = mapRef.current
    if (!map || !data.aurora) return

    // Remove old layers
    Object.values(brandLayers.current).forEach(l => map.removeLayer(l))
    Object.values(overlayLayers.current).forEach(l => map.removeLayer(l))
    brandLayers.current = {}
    overlayLayers.current = {}

    // ── Aurora ──────────────────────────────────────────────────────────────
    const auroraCluster = L.markerClusterGroup({ maxClusterRadius: 40, zIndexOffset: 1000 })
    for (const s of data.aurora ?? []) {
      if (!s.latitude || !s.longitude) continue
      if (filters.region && s.region !== filters.region) continue
      L.marker([s.latitude, s.longitude], {
        icon: circleIcon(BRANDS.aurora.fill, BRANDS.aurora.color, 14),
        title: `Aurora — ${s.city}`,
      })
        .bindPopup(auroraPopup(s, data.competitors), { maxWidth: 260 })
        .addTo(auroraCluster)
    }
    brandLayers.current.aurora = auroraCluster

    // ── Competitors ──────────────────────────────────────────────────────────
    const brandKeyMap = {
      Pepco: 'pepco', TEDi: 'tedi', KiK: 'kik', Action: 'action',
      Profi: 'profi', Penny: 'penny', MrDIY: 'mrdiy',
    }
    for (const [brand, stores] of Object.entries(data.competitors ?? {})) {
      const key = brandKeyMap[brand] ?? brand.toLowerCase()
      const cfg = BRANDS[key]
      if (!cfg) continue
      const cluster = L.markerClusterGroup({
        maxClusterRadius: 35,
        zIndexOffset: cfg.zIndex,
        iconCreateFunction: (c) => L.divIcon({
          html: `<div style="
            background:${cfg.fill};color:white;
            font-size:11px;font-weight:700;
            width:28px;height:28px;border-radius:50%;
            display:flex;align-items:center;justify-content:center;
            border:2px solid ${cfg.color};
            box-shadow:0 2px 4px rgba(0,0,0,.4);
          ">${c.getChildCount()}</div>`,
          className: '',
          iconSize: [28, 28],
          iconAnchor: [14, 14],
        }),
      })
      for (const s of stores) {
        if (!s.latitude || !s.longitude) continue
        L.marker([s.latitude, s.longitude], {
          icon: circleIcon(cfg.fill, cfg.color, 10),
          title: `${brand} — ${s.city}`,
        })
          .bindPopup(competitorPopup(s, brand, cfg), { maxWidth: 220 })
          .addTo(cluster)
      }
      brandLayers.current[key] = cluster
    }

    // ── Whitespace overlay ──────────────────────────────────────────────────
    const wsGroup = L.layerGroup()
    for (const ws of data.whitespace ?? []) {
      if (!ws.lat || !ws.lng) continue
      if ((ws.brand_count ?? 0) < (filters.minBrands ?? 1)) continue
      L.marker([ws.lat, ws.lng], {
        icon: diamondIcon('#FFC107', '#F57F17', 14),
        title: `White-space — ${ws.city}`,
      })
        .bindPopup(whitespacePopup(ws), { maxWidth: 220 })
        .addTo(wsGroup)
    }
    overlayLayers.current.whitespace = wsGroup

    // ── Future openings overlay ─────────────────────────────────────────────
    const foGroup = L.layerGroup()
    for (const fo of data.futureOpenings ?? []) {
      if (!fo.lat || !fo.lng) continue
      if ((fo.confidence_score ?? 0) < (filters.minConfidence ?? 0)) continue
      L.marker([fo.lat, fo.lng], {
        icon: pulseIcon('#E91E63'),
        title: `Можливе відкриття — ${fo.city}`,
      })
        .bindPopup(futurePopup(fo), { maxWidth: 220 })
        .addTo(foGroup)
    }
    overlayLayers.current.futureOpenings = foGroup

    // ── Heatmap overlay ─────────────────────────────────────────────────────
    const heatPoints = (data.allFlat ?? []).map(p => [p.lat, p.lng, p.intensity ?? 0.5])
    if (window.L && window.L.heatLayer && heatPoints.length) {
      overlayLayers.current.heatmap = window.L.heatLayer(heatPoints, {
        radius: 18, blur: 20, maxZoom: 12, max: 1.0,
        gradient: { 0.0: '#000080', 0.3: '#0000ff', 0.6: '#ff0000', 1.0: '#ffff00' },
      })
    }

    // ── Proximity circles (Aurora stores) ──────────────────────────────────
    const proxGroup = L.layerGroup()
    for (const s of data.aurora ?? []) {
      if (!s.latitude || !s.longitude) continue
      L.circle([s.latitude, s.longitude], {
        radius: 10000, // 10km
        color: '#4CAF50', fillColor: '#4CAF50',
        fillOpacity: 0.05, weight: 1, opacity: 0.3,
      }).addTo(proxGroup)
    }
    overlayLayers.current.proximity = proxGroup

    // Apply current visibility
    applyVisibility(map, brandLayers.current, overlayLayers.current, layers, overlays)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, filters.region, filters.minBrands, filters.minConfidence])

  // ── Toggle visibility when layers/overlays state changes ──────────────────
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    applyVisibility(map, brandLayers.current, overlayLayers.current, layers, overlays)
  }, [layers, overlays])

  return (
    <div style={{ flex: 1, position: 'relative' }}>
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
    </div>
  )
}

function applyVisibility(map, brandLayers, overlayLayers, layers, overlays) {
  // Brand layers
  for (const [key, layer] of Object.entries(brandLayers)) {
    const visible = layers[key] ?? true
    if (visible && !map.hasLayer(layer)) map.addLayer(layer)
    if (!visible && map.hasLayer(layer)) map.removeLayer(layer)
  }
  // Overlay layers
  for (const [key, layer] of Object.entries(overlayLayers)) {
    const visible = overlays[key] ?? false
    if (visible && !map.hasLayer(layer)) map.addLayer(layer)
    if (!visible && map.hasLayer(layer)) map.removeLayer(layer)
  }
}
