"""
2.3 Industry Research — daily research intelligence for Ukraine and Romania.
3 sub-topics × 2 countries → GPT-4o-mini paragraph summary.

Search strategy (4 stages per sub-topic):
  Stage 1: target domains + require_date + min_year=2026   (7→30→90→180d)
  Stage 2: no domain filter + require_date + min_year=2026  (30→90→180d)
  Stage 3: no domain filter + relaxed date                  (365d)
  Stage 4: broadened query (brand names stripped)           (365d)

Geographic injection: every query has country name baked in so Stage 3+4
never return "JLL UK" instead of "JLL Ukraine".
"""
import time
from src.config import OPENAI_API_KEY, setup_logging
from modules._tavily import search, validate_results
from modules._validator import validate_summary

logger = setup_logging("modules.industry_research")

# ── Google News optional channel ──────────────────────────────────────────────
try:
    from pygooglenews import GoogleNews
    _GNEWS_AVAILABLE = True
except ImportError:
    _GNEWS_AVAILABLE = False
    logger.debug("pygooglenews not installed — Google News channel disabled")

# ── Domain lists (verified open / partially-open sources) ────────────────────

DOMAINS_RETAIL_UA = [
    "gradus.app",           # Ukrainian open research
    "cedos.org.ua",         # UA think tank, open PDFs
    "kse.ua",               # Kyiv School of Economics
    "case-ukraine.in.ua",   # CASE Ukraine
    "retailgazette.co.uk",  # Trade press, open
    "just-food.com",        # FMCG research, partially open
    "ebrd.com",             # EBRD publications
    "worldbank.org",
]

DOMAINS_RETAIL_RO = [
    "profit.ro",
    "wall-street.ro",
    "retailtechnology.co.uk",
    "just-food.com",
    "ebrd.com",
    "worldbank.org",
    "doingbusiness.ro",
]

DOMAINS_REALESTATE = [
    "jll.com",
    "colliers.com",
    "cbre.com",
    "ebrd.com",
    "worldbank.org",
    "retailgazette.co.uk",
]

DOMAINS_FORECASTS = [
    "kse.ua",
    "case-ukraine.in.ua",
    "cedos.org.ua",
    "mckinsey.com",
    "deloitte.com",
    "pwc.com",
    "ebrd.com",
    "bruegel.org",          # European think tank, fully open
    "wiiw.ac.at",           # Vienna Institute CEE research
    "voxeu.org",            # Open economics research
]

# ── Geographic keyword injection ──────────────────────────────────────────────

_GEO_SUFFIXES = {
    "Ukraine": ["Ukraine", "Україна", "Ukrainian market"],
    "Romania": ["Romania", "România", "Romanian market"],
    "CEE":     ["CEE", "Central Eastern Europe"],
}

_GEO_KEYWORDS = {
    "Ukraine": ["ukraine", "ukrainian", "україн", "київ", "kyiv", "ucraina"],
    "Romania": ["romania", "romanian", "româniei", "bucurești", "bucharest", "rumänien"],
    "CEE":     ["cee", "central europe", "eastern europe"],
}

_BRAND_NAMES = [
    "JLL", "Cushman", "Wakefield", "CBRE", "Colliers",
    "McKinsey", "Deloitte", "PwC", "EY", "KPMG",
    "NielsenIQ", "Kantar", "Euromonitor", "Gradus",
    "Oliver", "Wyman", "Bruegel",
]


def _inject_geo(query: str, country: str) -> str:
    """Append geographic term if not already present."""
    suffixes = _GEO_SUFFIXES.get(country, [country])
    if not any(s.lower() in query.lower() for s in suffixes):
        return f"{query} {suffixes[0]}"
    return query


def _broaden_query(query: str, country: str) -> str:
    """Strip brand names, keep topic + geo. Last-resort Stage 4 query."""
    words = query.split()
    cleaned = [w for w in words if w not in _BRAND_NAMES]
    return _inject_geo(" ".join(cleaned), country)


def _is_geo_relevant(result: dict, country: str) -> bool:
    """True if snippet/title/url mentions the target country."""
    keywords = _GEO_KEYWORDS.get(country, [country.lower()])
    text = (
        result.get("title", "") + " " +
        result.get("snippet", "") + " " +
        result.get("url", "")
    ).lower()
    return any(kw in text for kw in keywords)


