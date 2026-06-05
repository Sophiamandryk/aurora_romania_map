"""
2.2 Retail News — daily retail intelligence for Ukraine and Romania.
6 sub-topics × 3 queries (UA / RO / EN) → per-query fallback → GPT summary.

Key design decisions:
- RSS feeds tried first (never blocked, always have parsed dates)
- Tavily as fallback when RSS yields insufficient results
- Year baked into every query string (Tavily date filter unreliable)
- Per-query fallback: each query expands its window independently (30 → 90 → 180 days)
- Published_date must be parseable — results without a parseable date are discarded
- Multi-signal year inference (published_date + URL + title + snippet)
- Preferred news domains boost for Stage 1 (days ≤ 30)
- Recency sort before GPT — freshest articles appear first in context
- Google News parallel channel (optional, graceful fallback if unavailable)
- QA log entry written for every source used in the final report
"""
import re
import time
from datetime import datetime

import requests

from src.config import OPENAI_API_KEY, TAVILY_API_KEY, REQUEST_TIMEOUT, setup_logging
from modules._tavily import _BLOCKED_DOMAINS, _is_blocked, _token_overlap, _parse_date
from modules._validator import validate_summary
from modules._qa_log import write_entry as _qa_write
from modules._rss import fetch_rss_for_domains, filter_by_keywords as _rss_filter

logger = setup_logging("modules.retail_news")

# ── Google News (optional) ────────────────────────────────────────────────────
try:
    from pygooglenews import GoogleNews
    _GNEWS_AVAILABLE = True
except ImportError:
    _GNEWS_AVAILABLE = False
    logger.debug("pygooglenews not installed — Google News channel disabled")

# ── Constants ─────────────────────────────────────────────────────────────────

CURRENT_YEAR = str(datetime.now().year)   # "2026"
_TAVILY_URL = "https://api.tavily.com/search"
_DELAY = 0.35

# Date recency windows per spec: 30 → 90 → 180 → "not found"
_FALLBACK_WINDOWS = [30, 90, 180]

# Preferred domains for Stage 1 (days ≤ 30) — open retail/business news sites
PREFERRED_NEWS_DOMAINS = [
    # Ukrainian retail & business
    "retail.in.ua", "retailer.ua", "retailers.ua",
    "business.ua", "epravda.com.ua", "latifundist.com", "landlord.ua",
    # Romanian retail & business
    "retail.ro", "profit.ro", "wall-street.ro", "zf.ro", "economica.net",
    # CEE trade press
    "retailgazette.co.uk", "just-food.com", "essentialretail.com",
    # Logistics
    "logisticsmanager.com", "supplychaindigital.com",
]

# RSS-enabled domains to pre-fetch before Tavily
_RSS_DOMAINS = [
    "retail.ro", "profit.ro", "wall-street.ro", "zf.ro", "economica.net",
    "retailer.ua", "retailers.ua", "rau.ua", "allretail.ua",
]

# ── Sub-topics ────────────────────────────────────────────────────────────────

