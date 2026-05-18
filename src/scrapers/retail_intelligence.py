"""
Romanian Retail Intelligence scraper.

Replaces the narrow "Aurora-only" retail_news approach with a broad expansion
intelligence sweep: retail parks, mall leasing, competitor openings, commercial
real-estate announcements.  Articles are classified into signal categories and
enriched with city / company metadata before being returned.

Signal categories
─────────────────
  aurora_direct       — mentions Aurora / Multimarket by name
  competitor_expansion— competitor brand (Pepco / TEDi / KiK / Action) openings
  retail_park         — retail park or commercial centre construction/opening
  mall_leasing        — leasing, tenant, or anchor-store announcements
  shopping_center     — shopping centre general news with expansion angle
  generic_retail      — other relevant retail openings/expansions in Romania
"""
import re
import time
from datetime import date
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import HEADERS, REQUEST_TIMEOUT, MAX_RETRIES, REQUEST_DELAY, HEADLESS, setup_logging

logger = setup_logging("scraper.retail_intelligence")

# ── Romanian city matching (shared regex) ─────────────────────────────────────

_RO_CITIES = [
    "București", "Bucuresti", "Bucharest",
    "Cluj-Napoca", "Cluj", "Timișoara", "Timisoara",
    "Iași", "Iasi", "Constanța", "Constanta",
    "Craiova", "Brașov", "Brasov", "Galați", "Galati",
    "Ploiești", "Ploiesti", "Oradea", "Brăila", "Braila",
    "Arad", "Pitești", "Pitesti", "Sibiu", "Bacău", "Bacau",
    "Târgu Mureș", "Targu Mures", "Baia Mare",
    "Buzău", "Buzau", "Satu Mare", "Botoșani", "Botosani",
    "Râmnicu Vâlcea", "Ramnicu Valcea", "Suceava",
    "Piatra Neamț", "Piatra Neamt", "Deva", "Bistrița", "Bistrita",
    "Alba Iulia", "Tulcea", "Giurgiu", "Alexandria",
    "Zalău", "Zalau", "Focșani", "Focsani",
    "Drobeta-Turnu Severin", "Reșița", "Resita",
    "Sfântu Gheorghe", "Miercurea Ciuc", "Slobozia",
    "Vaslui", "Dej", "Roman", "Turda", "Lugoj",
    "Mediaș", "Medias", "Hunedoara", "Sighișoara",
    "Voluntari", "Chiajna", "Balotești", "Popești-Leordeni",
]

_CITY_RE = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in _RO_CITIES) + r")\b",
    re.IGNORECASE,
)

# ── Signal classification rules ───────────────────────────────────────────────

_AURORA_KW = {"aurora", "multimarket", "aurora retail", "aurora multimarket"}

_COMPETITOR_KW = {
    "pepco", "tedi", "kik", "action discount", "action store",
    "primark",  # large format; indicates active retail park demand
}

_RETAIL_PARK_KW = {
    "retail park", "parc comercial", "retail hub", "retail hub",
    "strip mall", "open-air", "open air", "commercial park",
    "parcul comercial", "centru comercial nou",
}

_MALL_LEASING_KW = {
    "chirias", "chiriași", "tenant", "anchor", "ancoră",
    "inchiriere", "închiriere", "leasing", "spatii comerciale",
    "spații comerciale", "lease agreement", "contract chirie",
    "galerie comerciala", "galerie comercială",
}

_SHOPPING_CENTER_KW = {
    "shopping center", "shopping centre", "mall", "centru comercial",
    "complex comercial", "hypermarket", "carrefour", "auchan",
}

_EXPANSION_KW = {
    "deschidere", "deschid", "inaugurare", "opening",
    "extindere", "expansion", "retea", "rețea",
    "in curand", "în curând", "coming soon",
    "relocare", "rebranding", "nou magazin", "magazine noi",
}

