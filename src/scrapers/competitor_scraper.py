"""
Competitor store scraper: Pepco, TEDi, KiK, Action.

Real endpoints (discovered via Playwright network interception):
  Pepco  — pepco.ro/api/stores?market=RO  (GeoJSON FeatureCollection, 517 RO stores, no auth)
  KiK    — storefinder-microservice.kik.de/storefinder/results.json
             20-store cap per query; dedup key is `filiale` (stable store number), NOT `uid` (fresh UUID per response).
             Grid search required: 0.6° step, 100km radius, ~161 stores in RO.
  TEDi   — storeviewer-phkw2veu6jdfq.azureedge.net/StoreFinder/search
             Server-fixed 60km radius; no radius override accepted.
             Grid search required: 0.6° step, ~77 stores in RO.
  Action — Cloudflare Turnstile blocks all non-browser clients; skipped.
"""
import json
import pathlib
import time
from datetime import date
from itertools import product
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import HEADERS, REQUEST_TIMEOUT, MAX_RETRIES, REQUEST_DELAY, setup_logging

logger = setup_logging("scraper.competitors")

# Romania bounding box with a small buffer
_RO_LAT_MIN, _RO_LAT_MAX = 43.6, 48.3
_RO_LON_MIN, _RO_LON_MAX = 20.2, 30.0

# Grid step in decimal degrees.
# TEDi has a 60km server-side radius: safe step = 0.6° ≈ 67km (guaranteed overlap).
# KiK has a 20-store cap per query: same step with 100km radius keeps cap pressure low.
_GRID_STEP = 0.6

_DEBUG_DIR = pathlib.Path("data/debug")


def _grid_points() -> list[tuple[float, float]]:
    """Return lat/lon grid covering all of Romania."""
    lats = []
    lat = _RO_LAT_MIN
    while lat <= _RO_LAT_MAX + _GRID_STEP:
        lats.append(round(lat, 4))
        lat += _GRID_STEP
    lons = []
    lon = _RO_LON_MIN
    while lon <= _RO_LON_MAX + _GRID_STEP:
        lons.append(round(lon, 4))
        lon += _GRID_STEP
    return list(product(lats, lons))


def _make_store(brand: str, name: str, city: str, address: str,
                lat: Optional[float], lon: Optional[float]) -> dict:
    return {
        "brand": brand,
        "name": name,
        "city": city,
        "address": address,
        "latitude": lat,
        "longitude": lon,
        "scraped_date": date.today().isoformat(),
        "source": "",
    }


def _save_debug(brand: str, data: list) -> None:
    _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = _DEBUG_DIR / f"competitors_{brand.lower()}_{date.today().isoformat()}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    logger.debug(f"{brand}: raw data saved to {path}")


# ── Pepco ────────────────────────────────────────────────────────────────────