SUBTOPICS = [
    {
        "id": "ma_investments",
        "label": "M&A, інвестиції, нові гравці ринку",
        "queries": [
            f"злиття поглинання інвестиції ритейл Україна {CURRENT_YEAR}",
            f"retail M&A investment Romania {CURRENT_YEAR}",
            f"retail merger acquisition CEE investment {CURRENT_YEAR}",
        ],
        "keywords": ["злиття", "інвестиці", "M&A", "investment", "merger", "acquisition", "retail"],
        "geo": ["Ukraine", "Romania"],
    },
    {
        "id": "store_openings",
        "label": "Відкриття / закриття магазинів, розширення мережі",
        "queries": [
            f"відкриття закриття магазинів ритейл мережа Україна {CURRENT_YEAR}",
            f"deschidere inchidere magazine retail Romania {CURRENT_YEAR}",
            f"retail store openings closures Ukraine Romania {CURRENT_YEAR}",
        ],
        "keywords": ["відкрит", "закрит", "deschidere", "inchidere", "store opening", "retail"],
        "geo": ["Ukraine", "Romania"],
    },
    {
        "id": "new_formats",
        "label": "Нові формати, концепції, приватні марки",
        "queries": [
            f"новий формат магазину приватна марка ритейл Україна {CURRENT_YEAR}",
            f"format nou marca proprie retail Romania {CURRENT_YEAR}",
            f"new retail format private label launch Ukraine Romania {CURRENT_YEAR}",
        ],
        "keywords": ["формат", "приватна марка", "format", "private label", "retail"],
        "geo": ["Ukraine", "Romania"],
    },
    {
        "id": "ecommerce",
        "label": "E-commerce, маркетплейси, омніканальність",
        "queries": [
            f"e-commerce маркетплейс онлайн торгівля Україна {CURRENT_YEAR}",
            f"e-commerce marketplace omnichannel Romania {CURRENT_YEAR}",
            f"online retail marketplace growth CEE Ukraine Romania {CURRENT_YEAR}",
        ],
        "keywords": ["e-commerce", "marketplace", "онлайн", "omnichannel", "retail"],
        "geo": ["Ukraine", "Romania"],
    },
    {
        "id": "consumer_trends",
        "label": "Споживчі тренди, поведінка покупців",
        "queries": [
            f"споживчі тренди поведінка покупців Україна {CURRENT_YEAR}",
            f"tendinte consum comportament cumparator Romania {CURRENT_YEAR}",
            f"consumer trends shopper behavior Ukraine Romania {CURRENT_YEAR}",
        ],
        "keywords": ["споживч", "тренд", "consumer", "tendinte", "retail"],
        "geo": ["Ukraine", "Romania"],
    },
    {
        "id": "logistics",
        "label": "Логістика, склади, ланцюги постачання",
        "queries": [
            f"логістика склади ланцюг постачання ритейл Україна {CURRENT_YEAR}",
            f"logistica depozite supply chain retail Romania {CURRENT_YEAR}",
            f"retail logistics warehouse supply chain Ukraine Romania {CURRENT_YEAR}",
        ],
        "keywords": ["логістик", "склад", "logistica", "supply chain", "warehouse", "retail"],
        "geo": ["Ukraine", "Romania"],
    },
]

_GEO_SIGNALS = [
    "ukraine", "ukrainian", "україн", "київ", "kyiv", "ucraina",
    "romania", "romanian", "bucurești", "bucharest", "româniei",
    "cee", "central europe", "eastern europe",
]

# ── Date inference ─────────────────────────────────────────────────────────────

def _infer_year(result: dict) -> int | None:
    """Extract publication year from multiple signals. Returns int or None."""
    # Signal 1: published_date field
    pub = result.get("published_date") or result.get("date", "")
    if pub:
        m = re.search(r"(202[0-9])", str(pub))
        if m:
            return int(m.group(1))
    # Signal 2: year in URL path
    url = result.get("url", "")
    m = re.search(r"/(202[0-9])/", url)
    if m:
        return int(m.group(1))
    # Signal 3: year in title
    title = result.get("title", "")
    m = re.search(r"\b(202[0-9])\b", title)
    if m:
        return int(m.group(1))
    # Signal 4: most recent year in first 200 chars of snippet
    snippet = (result.get("snippet") or result.get("content", ""))[:200]
    years = re.findall(r"\b(202[0-9])\b", snippet)
    if years:
        return max(int(y) for y in years)
    return None


# ── Quality filter ─────────────────────────────────────────────────────────────

