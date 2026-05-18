"""
Broad web intelligence scraper.
Uses DuckDuckGo to discover Romanian retail expansion signals across the web —
not limited to hardcoded sources.

Signal classes (9-class taxonomy):
  aurora_confirmed    — from aurora-retail.com or strong Aurora mention in title
  aurora_mentioned    — article mentions Aurora; not official source
  competitor_expansion— Pepco/TEDi/KiK/Action opening news
  retail_park         — retail park construction/opening announcement
  mall_leasing        — tenant, leasing, chiriaș news
  local_news          — city/regional news about expansion
  influencer_signal   — social media / influencer content
  generic_market      — general retail expansion in Romania
  noise               — filtered out; not stored
"""
import re
import time
from datetime import date
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from src.config import HEADERS, REQUEST_TIMEOUT, REQUEST_DELAY, setup_logging

logger = setup_logging("scraper.web_intelligence")

# ── Search queries ─────────────────────────────────────────────────────────────

WEB_SEARCH_QUERIES = [
    # Aurora-specific — highest priority
    {"term": "Aurora Multimarket Romania deschidere", "priority": 1},
    {"term": "Aurora Romania magazin nou", "priority": 1},
    {"term": "Aurora Romania expansiune retea", "priority": 1},
    # Competitor openings
    {"term": "Pepco Romania deschidere magazin", "priority": 2},
    {"term": "TEDi Romania opening", "priority": 2},
    {"term": "KiK Romania extindere", "priority": 2},
    {"term": "Action Romania magazin nou", "priority": 2},
    {"term": "discount retailer Romania extindere", "priority": 2},
    # Retail real estate
    {"term": "retail park Romania deschidere 2025", "priority": 3},
    {"term": "parc de retail Romania inaugurat", "priority": 3},
    {"term": "shopping center Romania opening 2025", "priority": 3},
    {"term": "mall opening Romania", "priority": 3},
    # Leasing / tenants
    {"term": "chiriaș nou centru comercial Romania", "priority": 3},
    {"term": "retail leasing Romania spatii comerciale", "priority": 3},
    {"term": "new tenant mall Romania", "priority": 3},
    # General market
    {"term": "deschidere magazin Romania 2025", "priority": 4},
    {"term": "magazin nou Romania inaugurare", "priority": 4},
    {"term": "centru comercial Romania deschidere", "priority": 4},
    {"term": "inaugurare magazin Romania", "priority": 4},
]

# Only run the top-N queries per run to keep pipeline fast
_MAX_QUERIES = 12
_MAX_RESULTS_PER_QUERY = 5
_FETCH_ARTICLE_FOR_CLASSES = {"aurora_confirmed", "aurora_mentioned"}

# ── Domain → default signal class ─────────────────────────────────────────────

_DOMAIN_CLASS: dict[str, str] = {
    "aurora-retail.com": "aurora_confirmed",
    "retail.ro": "aurora_mentioned",
    "profit.ro": "local_news",
    "economica.net": "local_news",
    "zf.ro": "local_news",
    "business-review.eu": "local_news",
    "ziare.com": "local_news",
    "digi24.ro": "local_news",
    "stirileprotv.ro": "local_news",
    "antena3.ro": "local_news",
    "imopedia.ro": "mall_leasing",
    "stiriimobiliare.ro": "mall_leasing",
    "spatiicomerciale.ro": "mall_leasing",
    "instagram.com": "influencer_signal",
    "tiktok.com": "influencer_signal",
    "facebook.com": "noise",
    "twitter.com": "noise",
    "x.com": "noise",
    "linkedin.com": "noise",
    "wikipedia.org": "noise",
}

# ── Classification keywords ───────────────────────────────────────────────────

_AURORA_STRONG = {"aurora multimarket", "aurora-retail", "aurora retail"}
_AURORA_WEAK = {"aurora"}
_COMPETITOR_KW = {"pepco", "tedi", "kik", "action discount", "action store"}
_RETAIL_PARK_KW = {"retail park", "parc de retail", "parc comercial", "commercial park", "open-air", "open air"}
_MALL_LEASING_KW = {"chirias", "chiriași", "tenant", "leasing", "inchiriere", "închiriere",
                    "galerie comerciala", "galerie comercială", "spatii comerciale", "spații comerciale"}
_LOCAL_NEWS_KW = {"judet", "județ", "primarie", "primărie", "consiliu local", "oras", "oraș"}
_INFLUENCER_KW = {"instagram", "tiktok", "@", "influencer", "vlog"}

_EXPANSION_GATE = {
    "retail", "magazin", "store", "mall", "parc", "comercial", "deschid",
    "opening", "extindere", "expansion", "tenant", "chirias", "pepco", "tedi",
    "kik", "aurora", "spatii", "inchiriere", "centru comercial", "magazin nou",
    "parc de retail", "retail park",
}

_HARD_EXCLUDE = {
    "nato", "militar", "presedinte", "premier", "alegeri", "electoral",
    "vaccin", "covid", "pandemie", "banca nationala", "curs valutar",
    "bursa", "stock exchange", "nasdaq", "amazon global", "alibaba",
}

