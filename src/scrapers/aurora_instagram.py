"""
Aurora Instagram scraper.
Uses session cookies (no API key required) to extract recent posts.
Falls back to Playwright if session is not set.
"""
import re
import time
import json
from datetime import date

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import (
    AURORA_INSTAGRAM_URL, INSTAGRAM_SESSION,
    COMPETITOR_INSTAGRAM_PROFILES,
    HEADERS, REQUEST_TIMEOUT, MAX_RETRIES, REQUEST_DELAY,
    HEADLESS, setup_logging,
)

logger = setup_logging("scraper.aurora_instagram")

# ── Opening / expansion keywords ──────────────────────────────────────────────
_OPENING_KW = [
    "deschid", "deschidere", "deschidem", "s-a deschis", "am deschis",
    "se deschide", "inaugurăm", "inaugurare", "inauguram", "lansare",
    "magazin nou", "noul magazin", "new store", "opening", "grand opening",
    "locație nouă", "locatie noua", "coming soon", "în curând", "in curand",
    "extindere", "ne extindem",
]

# ── Presence / location phrases ───────────────────────────────────────────────
_PRESENCE_KW = [
    "ne găsești", "ne gasesti", "te așteptăm", "te asteptam",
    "vino în", "vino in", "acum în", "acum in",
    "ajungem în", "ajungem in", "suntem în", "suntem in",
    "ne găsim", "ne gasim", "adresă", "adresa", "ne vedem în", "ne vedem in",
]

# ── Retail venue generic keywords ─────────────────────────────────────────────
_RETAIL_VENUE_KW = [
    "centru comercial", "parc de retail", "parc comercial",
    "retail park", "shopping center", "shopping centre", "mall",
]

# ── Named malls / retail parks in Romania ────────────────────────────────────
_MALL_NAMES = [
    "supernova", "m park", "funshop park", "prima shops", "shopping city",
    "parklake", "park lake", "plaza românia", "plaza romania",
    "afi", "iulius mall", "iulius town", "coresi", "vivo", "city park mall",
    "promenada", "agora mall", "sun plaza", "baneasa", "palace",
    "mega mall", "unirea shopping", "lotus center", "era shopping",
    "winmarkt", "arena mall", "galaxy mall", "maritimo", "river plaza",
    "nest park", "colosseum", "electroputere", "pitești mall",
]

_MALL_RE = re.compile(
    r"\b(" + "|".join(re.escape(m) for m in _MALL_NAMES) + r")\b",
    re.IGNORECASE,
)

_RETAIL_VENUE_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _RETAIL_VENUE_KW) + r")\b",
    re.IGNORECASE,
)

_OPENING_RE = re.compile(
    r"(" + "|".join(re.escape(k) for k in _OPENING_KW) + r")",
    re.IGNORECASE,
)

_PRESENCE_RE = re.compile(
    r"(" + "|".join(re.escape(k) for k in _PRESENCE_KW) + r")",
    re.IGNORECASE,
)

_ADDRESS_RE = re.compile(
    r"\b(str\.?|strada|bdul|bd\.?|aleea|calea|piața|piaţa|nr\.?)\s+\w+",
    re.IGNORECASE,
)

# ── City list ─────────────────────────────────────────────────────────────────
ROMANIAN_CITIES = [
    "București", "Cluj-Napoca", "Timișoara", "Iași", "Constanța", "Craiova",
    "Brașov", "Galați", "Ploiești", "Oradea", "Brăila", "Arad", "Pitești",
    "Sibiu", "Bacău", "Târgu Mureș", "Baia Mare", "Buzău", "Satu Mare",
    "Botoșani", "Râmnicu Vâlcea", "Suceava", "Piatra Neamț", "Deva",
    "Bistrița", "Alba Iulia", "Tulcea", "Giurgiu", "Alexandria", "Zalău",
    "Focșani", "Câmpina", "Turda", "Dej", "Roman", "Fetești", "Mangalia",
    "Slobozia", "Sfântu Gheorghe", "Reșița", "Vaslui", "Slatina", "Targoviste",
    "Drobeta-Turnu Severin", "Miercurea Ciuc", "Baia Sprie", "Lugoj",
]

_CITY_RE = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in ROMANIAN_CITIES) + r")\b",
    re.IGNORECASE,
)

