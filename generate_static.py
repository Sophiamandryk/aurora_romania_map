"""
Exports all dashboard API data from aurora.db to static JSON files.
Run from project root: python generate_static.py
"""
import json
import math
import sqlite3
from pathlib import Path

DB = Path("data/aurora.db")
OUT = Path("dashboard/web/public/data")
OUT.mkdir(parents=True, exist_ok=True)

conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row


def dump(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=None))
    print(f"  {name}: {len(data) if isinstance(data, list) else 'object'}")


# stores/aurora
rows = conn.execute("""
    SELECT store_id, name, city, address, latitude, longitude,
           region, county, status, first_seen_date
    FROM stores
    WHERE status = 'active'
      AND snapshot_date = (SELECT MAX(snapshot_date) FROM stores)
    ORDER BY city
""").fetchall()
dump("stores-aurora.json", [dict(r) for r in rows if r["latitude"] and r["longitude"]])

# stores/competitors
rows = conn.execute("""
    SELECT brand, name, city, address, latitude, longitude
    FROM competitor_stores
    WHERE latitude IS NOT NULL AND longitude IS NOT NULL
    ORDER BY brand, city
""").fetchall()
result: dict = {}
for r in rows:
    d = dict(r)
    b = d.pop("brand")
    result.setdefault(b, []).append(d)
dump("stores-competitors.json", result)

# stores/all (heatmap)
aurora_flat = [
    {"brand": "Aurora", "lat": r["latitude"], "lng": r["longitude"], "city": r["city"], "intensity": 1.0}
    for r in conn.execute(
        "SELECT latitude, longitude, city FROM stores "
        "WHERE status='active' AND snapshot_date=(SELECT MAX(snapshot_date) FROM stores)"
    ).fetchall()
    if r["latitude"] and r["longitude"]
]
comp_flat = [
    {"brand": r["brand"], "lat": r["latitude"], "lng": r["longitude"], "city": r["city"], "intensity": 0.6}
    for r in conn.execute(
        "SELECT brand, latitude, longitude, city FROM competitor_stores "
        "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    ).fetchall()
]
dump("stores-all.json", aurora_flat + comp_flat)

# whitespace
rows = conn.execute("""
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
    HAVING brand_count >= 1
    ORDER BY brand_count DESC, total_stores DESC
    LIMIT 300
""").fetchall()
dump("whitespace.json", [dict(r) for r in rows])

# future-openings
rows = conn.execute("""
    SELECT city, change_type, confidence_score, confidence_level,
           details_json, detected_date
    FROM changes
    WHERE change_type IN ('POSSIBLE_FUTURE_OPENING', 'MARKET_ACTIVITY_SIGNAL')
      AND detected_date >= date('now', '-14 days')
    ORDER BY confidence_score DESC
""").fetchall()
openings = []
for r in rows:
    d = dict(r)
    if d.get("details_json"):
        try:
            d["details"] = json.loads(d["details_json"])
        except Exception:
            d["details"] = {}
    city = d.get("city", "")
    coords = conn.execute(
        "SELECT AVG(latitude) as lat, AVG(longitude) as lng "
        "FROM competitor_stores WHERE lower(city) = lower(?)", (city,)
    ).fetchone()
    if coords and coords["lat"]:
        d["lat"] = coords["lat"]
        d["lng"] = coords["lng"]
    else:
        ac = conn.execute(
            "SELECT latitude as lat, longitude as lng FROM stores "
            "WHERE lower(city) = lower(?) AND status='active' LIMIT 1", (city,)
        ).fetchone()
        if ac and ac["lat"]:
            d["lat"] = ac["lat"]
            d["lng"] = ac["lng"]
    openings.append(d)
dump("future-openings.json", openings)

# stats
aurora_n = conn.execute(
    "SELECT COUNT(*) FROM stores WHERE status='active' AND snapshot_date=(SELECT MAX(snapshot_date) FROM stores)"
).fetchone()[0]
aurora_cities = conn.execute(
    "SELECT COUNT(DISTINCT city) FROM stores WHERE status='active' AND snapshot_date=(SELECT MAX(snapshot_date) FROM stores)"
).fetchone()[0]
comp_brands: dict = {}
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
dump("stats.json", {
    "aurora_stores": aurora_n,
    "aurora_cities": aurora_cities,
    "competitor_brands": comp_brands,
    "whitespace_cities": whitespace_n,
    "overlap_cities": overlap,
    "last_updated": latest,
})

conn.close()
print("Done.")