_ROMANIAN_CITIES = [
    "bucurești", "cluj", "timișoara", "timisoara", "iași", "iasi",
    "constanța", "constanta", "craiova", "brașov", "brasov", "galați",
    "galati", "ploiești", "ploiesti", "oradea", "brăila", "braila",
    "arad", "pitești", "pitesti", "sibiu", "bacău", "bacau",
    "suceava", "deva", "alba iulia", "baia mare", "buzău", "buzau",
    "satu mare", "botoșani", "botosani", "râmnicu vâlcea", "piatra neamț",
    "bistrița", "tulcea", "giurgiu", "alexandria", "zalău", "focșani",
    "vaslui", "roman", "turda", "lugoj", "mediaș",
]
_CITY_RE = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in _ROMANIAN_CITIES) + r")\b",
    re.IGNORECASE,
)
_ROMANIA_KW = {"romania", "românia", "românesc", "romanesc", "ro-ro"}


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def _extract_cities(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0) for m in _CITY_RE.finditer(text)))


def _is_noise(title: str, body: str) -> bool:
    t = (title + " " + body).lower()
    if any(kw in title.lower() for kw in _HARD_EXCLUDE):
        return True
    has_expansion = any(kw in t for kw in _EXPANSION_GATE)
    return not has_expansion


def _classify(title: str, body: str, url: str) -> str:
    t = (title + " " + body).lower()
    dom = _domain(url)

    # Domain-based override
    if dom in _DOMAIN_CLASS:
        dom_class = _DOMAIN_CLASS[dom]
        if dom_class == "noise":
            return "noise"
        if dom_class == "aurora_confirmed":
            return "aurora_confirmed"

    # Aurora
    if any(kw in t for kw in _AURORA_STRONG):
        return "aurora_confirmed" if "aurora-retail.com" in url else "aurora_mentioned"
    if "aurora" in t and any(kw in t for kw in _EXPANSION_GATE):
        return "aurora_mentioned"

    # Competitors
    if any(kw in t for kw in _COMPETITOR_KW):
        return "competitor_expansion"

    # Real estate
    if any(kw in t for kw in _MALL_LEASING_KW):
        return "mall_leasing"
    if any(kw in t for kw in _RETAIL_PARK_KW):
        return "retail_park"

    # Influencer
    if dom in ("instagram.com", "tiktok.com") or any(kw in t for kw in _INFLUENCER_KW):
        return "influencer_signal"

    # Local news
    if dom_class := _DOMAIN_CLASS.get(dom):
        if dom_class == "local_news":
            return "local_news"
    if any(kw in t for kw in _LOCAL_NEWS_KW):
        return "local_news"

    return "generic_market"


def _fetch_article_excerpt(url: str, session: requests.Session) -> str:
    try:
        r = session.get(url, timeout=10, allow_redirects=True)
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.text, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = " ".join(soup.get_text(" ", strip=True).split())
        return text[:800]
    except Exception:
        return ""


# ── Main scraper ──────────────────────────────────────────────────────────────

def scrape_web_intelligence() -> list[dict]:
    """
    Search DuckDuckGo for Romanian retail expansion signals.
    Returns list of classified signal dicts, noise excluded.
    """
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            logger.error("ddgs not installed — run: pip install ddgs")
            return []

    today = date.today().isoformat()
    session = requests.Session()
    session.headers.update(HEADERS)

    all_results: list[dict] = []
    seen_urls: set[str] = set()

    queries = sorted(WEB_SEARCH_QUERIES, key=lambda q: q["priority"])[:_MAX_QUERIES]

    with DDGS() as ddgs:
        for query in queries:
            try:
                results = list(ddgs.text(
                    query["term"],
                    region="ro-ro",
                    safesearch="off",
                    max_results=_MAX_RESULTS_PER_QUERY,
                ))
            except Exception as e:
                logger.warning(f"DuckDuckGo search failed for '{query['term']}': {e}")
                results = []
                time.sleep(2)

            for r in results:
                url = r.get("href", "")
                title = r.get("title", "")
                body = r.get("body", "")

                if not url or url in seen_urls:
                    continue
                dom = _domain(url)
                if dom in ("facebook.com", "twitter.com", "x.com", "linkedin.com",
                           "wikipedia.org", "youtube.com"):
                    continue

                if _is_noise(title, body):
                    continue

                # Romania relevance check
                full_text = f"{title} {body}".lower()
                has_ro = (
                    any(kw in full_text for kw in _ROMANIA_KW)
                    or bool(_CITY_RE.search(full_text))
                )
                if not has_ro:
                    continue

                signal_class = _classify(title, body, url)
                if signal_class == "noise":
                    continue

                # Fetch article for high-value classes
                excerpt = body
                if signal_class in _FETCH_ARTICLE_FOR_CLASSES and len(body) < 200:
                    excerpt = _fetch_article_excerpt(url, session) or body

                cities = _extract_cities(f"{title} {excerpt}")
                seen_urls.add(url)

                all_results.append({
                    "title": title,
                    "url": url,
                    "excerpt": excerpt[:500],
                    "source": f"web:{dom}",
                    "source_domain": dom,
                    "signal_category": signal_class,
                    "signal_class": signal_class,
                    "cities_mentioned": cities,
                    "company": "",
                    "aurora_specific": signal_class in ("aurora_confirmed", "aurora_mentioned"),
                    "related_to_aurora": signal_class in ("aurora_confirmed", "aurora_mentioned"),
                    "scraped_date": today,
                    "published_date": today,
                    "query_term": query["term"],
                    # Compat field for diff.py classify_signal
                    "signals": {"signal_category": signal_class},
                })

            time.sleep(REQUEST_DELAY)

    from collections import Counter
    cats = Counter(r["signal_category"] for r in all_results)
    logger.info(
        f"Web intelligence: {len(all_results)} signals from {len(seen_urls)} unique URLs — "
        + ", ".join(f"{c}={n}" for c, n in cats.most_common())
    )
    return all_results
