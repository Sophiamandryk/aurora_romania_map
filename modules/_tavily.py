"""Shared Tavily search helper for modules."""
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from urllib.parse import urlparse

from src.config import TAVILY_API_KEY, REQUEST_TIMEOUT, setup_logging

logger = setup_logging("modules.tavily")

_URL   = "https://api.tavily.com/search"
_DELAY = 0.35

# Domains blocked because they require login/subscription or return irrelevant content.
# Tavily can only get their snippet from Google's index — the actual page is inaccessible.
_BLOCKED_DOMAINS = {
    # ── Paid data / research platforms ───────────────────────────────────────
    "statista.com",         # requires free account for any data
    "euromonitor.com",      # subscription only
    "bloomberg.com",        # paywall
    "ft.com",               # Financial Times — paywall
    "wsj.com",              # Wall Street Journal — paywall
    "economist.com",        # paywall
    "businessinsider.com",  # metered paywall
    "hbr.org",              # Harvard Business Review — paywall
    # mckinsey.com — public Insights & reports accessible without login
    # pwc.com      — public reports and press releases accessible
    # deloitte.com — public reports accessible
    # ey.com       — public reports accessible
    # kpmg.com     — public reports accessible
    "gartner.com",          # subscription
    "forrester.com",        # subscription
    "idc.com",              # subscription
    # ── Academic / research repositories ─────────────────────────────────────
    "researchgate.net",     # requires login for full text
    "academia.edu",         # requires login
    "jstor.org",            # paywall
    "sciencedirect.com",    # paywall
    "springer.com",         # paywall
    "tandfonline.com",      # paywall
    # ── Social media (no scrapeable article content) ──────────────────────────
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "youtube.com",
    "linkedin.com",
    # ── E-commerce / job boards (irrelevant as news sources) ─────────────────
    "amazon.com",
    "ebay.com",
    "indeed.com",
    "glassdoor.com",
}


def _parse_date(s: str):
    """Return datetime if parseable, else None."""
    if not s:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d", "%B %d, %Y", "%d %B %Y",
    ):
        try:
            return datetime.strptime(s.strip()[:25], fmt)
        except ValueError:
            continue
    return None


def _is_blocked(url: str) -> bool:
    """Return True if URL is from a known paywall/login/irrelevant domain."""
    try:
        domain = urlparse(url).netloc.lower().replace("www.", "")
        return any(domain == d or domain.endswith("." + d) for d in _BLOCKED_DOMAINS)
    except Exception:
        return False


def _snippet_ok(snippet: str) -> bool:
    """Snippet must have at least 200 chars — shorter ones are usually paywalled/empty."""
    return len((snippet or "").strip()) >= 200


