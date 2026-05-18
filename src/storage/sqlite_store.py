"""
SQLite storage layer.
Stores snapshots, changes, jobs, news, and competitor data.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from src.config import DB_PATH, SNAPSHOT_RETENTION_DAYS, setup_logging

logger = setup_logging("storage.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
    store_id TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    name TEXT,
    city TEXT,
    county TEXT,
    county_code TEXT,
    region TEXT,
    address TEXT,
    latitude REAL,
    longitude REAL,
    source_url TEXT,
    first_seen_date TEXT,
    last_seen_date TEXT,
    status TEXT DEFAULT 'active',
    notes TEXT DEFAULT '',
    PRIMARY KEY (store_id, snapshot_date)
);

CREATE TABLE IF NOT EXISTS retail_parks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    city TEXT,
    county TEXT,
    region TEXT,
    status TEXT,
    developer TEXT,
    opening_date TEXT,
    url TEXT UNIQUE,
    source TEXT,
    detected_date TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS shopping_centers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    city TEXT,
    county TEXT,
    region TEXT,
    status TEXT,
    developer TEXT,
    gla_sqm INTEGER,
    opening_date TEXT,
    url TEXT UNIQUE,
    source TEXT,
    detected_date TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    change_type TEXT NOT NULL,
    detected_date TEXT NOT NULL,
    city TEXT,
    store_id TEXT,
    store_json TEXT,
    previous_store_json TEXT,
    details_json TEXT,
    confidence_score REAL,
    confidence_level TEXT,
    competitor_json TEXT,
    alerted INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    company TEXT,
    location TEXT,
    url TEXT UNIQUE,
    canonical_job_id TEXT,
    cities_mentioned TEXT,
    signal_score INTEGER DEFAULT 0,
    source TEXT,
    scraped_date TEXT,
    published_date TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS news_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    url TEXT UNIQUE,
    published_date TEXT,
    excerpt TEXT,
    signals_json TEXT,
    source TEXT,
    scraped_date TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS instagram_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shortcode TEXT UNIQUE,
    url TEXT,
    caption TEXT,
    timestamp INTEGER,
    published_date TEXT,
    cities_mentioned TEXT,
    expansion_signals TEXT,
    brand TEXT DEFAULT '',
    signal_category TEXT DEFAULT 'aurora_direct',
    signal_type TEXT DEFAULT 'generic_promo',
    signal_score INTEGER DEFAULT 0,
    detected_malls TEXT DEFAULT '[]',
    detected_locations TEXT DEFAULT '[]',
    detected_companies TEXT DEFAULT '[]',
    reason TEXT DEFAULT '',
    company TEXT DEFAULT '',
    source TEXT,
    scraped_date TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS competitor_stores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand TEXT NOT NULL,
    name TEXT,
    city TEXT,
    address TEXT,
    latitude REAL,
    longitude REAL,
    scraped_date TEXT NOT NULL,
    source TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(brand, latitude, longitude)
);

CREATE INDEX IF NOT EXISTS idx_stores_date ON stores(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_changes_date ON changes(detected_date);
CREATE INDEX IF NOT EXISTS idx_changes_alerted ON changes(alerted);
CREATE INDEX IF NOT EXISTS idx_competitors_brand ON competitor_stores(brand);
"""


