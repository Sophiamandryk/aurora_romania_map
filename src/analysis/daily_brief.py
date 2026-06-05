"""
Daily analytic brief: Tavily web search + GPT-4o-mini analysis.
Each run performs fresh searches, deduplicates against the last 7 days,
and produces a Ukrainian-language intelligence report.
"""
import json
import re
import time
from datetime import date, datetime
from typing import Optional

import requests

from src.config import (
    TAVILY_API_KEY, OPENAI_API_KEY, REQUEST_TIMEOUT, DB_PATH, setup_logging,
)

logger = setup_logging("analysis.daily_brief")

_TAVILY_URL = "https://api.tavily.com/search"
_MODEL = "gpt-4o-mini"
_MAX_RESULTS_PER_QUERY = 5
_MAX_RESULTS_FOR_AI = 40          # cap sent to AI to control token usage
_SNIPPET_LEN = 250                 # chars per snippet in AI prompt
_QUERY_DELAY = 0.4                 # seconds between Tavily calls
_TELEGRAM_MAX_LEN = 4000

_RO_MONTHS = [
    "", "ianuarie", "februarie", "martie", "aprilie", "mai", "iunie",
    "iulie", "august", "septembrie", "octombrie", "noiembrie", "decembrie",
]


def _month_ro(d: date) -> str:
    return _RO_MONTHS[d.month]


def _build_queries(d: date) -> list[tuple[str, str]]:
    """Return (topic, query) pairs with today's month/year substituted."""
    m = _month_ro(d)
    y = d.year
    return [
        ("competitor",       f"Pepco Romania {m} {y}"),
        ("competitor",       f"Action Romania deschidere magazin {m} {y}"),
        ("competitor",       f"Profi Penny TEDi KiK Romania stiri {m} {y}"),
        ("competitor_promo", f"Pepco promotii oferte Romania {m} {y}"),
        ("competitor_promo", f"TEDi KiK oferte saptamana Romania {m} {y}"),
        ("competitor_promo", f"Penny Profi reduceri promotii Romania {m} {y}"),
        ("competitor_promo", f"Mr.DIY Action promotii Romania {y}"),
        ("aurora",           f"Aurora Multimarket Romania {m} {y}"),
        ("aurora",           f"Aurora Multimarket deschidere {m} {y}"),
        ("retail_trends",    f"retail Romania tendinte {m} {y}"),
        ("retail_trends",    f"supermarket discount Romania {y}"),
        ("consumer",         f"comportament cumparator Romania {y}"),
        ("expansion",        f"retail park Romania {y} deschidere"),
        ("expansion",        f"parc comercial Romania {y}"),
        ("expansion",        f"best cities retail expansion Romania"),
        ("products",         f"produse populare discount Romania {y}"),
        ("products",         f"one euro store trends Europe {y}"),
        ("products",         f"dollar store equivalent popular products {y}"),
        ("consumer",         f"Romanian consumer spending trends {y}"),
    ]


# ── Tavily ────────────────────────────────────────────────────────────────────

