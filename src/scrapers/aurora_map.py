"""
Aurora store map scraper.
Attempts direct HTML parsing first, falls back to Playwright for dynamic content.
Also handles Google My Maps embeds.
"""
import json
import re
import time
from datetime import date
from typing import Optional
from dataclasses import dataclass, asdict

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import (
    AURORA_STORE_MAP_URL, HEADERS, REQUEST_TIMEOUT,
    MAX_RETRIES, REQUEST_DELAY, HEADLESS, setup_logging,
)

logger = setup_logging("scraper.aurora_map")

# Comprehensive Romanian city list for parsing
_RO_CITIES = [
    "București", "Cluj-Napoca", "Cluj", "Timișoara", "Iași", "Constanța",
    "Craiova", "Brașov", "Galați", "Ploiești", "Oradea", "Brăila", "Arad",
    "Pitești", "Sibiu", "Bacău", "Târgu Mureș", "Baia Mare", "Buzău",
    "Satu Mare", "Botoșani", "Râmnicu Vâlcea", "Suceava", "Piatra Neamț",
    "Drobeta-Turnu Severin", "Focșani", "Deva", "Bistrița", "Reșița",
    "Alba Iulia", "Tulcea", "Sfântu Gheorghe", "Alexandria", "Zalău",
    "Giurgiu", "Slobozia", "Dej", "Turda", "Câmpina", "Medgidia",
    "Roman", "Fetești", "Mangalia", "Câmpulung", "Fălticeni", "Rădăuți",
    "Vatra Dornei", "Câmpulung Moldovenesc", "Gura Humorului", "Siret",
    "Dorohoi", "Pașcani", "Huși", "Vaslui", "Tecuci", "Adjud",
    "Onești", "Moinești", "Comănești", "Pătărlagele", "Buziaș",
    "Lugoj", "Deta", "Sannicolau Mare", "Jimbolia", "Sânnicolau Mare",
    "Caransebeș", "Băile Herculane", "Moldova Nouă", "Orșova",
    "Motru", "Târgu Jiu", "Rovinari", "Filiaș", "Segarcea",
    "Caracal", "Balș", "Corabia", "Drăgășani", "Râmnicu Vâlcea",
    "Câmpulung Muscel", "Curtea de Argeș", "Mioveni", "Costești",
    "Târgoviște", "Moreni", "Pucioasa", "Titu", "Găești",
    "Urziceni", "Oltenița", "Călărași", "Lehliu-Gară",
    "Buftea", "Voluntari", "Pantelimon", "Popești-Leordeni",
    "Chitila", "Otopeni", "Tunari", "Balotești",
    "Ploiești", "Câmpina", "Sinaia", "Azuga", "Bușteni",
    "Vălenii de Munte", "Urlați", "Mizil",
    "Slobozia", "Fetești", "Urziceni", "Amara",
    "Mangalia", "Neptun", "Eforie", "Năvodari", "Medgidia",
    "Cernavodă", "Hârșova", "Topalu",
]
_RO_CITY_RE = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in sorted(_RO_CITIES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


@dataclass
class StoreRecord:
    store_id: str
    name: str
    city: str
    address: str
    latitude: Optional[float]
    longitude: Optional[float]
    source_url: str
    first_seen_date: str
    last_seen_date: str
    status: str  # active | removed | unconfirmed
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def location_key(self) -> str:
        """Stable identity key for diff comparison."""
        if self.latitude and self.longitude:
            return f"{self.latitude:.4f},{self.longitude:.4f}"
        return f"{self.city.lower().strip()}::{self.address.lower().strip()[:40]}"


def _generate_store_id(name: str, city: str, lat: Optional[float], lon: Optional[float]) -> str:
    if lat and lon:
        return f"aurora_{lat:.4f}_{lon:.4f}".replace(".", "_").replace("-", "n")
    slug = f"{city}_{name}".lower()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    return f"aurora_{slug}"


class AuroraMapScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    @retry(stop=stop_after_attempt(MAX_RETRIES), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _fetch_page(self, url: str) -> str:
        resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.text

    def _parse_html(self, html: str) -> list[StoreRecord]:
        """Try multiple patterns to extract store data from raw HTML."""
        stores = []
        soup = BeautifulSoup(html, "lxml")

        # Pattern 1: JSON-LD structured data
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "")
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict):
                    items = data.get("@graph", [data])
                else:
                    continue
                for item in items:
                    if item.get("@type") in ("Store", "LocalBusiness", "GroceryStore"):
                        geo = item.get("geo", {})
                        addr = item.get("address", {})
                        name = item.get("name", "Aurora")
                        city = addr.get("addressLocality", "")
                        address = addr.get("streetAddress", "")
                        lat = float(geo.get("latitude", 0)) or None
                        lon = float(geo.get("longitude", 0)) or None
                        today = date.today().isoformat()
                        sid = _generate_store_id(name, city, lat, lon)
                        stores.append(StoreRecord(
                            store_id=sid, name=name, city=city,
                            address=address, latitude=lat, longitude=lon,
                            source_url=AURORA_STORE_MAP_URL,
                            first_seen_date=today, last_seen_date=today,
                            status="active",
                        ))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue

        if stores:
            logger.info(f"Parsed {len(stores)} stores from JSON-LD")
            return stores

        # Pattern 2: inline JS variables with marker data
        js_patterns = [
            r"var\s+markers?\s*=\s*(\[.*?\]);",
            r"var\s+stores?\s*=\s*(\[.*?\]);",
            r"var\s+locations?\s*=\s*(\[.*?\]);",
            r'"stores"\s*:\s*(\[.*?\])',
            r'"markers"\s*:\s*(\[.*?\])',
            r'"locations"\s*:\s*(\[.*?\])',
        ]
        for script in soup.find_all("script"):
            text = script.string or ""
            for pattern in js_patterns:
                match = re.search(pattern, text, re.DOTALL)
                if match:
                    try:
                        raw = json.loads(match.group(1))
                        parsed = self._parse_marker_list(raw)
                        if parsed:
                            logger.info(f"Parsed {len(parsed)} stores from inline JS ({pattern})")
                            return parsed
                    except (json.JSONDecodeError, TypeError):
                        continue

        # Pattern 3: Google My Maps iframe src
        iframe = soup.find("iframe", src=re.compile(r"google\.com/maps"))
        if iframe:
            src = iframe.get("src", "")
            logger.info(f"Found Google Maps iframe: {src}")
            parsed = self._extract_from_maps_url(src)
            if parsed:
                return parsed

        # Pattern 4: data attributes on map markers
        marker_divs = soup.find_all(attrs={"data-lat": True, "data-lng": True})
        if marker_divs:
            today = date.today().isoformat()
            for div in marker_divs:
                try:
                    lat = float(div["data-lat"])
                    lon = float(div["data-lng"])
                    name = div.get("data-name", div.get("data-title", "Aurora"))
                    city = div.get("data-city", "")
                    address = div.get("data-address", "")
                    sid = _generate_store_id(name, city, lat, lon)
                    stores.append(StoreRecord(
                        store_id=sid, name=name, city=city,
                        address=address, latitude=lat, longitude=lon,
                        source_url=AURORA_STORE_MAP_URL,
                        first_seen_date=today, last_seen_date=today,
                        status="active",
                    ))
                except (ValueError, KeyError):
                    continue
            if stores:
                logger.info(f"Parsed {len(stores)} stores from data-lat/lng attributes")
                return stores

        logger.warning("No stores parsed from static HTML — Playwright fallback needed")
        return []

    def _parse_marker_list(self, raw: list) -> list[StoreRecord]:
        stores = []
        today = date.today().isoformat()
        for item in raw:
            if not isinstance(item, dict):
                continue
            lat = float(item.get("lat", item.get("latitude", item.get("Lat", 0))) or 0) or None
            lon = float(item.get("lng", item.get("lon", item.get("longitude", item.get("Lng", 0)))) or 0) or None
            name = item.get("name", item.get("title", item.get("Name", "Aurora")))
            city = item.get("city", item.get("City", item.get("town", "")))
            address = item.get("address", item.get("Address", item.get("street", "")))
            if not city and address:
                parts = address.split(",")
                city = parts[-1].strip() if len(parts) > 1 else ""
            sid = _generate_store_id(str(name), str(city), lat, lon)
            stores.append(StoreRecord(
                store_id=sid, name=str(name), city=str(city),
                address=str(address), latitude=lat, longitude=lon,
                source_url=AURORA_STORE_MAP_URL,
                first_seen_date=today, last_seen_date=today,
                status="active",
            ))
        return stores

    def _extract_from_maps_url(self, src: str) -> list[StoreRecord]:
        """Pull KML/GeoJSON from a Google My Maps embed."""
        mid_match = re.search(r"mid=([A-Za-z0-9_-]+)", src)
        if not mid_match:
            return []
        mid = mid_match.group(1)
        kml_url = f"https://www.google.com/maps/d/kml?mid={mid}&forcekml=1"
        logger.info(f"Fetching KML from Google My Maps: {kml_url}")
        try:
            resp = self.session.get(kml_url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return self._parse_kml(resp.text)
        except Exception as e:
            logger.warning(f"Failed to fetch KML: {e}")
            return []

    def _parse_kml(self, kml: str) -> list[StoreRecord]:
        soup = BeautifulSoup(kml, "lxml-xml")
        stores = []
        today = date.today().isoformat()
        for pm in soup.find_all("Placemark"):
            name_tag = pm.find("name")
            full_name = name_tag.text.strip() if name_tag else "Aurora"
            coords_tag = pm.find("coordinates")
            if not coords_tag:
                continue
            parts = coords_tag.text.strip().split(",")
            if len(parts) < 2:
                continue
            try:
                lon, lat = float(parts[0]), float(parts[1])
            except ValueError:
                continue

            city, address = self._parse_kml_name(full_name)

            # Extract store number and hours from description
            desc = pm.find("description")
            desc_text = desc.text.strip() if desc else ""
            notes = self._extract_kml_notes(desc_text)

            sid = _generate_store_id(full_name, city, lat, lon)
            stores.append(StoreRecord(
                store_id=sid, name="Aurora Multimarket", city=city,
                address=address, latitude=lat, longitude=lon,
                source_url=AURORA_STORE_MAP_URL,
                first_seen_date=today, last_seen_date=today,
                status="active",
                notes=notes,
            ))
        logger.info(f"Parsed {len(stores)} stores from KML")
        return stores

    def _normalize_ro(self, text: str) -> str:
        """Normalize Romanian diacritics — both cedilla and comma-below forms."""
        return (text
                .replace("ş", "ș").replace("Ş", "Ș")
                .replace("ţ", "ț").replace("Ţ", "Ț"))

    def _parse_kml_name(self, name: str) -> tuple[str, str]:
        """
        Parse city and address from KML name field.
        Format: 'Aurora Multimarket, [City], [Address...]'
        """
        # Strip brand prefix
        clean = re.sub(r"^Aurora\s+Multimarket\s*[,\s]+", "", name, flags=re.IGNORECASE).strip()
        clean = re.sub(r"^Aurora\s*[,\s]+", "", clean, flags=re.IGNORECASE).strip()
        # Normalize diacritics (ş→ș, ţ→ț)
        clean_norm = self._normalize_ro(clean)

        # Find city using known Romanian cities (longest match first)
        city = ""
        m = _RO_CITY_RE.search(clean_norm)
        if m:
            city = m.group(0)
            # Address = everything after first occurrence of city name, trimmed
            after_city = clean_norm[m.end():].strip().lstrip(",").strip()
            # Remove city repeat, postal codes, country names from end
            after_city = re.sub(
                r",?\s*" + re.escape(city) + r"\s*\d{0,6}\s*[,]?\s*$",
                "", after_city, flags=re.IGNORECASE,
            ).strip()
            after_city = re.sub(
                r",?\s*(Румыния|Romania|România)\s*$", "", after_city, flags=re.IGNORECASE
            ).strip().strip(",").strip()
            # If nothing after city, try text before city as address
            if not after_city:
                before_city = clean_norm[:m.start()].strip().strip(",").strip()
                after_city = before_city
            address = after_city
        else:
            # No known city found — split on first comma
            parts = [p.strip() for p in clean_norm.split(",", 1)]
            city = parts[0]
            address = parts[1] if len(parts) > 1 else ""
            address = re.sub(
                r",?\s*(Румыния|Romania|România)\s*$", "", address, flags=re.IGNORECASE
            ).strip()

        return city, address

    def _extract_kml_notes(self, desc: str) -> str:
        """Extract store number from KML description."""
        clean = re.sub(r"<[^>]+>", " ", desc)
        m = re.search(r"(A-\d{4}\s*RO)", clean)
        return m.group(1).strip() if m else ""

    def _parse_description(self, desc: str, name: str) -> tuple[str, str]:
        """Legacy helper for non-KML sources."""
        clean = re.sub(r"<[^>]+>", " ", desc).strip()
        lines = [l.strip() for l in clean.splitlines() if l.strip()]
        address = lines[0] if lines else ""
        city = ""
        m = _RO_CITY_RE.search(clean)
        if m:
            city = m.group(0)
        elif lines:
            city = lines[-1] if len(lines) > 1 else ""
        return city, address

    def _playwright_scrape(self) -> list[StoreRecord]:
        """Playwright fallback for JS-heavy map pages."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error("Playwright not installed. Run: playwright install chromium")
            return []

        logger.info("Starting Playwright scrape for Aurora store map")
        stores = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=HEADLESS)
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=HEADERS["User-Agent"],
            )
            page = context.new_page()
            captured_requests = []

            def intercept(route, request):
                url = request.url
                if any(k in url for k in ["api", "store", "location", "marker", "json", "map"]):
                    captured_requests.append(url)
                route.continue_()

            page.route("**/*", intercept)

            try:
                page.goto(AURORA_STORE_MAP_URL, timeout=60000, wait_until="networkidle")
                time.sleep(3)
                html = page.content()
                stores = self._parse_html(html)

                if not stores:
                    # Try capturing XHR responses
                    api_data = page.evaluate("""() => {
                        const candidates = [];
                        // check window vars
                        for (const key of Object.keys(window)) {
                            const val = window[key];
                            if (Array.isArray(val) && val.length > 0) {
                                const first = val[0];
                                if (first && typeof first === 'object' &&
                                    ('lat' in first || 'latitude' in first || 'Lat' in first)) {
                                    candidates.push({key, val});
                                }
                            }
                        }
                        return candidates;
                    }""")
                    for item in (api_data or []):
                        parsed = self._parse_marker_list(item.get("val", []))
                        if parsed:
                            logger.info(f"Found {len(parsed)} stores in window.{item['key']}")
                            stores.extend(parsed)

                if not stores:
                    # Click all markers to collect popup data
                    markers = page.query_selector_all("[data-lat], .leaflet-marker-icon, .gm-style img[src*='marker']")
                    logger.info(f"Found {len(markers)} map markers via selector")
                    today = date.today().isoformat()
                    for marker in markers[:200]:
                        try:
                            marker.click(timeout=2000)
                            time.sleep(0.5)
                            popup = page.query_selector(".leaflet-popup-content, .gm-style-iw, .info-window")
                            if popup:
                                text = popup.inner_text()
                                city, address = self._parse_description(text, "Aurora")
                                sid = _generate_store_id("Aurora", city, None, None)
                                stores.append(StoreRecord(
                                    store_id=sid, name="Aurora", city=city,
                                    address=address, latitude=None, longitude=None,
                                    source_url=AURORA_STORE_MAP_URL,
                                    first_seen_date=today, last_seen_date=today,
                                    status="active",
                                    notes="lat/lon not captured from popup",
                                ))
                        except Exception:
                            continue

            except Exception as e:
                logger.error(f"Playwright error: {e}")
            finally:
                browser.close()

        logger.info(f"Playwright scrape returned {len(stores)} stores")
        return stores

    def scrape(self) -> list[StoreRecord]:
        logger.info(f"Scraping Aurora store map: {AURORA_STORE_MAP_URL}")
        try:
            html = self._fetch_page(AURORA_STORE_MAP_URL)
            stores = self._parse_html(html)
        except Exception as e:
            logger.warning(f"Static fetch failed: {e}")
            stores = []

        if not stores:
            logger.info("Falling back to Playwright scraper")
            stores = self._playwright_scrape()

        # Deduplicate by location_key
        seen = {}
        unique = []
        for s in stores:
            key = s.location_key()
            if key not in seen:
                seen[key] = True
                unique.append(s)

        logger.info(f"Total unique stores scraped: {len(unique)}")
        time.sleep(REQUEST_DELAY)
        return unique


def scrape_aurora_map() -> list[dict]:
    scraper = AuroraMapScraper()
    stores = scraper.scrape()
    return [s.to_dict() for s in stores]
