"""
Historical trend analysis using the SQLite store history.

Queries the `changes` and `stores` tables to compute:
  - Monthly opening / closure counts
  - Fastest-growing cities
  - Expansion velocity (stores/month)
  - Regional activity summary
  - Competitor trend summary
"""
from datetime import date, datetime, timedelta

from src.config import DB_PATH, setup_logging
from src.data.ro_counties import county_for_city, region_for_city, normalize_city

logger = setup_logging("analysis.trends")


def _connect():
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def monthly_opening_counts(months_back: int = 6) -> list[dict]:
    """
    Returns [{month: "2026-04", openings: 5, closures: 1}, ...] sorted ascending.
    Returns [] if fewer than 2 distinct run-dates have NEW_STORE changes
    (prevents counting all current stores as openings on a first/re-baseline run).
    Uses DISTINCT store_id per month to avoid counting the same store multiple times.
    """
    conn = _connect()
    try:
        # Guard: require 2+ distinct dates with actual new-store events
        run_count = conn.execute(
            "SELECT COUNT(DISTINCT substr(detected_date, 1, 10)) FROM changes "
            "WHERE change_type = 'NEW_STORE' AND store_id IS NOT NULL AND store_id != ''"
        ).fetchone()[0] or 0
        if run_count < 2:
            return []

        cutoff = (datetime.now() - timedelta(days=months_back * 30)).date().isoformat()
        sql = """
            SELECT
                substr(detected_date, 1, 7) AS month,
                COUNT(DISTINCT CASE WHEN change_type = 'NEW_STORE'
                      THEN coalesce(store_id, detected_date || city) END) AS openings,
                COUNT(DISTINCT CASE WHEN change_type = 'REMOVED_STORE'
                      THEN coalesce(store_id, detected_date || city) END) AS closures
            FROM changes
            WHERE detected_date >= ?
              AND change_type IN ('NEW_STORE', 'REMOVED_STORE')
            GROUP BY month
            ORDER BY month ASC
        """
        rows = conn.execute(sql, (cutoff,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def city_growth_ranking(months_back: int = 3) -> list[dict]:
    """
    Top cities by new store openings in recent months.
    Returns [{city, county, region, openings}, ...] desc.
    """
    cutoff = (datetime.now() - timedelta(days=months_back * 30)).date().isoformat()
    sql = """
        SELECT city, COUNT(*) AS openings
        FROM changes
        WHERE change_type = 'NEW_STORE'
          AND detected_date >= ?
          AND city IS NOT NULL AND city != ''
        GROUP BY city
        ORDER BY openings DESC
        LIMIT 20
    """
    conn = _connect()
    try:
        rows = conn.execute(sql, (cutoff,)).fetchall()
        result = []
        for r in rows:
            city = r["city"]
            result.append({
                "city": city,
                "county": county_for_city(city),
                "region": region_for_city(city),
                "openings": r["openings"],
            })
        return result
    finally:
        conn.close()


def expansion_velocity() -> dict:
    """
    Compute current expansion velocity (stores added per month).
    Requires at least 2 distinct snapshot dates to avoid baseline inflation.
    Returns dict with "insufficient_data": True when there is only 1 snapshot.
    """
    sql = """
        SELECT
            MIN(detected_date) AS first_date,
            MAX(detected_date) AS last_date,
            COUNT(DISTINCT detected_date) AS date_count,
            SUM(CASE WHEN change_type = 'NEW_STORE' THEN 1 ELSE 0 END) AS total_new
        FROM changes
        WHERE change_type NOT IN ('MARKET_ACTIVITY_SIGNAL', 'POSSIBLE_FUTURE_OPENING')
    """
    conn = _connect()
    try:
        row = conn.execute(sql).fetchone()
        if not row or not row["first_date"]:
            return {"stores_per_month": None, "total_new": 0, "months_observed": 0,
                    "insufficient_data": True}

        date_count = row["date_count"] or 0
        total = row["total_new"] or 0

        if date_count < 2:
            return {
                "stores_per_month": None,
                "total_new": total,
                "months_observed": 0,
                "insufficient_data": True,
                "reason": "Need at least 2 daily snapshots for trend calculation.",
            }

        first = datetime.fromisoformat(row["first_date"]).date()
        last = datetime.fromisoformat(row["last_date"]).date()
        days = (last - first).days
        if days == 0:
            return {
                "stores_per_month": None,
                "total_new": total,
                "months_observed": 0,
                "insufficient_data": True,
                "reason": "All snapshots share the same date.",
            }

        months = days / 30.0
        rate = round(total / months, 1)
        return {
            "stores_per_month": rate,
            "total_new": total,
            "months_observed": round(months, 1),
            "insufficient_data": False,
        }
    finally:
        conn.close()


def region_activity_summary(months_back: int = 3) -> list[dict]:
    """
    Group recent openings by development region.
    Returns [{region, openings, top_city}, ...] desc by openings.
    """
    cities = city_growth_ranking(months_back)
    by_region: dict[str, dict] = {}
    for entry in cities:
        region = entry["region"] or "Unknown"
        if region not in by_region:
            by_region[region] = {"region": region, "openings": 0, "top_city": entry["city"]}
        by_region[region]["openings"] += entry["openings"]

    return sorted(by_region.values(), key=lambda x: -x["openings"])


def competitor_store_counts() -> dict[str, int]:
    """
    Latest competitor store counts by brand from the DB.
    Returns {"Pepco": 517, "TEDi": 77, ...}
    """
    sql = """
        SELECT brand, COUNT(*) AS cnt
        FROM competitor_stores
        WHERE scraped_date = (SELECT MAX(scraped_date) FROM competitor_stores)
        GROUP BY brand
    """
    conn = _connect()
    try:
        rows = conn.execute(sql).fetchall()
        return {r["brand"]: r["cnt"] for r in rows}
    finally:
        conn.close()


def store_count_history() -> list[dict]:
    """
    Daily Aurora store count from snapshots.
    Returns [{date, count}, ...] asc.
    """
    sql = """
        SELECT snapshot_date AS date, COUNT(DISTINCT store_id) AS count
        FROM stores
        GROUP BY snapshot_date
        ORDER BY snapshot_date ASC
    """
    conn = _connect()
    try:
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def weekly_change_summary(weeks_back: int = 2) -> dict:
    """
    Compare this week vs last week: new stores, closures, predictions.
    Returns {"this_week": {...}, "last_week": {...}, "delta": {...}}.
    """
    today = date.today()
    this_start = (today - timedelta(days=7)).isoformat()
    last_start = (today - timedelta(days=14)).isoformat()
    last_end = (today - timedelta(days=7)).isoformat()

    def _fetch(from_date, to_date):
        sql = """
            SELECT change_type, COUNT(*) AS cnt
            FROM changes
            WHERE detected_date >= ? AND detected_date < ?
            GROUP BY change_type
        """
        conn = _connect()
        try:
            rows = conn.execute(sql, (from_date, to_date)).fetchall()
            return {r["change_type"]: r["cnt"] for r in rows}
        finally:
            conn.close()

    this_week = _fetch(this_start, today.isoformat())
    last_week = _fetch(last_start, last_end)

    def _delta(key):
        return this_week.get(key, 0) - last_week.get(key, 0)

    return {
        "this_week": this_week,
        "last_week": last_week,
        "delta": {
            "NEW_STORE": _delta("NEW_STORE"),
            "REMOVED_STORE": _delta("REMOVED_STORE"),
            "POSSIBLE_FUTURE_OPENING": _delta("POSSIBLE_FUTURE_OPENING"),
        },
    }
