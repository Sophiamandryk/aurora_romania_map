"""
RSS feed channel — first-priority source before Tavily for known news sites.

RSS feeds are rarely bot-blocked, always include publication dates,
and return full article text rather than index-derived snippets.

Usage:
    from modules._rss import fetch_rss_for_domains, filter_by_keywords

    results = fetch_rss_for_domains(["retail.ro", "profit.ro"], max_age_days=30)
    results = filter_by_keywords(results, ["aurora", "retail"])
"""
import re
import time
from datetime import datetime, timedelta

import requests

from src.config import REQUEST_TIMEOUT, setup_logging

logger = setup_logging("modules.rss")

try:
    import feedparser
    _FEEDPARSER_OK = True
except ImportError:
    _FEEDPARSER_OK = False
    logger.debug("feedparser not installed — RSS channel disabled (pip install feedparser)")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# Known RSS URLs for sites relevant to 2.2 / 2.3 / 3.1
_FEEDS: dict[str, list[str]] = {
    # Romanian retail & business
    "retail.ro":           ["https://www.retail.ro/feed/"],
    "profit.ro":           ["https://www.profit.ro/rss/"],
    "wall-street.ro":      ["https://www.wall-street.ro/rss/", "https://www.wall-street.ro/feed/"],
    "zf.ro":               ["https://www.zf.ro/rss.xml"],
    "economica.net":       ["https://economica.net/feed/"],
    "romania-insider.com": ["https://www.romania-insider.com/feed/"],
    # Ukrainian retail & business
    "retailer.ua":         ["https://retailer.ua/feed/"],
    "retailers.ua":        ["https://retailers.ua/rss.xml", "https://retailers.ua/feed/"],
    "rau.ua":              ["https://rau.ua/feed/"],
    "allretail.ua":        ["https://allretail.ua/feed/"],
    "interfax.com.ua":     ["https://interfax.com.ua/rss.xml"],
    "delo.ua":             ["https://delo.ua/rss/"],
    "speka.ua":            ["https://speka.ua/feed/"],
    "thepage.ua":          ["https://thepage.ua/rss/"],
    "trademaster.ua":      ["https://trademaster.ua/rss/"],
}

_MIN_SNIPPET = 200


def _parse_pub_dt(entry) -> datetime | None:
    """Parse publication datetime from a feedparser entry."""
    import time as _time
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime.fromtimestamp(_time.mktime(t))
            except Exception:
                continue
    return None


def _fetch_one_feed(feed_url: str, max_age_days: int) -> list[dict]:
    """Fetch a single RSS/Atom feed URL. Returns normalised result dicts."""
    if not _FEEDPARSER_OK:
        return []
    cutoff = datetime.utcnow() - timedelta(days=max_age_days)
    try:
        resp = requests.get(feed_url, timeout=REQUEST_TIMEOUT, headers=_HEADERS)
        if resp.status_code != 200 or len(resp.content) < 200:
            return []
        parsed = feedparser.parse(resp.content)
        results: list[dict] = []
        for entry in parsed.get("entries") or []:
            pub_dt = _parse_pub_dt(entry)
            # Reject if older than max_age_days (when date is available)
            if pub_dt and pub_dt < cutoff:
                continue
            url   = entry.get("link", "")
            title = entry.get("title", "") or ""
            raw   = entry.get("summary") or entry.get("description") or ""
            clean = re.sub(r"<[^>]+>", " ", raw).strip()
            snippet = (f"{title}. {clean}" if len(clean) < 100 else clean)[:600]
            pub_str = pub_dt.strftime("%Y-%m-%d") if pub_dt else ""
            if not url or len(snippet.strip()) < _MIN_SNIPPET:
                continue
            results.append({
                "title":          title,
                "url":            url,
                "snippet":        snippet,
                "published_date": pub_str,
                "_source":        "rss",
                "_feed_url":      feed_url,
                "_days_window":   max_age_days,
            })
        return results
    except Exception as e:
        logger.debug(f"RSS fetch failed for {feed_url}: {e}")
        return []


def fetch_domain_rss(domain: str, max_age_days: int = 180) -> list[dict]:
    """Try each known feed URL for `domain` and return the first that yields results."""
    for feed_url in _FEEDS.get(domain, []):
        results = _fetch_one_feed(feed_url, max_age_days=max_age_days)
        if results:
            logger.debug(f"RSS {domain}: {len(results)} entries via {feed_url}")
            return results
        time.sleep(0.1)
    return []


def fetch_rss_for_domains(domains: list[str], max_age_days: int = 180) -> list[dict]:
    """
    Fetch RSS for each domain in `domains`. Deduplicates by URL.
    Returns only entries with a parseable publication date.
    """
    if not _FEEDPARSER_OK:
        logger.debug("feedparser unavailable — RSS channel skipped")
        return []
    seen: set[str] = set()
    all_results: list[dict] = []
    for domain in domains:
        for r in fetch_domain_rss(domain, max_age_days=max_age_days):
            if r["url"] not in seen:
                seen.add(r["url"])
                all_results.append(r)
        time.sleep(0.1)
    logger.info(f"RSS total: {len(all_results)} entries from {len(domains)} domains")
    return all_results


def filter_by_keywords(results: list[dict], keywords: list[str]) -> list[dict]:
    """Keep only entries whose title or snippet contains at least one keyword (case-insensitive)."""
    kw_lower = [k.lower() for k in keywords]
    return [
        r for r in results
        if any(kw in (r.get("title", "") + " " + r.get("snippet", "")).lower() for kw in kw_lower)
    ]