# Noise: reject articles dominated by these if no Romania signal present
_NOISE_KW = {
    "cruise", "luxury fashion", "louis vuitton", "chanel", "gucci",
    "hermes", "prada", "burberry", "versace", "armani",
    "wall street", "nasdaq", "nyse", "stock exchange",
    "amazon", "alibaba",  # global e-commerce not relevant unless RO angle
}

_ROMANIA_KW = {"romania", "românia", "românesc", "romanesc"}

# Articles must contain at least one of these to be kept (expansion relevance gate)
_EXPANSION_GATE_KW = {
    "retail park", "parc comercial", "shopping center", "shopping centre",
    "mall", "centru comercial", "store", "magazin", "retail",
    "deschidere", "deschid", "inaugur", "opening", "extindere", "expansion",
    "tenant", "chirias", "leasing", "inchiriere", "spatii comerciale",
    "commercial center", "magazin nou", "parc de retail",
    "pepco", "tedi", "kik", "action", "aurora", "multimarket",
    "se deschide", "a deschis", "va deschide", "va inaugura",
}

# Hard-exclude if title contains these (politics/military/finance — unrelated)
_HARD_EXCLUDE_TITLE = {
    "nato", "militar", "armata", "presedinte", "premier",
    "bcr", "bnr", "banca nationala", "curs valutar", "inflatia",
    "vaccinare", "vaccin", "covid", "pandemie",
}

# Known retail park developers / operators in Romania
_RETAIL_DEVELOPERS = {
    "scallier", "oasis", "square 7", "agora", "nova imob", "prime kapital",
    "mitiska", "cpi property", "revetas", "nai", "nepi", "nepi rockcastle",
    "cbre", "jll", "colliers", "cushman", "globalworth", "immofinanz",
    "iulius", "afi", "baneasa", "sun plaza", "palace", "park lake",
    "promenada", "winmarkt", "multiport",
}

# ── Search queries per category ───────────────────────────────────────────────

INTEL_QUERIES: list[dict] = [
    # Aurora direct — highest priority
    {"term": "aurora multimarket", "category": "aurora_direct", "priority": 1},
    {"term": "aurora retail", "category": "aurora_direct", "priority": 1},
    # Competitor expansion
    {"term": "pepco romania", "category": "competitor_expansion", "priority": 2},
    {"term": "tedi romania", "category": "competitor_expansion", "priority": 2},
    {"term": "kik romania", "category": "competitor_expansion", "priority": 2},
    {"term": "action discount romania", "category": "competitor_expansion", "priority": 2},
    # Retail park / commercial real estate
    {"term": "retail park romania", "category": "retail_park", "priority": 3},
    {"term": "parc comercial romania", "category": "retail_park", "priority": 3},
    {"term": "centru comercial deschidere", "category": "retail_park", "priority": 3},
    # Mall leasing / tenants
    {"term": "chiriasi mall romania", "category": "mall_leasing", "priority": 4},
    {"term": "spatii comerciale inchiriere", "category": "mall_leasing", "priority": 4},
    # Generic retail openings
    {"term": "deschidere magazin romania", "category": "generic_retail", "priority": 5},
    {"term": "extindere retea retail", "category": "generic_retail", "priority": 5},
    {"term": "discount retail romania", "category": "generic_retail", "priority": 5},
]

# Sources with their search URL templates
INTEL_SOURCES: list[dict] = [
    {
        "name": "Retail.ro",
        "base": "https://www.retail.ro/",
        "search_tpl": "https://www.retail.ro/?s={q}",
        "playwright": True,
        "romania_specific": True,  # all content is Romanian retail; skip Romania keyword filter
    },
    {
        "name": "Economica.net",
        "base": "https://economica.net/",
        "search_tpl": "https://economica.net/search/{q}",
        "playwright": True,
        "romania_specific": True,
    },
    {
        "name": "Profit.ro",
        "base": "https://www.profit.ro/",
        "search_tpl": "https://www.profit.ro/cautare?q={q}",
        "playwright": True,
        "romania_specific": True,
    },
    {
        "name": "ZF.ro",
        "base": "https://www.zf.ro/",
        "search_tpl": "https://www.zf.ro/?s={q}",  # /search/?q= returns 404
        "playwright": False,
        "romania_specific": True,
    },
    {
        "name": "Business Review",
        "base": "https://business-review.eu/",
        "search_tpl": "https://business-review.eu/?s={q}",
        "playwright": False,
        # Structure: div.box > div.box__content > h2.box__title > a
    },
]