def _passes_quality(
    result: dict,
    accepted: list[dict],
    min_year: int = 2026,
    require_parsed_date: bool = True,
) -> bool:
    """Combined quality + freshness + geo-relevance check."""
    snippet = result.get("snippet") or result.get("content", "")
    url = result.get("url", "")

    # 1. Snippet too short
    if len(snippet.strip()) < 200:
        return False
    # 2. Blocked domain
    if _is_blocked(url):
        return False
    # 3. Published date must be parseable (per spec: discard unparseable dates)
    pub_dt = _parse_date(result.get("published_date", ""))
    if require_parsed_date and pub_dt is None:
        return False
    # 4. Year check with multi-signal inference
    inferred = _infer_year(result)
    if inferred is not None and inferred < min_year:
        return False   # confirmed old — reject
    # 5. Geographic relevance
    text = (result.get("title", "") + " " + snippet[:400]).lower()
    if not any(g in text for g in _GEO_SIGNALS):
        return False
    # 6. Jaccard dedup against already accepted
    for acc in accepted:
        acc_snip = acc.get("snippet") or acc.get("content", "")
        if _token_overlap(snippet[:300], acc_snip[:300]) >= 0.70:
            return False
    return True


# ── Recency sort ───────────────────────────────────────────────────────────────

def _sort_by_recency(results: list[dict]) -> list[dict]:
    """Fresher results first: higher year, then smaller window."""
    def _key(r):
        year   = _infer_year(r) or 2020
        window = r.get("_days_window", 180)
        return (-year, window)
    return sorted(results, key=_key)


# ── Tavily call ────────────────────────────────────────────────────────────────