def _call_tavily(query: str) -> list[dict]:
    """Single Tavily search call. Returns [{title, url, snippet}]."""
    if not TAVILY_API_KEY:
        return []
    try:
        resp = requests.post(
            _TAVILY_URL,
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "search_depth": "basic",
                "max_results": _MAX_RESULTS_PER_QUERY,
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return [
            {
                "title":   r.get("title", ""),
                "url":     r.get("url", ""),
                "snippet": r.get("content", ""),
            }
            for r in resp.json().get("results", [])
            if r.get("url")
        ]
    except Exception as e:
        logger.warning(f"Tavily query failed '{query[:50]}': {e}")
        return []


def _search_all(today: date) -> list[dict]:
    """Run all queries, return cross-query deduplicated results with topic tags."""
    queries = _build_queries(today)
    seen: set[str] = set()
    results: list[dict] = []
    now = datetime.utcnow().isoformat()

    for topic, query in queries:
        for hit in _call_tavily(query):
            url = hit["url"]
            if url not in seen:
                seen.add(url)
                results.append({
                    "url":         url,
                    "title":       hit["title"],
                    "snippet":     hit["snippet"],
                    "query_topic": topic,
                    "searched_at": now,
                })
        time.sleep(_QUERY_DELAY)

    logger.info(f"Tavily: {len(results)} unique results from {len(queries)} queries")
    return results


# ── Aurora network stats ──────────────────────────────────────────────────────

def _get_aurora_stats() -> dict:
    """Pull current Aurora network stats directly from the database."""
    try:
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        _ACTIVE = "status='active' AND snapshot_date=(SELECT MAX(snapshot_date) FROM stores)"

        row = conn.execute(
            f"SELECT COUNT(*) as n, COUNT(DISTINCT city) as c FROM stores WHERE {_ACTIVE}"
        ).fetchone()

        by_region = {}
        for r in conn.execute(
            f"SELECT region, COUNT(*) as n FROM stores WHERE {_ACTIVE} "
            "GROUP BY region ORDER BY n DESC"
        ):
            by_region[r["region"] or "Unknown"] = r["n"]

        gaps = [
            r["city"] for r in conn.execute("""
                SELECT cs.city, COUNT(DISTINCT cs.brand) as brands
                FROM competitor_stores cs
                WHERE cs.city NOT IN (
                    SELECT DISTINCT city FROM stores
                    WHERE status='active'
                    AND snapshot_date=(SELECT MAX(snapshot_date) FROM stores)
                )
                GROUP BY cs.city HAVING brands >= 2
                ORDER BY brands DESC LIMIT 6
            """)
        ]
        conn.close()
        return {
            "stores":    row["n"] if row else 0,
            "cities":    row["c"] if row else 0,
            "by_region": by_region,
            "gap_cities": gaps,
        }
    except Exception as e:
        logger.warning(f"Aurora stats DB query failed: {e}")
        return {"stores": 0, "cities": 0, "by_region": {}, "gap_cities": []}


# ── AI analysis ───────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
Ти аналітик конкурентної розвідки для Aurora Multimarket — \
українського дискаунтера, що розширюється в Румунії.
Твоє завдання — щодня надавати стислий, практичний аналітичний бриф \
виключно українською мовою.
Return ONLY valid JSON — no markdown fences, no explanation outside the JSON object."""

_USER_TEMPLATE = """\
Дата: {date}

Поточний стан мережі Aurora Multimarket в Румунії:
- Активних магазинів: {stores}
- Міст присутності: {cities}
- Регіональний розподіл: {regions}
- Топ-міста без Aurora (конкуренти вже присутні): {gap_cities}

Нові результати веб-пошуку сьогодні ({n} джерел):

{results_block}

Поверни JSON точно в такому форматі:
{{
  "headline": "одне речення — найважливіший висновок дня",
  "sections": [
    {{
      "title": "назва секції",
      "content": "2-4 речення аналізу",
      "sources": [{{"text": "анкорний текст", "url": "..."}}]
    }}
  ],
  "top_actions": ["дія 1 для Aurora", "дія 2", "дія 3"],
  "expansion_signal": "місто або регіон для перевірки сьогодні, або null"
}}

Правила:
- Включай секцію ТІЛЬКИ якщо є конкретні релевантні результати сьогодні.
- Не заповнюй секцію загальними фразами, якщо немає реального джерела.
- Якщо немає жодних значущих результатів — поверни порожній масив sections.
- Кожне конкретне твердження підкріплюй джерелом зі списку (url + анкорний текст).
- Обов'язкові секції (якщо є дані): "Конкурентна активність", \
"Акції та каталоги конкурентів", "Ринкові тренди Румунії", \
"Можливості для розширення", "Популярні категорії товарів", "Поведінка покупців".
- Секція "Акції та каталоги конкурентів" — це комерційна розвідка: \
знижки, сезонні кампанії, нові категорії. Не прогнози розширення.
"""

_TOPIC_LABELS = {
    "competitor":       "АКТИВНІСТЬ КОНКУРЕНТІВ",
    "competitor_promo": "АКЦІЇ ТА КАТАЛОГИ КОНКУРЕНТІВ",
    "aurora":           "AURORA MULTIMARKET",
    "retail_trends":    "РИНКОВІ ТРЕНДИ",
    "consumer":         "ПОВЕДІНКА СПОЖИВАЧІВ",
    "expansion":        "РОЗШИРЕННЯ / РИТЕЙЛ-ПАРКИ",
    "products":         "ПОПУЛЯРНІ ПРОДУКТИ",
}


def _build_results_block(results: list[dict]) -> str:
    """Group results by topic and format for the AI prompt."""
    by_topic: dict[str, list[dict]] = {}
    for r in results:
        by_topic.setdefault(r.get("query_topic", "other"), []).append(r)

    lines: list[str] = []
    for topic, items in by_topic.items():
        lines.append(f"[{_TOPIC_LABELS.get(topic, topic.upper())}]")
        for r in items:
            snippet = (r.get("snippet") or "")[:_SNIPPET_LEN]
            lines += [
                f"Title: {r['title']}",
                f"URL: {r['url']}",
                f"Snippet: {snippet}",
                "",
            ]
    return "\n".join(lines)


def _analyze_with_ai(results: list[dict], stats: dict, today_str: str) -> dict:
    """Call GPT-4o-mini with fresh results. Returns parsed JSON or {}."""
    try:
        from openai import OpenAI
    except ImportError:
        logger.error("openai package not installed — pip install openai")
        return {}

    # Prioritise: limit to _MAX_RESULTS_FOR_AI, keeping topic diversity
    capped = results[:_MAX_RESULTS_FOR_AI]

    regions_str = ", ".join(f"{k}: {v}" for k, v in stats.get("by_region", {}).items())
    gap_str = ", ".join(stats.get("gap_cities", [])) or "немає даних"

    user_prompt = _USER_TEMPLATE.format(
        date=today_str,
        stores=stats.get("stores", "?"),
        cities=stats.get("cities", "?"),
        regions=regions_str or "немає даних",
        gap_cities=gap_str,
        n=len(capped),
        results_block=_build_results_block(capped),
    )

    client = OpenAI(api_key=OPENAI_API_KEY)
    try:
        logger.info(f"Daily brief AI analysis: {len(capped)} results → {_MODEL}")
        resp = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=2000,
        )
        analysis = json.loads(resp.choices[0].message.content)
        n_sections = len(analysis.get("sections") or [])
        logger.info(f"Daily brief: AI returned {n_sections} sections")
        return analysis
    except json.JSONDecodeError as e:
        logger.error(f"Daily brief: AI returned invalid JSON: {e}")
        return {}
    except Exception as e:
        logger.error(f"Daily brief: AI call failed: {e}")
        return {}


# ── Telegram formatter ────────────────────────────────────────────────────────

def _linkify(text: str) -> str:
    """Convert (url) and bare urls to Telegram markdown hyperlinks."""
    text = re.sub(r"\((https?://[^\s)]+)\)", r"([джерело](\1))", text)
    text = re.sub(r"(?<!\]\()(https?://[^\s)]+)", r"[джерело](\1)", text)
    return text


def _format_telegram(analysis: dict, today_str: str) -> str:
    """Build Telegram message from AI JSON. Returns ready-to-send string."""
    headline  = (analysis.get("headline") or "").strip()
    sections  = analysis.get("sections") or []
    actions   = analysis.get("top_actions") or []
    expansion = analysis.get("expansion_signal")

    parts: list[str] = [f"📍 *Aurora Romania — аналітичний бриф {today_str}*"]

    if headline:
        parts.append(f"🔍 {headline}")

    for sec in sections:
        title   = (sec.get("title") or "").strip()
        content = (sec.get("content") or "").strip()
        sources = sec.get("sources") or []

        if not content:
            continue

        content = re.sub(r"\n+", "\n\n", content)
        content = _linkify(content)

        block = f"*{title}*\n{content}" if title else content

        if sources:
            links = " · ".join(
                f"[{s.get('text', 'джерело')}]({s['url']})"
                for s in sources[:4]
                if s.get("url")
            )
            if links:
                block += f"\n\nДжерела: {links}"

        parts.append(block)

    if actions:
        parts.append("🎯 *Дії на сьогодні*\n" + "\n".join(f"• {a}" for a in actions[:5]))

    if expansion:
        parts.append(f"📌 *Місто для перевірки:* {expansion}")

    msg = "\n\n".join(p for p in parts if p)
    # Telegram hard limit is 4096; leave headroom for encoding
    if len(msg) > _TELEGRAM_MAX_LEN:
        msg = msg[:_TELEGRAM_MAX_LEN - 3] + "…"
    return msg


def _send_brief(msg: str) -> None:
    from src.alerts.telegram_alerts import TelegramBot
    try:
        TelegramBot()._send(msg, disable_preview=True)
        logger.info("Daily brief sent to Telegram")
    except Exception as e:
        logger.error(f"Failed to send daily brief: {e}")


# ── Public entry point ────────────────────────────────────────────────────────

def run_daily_brief(dry_run: bool = False, skip_alerts: bool = False) -> Optional[str]:
    """
    Full pipeline: Tavily search → 7-day dedup → AI analysis → Telegram.
    dry_run=True: skips DB writes and Telegram send.
    Returns the Telegram message string, or None if Tavily key is missing.
    """
    today = date.today()
    today_str = today.isoformat()

    if not TAVILY_API_KEY:
        logger.warning("TAVILY_API_KEY not set — skipping daily brief")
        return None

    # 1. Fresh Tavily searches
    all_results = _search_all(today)
    if not all_results:
        logger.warning("Daily brief: Tavily returned no results")
        return None

    # 2. Dedup against last 7 days in DB, then quality-filter
    from src.storage.sqlite_store import get_known_search_urls, save_web_search_results
    from modules._tavily import validate_results
    known_urls  = get_known_search_urls(days=7)
    deduped     = [r for r in all_results if r["url"] not in known_urls]
    new_results = validate_results(deduped)
    logger.info(
        f"Daily brief: {len(all_results)} fetched, "
        f"{len(new_results)} new (not seen in 7 days)"
    )

    if not dry_run and new_results:
        saved = save_web_search_results(new_results)
        logger.info(f"Daily brief: saved {saved} results to web_search_results")
    elif dry_run:
        pass  # already logged above
    elif dry_run:
        logger.info(f"[DRY RUN] Would save {len(new_results)} new search results")

    # 3. No fresh data → minimal message
    if not new_results:
        msg = (
            f"📍 Аналітичний бриф — {today_str}\n"
            "Сьогодні нових релевантних сигналів не знайдено."
        )
        if not skip_alerts and not dry_run:
            _send_brief(msg)
        return msg

    # 4. Aurora network stats for AI context
    stats = _get_aurora_stats()

    # 5. AI analysis
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set — skipping AI analysis for brief")
        msg = (
            f"📍 Аналітичний бриф — {today_str}\n"
            f"Знайдено {len(new_results)} нових джерел, але OPENAI_API_KEY не налаштовано."
        )
        return msg

    analysis = _analyze_with_ai(new_results, stats, today_str)

    # 5b. Persist brief output so presentation can reference the exact text
    if analysis and not dry_run:
        from src.storage.sqlite_store import save_daily_brief
        save_daily_brief(analysis, run_date=today_str)

    # 6. Format message
    sections = analysis.get("sections") or []
    if not sections:
        msg = (
            f"📍 Аналітичний бриф — {today_str}\n"
            "Сьогодні нових релевантних сигналів не знайдено."
        )
    else:
        msg = _format_telegram(analysis, today_str)

    # 7. Send or log
    if dry_run:
        logger.info(
            f"[DRY RUN] Brief ready ({len(msg)} chars, {len(sections)} sections):\n"
            f"{msg[:600]}{'…' if len(msg) > 600 else ''}"
        )
    elif not skip_alerts:
        _send_brief(msg)

    return msg
