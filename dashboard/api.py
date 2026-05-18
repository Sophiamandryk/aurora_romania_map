"""
Aurora Dashboard API — FastAPI backend.
Reads directly from SQLite, always returns latest snapshot data.
Run: uvicorn dashboard.api:app --reload --port 8000
"""
import json
import math
import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

_ROOT = Path(__file__).parent
DB_PATH = _ROOT.parent / "data" / "aurora.db"
DIST_DIR = _ROOT / "web" / "dist"

app = FastAPI(title="Aurora Dashboard API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _haversine(lat1, lng1, lat2, lng2) -> float:
    """Distance in km between two lat/lng points."""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Stores ────────────────────────────────────────────────────────────────────

@app.get("/api/stores/aurora")
def aurora_stores():
    conn = _db()
    rows = conn.execute("""
        SELECT store_id, name, city, address, latitude, longitude,
               region, county, status, first_seen_date
        FROM stores
        WHERE status = 'active'
          AND snapshot_date = (SELECT MAX(snapshot_date) FROM stores)
        ORDER BY city
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows if r["latitude"] and r["longitude"]]


@app.get("/api/stores/competitors")
def competitor_stores(brand: str = Query(None)):
    conn = _db()
    q = """
        SELECT brand, name, city, address, latitude, longitude
        FROM competitor_stores
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
    """
    params: list = []
    if brand:
        q += " AND brand = ?"
        params.append(brand)
    q += " ORDER BY brand, city"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    result: dict[str, list] = {}
    for r in rows:
        d = dict(r)
        b = d.pop("brand")
        result.setdefault(b, []).append(d)
    return result


@app.get("/api/stores/all")
def all_stores_flat():
    """Flat list of all stores (Aurora + competitors) for heatmap."""
    conn = _db()
    aurora = [
        {"brand": "Aurora", "lat": r["latitude"], "lng": r["longitude"],
         "city": r["city"], "intensity": 1.0}
        for r in conn.execute(
            "SELECT latitude, longitude, city FROM stores "
            "WHERE status='active' AND snapshot_date=(SELECT MAX(snapshot_date) FROM stores)"
        ).fetchall()
        if r["latitude"] and r["longitude"]
    ]
    comps = [
        {"brand": r["brand"], "lat": r["latitude"], "lng": r["longitude"],
         "city": r["city"], "intensity": 0.6}
        for r in conn.execute(
            "SELECT brand, latitude, longitude, city FROM competitor_stores "
            "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
        ).fetchall()
    ]
    conn.close()
    return aurora + comps


# ── Whitespace ────────────────────────────────────────────────────────────────

@app.get("/api/whitespace")
def whitespace_cities(min_brands: int = Query(1), limit: int = Query(200)):
    conn = _db()
    rows = conn.execute(f"""
        SELECT
            cs.city,
            COUNT(DISTINCT cs.brand) AS brand_count,
            GROUP_CONCAT(DISTINCT cs.brand) AS brands,
            COUNT(*) AS total_stores,
            AVG(cs.latitude) AS lat,
            AVG(cs.longitude) AS lng
        FROM competitor_stores cs
        WHERE cs.city NOT IN (
            SELECT DISTINCT city FROM stores
            WHERE status = 'active'
              AND snapshot_date = (SELECT MAX(snapshot_date) FROM stores)
        )
          AND cs.latitude IS NOT NULL
        GROUP BY cs.city
        HAVING brand_count >= {min_brands}
        ORDER BY brand_count DESC, total_stores DESC
        LIMIT {limit}
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Future openings ───────────────────────────────────────────────────────────

@app.get("/api/future-openings")
def future_openings():
    conn = _db()
    rows = conn.execute("""
        SELECT city, change_type, confidence_score, confidence_level,
               details_json, detected_date
        FROM changes
        WHERE change_type IN ('POSSIBLE_FUTURE_OPENING', 'MARKET_ACTIVITY_SIGNAL')
          AND detected_date >= date('now', '-14 days')
        ORDER BY confidence_score DESC
    """).fetchall()
    # Attach coordinates from competitor_stores (best match city)
    result = []
    for r in rows:
        d = dict(r)
        if d.get("details_json"):
            try:
                d["details"] = json.loads(d["details_json"])
            except Exception:
                d["details"] = {}
        # Try to get city coords from competitor_stores
        city = d.get("city","")
        coords = conn.execute(
            "SELECT AVG(latitude) as lat, AVG(longitude) as lng "
            "FROM competitor_stores WHERE lower(city) = lower(?)", (city,)
        ).fetchone()
        if coords and coords["lat"]:
            d["lat"] = coords["lat"]
            d["lng"] = coords["lng"]
        # Also check Aurora stores
        elif not coords or not coords["lat"]:
            ac = conn.execute(
                "SELECT latitude as lat, longitude as lng FROM stores "
                "WHERE lower(city) = lower(?) AND status='active' LIMIT 1", (city,)
            ).fetchone()
            if ac and ac["lat"]:
                d["lat"] = ac["lat"]
                d["lng"] = ac["lng"]
        result.append(d)
    conn.close()
    return result


# ── Retail infrastructure ─────────────────────────────────────────────────────

@app.get("/api/retail-parks")
def retail_parks():
    conn = _db()
    rows = conn.execute(
        "SELECT name, city, county, region, status, developer, opening_date FROM retail_parks ORDER BY city"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/shopping-centers")
def shopping_centers():
    conn = _db()
    rows = conn.execute(
        "SELECT name, city, county, region, status, developer, gla_sqm, opening_date FROM shopping_centers ORDER BY city"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Stats ─────────────────────────────────────────────────────────────────────

@app.get("/api/stats")
def stats():
    conn = _db()
    aurora_n = conn.execute(
        "SELECT COUNT(*) FROM stores WHERE status='active' AND snapshot_date=(SELECT MAX(snapshot_date) FROM stores)"
    ).fetchone()[0]
    aurora_cities = conn.execute(
        "SELECT COUNT(DISTINCT city) FROM stores WHERE status='active' AND snapshot_date=(SELECT MAX(snapshot_date) FROM stores)"
    ).fetchone()[0]

    comp_brands: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT brand, COUNT(*) as n, COUNT(DISTINCT city) as cities FROM competitor_stores GROUP BY brand"
    ):
        comp_brands[r[0]] = {"stores": r[1], "cities": r[2]}

    whitespace_n = conn.execute("""
        SELECT COUNT(DISTINCT city) FROM competitor_stores
        WHERE city NOT IN (
            SELECT DISTINCT city FROM stores
            WHERE status='active' AND snapshot_date=(SELECT MAX(snapshot_date) FROM stores)
        )
    """).fetchone()[0]

    overlap = conn.execute("""
        SELECT COUNT(DISTINCT lower(s.city)) FROM stores s
        JOIN competitor_stores cs ON lower(s.city) = lower(cs.city)
        WHERE s.status='active' AND s.snapshot_date=(SELECT MAX(snapshot_date) FROM stores)
    """).fetchone()[0]

    latest = conn.execute("SELECT MAX(snapshot_date) FROM stores").fetchone()[0]
    conn.close()

    return {
        "aurora_stores": aurora_n,
        "aurora_cities": aurora_cities,
        "competitor_brands": comp_brands,
        "whitespace_cities": whitespace_n,
        "overlap_cities": overlap,
        "last_updated": latest,
    }


# ── Serve built frontend ───────────────────────────────────────────────────────

if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets")), name="assets")

    @app.get("/", include_in_schema=False)
    @app.get("/{path:path}", include_in_schema=False)
    def serve_spa(path: str = ""):
        index = DIST_DIR / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return {"error": "Frontend not built. Run: cd dashboard/web && npm run build"}
