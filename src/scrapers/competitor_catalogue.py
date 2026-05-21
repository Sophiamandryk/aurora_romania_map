"""
Competitor promo/catalogue page scraper.
Fetches live promotional content from 7 competitor websites and returns
structured commercial intelligence per brand.

Tracks: current promos, featured categories, pricing signals,
seasonal pushes, and any opening/expansion mentions.
"""
import re
import time
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from src.config import HEADERS, REQUEST_TIMEOUT, REQUEST_DELAY, HEADLESS, setup_logging

logger = setup_logging("scraper.competitor_catalogue")

CATALOGUE_SOURCES = [
    {"brand": "Pepco",  "url": "https://pepco.ro/colectii",                        "playwright": False},
    {"brand": "TEDi",   "url": "https://www.tedi.com/ro/oferte/",                  "playwright": True},
    {"brand": "KiK",    "url": "https://www.kik.ro/c/oferte/",                     "playwright": True},
    {"brand": "Action", "url": "https://www.action.com/ro-ro/oferta-saptamanii/",  "playwright": True},
    {"brand": "Penny",  "url": "https://www.penny.ro/marci-proprii",               "playwright": False},
    {"brand": "Profi",  "url": "https://www.profi.ro/oferte/",                     "playwright": True},
    {"brand": "Mr.DIY", "url": "https://www.mrdiy.com/ro/our-products",            "playwright": False},
]

_DATE_RE     = re.compile(r"\d{1,2}[.\-/]\d{1,2}(?:[.\-/]\d{2,4})?")
_DISCOUNT_RE = re.compile(r"(?:pana la\s*|până la\s*)?[-–]?\s*(\d{1,2})\s*%", re.IGNORECASE)
_OPENING_KW  = re.compile(
    r"\b(deschid|inaugur|opening|magazin nou|extindem|new store|coming soon|locatie noua)\b",
    re.IGNORECASE,
)

_CATEGORY_TERMS = [
    "home", "garden", "gradina", "grădină", "toys", "jucarii", "jucărie",
    "kids", "copii", "clothing", "haine", "îmbrăcăminte", "imbracaminte",
    "textile", "electronics", "electrocasnice", "cleaning", "curățenie",
    "curatenie", "food", "pet", "sport", "beauty", "cosmetice", "seasonal",
    "sezon", "decor", "stationery", "papetarie", "tools", "diy", "bricolaj",
    "mobilier", "furniture", "outdoor", "gradinarit",
]
_CAT_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _CATEGORY_TERMS) + r")\b",
    re.IGNORECASE,
)


def _extract_categories(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0).lower() for m in _CAT_RE.finditer(text)))


def _extract_dates(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0) for m in _DATE_RE.finditer(text)))[:5]


def _extract_discounts(text: str) -> list[str]:
    return list(dict.fromkeys(f"-{m.group(1)}%" for m in _DISCOUNT_RE.finditer(text)))[:5]


def _parse_page(html: str, brand: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    today = date.today().isoformat()

    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    containers = (
        soup.find_all(class_=re.compile(
            r"offer|promo|leaflet|flyer|catalog|catalogue|deal|discount|weekly|card|item|product",
            re.I,
        ))
        or soup.find_all("article")
        or soup.find_all("li", class_=re.compile(r"item|card|product|offer", re.I))
    )

    promos: list[dict] = []
    for el in containers[:20]:
        text = el.get_text(separator=" ", strip=True)
        if len(text) < 15:
            continue
        title_el = el.find(["h1", "h2", "h3", "h4", "h5", "strong"])
        title = title_el.get_text(strip=True)[:100] if title_el else text[:80]
        link_el = el.find("a", href=True)
        item_url = ""
        if link_el:
            href = link_el["href"]
            item_url = href if href.startswith("http") else urljoin(url, href)
        promos.append({
            "title": title,
            "url": item_url,
            "dates": _extract_dates(text),
            "discounts": _extract_discounts(text),
            "categories": _extract_categories(text),
            "has_opening": bool(_OPENING_KW.search(text)),
        })

    full_text = soup.get_text(separator=" ", strip=True)
    return {
        "brand": brand,
        "source_url": url,
        "scraped_date": today,
        "promo_count": len(promos),
        "promos": promos[:10],
        "page_dates": _extract_dates(full_text),
        "page_discounts": _extract_discounts(full_text),
        "page_categories": _extract_categories(full_text)[:8],
        "has_opening_signal": bool(_OPENING_KW.search(full_text)),
        "full_text_snippet": full_text[:600],
    }


class CatalogueScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def _fetch(self, url: str) -> str:
        try:
            r = self.session.get(url, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            return r.text
        except Exception as e:
            logger.warning(f"HTTP fetch failed {url}: {e}")
            return ""

    def _fetch_playwright(self, url: str, brand: str) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return ""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=HEADLESS)
            page = browser.new_page(extra_http_headers=HEADERS)
            try:
                page.goto(url, timeout=60000, wait_until="networkidle")
                time.sleep(2)
                return page.content()
            except Exception as e:
                logger.warning(f"Playwright {brand}: {e}")
                return ""
            finally:
                browser.close()

    def scrape_one(self, source: dict) -> dict:
        brand = source["brand"]
        url   = source["url"]
        html  = self._fetch(url)
        if not html and source.get("playwright"):
            html = self._fetch_playwright(url, brand)
        if not html:
            logger.warning(f"No content fetched for {brand}")
            return {
                "brand": brand, "source_url": url,
                "scraped_date": date.today().isoformat(),
                "promo_count": 0, "promos": [],
                "page_dates": [], "page_discounts": [],
                "page_categories": [], "has_opening_signal": False,
                "full_text_snippet": "", "error": "fetch_failed",
            }
        result = _parse_page(html, brand, url)
        logger.info(
            f"{brand}: {result['promo_count']} promos | "
            f"cats={result['page_categories'][:3]} | "
            f"discounts={result['page_discounts'][:3]}"
        )
        return result

    def scrape(self) -> list[dict]:
        results = []
        for source in CATALOGUE_SOURCES:
            try:
                results.append(self.scrape_one(source))
            except Exception as e:
                logger.error(f"Catalogue scrape error {source['brand']}: {e}")
            time.sleep(REQUEST_DELAY)
        return results


def scrape_competitor_catalogues() -> list[dict]:
    return CatalogueScraper().scrape()