def _tag(results: list[dict], stage: int) -> list[dict]:
    return [{**r, "_search_stage": stage} for r in results]


# ── Google News channel ───────────────────────────────────────────────────────

_GNEWS_QUERIES = {
    "Ukraine": [
        "ритейл Україна дослідження 2026",
        "retail market Ukraine 2026",
    ],
    "Romania": [
        "retail Romania piata 2026",
        "retail market Romania 2026",
    ],
    "CEE": [
        "CEE retail market research 2026",
        "Central Eastern Europe consumer 2026",
    ],
}

_GNEWS_LANG = {"Ukraine": ("uk", "UA"), "Romania": ("ro", "RO"), "CEE": ("en", "US")}


def _resolve_gnews_url(url: str) -> str:
    """Follow Google News RSS redirect (GET) to get the real article URL."""
    if "news.google.com" not in url:
        return url
    try:
        import requests as _req
        r = _req.get(url, timeout=6, allow_redirects=True,
                     headers={"User-Agent": "Mozilla/5.0"},
                     stream=True)
        r.close()
        return r.url if r.url and r.url != url else url
    except Exception:
        return url


def _search_google_news(country: str) -> list[dict]:
    if not _GNEWS_AVAILABLE:
        return []
    lang, cc = _GNEWS_LANG.get(country, ("en", "US"))
    gn = GoogleNews(lang=lang, country=cc)
    results = []
    for q in _GNEWS_QUERIES.get(country, [f"retail {country} 2026"]):
        try:
            data = gn.search(q, when="2m")
            for entry in (data.get("entries") or [])[:3]:
                raw_url = entry.get("link", "")
                real_url = _resolve_gnews_url(raw_url)
                # Build a meaningful snippet — combine summary + source
                summary = (entry.get("summary") or "").strip()
                title   = entry.get("title", "")
                # Pad short snippets so they pass the 200-char threshold
                snippet = f"{title}. {summary}" if len(summary) < 180 else summary
                results.append({
                    "url":            real_url,
                    "title":          title,
                    "snippet":        snippet[:600],
                    "published_date": entry.get("published", ""),
                    "_source":        "google_news",
                })
            time.sleep(0.2)
        except Exception as e:
            logger.debug(f"GoogleNews '{q}': {e}")
    return results


# ── Core 4-stage search ───────────────────────────────────────────────────────

def _tavily_search_stage(
    queries: list[str],
    domains: list,
    require_date: bool,
    windows: list[int],
) -> list[dict]:
    """
    One Tavily stage: tries all queries across increasing day-windows until
    min_n=2 results found. Single collect() call — not N×M individual calls.
    """
    from modules._tavily import collect
    results, _ = collect(
        queries,
        fallback_windows=windows,
        min_n=2,
        domains=domains or None,
        require_date=require_date,
        min_year=2026 if require_date else None,
    )
    return results


def _search_subtopic(queries: list[str], domains: list[str], country: str) -> tuple[list[dict], int]:
    """
    Run 4-stage search. Each stage is ONE collect() call (not N×M individual calls).
    Max Tavily calls per subtopic: ~12 worst-case (was up to 27).
    """
    geo_queries = [_inject_geo(q, country) for q in queries]

    # Stage 1 — strict domains + date filter + year 2026
    raw = _tavily_search_stage(geo_queries, domains, require_date=True, windows=[7, 30, 90, 180])
    filtered = [r for r in raw if _is_geo_relevant(r, country)]
    if len(filtered) >= 2:
        logger.debug(f"  [{country}] Stage 1: {len(filtered)} results")
        return _tag(filtered, 1), 1

    # Stage 2 — no domain filter + date filter + year 2026
    raw = _tavily_search_stage(geo_queries, [], require_date=True, windows=[30, 90, 180])
    filtered = [r for r in raw if _is_geo_relevant(r, country)]
    if len(filtered) >= 2:
        logger.debug(f"  [{country}] Stage 2: {len(filtered)} results")
        return _tag(filtered, 2), 2

    # Stage 3 — no domain filter, relaxed date
    raw = _tavily_search_stage(geo_queries, [], require_date=False, windows=[365])
    deduped = list({r["url"]: r for r in raw if r.get("url")}.values())
    filtered = validate_results(deduped)
    filtered = [r for r in filtered if _is_geo_relevant(r, country)]
    if filtered:
        logger.debug(f"  [{country}] Stage 3: {len(filtered)} results")
        return _tag(filtered, 3), 3

    # Stage 4 — broadened queries, last resort
    broad = [_broaden_query(q, country) for q in queries]
    raw = _tavily_search_stage(broad, [], require_date=False, windows=[365])
    deduped = list({r["url"]: r for r in raw if r.get("url")}.values())
    filtered = validate_results(deduped)
    filtered = [r for r in filtered if _is_geo_relevant(r, country)]
    logger.debug(f"  [{country}] Stage 4: {len(filtered)} results")
    return _tag(filtered, 4), 4