@contextmanager
def _connect(db_path: Path = DB_PATH):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path = DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)
        existing_stores = {row[1] for row in conn.execute("PRAGMA table_info(stores)").fetchall()}
        for col, typedef in [("county", "TEXT"), ("county_code", "TEXT"), ("region", "TEXT")]:
            if col not in existing_stores:
                conn.execute(f"ALTER TABLE stores ADD COLUMN {col} {typedef}")
                logger.info(f"Migration: added column stores.{col}")
        existing_jobs = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if "canonical_job_id" not in existing_jobs:
            conn.execute("ALTER TABLE jobs ADD COLUMN canonical_job_id TEXT")
            logger.info("Migration: added column jobs.canonical_job_id")
        existing_ig = {row[1] for row in conn.execute("PRAGMA table_info(instagram_posts)").fetchall()}
        for col, typedef in [
            ("brand", "TEXT DEFAULT ''"),
            ("signal_category", "TEXT DEFAULT 'aurora_direct'"),
            ("signal_type", "TEXT DEFAULT 'generic_promo'"),
            ("signal_score", "INTEGER DEFAULT 0"),
            ("detected_malls", "TEXT DEFAULT '[]'"),
            ("detected_locations", "TEXT DEFAULT '[]'"),
            ("detected_companies", "TEXT DEFAULT '[]'"),
            ("reason", "TEXT DEFAULT ''"),
            ("company", "TEXT DEFAULT ''"),
        ]:
            if col not in existing_ig:
                conn.execute(f"ALTER TABLE instagram_posts ADD COLUMN {col} {typedef}")
                logger.info(f"Migration: added column instagram_posts.{col}")
    logger.info(f"Database initialized: {db_path}")


# ── Snapshots ────────────────────────────────────────────────────────────────

def save_snapshot(stores: list[dict], snapshot_date: Optional[str] = None) -> None:
    today = snapshot_date or date.today().isoformat()
    with _connect() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO stores
               (store_id, snapshot_date, name, city, county, county_code, region,
                address, latitude, longitude, source_url,
                first_seen_date, last_seen_date, status, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    s["store_id"], today, s.get("name"), s.get("city"),
                    s.get("county", ""), s.get("county_code", ""), s.get("region", ""),
                    s.get("address"), s.get("latitude"), s.get("longitude"),
                    s.get("source_url"),
                    s.get("first_seen_date", today), s.get("last_seen_date", today),
                    s.get("status", "active"), s.get("notes", ""),
                )
                for s in stores
            ],
        )
    logger.info(f"Saved {len(stores)} stores for snapshot {today}")


def load_snapshot(snapshot_date: Optional[str] = None) -> list[dict]:
    if snapshot_date:
        sql = "SELECT * FROM stores WHERE snapshot_date = ? ORDER BY store_id"
        params = (snapshot_date,)
    else:
        # Latest snapshot
        sql = """SELECT * FROM stores WHERE snapshot_date = (
                     SELECT MAX(snapshot_date) FROM stores
                 ) ORDER BY store_id"""
        params = ()

    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    result = [dict(r) for r in rows]
    logger.info(f"Loaded {len(result)} stores from snapshot {snapshot_date or 'latest'}")
    return result


def load_previous_snapshot() -> list[dict]:
    """Load the second-most-recent snapshot."""
    with _connect() as conn:
        dates = conn.execute(
            "SELECT DISTINCT snapshot_date FROM stores ORDER BY snapshot_date DESC LIMIT 2"
        ).fetchall()

    if len(dates) < 2:
        logger.info("No previous snapshot found")
        return []

    prev_date = dates[1]["snapshot_date"]
    return load_snapshot(prev_date)


def get_latest_snapshot_date() -> Optional[str]:
    with _connect() as conn:
        row = conn.execute("SELECT MAX(snapshot_date) as d FROM stores").fetchone()
    return row["d"] if row else None


def purge_old_snapshots(retention_days: int = SNAPSHOT_RETENTION_DAYS) -> None:
    cutoff = (datetime.now() - timedelta(days=retention_days)).date().isoformat()
    with _connect() as conn:
        result = conn.execute("DELETE FROM stores WHERE snapshot_date < ?", (cutoff,))
    logger.info(f"Purged snapshots older than {cutoff}: {result.rowcount} rows deleted")


# ── Changes ──────────────────────────────────────────────────────────────────