# Limit per-source per-query: don't hammer a single source
_MAX_PER_QUERY = 20
# Max queries tried per source regardless of success (controls total runtime)
_QUERIES_PER_SOURCE = 5
# Playwright fallback only for these categories (high-value; worth the wait)
_PLAYWRIGHT_CATEGORIES = {"aurora_direct", "competitor_expansion"}
# ISO date pattern for extraction from arbitrary date strings
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_cities(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0) for m in _CITY_RE.finditer(text)))


_ALL_COMPANY_KW: list[tuple[str, str]] = [
    # (keyword to match, canonical display name)
    ("aurora multimarket", "Aurora Multimarket"),
    ("aurora retail", "Aurora Retail"),
    ("aurora", "Aurora"),
    ("multimarket", "Aurora"),
    ("pepco", "Pepco"),
    ("tedi", "TEDi"),
    ("kik", "KiK"),
    ("action discount", "Action"),
    ("action store", "Action"),
    ("primark", "Primark"),
    ("lidl", "Lidl"),
    ("kaufland", "Kaufland"),
    ("mega image", "Mega Image"),
    ("carrefour", "Carrefour"),
    ("auchan", "Auchan"),
    ("ikea", "IKEA"),
    ("decathlon", "Decathlon"),
    ("leroy merlin", "Leroy Merlin"),
    ("scallier", "Scallier"),
    ("oasis", "Oasis"),
    ("square 7", "Square 7"),
    ("agora mall", "Agora Mall"),
    ("agora", "Agora"),
    ("prime kapital", "Prime Kapital"),
    ("mitiska", "Mitiska REIM"),
    ("nepi rockcastle", "NEPI Rockcastle"),
    ("nepi", "NEPI"),
    ("globalworth", "Globalworth"),
    ("immofinanz", "Immofinanz"),
    ("iulius", "Iulius"),
]


def _detect_companies(text: str) -> list[str]:
    t = text.lower()
    found = []
    for kw, display in _ALL_COMPANY_KW:
        if kw in t and display not in found:
            found.append(display)
    return found


def _primary_company(title: str, excerpt: str) -> str:
    """Extract the most prominent company from title first, then excerpt. Never defaults to Aurora."""
    for text in (title, excerpt):
        t = text.lower()
        for kw, display in _ALL_COMPANY_KW:
            if kw in t:
                return display
    return ""


def _classify_category(text: str) -> str:
    t = text.lower()
    if any(kw in t for kw in _AURORA_KW):
        return "aurora_direct"
    if any(kw in t for kw in _COMPETITOR_KW):
        return "competitor_expansion"
    if any(kw in t for kw in _RETAIL_PARK_KW):
        return "retail_park"
    if any(kw in t for kw in _MALL_LEASING_KW):
        return "mall_leasing"
    if any(kw in t for kw in _SHOPPING_CENTER_KW):
        return "shopping_center"
    if any(kw in t for kw in _EXPANSION_KW):
        return "generic_retail"
    return "generic_retail"


def _is_noise(title: str, full_text: str) -> bool:
    t_title = title.lower()
    t_full = full_text.lower()

    # Hard-exclude by title keyword
    if any(kw in t_title for kw in _HARD_EXCLUDE_TITLE):
        return True

    # Reject if dominated by luxury/finance noise without Romania context
    has_noise = sum(1 for kw in _NOISE_KW if kw in t_full) >= 2
    if has_noise:
        has_ro = any(kw in t_full for kw in _ROMANIA_KW) or bool(_CITY_RE.search(full_text))
        if not has_ro:
            return True

    # Expansion gate: must mention at least one expansion-relevant keyword
    has_expansion = any(kw in t_full for kw in _EXPANSION_GATE_KW)
    return not has_expansion