# ── Sub-topics ────────────────────────────────────────────────────────────────

_SUBTOPICS = [
    {
        "id": "consumer_ua", "country": "Ukraine",
        "label": "Retail & споживча поведінка — Україна",
        "queries": [
            "GRADUS retail consumer Ukraine report 2026",
            "NielsenIQ consumer trends retail Ukraine 2026",
            "retail consumer behavior Ukraine FMCG 2026",
        ],
        "domains": DOMAINS_RETAIL_UA,
    },
    {
        "id": "realestate_ua", "country": "Ukraine",
        "label": "Комерційна нерухомість — Україна",
        "queries": [
            "JLL Cushman Wakefield retail real estate Ukraine 2026",
            "commercial real estate shopping centers Ukraine report",
            "CBRE Colliers retail property Ukraine 2026",
        ],
        "domains": DOMAINS_REALESTATE,
    },
    {
        "id": "forecast_ua", "country": "Ukraine",
        "label": "Галузеві прогнози & CEE — Україна",
        "queries": [
            "McKinsey Deloitte retail Ukraine forecast 2026",
            "KSE CASE Ukraine retail industry outlook 2026",
            "EBRD Ukraine economic retail forecast 2026",
        ],
        "domains": DOMAINS_FORECASTS,
    },
    {
        "id": "consumer_ro", "country": "Romania",
        "label": "Retail & споживча поведінка — Румунія",
        "queries": [
            "NielsenIQ consumer trends retail Romania 2026",
            "retail consumer behavior Romania FMCG report 2026",
            "consumer spending retail market Romania CEE 2026",
        ],
        "domains": DOMAINS_RETAIL_RO,
    },
    {
        "id": "realestate_ro", "country": "Romania",
        "label": "Комерційна нерухомість — Румунія",
        "queries": [
            "JLL Cushman Wakefield retail real estate Romania 2026",
            "commercial real estate shopping centers Romania CEE",
            "CBRE Colliers retail property Romania 2026",
        ],
        "domains": DOMAINS_REALESTATE,
    },
    {
        "id": "forecast_ro", "country": "Romania",
        "label": "Галузеві прогнози & CEE — Румунія",
        "queries": [
            "McKinsey Deloitte retail Romania forecast 2026",
            "Deloitte PwC Romania CEE retail outlook report 2026",
            "EBRD Bruegel Romania CEE industry forecast 2026",
        ],
        "domains": DOMAINS_FORECASTS,
    },
]

# ── AI summarizer ─────────────────────────────────────────────────────────────

_SYSTEM = (
    "You are a retail intelligence analyst writing a daily briefing. "
    "Given fresh research snippets on a topic and country, write a 3–5 sentence summary in Ukrainian: "
    "state the key finding, name specific companies/reports/figures, "
    "highlight any CEE or UA–RO comparison if present. "
    "Start with the date range covered "
    "(e.g. 'За останній тиждень...' or 'За останні 30 днів...'). "
    "After each specific fact or statistic, add the source URL in parentheses, "
    "e.g. 'показники зросли на 3% (https://example.com/article)'. "
    "Only cite URLs from the provided source list. "
    "Neutral tone, no bullets, plain paragraph. "
    "\n\n"
    "GROUNDING RULE: Only use information explicitly present in the provided source texts. "
    "If you are not certain a fact appears in the source, omit it. "
    "Do not infer, extrapolate, or use training knowledge to fill gaps. "
    "When in doubt, leave it out. "
    "If the sources contain no usable information — write: "
    "'Новин за цей період не знайдено.' "
    "Do NOT invent companies, numbers, or facts."
)