class PepcoScraper:
    URL = "https://pepco.ro/api/stores?market=RO"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    @retry(stop=stop_after_attempt(MAX_RETRIES), wait=wait_exponential(multiplier=1, min=2, max=10))
    def scrape(self) -> list[dict]:
        logger.info("Scraping Pepco (pepco.ro/api/stores?market=RO)")
        resp = self.session.get(self.URL, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        features = data["globalStoreDataSet"]["stores"]["features"]
        _save_debug("pepco", features)

        stores = []
        for f in features:
            props = f.get("properties", {})
            coords = f.get("geometry", {}).get("coordinates", [None, None])
            lon, lat = coords[0], coords[1]  # GeoJSON is [lon, lat]
            city = props.get("city", "")
            street = props.get("street", "")
            number = props.get("street_number", "")
            address = f"{street} {number}".strip()
            stores.append(_make_store("Pepco", props.get("name", "Pepco"), city, address,
                                      float(lat) if lat else None,
                                      float(lon) if lon else None))

        logger.info(f"Pepco: {len(stores)} stores (verified, single endpoint)")
        return stores


# ── KiK ──────────────────────────────────────────────────────────────────────

class KiKScraper:
    """
    KiK API quirks:
    - Server caps results at 20 stores per query regardless of distance parameter.
    - `uid` is a fresh UUID generated per query response — NOT a stable store ID.
    - `filiale` is the stable store number; use it for deduplication.
    - Grid search with 0.6° step + 100km radius ensures complete national coverage.
    """
    URL = "https://storefinder-microservice.kik.de/storefinder/results.json"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def _fetch_grid_point(self, lat: float, lon: float) -> tuple[list[dict], bool]:
        """Returns (stores, hit_cap)."""
        try:
            resp = self.session.get(
                self.URL,
                params={"lat": lat, "long": lon, "country": "RO", "distance": 60},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            stores = []
            for batch in data.get("stores", []):
                stores.extend(batch.get("results", {}).values())
            hit_cap = len(stores) >= 20
            return stores, hit_cap
        except Exception as e:
            logger.debug(f"KiK ({lat},{lon}): {e}")
            return [], False

    def scrape(self) -> list[dict]:
        logger.info("Scraping KiK (grid search, dedup by filiale)")
        grid = _grid_points()
        by_filiale: dict[str, dict] = {}
        capped_queries = 0
        raw_all = []

        for lat, lon in grid:
            results, hit_cap = self._fetch_grid_point(lat, lon)
            if hit_cap:
                capped_queries += 1
            for store in results:
                filiale = store.get("filiale")
                if filiale and filiale not in by_filiale:
                    by_filiale[filiale] = store
            raw_all.extend(results)
            time.sleep(0.15)

        _save_debug("kik", list(by_filiale.values()))

        if capped_queries > 0:
            logger.warning(
                f"KiK: {capped_queries}/{len(grid)} grid points hit the 20-store cap — "
                f"coverage may be incomplete in dense areas. Consider reducing grid step."
            )

        stores = []
        for store in by_filiale.values():
            lat = store.get("latitude")
            lon = store.get("longitude")
            city = store.get("city", "")
            address = store.get("address", "")
            stores.append(_make_store(
                "KiK", f"KiK {city}", city, address,
                float(lat) if lat else None,
                float(lon) if lon else None,
            ))

        cities = set(s["city"] for s in stores)
        if len(cities) < 50:
            logger.warning(f"KiK: only {len(cities)} cities covered — coverage may be suspiciously low")

        logger.info(
            f"KiK: {len(stores)} stores across {len(cities)} cities "
            f"(grid={len(grid)} points, capped={capped_queries})"
        )
        return stores


# ── TEDi ─────────────────────────────────────────────────────────────────────

class TEDiScraper:
    """
    TEDi API quirks:
    - Embedded via Azure CDN iframe; store data comes from storeviewer endpoint.
    - Server always returns results within a fixed 60km radius; no radius parameter accepted.
    - Grid search with 0.6° step (~67km) ensures full overlap coverage.
    - Dedup key is store `id` (stable integer per store).
    - Filter by countryCode=RO to exclude stores in neighboring countries.
    """
    URL = "https://storeviewer-phkw2veu6jdfq.azureedge.net/StoreFinder/search"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def _fetch_grid_point(self, lat: float, lon: float) -> list[dict]:
        try:
            resp = self.session.get(
                self.URL,
                params={"lat": lat, "lng": lon, "culture": "ro"},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json().get("stores", [])
        except Exception as e:
            logger.debug(f"TEDi ({lat},{lon}): {e}")
            return []

    def scrape(self) -> list[dict]:
        logger.info("Scraping TEDi (grid search, 60km radius, dedup by id)")
        grid = _grid_points()
        by_id: dict[int, dict] = {}

        for lat, lon in grid:
            for raw in self._fetch_grid_point(lat, lon):
                sid = raw.get("id")
                if sid is not None and raw.get("countryCode", "").upper() == "RO":
                    by_id[sid] = raw
            time.sleep(0.15)

        _save_debug("tedi", list(by_id.values()))

        stores = []
        for raw in by_id.values():
            lat = raw.get("latitude")
            lon = raw.get("longitude")
            city = raw.get("city", "")
            address = raw.get("addressLine1", "")
            stores.append(_make_store(
                "TEDi", raw.get("name", f"TEDi {city}"), city, address,
                float(lat) if lat else None,
                float(lon) if lon else None,
            ))

        cities = set(s["city"] for s in stores)
        if len(cities) < 30:
            logger.warning(f"TEDi: only {len(cities)} cities covered — coverage may be suspiciously low")

        logger.info(
            f"TEDi: {len(stores)} stores across {len(cities)} cities "
            f"(grid={len(grid)} points)"
        )
        return stores


# ── Action ───────────────────────────────────────────────────────────────────

class ActionScraper:
    def scrape(self) -> list[dict]:
        logger.warning("Action: Cloudflare Turnstile protection — skipping (0 stores). "
                       "No public API accessible without a real browser session.")
        return []


# ── Entry point ───────────────────────────────────────────────────────────────

def scrape_competitors() -> dict[str, list[dict]]:
    result = {}
    scrapers = {
        "Pepco": PepcoScraper(),
        "TEDi": TEDiScraper(),
        "KiK": KiKScraper(),
        "Action": ActionScraper(),
    }
    for brand, scraper in scrapers.items():
        try:
            stores = scraper.scrape()
            result[brand] = stores
        except Exception as e:
            logger.error(f"Failed to scrape {brand}: {e}")
            result[brand] = []
        time.sleep(REQUEST_DELAY)

    total = sum(len(v) for v in result.values())
    logger.info(f"Competitors total: {total} stores across {len(result)} brands")
    return result


def debug_competitors() -> None:
    """Print a detailed spot-check report for each competitor brand."""
    from collections import Counter
    results = scrape_competitors()

    print("\n" + "=" * 60)
    print("COMPETITOR STORE DEBUG REPORT")
    print("=" * 60)

    endpoints = {
        "Pepco": "pepco.ro/api/stores?market=RO (GeoJSON, single call)",
        "KiK": "storefinder-microservice.kik.de (grid, dedup by filiale)",
        "TEDi": "storeviewer-phkw2veu6jdfq.azureedge.net/StoreFinder/search (grid, 60km radius)",
        "Action": "BLOCKED — Cloudflare Turnstile",
    }

    for brand, stores in results.items():
        print(f"\n{'─'*40}")
        print(f"Brand:    {brand}")
        print(f"Endpoint: {endpoints.get(brand, 'unknown')}")
        print(f"Total:    {len(stores)} stores")

        if not stores:
            continue

        cities = Counter(s.get("city", "") for s in stores)
        with_coords = sum(1 for s in stores if s.get("latitude") and s.get("longitude"))
        print(f"Cities:   {len(cities)}")
        print(f"With GPS: {with_coords}/{len(stores)}")

        if len(cities) < 20 and brand not in ("Action",):
            print(f"  ⚠ WARNING: city coverage looks suspiciously low ({len(cities)} cities)")

        print(f"\nFirst 10 stores:")
        for s in stores[:10]:
            print(f"  {s.get('city','?'):20s}  {s.get('address','')[:40]:40s}  "
                  f"lat={s.get('latitude','?')} lon={s.get('longitude','?')}")

        print(f"\nTop 10 cities by store count:")
        for city, n in cities.most_common(10):
            print(f"  {city:25s} {n}")

    print("\n" + "=" * 60)
    print(f"Debug raw data saved to: data/debug/")
    print("=" * 60)