def _signal_confidence(category: str, text: str) -> float:
    t = text.lower()
    base = {
        "aurora_direct": 0.80,
        "competitor_expansion": 0.45,
        "retail_park": 0.35,
        "mall_leasing": 0.30,
        "shopping_center": 0.25,
        "generic_retail": 0.15,
    }.get(category, 0.10)
    # Boost if a Romanian city is named
    if _CITY_RE.search(text):
        base = min(base + 0.10, 1.0)
    # Boost if expansion keywords present
    if any(kw in t for kw in _EXPANSION_KW):
        base = min(base + 0.05, 1.0)
    return round(base, 2)


# ── Parser ────────────────────────────────────────────────────────────────────

def _parse_pub_date(date_el, fallback: str) -> str:
    if not date_el:
        return fallback
    raw = date_el.get("datetime") or date_el.get_text(strip=True)
    if not raw:
        return fallback
    m = _ISO_DATE_RE.search(raw)
    return m.group(0) if m else fallback


def _parse_html_articles(
    html: str, source: dict, query: dict, source_url: str
) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    today = date.today().isoformat()
    name = source.get("name", "")
    articles = []

    # Site-specific container selection
    if name == "Retail.ro":
        # Structure: div.listingArticle > [a.imgWrap (link), div.articleText > h2.title + p.date]
        containers = soup.find_all("div", class_="listingArticle")
    elif name == "ZF.ro":
        containers = (
            soup.find_all("article")
            or soup.find_all("div", class_=re.compile(r"article|stire|news|item|post|card|zf-", re.I))
            or soup.find_all("div", class_=re.compile(r"entry|result|teaser", re.I))
        )
    elif name == "Economica.net":
        containers = (
            soup.find_all("article")
            or soup.find_all("div", class_=re.compile(r"article|news|stire|card|item|post|entry", re.I))
        )
    elif name == "Profit.ro":
        containers = (
            soup.find_all("article")
            or soup.find_all("div", class_=re.compile(r"article|news|stire|item|result|listing", re.I))
        )
    elif name == "Business Review":
        # div.box > div.box__content > h2.box__title > a
        containers = soup.find_all("div", class_="box")
    else:
        containers = (
            soup.find_all("article")
            or soup.find_all("div", class_=re.compile(r"article|post|news[-_]?item|story|card", re.I))
            or soup.find_all("li", class_=re.compile(r"article|post|news|item", re.I))
        )

    logger.debug(f"{name}: {len(containers)} containers in {len(html)} bytes")

    for el in containers[:_MAX_PER_QUERY]:
        try:
            if name == "Retail.ro":
                # Link lives on a.imgWrap (sibling of articleText), not inside the heading
                title_tag = el.find(class_="title") or el.find(["h1", "h2", "h3", "h4"])
                link_el = el.find("a", class_="imgWrap") or el.find("a", href=True)
                date_el = el.find("p", class_="date") or el.find("time")
            else:
                title_tag = el.find(["h1", "h2", "h3", "h4"])
                link_el = (title_tag.find("a") if title_tag else None) or el.find("a", href=True)
                date_el = el.find("time") or el.find(class_=re.compile(r"date|time|published", re.I))

            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            if not title or len(title) < 10:
                continue

            url = ""
            if link_el and link_el.get("href"):
                href = link_el["href"]
                url = href if href.startswith("http") else urljoin(source["base"], href)

            pub_date = _parse_pub_date(date_el, today)

            excerpt_el = el.find(class_=re.compile(r"excerpt|summary|desc|lead|intro|teaser", re.I))
            excerpt = excerpt_el.get_text(strip=True)[:500] if excerpt_el else ""

            full_text = f"{title} {excerpt}"

            if _is_noise(title, full_text):
                continue

            # Romania-specific sources: trust the source; no keyword filter needed
            if not source.get("romania_specific"):
                has_ro = (
                    any(kw in full_text.lower() for kw in _ROMANIA_KW)
                    or bool(_CITY_RE.search(full_text))
                    or any(kw in full_text.lower() for kw in _AURORA_KW)
                )
                if not has_ro:
                    continue

            category = _classify_category(full_text)
            cities = _extract_cities(full_text)
            companies = _detect_companies(full_text)
            confidence = _signal_confidence(category, full_text)
            primary_co = _primary_company(title, excerpt)
            aurora_specific = category == "aurora_direct"
            related_to_aurora = aurora_specific or any(
                kw in full_text.lower() for kw in _AURORA_KW
            )

            articles.append({
                "title": title,
                "url": url,
                "published_date": pub_date,
                "excerpt": excerpt,
                "source": source["name"],
                "source_url": source_url,
                "scraped_date": today,
                "signal_category": category,
                "cities_mentioned": cities,
                "companies_mentioned": companies,
                "company": primary_co,
                "aurora_specific": aurora_specific,
                "related_to_aurora": related_to_aurora,
                "confidence": confidence,
                "query_term": query["term"],
                # legacy compat for diff.py classify_signal
                "signals": {
                    "aurora_mentioned": aurora_specific,
                    "signal_category": category,
                },
            })
        except Exception as e:
            logger.debug(f"Article parse error ({source['name']}): {e}")

    return articles