def save_changes(changes: list[dict]) -> list[int]:
    inserted_ids = []
    with _connect() as conn:
        for c in changes:
            store = c.get("store") or {}
            confidence = c.get("confidence", {})
            try:
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO changes
                       (change_type, detected_date, city, store_id, store_json,
                        previous_store_json, details_json, confidence_score,
                        confidence_level, competitor_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        c["change_type"],
                        c.get("detected_date", date.today().isoformat()),
                        store.get("city", c.get("city", "")),
                        store.get("store_id", ""),
                        json.dumps(store),
                        json.dumps(c.get("previous_store") or {}),
                        json.dumps(c.get("details", {})),
                        confidence.get("score"),
                        confidence.get("level"),
                        json.dumps(c.get("competitor_analysis", {})),
                    ),
                )
                if cursor.lastrowid:
                    inserted_ids.append(cursor.lastrowid)
            except sqlite3.Error as e:
                logger.error(f"Failed to save change: {e}")
    logger.info(f"Saved {len(inserted_ids)} new changes")
    return inserted_ids


def load_unalerted_changes() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM changes WHERE alerted = 0 ORDER BY created_at"
        ).fetchall()
    return [dict(r) for r in rows]


def mark_changes_alerted(change_ids: list[int]) -> None:
    if not change_ids:
        return
    placeholders = ",".join("?" * len(change_ids))
    with _connect() as conn:
        conn.execute(
            f"UPDATE changes SET alerted = 1 WHERE id IN ({placeholders})",
            change_ids,
        )
    logger.info(f"Marked {len(change_ids)} changes as alerted")


def load_recent_changes(days: int = 7) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=days)).date().isoformat()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM changes WHERE detected_date >= ? ORDER BY detected_date DESC",
            (cutoff,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Jobs ─────────────────────────────────────────────────────────────────────

def save_jobs(jobs: list[dict]) -> None:
    saved = 0
    with _connect() as conn:
        for j in jobs:
            canonical_id = j.get("canonical_job_id", "")
            # Skip if we already have this canonical job ID in the DB
            if canonical_id:
                existing = conn.execute(
                    "SELECT id FROM jobs WHERE canonical_job_id = ?", (canonical_id,)
                ).fetchone()
                if existing:
                    continue
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO jobs
                       (title, company, location, url, canonical_job_id,
                        cities_mentioned, signal_score, source, scraped_date, published_date)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        j["title"], j.get("company", ""), j.get("location", ""),
                        j.get("url", ""), canonical_id,
                        json.dumps(j.get("cities_mentioned", [])),
                        j.get("signal_score", 0), j.get("source", ""),
                        j.get("scraped_date", date.today().isoformat()),
                        j.get("published_date", date.today().isoformat()),
                    ),
                )
                saved += 1
            except Exception as e:
                logger.debug(f"Job insert skipped: {e}")
    logger.info(f"Saved {saved} new jobs (of {len(jobs)} provided)")


def load_recent_jobs(days: int = 14) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=days)).date().isoformat()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE scraped_date >= ? ORDER BY signal_score DESC",
            (cutoff,),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["cities_mentioned"] = json.loads(d.get("cities_mentioned") or "[]")
        result.append(d)
    return result


# ── News ─────────────────────────────────────────────────────────────────────

def save_news(articles: list[dict]) -> None:
    _SIGNAL_FIELDS = {
        "signal_category", "signal_class", "cities_mentioned", "aurora_specific",
        "related_to_aurora", "company", "source_domain", "query_term",
    }
    with _connect() as conn:
        conn.executemany(
            """INSERT OR IGNORE INTO news_articles
               (title, url, published_date, excerpt, signals_json, source, scraped_date)
               VALUES (?,?,?,?,?,?,?)""",
            [
                (
                    a["title"], a.get("url", ""), a.get("published_date", ""),
                    a.get("excerpt", ""),
                    json.dumps({
                        **a.get("signals", {}),
                        **{k: a[k] for k in _SIGNAL_FIELDS if k in a},
                    }),
                    a.get("source", ""), a.get("scraped_date", date.today().isoformat()),
                )
                for a in articles
            ],
        )
    logger.info(f"Saved {len(articles)} news articles")