def _summarize(label: str, results: list[dict], max_stage: int) -> tuple[str, list[dict]]:
    """Returns (summary_text, cited_sources). cited_sources contains only URLs mentioned."""
    if not results:
        return "Новин за цей період не знайдено.", []
    if not OPENAI_API_KEY:
        return f"OPENAI_API_KEY не налаштовано ({len(results)} джерел знайдено).", []

    snippets = "\n\n".join(
        f"Title: {r['title']}\nURL: {r['url']}\n"
        f"Date: {r.get('published_date') or 'n/a'}\nSource: {r.get('_source', 'tavily')}\n"
        f"{r['snippet']}"
        for r in results[:8]
    )
    user_msg = f"Topic: {label}\n\n{snippets}"

    try:
        from openai import OpenAI
        resp = OpenAI(api_key=OPENAI_API_KEY).chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=400,
        )
        raw = resp.choices[0].message.content.strip()
        validated = validate_summary(raw, results, topic=label)

        # Append warning if results came from broad search (stage 3+)
        if max_stage >= 3:
            validated += (
                "\n⚠️ _Частина результатів знайдена через широкий пошук (стадія 3–4) — "
                "перевіряйте посилання вручну._"
            )

        # Only include cited sources (no orphan links)
        import re as _re
        cited_urls = set(_re.findall(r'https?://[^\s\)]+', validated))
        cited_sources = [
            {"title": r["title"], "url": r["url"]}
            for r in results if r.get("url") and r["url"] in cited_urls
        ]
        return validated, cited_sources
    except Exception as e:
        logger.error(f"AI summary '{label}': {e}")
        return f"AI недоступний ({len(results)} джерел знайдено).", []


# ── Main entry point ──────────────────────────────────────────────────────────

def run() -> list[dict]:
    """Run all 6 sub-topics. Returns list of result dicts."""
    from datetime import date as _date
    from modules._qa_log import write_entry as _qa_write
    today = _date.today().isoformat()
    output = []
    for st in _SUBTOPICS:
        logger.info(f"Industry research: {st['label']}")
        results, max_stage = _search_subtopic(st["queries"], st["domains"], st["country"])
        logger.info(f"  {len(results)} results (max_stage={max_stage})")
        summary, cited_sources = _summarize(st["label"], results, max_stage)

        # QA log for every result
        cited_urls = {s["url"] for s in cited_sources}
        for r in results:
            url = r.get("url", "")
            _qa_write(
                section="2.3",
                url=url,
                fetch_method=r.get("_source", "tavily"),
                status="fetched",
                published_date=r.get("published_date", ""),
                used_in_report=url in cited_urls,
                content_chars=len(r.get("snippet", "")),
                today=today,
            )

        output.append({
            "id":            st["id"],
            "label":         st["label"],
            "summary":       summary,
            "results_count": len(results),
            "days_used":     180,
            "max_stage":     max_stage,
            "sources":       cited_sources[:3],
        })
    return output


# ── Manual test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    from src.config import setup_logging as _sl
    _sl()

    test_st = {
        "id": "realestate_ua",
        "label": "Комерційна нерухомість — Україна",
        "country": "Ukraine",
        "queries": [
            "JLL Cushman Wakefield retail real estate Ukraine 2026",
            "commercial real estate shopping centers Ukraine CEE",
        ],
        "domains": DOMAINS_REALESTATE,
    }
    results, stage = _search_subtopic(test_st["queries"], test_st["domains"], test_st["country"])
    print(f"\nResults: {len(results)}, max_stage={stage}")
    for r in results:
        bad = any(x in (r.get("url","") + r.get("snippet","")).lower()
                  for x in ["uk real estate", "united kingdom", "britain", " uk "])
        flag = " ⚠️ GEO MISMATCH" if bad else ""
        print(f"  [stage {r.get('_search_stage')}] {r['url'][:80]}{flag}")
        print(f"    → {r['snippet'][:120]}")