# ── Signal types (ordered strongest → weakest) ────────────────────────────────
SIGNAL_TYPES = [
    "confirmed_opening_signal",
    "possible_store_location_signal",
    "mall_or_retail_park_signal",
    "city_presence_signal",
    "promo_with_city_signal",
    "generic_promo",
    "noise",
]

# Minimum score for a signal type to be considered actionable in predictions
SIGNAL_SCORE_THRESHOLD = 35


def _extract_cities(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0) for m in _CITY_RE.finditer(text)))


def _classify_post(caption: str) -> dict:
    """
    Score and classify an Instagram caption into a signal type.
    Returns dict with signal_type, signal_score, detected_malls,
    detected_locations, reason.
    """
    t = caption.lower()
    score = 0
    reasons = []

    has_opening = bool(_OPENING_RE.search(t))
    has_presence = bool(_PRESENCE_RE.search(t))
    has_address = bool(_ADDRESS_RE.search(caption))
    has_retail_venue = bool(_RETAIL_VENUE_RE.search(t))
    detected_malls = list(dict.fromkeys(
        m.group(0) for m in _MALL_RE.finditer(t)
    ))
    detected_locations = list(dict.fromkeys(
        m.group(0) for m in _ADDRESS_RE.finditer(caption)
    ))
    cities = _extract_cities(caption)

    if has_opening:
        score += 50
        reasons.append("opening keyword")
    if has_presence:
        score += 30
        reasons.append("presence phrase")
    if has_address:
        score += 25
        reasons.append("address text")
    if detected_malls:
        score += 25
        reasons.append(f"mall: {', '.join(detected_malls[:2])}")
    if has_retail_venue:
        score += 20
        reasons.append("retail venue mention")
    if cities:
        score += 15 * min(len(cities), 3)
        reasons.append(f"cities: {', '.join(cities[:3])}")
    if len(cities) > 1:
        score += 10
        reasons.append("multiple city mentions")

    # Classify
    if has_opening and score >= 50:
        signal_type = "confirmed_opening_signal"
    elif (has_presence or has_address) and cities and score >= 35:
        signal_type = "possible_store_location_signal"
    elif (detected_malls or has_retail_venue) and cities:
        signal_type = "mall_or_retail_park_signal"
    elif cities and score >= 15:
        signal_type = "city_presence_signal"
    elif cities:
        signal_type = "promo_with_city_signal"
    elif len(caption.strip()) < 20:
        signal_type = "noise"
    else:
        signal_type = "generic_promo"

    return {
        "signal_type": signal_type,
        "signal_score": score,
        "detected_malls": detected_malls,
        "detected_locations": detected_locations,
        "reason": "; ".join(reasons) if reasons else "no signals",
    }