def _tavily_call(query: str, days: int, include_domains: list = None) -> list[dict]:
    if not TAVILY_API_KEY:
        return []
    payload = {
        "api_key":      TAVILY_API_KEY,
        "query":        query,
        "search_depth": "advanced",
        "max_results":  7,
        "days":         days,
    }
    if include_domains:
        payload["include_domains"] = include_domains
    try:
        resp = requests.post(_TAVILY_URL, json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        out = []
        for r in resp.json().get("results", []):
            if r.get("url"):
                out.append({
                    "title":          r.get("title", ""),
                    "url":            r.get("url", ""),
                    "snippet":        (r.get("content") or r.get("snippet", ""))[:600],
                    "published_date": r.get("published_date", ""),
                    "_source":        "tavily",
                })
        return out
    except Exception as e:
        logger.warning(f"Tavily '{query[:60]}': {e}")
        return []
    finally:
        time.sleep(_DELAY)


# ── Google News channel ────────────────────────────────────────────────────────

def _search_google_news_retail(subtopic: dict) -> list[dict]:
    if not _GNEWS_AVAILABLE:
        return []
    configs = [
        ("uk", "UA", subtopic["queries"][0]),
        ("ro", "RO", subtopic["queries"][1]),
        ("en", "US", subtopic["queries"][2]),
    ]
    results = []
    for lang, cc, query in configs:
        try:
            gn = GoogleNews(lang=lang, country=cc)
            data = gn.search(query, when="1m")
            for entry in (data.get("entries") or [])[:4]:
                raw_url = entry.get("link", "")
                title   = entry.get("title", "")
                summary = re.sub(r"<[^>]+>", " ", entry.get("summary") or "").strip()
                snippet = f"{title}. {summary}" if len(summary) < 180 else summary
                pub_str = entry.get("published", "")
                # Require parseable date for Google News results too
                if not _parse_date(pub_str):
                    continue
                results.append({
                    "url":            raw_url,
                    "title":          title,
                    "snippet":        snippet[:600],
                    "published_date": pub_str,
                    "_source":        "google_news",
                    "_days_window":   30,
                })
            time.sleep(0.2)
        except Exception as e:
            logger.debug(f"GoogleNews retail '{subtopic['id']}': {e}")
    return results


# ── Per-query collect ──────────────────────────────────────────────────────────

def _collect_subtopic(
    subtopic: dict,
    fallback_windows: list[int] = None,
    min_year: int = 2026,
    min_n: int = 3,
    max_n: int = 10,
) -> tuple[list[dict], int]:
    """
    Source priority: RSS → Google News → Tavily (with per-query window fallback).
    Date recency fallback windows: 30 → 90 → 180 days (per spec).
    Results without a parseable published_date are discarded.
    Returns (results, max_window_used).
    """
    if fallback_windows is None:
        fallback_windows = _FALLBACK_WINDOWS  # [30, 90, 180]

    accepted: list[dict] = []
    seen_urls: set[str] = set()
    max_window_used = fallback_windows[0]

    # ── Channel 1: RSS (never blocked, always has parsed dates) ──────────────
    rss_raw = fetch_rss_for_domains(_RSS_DOMAINS, max_age_days=fallback_windows[-1])
    rss_filtered = _rss_filter(rss_raw, subtopic.get("keywords", []) + subtopic.get("queries", []))
    for r in rss_filtered:
        if r["url"] and r["url"] not in seen_urls and _passes_quality(r, accepted, min_year):
            r["_days_window"] = 30
            accepted.append(r)
            seen_urls.add(r["url"])

    # ── Channel 2: Google News ────────────────────────────────────────────────
    for r in _search_google_news_retail(subtopic):
        if r["url"] and r["url"] not in seen_urls and _passes_quality(r, accepted, min_year):
            r["_days_window"] = 30
            accepted.append(r)
            seen_urls.add(r["url"])

    # ── Channel 3: Tavily (per-query window fallback) ─────────────────────────
    query_contributed: dict[str, bool] = {q: False for q in subtopic["queries"]}

    for window in fallback_windows:
        max_window_used = max(max_window_used, window)

        pending = [q for q in subtopic["queries"] if not query_contributed[q]]
        if not pending:
            break

        domains = PREFERRED_NEWS_DOMAINS if window <= 30 else None

        for q in pending:
            raw = _tavily_call(q, days=window, include_domains=domains)
            contributed = False
            for r in raw:
                url = r.get("url", "")
                if not url or url in seen_urls:
                    continue
                r["_days_window"] = window
                r["_source_query"] = q
                if _passes_quality(r, accepted, min_year):
                    accepted.append(r)
                    seen_urls.add(url)
                    contributed = True
            if contributed:
                query_contributed[q] = True

        if len(accepted) >= min_n:
            break

    results = _sort_by_recency(accepted)[:max_n]
    return results, max_window_used


# ── GPT summarizer ─────────────────────────────────────────────────────────────

_SYSTEM = (
    "You are a retail intelligence analyst writing a daily briefing for Aurora Multimarket. "
    "Given fresh news snippets on a topic covering Ukraine and Romania, "
    "write a 3–5 sentence summary in Ukrainian: "
    "state the key finding, name specific companies/reports/figures only if present in sources, "
    "highlight any UA–RO comparison if data exists for both countries. "
    "After each specific fact or statistic, add the source URL in parentheses on the same line, "
    "e.g. 'продажі зросли на 5% (https://example.com/article)'. "
    "Only cite URLs from the provided source list. "
    "Never include a URL that has no preceding fact from that source. "
    "Never include a fact without a backing URL. "
    "\n\n"
    "GROUNDING RULE: Only use information explicitly present in the provided source texts. "
    "If you are not certain a fact appears in the source, omit it. "
    "Do not infer, extrapolate, or use training knowledge to fill gaps. "
    "When in doubt, leave it out. "
    "If sources don't contain enough information — write: "
    "'Новин за цей період не знайдено.' "
    "Do NOT invent companies, numbers, or facts."
)


def _build_prompt(results: list[dict], label: str, max_window: int) -> str:
    inferred_years = [_infer_year(r) for r in results]
    confirmed_2026 = any(y and y >= 2026 for y in inferred_years)
    freshness = (
        f"за останні {max_window} днів (дані {CURRENT_YEAR} року)"
        if confirmed_2026
        else f"знайдені матеріали (дати частково не підтверджені)"
    )

    sources_block = "\n\n".join(
        f"[{i+1}] {r.get('title','')}\n"
        f"Дата: {r.get('published_date') or (_infer_year(r) or 'невідома')} | "
        f"Джерело: {r.get('_source', 'tavily')} | "
        f"Вікно пошуку: {r.get('_days_window', '?')}д\n"
        f"URL: {r.get('url','')}\n"
        f"Текст: {(r.get('snippet') or r.get('content',''))[:500]}"
        for i, r in enumerate(results[:10])
    )

    return (
        f"Тема: {label}\n"
        f"Починай відповідь: 'За {freshness}...'\n\n"
        f"Джерела:\n{sources_block}"
    )


def _extract_cited_urls(summary: str) -> set[str]:
    """Find all https?:// URLs appearing in parentheses in the summary."""
    return set(re.findall(r'\(https?://[^\s\)]+\)', summary))


def _summarize(
    label: str,
    results: list[dict],
    max_window: int,
) -> tuple[str, list[dict]]:
    """
    Returns (summary_text, sources_actually_cited).
    sources_actually_cited: only results whose URL appears in the summary text.
    """
    if not results:
        return "Новин за цей період не знайдено.", []
    if not OPENAI_API_KEY:
        return f"OPENAI_API_KEY не налаштовано ({len(results)} джерел знайдено).", []

    user_msg = _build_prompt(results, label, max_window)

    try:
        from openai import OpenAI
        resp = OpenAI(api_key=OPENAI_API_KEY).chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=450,
        )
        raw = resp.choices[0].message.content.strip()
        validated = validate_summary(raw, results, topic=label)

        # Build sources list: only URLs actually cited in the summary (no orphan links)
        cited_urls = set(re.findall(r'https?://[^\s\)]+', validated))
        cited_sources = [
            {"title": r["title"], "url": r["url"]}
            for r in results
            if r.get("url") and r["url"] in cited_urls
        ]
        return validated, cited_sources

    except Exception as e:
        logger.error(f"AI summary '{label}': {e}")
        return f"AI недоступний ({len(results)} джерел знайдено).", []