# ── Scraper ───────────────────────────────────────────────────────────────────

class RetailIntelligenceScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    @retry(stop=stop_after_attempt(MAX_RETRIES), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _fetch(self, url: str) -> str:
        resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.text

    def _fetch_playwright(self, url: str, source_name: str) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return ""
        logger.info(f"Playwright for {source_name}: {url}")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=HEADLESS)
            page = browser.new_page(extra_http_headers=HEADERS)
            try:
                page.goto(url, timeout=60000, wait_until="networkidle")
                time.sleep(2)
                return page.content()
            except Exception as e:
                logger.warning(f"Playwright {source_name}: {e}")
                return ""
            finally:
                browser.close()

    def _search(self, source: dict, query: dict) -> list[dict]:
        q = query["term"].replace(" ", "+")
        url = source["search_tpl"].format(q=q)
        html = ""

        if source.get("playwright_always"):
            # JS-rendered source — skip static fetch entirely
            html = self._fetch_playwright(url, source["name"])
        else:
            try:
                html = self._fetch(url)
            except Exception:
                pass

        articles = _parse_html_articles(html, source, query, url) if html else []

        # Playwright fallback for high-value categories on non-always-playwright sources
        if (
            not articles
            and not source.get("playwright_always")
            and source.get("playwright")
            and query["category"] in _PLAYWRIGHT_CATEGORIES
        ):
            html = self._fetch_playwright(url, source["name"])
            if html:
                articles = _parse_html_articles(html, source, query, url)

        return articles

    def scrape(self) -> list[dict]:
        all_articles: list[dict] = []
        seen_urls: set[str] = set()

        # Run top-priority queries first; cap per source to control runtime
        sorted_queries = sorted(INTEL_QUERIES, key=lambda q: q["priority"])

        for source in INTEL_SOURCES:
            queries_tried = 0
            for query in sorted_queries:
                if queries_tried >= _QUERIES_PER_SOURCE:
                    break
                articles = self._search(source, query)
                queries_tried += 1  # always increment — count attempts not successes
                for a in articles:
                    u = a.get("url", "")
                    if u and u not in seen_urls:
                        seen_urls.add(u)
                        all_articles.append(a)
                time.sleep(REQUEST_DELAY)

        # Summary by category
        from collections import Counter
        cats = Counter(a["signal_category"] for a in all_articles)
        logger.info(
            f"Retail intelligence: {len(all_articles)} signals — "
            + ", ".join(f"{c}={n}" for c, n in cats.most_common())
        )
        return all_articles


def scrape_retail_intelligence() -> list[dict]:
    return RetailIntelligenceScraper().scrape()