class InstagramScraper:
    def __init__(self):
        self._loader = None

    @property
    def _api_headers(self) -> dict:
        return {
            "User-Agent": HEADERS["User-Agent"],
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "X-IG-App-ID": "936619743392459",
            "Referer": "https://www.instagram.com/",
        }

    @property
    def _cookies(self) -> dict:
        return {"sessionid": INSTAGRAM_SESSION} if INSTAGRAM_SESSION else {}

    def _get_user_id(self, username: str) -> str | None:
        """Fetch numeric user ID via web_profile_info endpoint."""
        try:
            r = requests.get(
                f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}",
                headers=self._api_headers,
                cookies=self._cookies,
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code == 200:
                return r.json().get("data", {}).get("user", {}).get("id")
        except Exception as e:
            logger.warning(f"web_profile_info failed for @{username}: {e}")
        return None

    def _fetch_feed(self, user_id: str, count: int = 20) -> list[dict]:
        """Fetch posts from private feed endpoint by user ID."""
        try:
            r = requests.get(
                f"https://www.instagram.com/api/v1/feed/user/{user_id}/?count={count}",
                headers=self._api_headers,
                cookies=self._cookies,
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code == 200:
                return r.json().get("items", [])
        except Exception as e:
            logger.warning(f"feed/user failed for id={user_id}: {e}")
        return []

    def _build_post(self, shortcode: str, url: str, caption: str,
                    timestamp: int, today: str, brand: str) -> dict:
        is_competitor = bool(brand)
        cities = _extract_cities(caption)
        classification = _classify_post(caption)
        return {
            "shortcode": shortcode,
            "url": url,
            "caption": caption[:500],
            "timestamp": timestamp,
            "published_date": today,
            "cities_mentioned": cities,
            "expansion_signals": [classification["signal_type"]]
                if classification["signal_type"] not in ("generic_promo", "noise") else [],
            "brand": brand,
            "signal_type": classification["signal_type"],
            "signal_score": classification["signal_score"],
            "detected_malls": classification["detected_malls"],
            "detected_locations": classification["detected_locations"],
            "detected_companies": [brand] if brand else ["Aurora"],
            "reason": classification["reason"],
            "signal_category": "competitor_expansion" if is_competitor else "aurora_direct",
            "company": brand if brand else "Aurora",
            "source": f"instagram_{brand.lower().replace(' ', '_')}" if brand else "instagram",
            "scraped_date": today,
        }

    def _parse_posts(self, data: dict, brand: str = "", profile_url: str = "") -> list[dict]:
        posts = []
        today = date.today().isoformat()
        try:
            edges = (
                data.get("data", {})
                .get("user", {})
                .get("edge_owner_to_timeline_media", {})
                .get("edges", [])
            )
        except AttributeError:
            return []

        for edge in edges:
            node = edge.get("node", {})
            caption_edges = node.get("edge_media_to_caption", {}).get("edges", [])
            caption = "".join(ce.get("node", {}).get("text", "") for ce in caption_edges)
            timestamp = node.get("taken_at_timestamp", 0)
            shortcode = node.get("shortcode", "")
            fallback_url = profile_url or AURORA_INSTAGRAM_URL
            url = f"https://www.instagram.com/p/{shortcode}/" if shortcode else fallback_url
            posts.append(self._build_post(shortcode, url, caption, timestamp, today, brand))
        return posts

    def _playwright_scrape(self, username: str, brand: str = "") -> list[dict]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error("Playwright not installed")
            return []

        logger.info(f"Using Playwright for Instagram @{username}")
        today = date.today().isoformat()
        profile_url = f"https://www.instagram.com/{username}/"
        captured: list[dict] = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=HEADLESS)
            context = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )
            if INSTAGRAM_SESSION:
                context.add_cookies([{
                    "name": "sessionid", "value": INSTAGRAM_SESSION,
                    "domain": ".instagram.com", "path": "/",
                    "httpOnly": True, "secure": True,
                }])
            page = context.new_page()

            # Intercept feed API responses — these carry full caption data
            def _on_response(response):
                url = response.url
                if not any(k in url for k in ("api/v1/feed/user", "api/v1/users/web_profile_info",
                                               "graphql/query")):
                    return
                try:
                    data = response.json()
                    # Feed endpoint: {"items": [...]}
                    items = data.get("items") or []
                    # Profile info endpoint: {"data": {"user": {"edge_owner_to_timeline_media": ...}}}
                    if not items:
                        items_raw = (
                            data.get("data", {})
                            .get("user", {})
                            .get("edge_owner_to_timeline_media", {})
                            .get("edges", [])
                        )
                        items = [e.get("node", {}) for e in items_raw]

                    for item in items:
                        shortcode = item.get("code") or item.get("shortcode", "")
                        ts = item.get("taken_at") or item.get("taken_at_timestamp", 0)
                        # caption: feed uses caption.text; profile edge uses edge_media_to_caption
                        cap_obj = item.get("caption") or {}
                        if isinstance(cap_obj, dict):
                            caption = cap_obj.get("text", "")
                        else:
                            caption = "".join(
                                e.get("node", {}).get("text", "")
                                for e in item.get("edge_media_to_caption", {}).get("edges", [])
                            )
                        url_post = (
                            f"https://www.instagram.com/p/{shortcode}/" if shortcode
                            else profile_url
                        )
                        if shortcode:
                            captured.append(
                                self._build_post(shortcode, url_post, caption, ts, today, brand)
                            )
                except Exception:
                    pass

            page.on("response", _on_response)
            try:
                page.goto(profile_url, timeout=60000, wait_until="networkidle")
                time.sleep(3)
                # Scroll to trigger lazy-load of more posts
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)
            except Exception as e:
                logger.error(f"Playwright Instagram error @{username}: {e}")
            finally:
                browser.close()

        # Deduplicate by shortcode
        seen: set[str] = set()
        posts = []
        for p in captured:
            sc = p.get("shortcode", "")
            if sc not in seen:
                seen.add(sc)
                posts.append(p)

        # If interception yielded nothing, fall back to post-page scraping
        if not posts:
            logger.warning(f"Response interception empty for @{username}, trying post-page fallback")
            posts = self._scrape_post_pages(username, brand, today)

        return posts

    def _scrape_post_pages(self, username: str, brand: str, today: str) -> list[dict]:
        """Last-resort fallback: navigate to each post page and extract caption."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return []

        profile_url = f"https://www.instagram.com/{username}/"
        posts = []

        _CAPTION_SELECTORS = [
            "article div._a9zs span",
            "article div[class*='caption'] span",
            "article h1",
            "div[data-testid='post-comment-root'] span",
            "article span",
        ]

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=HEADLESS)
            context = browser.new_context(user_agent=HEADERS["User-Agent"])
            if INSTAGRAM_SESSION:
                context.add_cookies([{
                    "name": "sessionid", "value": INSTAGRAM_SESSION,
                    "domain": ".instagram.com", "path": "/",
                }])
            page = context.new_page()
            try:
                page.goto(profile_url, timeout=60000, wait_until="networkidle")
                time.sleep(3)
                post_links = page.eval_on_selector_all(
                    "a[href*='/p/']", "els => [...new Set(els.map(e => e.href))]"
                )
                for link in post_links[:15]:
                    try:
                        page.goto(link, timeout=30000, wait_until="networkidle")
                        time.sleep(2)
                        caption = ""
                        for sel in _CAPTION_SELECTORS:
                            el = page.query_selector(sel)
                            if el:
                                text = el.inner_text().strip()
                                if len(text) > 10:
                                    caption = text
                                    break
                        shortcode = link.rstrip("/").split("/")[-1]
                        posts.append(self._build_post(shortcode, link, caption, 0, today, brand))
                    except Exception as e:
                        logger.debug(f"Post page fallback error @{username}: {e}")
            except Exception as e:
                logger.error(f"Post-page fallback error @{username}: {e}")
            finally:
                browser.close()
        return posts

    def scrape(self, username: str = "aurora.multimarket", brand: str = "") -> list[dict]:
        logger.info(f"Scraping Instagram @{username}{f' ({brand})' if brand else ''}")
        today = date.today().isoformat()
        posts = []

        # Primary: Instagram private API (sessionid only, no Instaloader needed)
        if INSTAGRAM_SESSION:
            user_id = self._get_user_id(username)
            if user_id:
                items = self._fetch_feed(user_id, count=20)
                for item in items:
                    shortcode = item.get("code") or item.get("shortcode", "")
                    ts = item.get("taken_at", 0)
                    cap_obj = item.get("caption") or {}
                    caption = cap_obj.get("text", "") if isinstance(cap_obj, dict) else ""
                    url = (
                        f"https://www.instagram.com/p/{shortcode}/"
                        if shortcode else f"https://www.instagram.com/{username}/"
                    )
                    posts.append(self._build_post(shortcode, url, caption, ts, today, brand))
                logger.info(f"API: got {len(posts)} posts for @{username}")

        # Fallback: Playwright response interception
        if not posts:
            logger.info(f"Falling back to Playwright for @{username}")
            posts = self._playwright_scrape(username, brand=brand)

        from collections import Counter
        type_counts = Counter(p["signal_type"] for p in posts)
        logger.info(
            f"@{username}: {len(posts)} posts — "
            + ", ".join(f"{t}: {n}" for t, n in type_counts.most_common())
        )
        time.sleep(REQUEST_DELAY)
        return posts


def scrape_aurora_instagram() -> list[dict]:
    return InstagramScraper().scrape(username="aurora.multimarket", brand="")


def scrape_competitor_instagram() -> list[dict]:
    scraper = InstagramScraper()
    all_posts: list[dict] = []
    for brand, username in COMPETITOR_INSTAGRAM_PROFILES.items():
        try:
            posts = scraper.scrape(username=username, brand=brand)
            all_posts.extend(posts)
            logger.info(f"Competitor Instagram {brand} (@{username}): {len(posts)} posts")
        except Exception as e:
            logger.warning(f"Competitor Instagram failed for {brand} (@{username}): {e}")
    return all_posts