# ── Main entry point ───────────────────────────────────────────────────────────

def run() -> list[dict]:
    from datetime import date as _date
    today = _date.today().isoformat()
    output = []
    for st in SUBTOPICS:
        logger.info(f"Retail news: {st['label']}")
        results, max_window = _collect_subtopic(st)
        years = [_infer_year(r) for r in results if _infer_year(r)]
        logger.info(
            f"  {len(results)} results (max_window={max_window}d, "
            f"years={sorted(set(years), reverse=True)[:3] if years else 'unknown'})"
        )
        summary, cited_sources = _summarize(st["label"], results, max_window)

        # QA log: every result, tagged used_in_report based on citation
        cited_urls = {s["url"] for s in cited_sources}
        for r in results:
            url = r.get("url", "")
            _qa_write(
                section="2.2",
                url=url,
                fetch_method=r.get("_source", "tavily"),
                status="fetched",
                published_date=r.get("published_date", ""),
                used_in_report=url in cited_urls,
                content_chars=len(r.get("snippet", "")),
                today=today,
            )

        # Fallback message if nothing found
        if not results:
            summary = "Новин за цей період не знайдено."

        output.append({
            "id":            st["id"],
            "label":         st["label"],
            "summary":       summary,
            "results_count": len(results),
            "days_used":     max_window,
            "sources":       cited_sources[:3],  # only cited sources, no orphan links
        })
    return output


# ── Manual test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

    st = SUBTOPICS[0]
    print(f"Testing: {st['label']}")
    results, max_window = _collect_subtopic(st)
    print(f"\nTotal: {len(results)}, max_window={max_window}d")
    for r in results:
        year = _infer_year(r) or "?"
        src  = r.get("_source", "tavily")
        print(f"  [{year}] [{r.get('_days_window','?')}d] [{src}] {r['url'][:80]}")
        bad_geo = not any(g in (r.get("title","") + r.get("snippet","")).lower()
                          for g in _GEO_SIGNALS)
        if bad_geo:
            print("    ⚠️  GEO MISMATCH")

    summary, sources = _summarize(st["label"], results, max_window)
    print(f"\nSummary:\n{summary}")
    print(f"\nCited sources ({len(sources)}):")
    for s in sources:
        print(f"  {s['url']}")