def _token_overlap(a: str, b: str) -> float:
    """Jaccard similarity on word tokens."""
    wa = set(a.lower().split())
    wb = set(b.lower().split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _is_duplicate(result: dict, accepted: list[dict], threshold: float = 0.70) -> bool:
    """True if this result's snippet overlaps ≥70% with any already-accepted result."""
    snippet = result.get("snippet") or ""
    for r in accepted:
        if _token_overlap(snippet, r.get("snippet") or "") >= threshold:
            return True
    return False


_NOT_FOUND_SIGNALS = [
    "page not found", "404 not found", "не знайдено", "pagina negasita",
    "pagina nu a fost gasita", "this page does not exist",
    "the page you requested", "couldn't find this page",
    "page doesn't exist", "nothing here", "no page found",
]
_LOGIN_SIGNALS = [
    "sign in to continue", "log in to read", "subscribe to access",
    "create a free account", "register to view", "members only",
    "please login", "access denied", "you must be logged in",
]
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def _check_accessible(url: str, timeout: int = 5) -> bool:
    """
    HEAD request — fast check that the URL exists and isn't a 4xx/5xx.
    For known custom-404 domains (returns 200 for missing pages) we do a
    lightweight GET and scan the first 4KB for "not found" signals.
    """
    _CUSTOM_404_DOMAINS = {"gradus.app", "medium.com", "substack.com"}
    try:
        domain = urlparse(url).netloc.lower().replace("www.", "")
        use_get = any(domain == d or domain.endswith("." + d) for d in _CUSTOM_404_DOMAINS)

        if use_get:
            with requests.get(url, timeout=timeout, allow_redirects=True,
                              headers=_HEADERS, stream=True) as resp:
                if resp.status_code >= 400:
                    return False
                chunk = b""
                for data in resp.iter_content(chunk_size=4096):
                    chunk = data
                    break
                body = chunk.decode("utf-8", errors="replace").lower()
            if any(sig in body for sig in _NOT_FOUND_SIGNALS):
                return False
        else:
            resp = requests.head(url, timeout=timeout, allow_redirects=True,
                                 headers=_HEADERS)
            if resp.status_code >= 400:
                return False

        return True
    except Exception:
        return False


def _check_accessible_batch(urls: list[str], timeout: int = 6, workers: int = 8) -> dict[str, bool]:
    """Check a list of URLs concurrently. Returns {url: is_accessible}."""
    results: dict[str, bool] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {pool.submit(_check_accessible, u, timeout): u for u in urls}
        for future in as_completed(future_map):
            url = future_map[future]
            try:
                results[url] = future.result()
            except Exception:
                results[url] = False
    return results


def validate_results(
    results: list[dict],
    check_http: bool = False,
) -> list[dict]:
    """
    Quality-filter Tavily results for AI analysis use.

    Checks applied:
      1. Snippet ≥ 100 chars  (empty / paywall pages have short snippets)
      2. Not a blocked domain (paid platforms, social media, e-commerce)
      3. Not a near-duplicate (Jaccard token overlap ≥ 70%)
      4. HTTP check (optional, default OFF) — Tavily URLs are often fabricated/dead;
         disabling avoids dropping valid snippet content just because the URL 404s.
         Use filter_live_sources() separately when building user-facing links.
    """
    pre: list[dict] = []
    for r in results:
        url     = r.get("url") or ""
        snippet = r.get("snippet") or ""

        if not _snippet_ok(snippet):
            logger.debug(f"Dropped (snippet {len(snippet)}ch < 100): {url[:80]}")
            continue
        if _is_blocked(url):
            logger.info(f"Dropped (blocked domain): {url[:80]}")
            continue
        if _is_duplicate(r, pre):
            logger.debug(f"Dropped (duplicate): {url[:80]}")
            continue
        pre.append(r)

    if check_http and pre:
        urls = [r["url"] for r in pre]
        accessible = _check_accessible_batch(urls)
        accepted = [r for r in pre if accessible.get(r["url"], True)]
        dropped_http = len(pre) - len(accepted)
        if dropped_http:
            logger.info(f"HTTP check dropped {dropped_http} URLs")
        pre = accepted

    dropped = len(results) - len(pre)
    if dropped:
        logger.info(f"validate_results: kept {len(pre)}/{len(results)} ({dropped} dropped)")
    return pre


def filter_live_sources(sources: list[dict], max_sources: int = 3) -> list[dict]:
    """
    HTTP-check a list of {title, url} source dicts and return only live ones.
    Use this when building user-facing links — not for AI analysis input.
    """
    if not sources:
        return []
    urls = [s["url"] for s in sources if s.get("url")]
    accessible = _check_accessible_batch(urls)
    live = [s for s in sources if accessible.get(s.get("url", ""), False)]
    return live[:max_sources]


def search(query: str, days: int, n: int = 5, domains: list = None) -> list[dict]:
    """Single Tavily call. Returns [{title, url, snippet, published_date}]."""
    if not TAVILY_API_KEY:
        return []
    payload = {
        "api_key":      TAVILY_API_KEY,
        "query":        query,
        "search_depth": "basic",
        "max_results":  n,
        "days":         days,
    }
    if domains:
        payload["include_domains"] = domains
    try:
        resp = requests.post(_URL, json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        out = []
        for r in resp.json().get("results", []):
            if r.get("url"):
                out.append({
                    "title":          r.get("title", ""),
                    "url":            r.get("url", ""),
                    "snippet":        (r.get("content") or r.get("snippet", ""))[:500],
                    "published_date": r.get("published_date", ""),
                    "_source":        "tavily",
                })
        return out
    except Exception as e:
        logger.warning(f"Tavily '{query[:60]}': {e}")
        return []
    finally:
        time.sleep(_DELAY)


def collect(
    queries: list[str],
    fallback_windows: list[int],
    min_n: int = 3,
    domains: list = None,
    require_date: bool = False,
    max_age_days: int = None,
    min_year: int = None,
) -> tuple[list[dict], int]:
    """
    Run queries through increasing day-windows until min_n quality results found.
    Returns (quality-filtered results, actual_days_used).

    min_year: reject articles from before this year (e.g. min_year=2026 drops 2025 articles).
    max_age_days: reject articles older than N days (applied only when date is parseable).
    """
    cutoff = datetime.utcnow() - timedelta(days=max_age_days) if max_age_days else None
    last_results: list[dict] = []
    last_days: int = fallback_windows[-1]

    for days in fallback_windows:
        seen: set[str] = set()
        raw: list[dict] = []
        for q in queries:
            for r in search(q, days=days, domains=domains):
                if r["url"] in seen:
                    continue
                if require_date and not _parse_date(r["published_date"]):
                    continue
                if cutoff or min_year:
                    parsed = _parse_date(r.get("published_date", ""))
                    if parsed:
                        # Date available — apply filters
                        if cutoff and parsed < cutoff:
                            continue
                        if min_year and parsed.year < min_year:
                            continue
                    elif require_date:
                        continue  # no date, but caller explicitly needs one
                seen.add(r["url"])
                raw.append(r)

        results = validate_results(raw)

        if len(results) >= min_n:
            return results, days
        last_results, last_days = results, days

    return last_results, last_days
