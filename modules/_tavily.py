"""Shared Tavily search helper for modules."""
import time
import requests
from datetime import datetime, timedelta

from src.config import TAVILY_API_KEY, REQUEST_TIMEOUT, setup_logging

logger = setup_logging("modules.tavily")

_URL   = "https://api.tavily.com/search"
_DELAY = 0.35


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
) -> tuple[list[dict], int]:
    """
    Run queries through increasing day-windows until min_n unique results.
    Returns (deduplicated results, actual_days_used).
    require_date=True discards results with no parseable published_date.
    max_age_days: discard results whose published_date parses to older than N days ago.
    """
    cutoff = datetime.utcnow() - timedelta(days=max_age_days) if max_age_days else None
    last_results: list[dict] = []
    last_days: int = fallback_windows[-1]

    for days in fallback_windows:
        seen: set[str] = set()
        results: list[dict] = []
        for q in queries:
            for r in search(q, days=days, domains=domains):
                if r["url"] in seen:
                    continue
                if require_date and not _parse_date(r["published_date"]):
                    continue
                if cutoff:
                    parsed = _parse_date(r.get("published_date", ""))
                    if parsed and parsed < cutoff:
                        continue  # article too old despite Tavily's days parameter
                seen.add(r["url"])
                results.append(r)
        if len(results) >= min_n:
            return results, days
        last_results, last_days = results, days

    return last_results, last_days