def load_recent_news(days: int = 7) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=days)).date().isoformat()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM news_articles WHERE scraped_date >= ? ORDER BY published_date DESC",
            (cutoff,),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        signals = json.loads(d.get("signals_json") or "{}")
        d["signals"] = signals
        # Unpack all signal fields to top-level so callers don't need to know the nesting
        for k, v in signals.items():
            if k not in d or d[k] is None:
                d[k] = v
        result.append(d)
    return result


# ── Instagram ────────────────────────────────────────────────────────────────

def save_instagram_posts(posts: list[dict]) -> None:
    with _connect() as conn:
        conn.executemany(
            """INSERT OR IGNORE INTO instagram_posts
               (shortcode, url, caption, timestamp, published_date,
                cities_mentioned, expansion_signals, brand, signal_category,
                signal_type, signal_score, detected_malls, detected_locations,
                detected_companies, reason, company, source, scraped_date)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    p.get("shortcode", ""), p.get("url", ""), p.get("caption", ""),
                    p.get("timestamp", 0), p.get("published_date", ""),
                    json.dumps(p.get("cities_mentioned", [])),
                    json.dumps(p.get("expansion_signals", [])),
                    p.get("brand", ""),
                    p.get("signal_category", "aurora_direct"),
                    p.get("signal_type", "generic_promo"),
                    p.get("signal_score", 0),
                    json.dumps(p.get("detected_malls", [])),
                    json.dumps(p.get("detected_locations", [])),
                    json.dumps(p.get("detected_companies", [])),
                    p.get("reason", ""),
                    p.get("company", ""),
                    p.get("source", "instagram"),
                    p.get("scraped_date", date.today().isoformat()),
                )
                for p in posts
            ],
        )
    logger.info(f"Saved {len(posts)} Instagram posts")


def load_recent_instagram(days: int = 30) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=days)).date().isoformat()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM instagram_posts WHERE scraped_date >= ?",
            (cutoff,),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["cities_mentioned"] = json.loads(d.get("cities_mentioned") or "[]")
        d["expansion_signals"] = json.loads(d.get("expansion_signals") or "[]")
        d["detected_malls"] = json.loads(d.get("detected_malls") or "[]")
        d["detected_locations"] = json.loads(d.get("detected_locations") or "[]")
        d["detected_companies"] = json.loads(d.get("detected_companies") or "[]")
        result.append(d)
    return result


# ── Competitor stores ─────────────────────────────────────────────────────────

def save_competitor_stores(competitor_data: dict[str, list[dict]]) -> None:
    today = date.today().isoformat()
    rows = []
    for brand, stores in competitor_data.items():
        for s in stores:
            rows.append((
                brand, s.get("name", brand), s.get("city", ""), s.get("address", ""),
                s.get("latitude"), s.get("longitude"), today, s.get("source", ""),
            ))

    with _connect() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO competitor_stores
               (brand, name, city, address, latitude, longitude, scraped_date, source)
               VALUES (?,?,?,?,?,?,?,?)""",
            rows,
        )
    logger.info(f"Saved competitor stores: {sum(len(v) for v in competitor_data.values())} total")


def load_competitor_stores() -> dict[str, list[dict]]:
    """Load the most recent competitor stores for each brand."""
    with _connect() as conn:
        rows = conn.execute("""
            SELECT * FROM competitor_stores
            WHERE scraped_date = (
                SELECT MAX(scraped_date) FROM competitor_stores
            )
            ORDER BY brand, city
        """).fetchall()

    result: dict[str, list[dict]] = {}
    for r in rows:
        d = dict(r)
        brand = d["brand"]
        result.setdefault(brand, []).append(d)
    return result
