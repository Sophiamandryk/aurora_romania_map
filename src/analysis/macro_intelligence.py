"""
Macro-economic intelligence module for section 2.1.
Runs targeted Tavily searches against authoritative government/central-bank sources
for Romania and Ukraine, then synthesizes short analytical bullets via GPT-4o-mini.
"""
import json
import time
import requests
from datetime import date

from src.config import TAVILY_API_KEY, OPENAI_API_KEY, REQUEST_TIMEOUT, setup_logging
from modules._tavily import validate_results

logger = setup_logging("analysis.macro_intel")

_TAVILY_URL = "https://api.tavily.com/search"
_MODEL = "gpt-4o-mini"
_QUERY_DELAY = 0.3
_MAX_RESULTS = 4
_SNIPPET_LEN = 300

# (country, topic_key, query_string, include_domains)
_QUERIES: list[tuple[str, str, str, list[str]]] = [
    # ── Romania — broad news queries, no domain restrictions ──────────────────
    ("ro", "macro",
     "Romania inflation rate BNR interest rate GDP growth 2026",
     []),
    ("ro", "fiscal",
     "Romania fiscal policy budget deficit tax changes 2026",
     []),
    ("ro", "consumer",
     "Romania consumer confidence retail spending discount stores 2026",
     []),
    ("ro", "labor",
     "Romania unemployment rate wages labor market retail 2026",
     []),
    ("ro", "trade",
     "Romania trade balance imports exports 2026",
     []),
    ("ro", "energy",
     "Romania electricity gas prices energy tariffs retailers 2026",
     []),
    # ── Ukraine — broad news queries ──────────────────────────────────────────
    ("ua", "macro",
     "Ukraine inflation NBU interest rate GDP economic growth 2026",
     []),
    ("ua", "fiscal",
     "Ukraine tax policy fiscal changes business regulation 2026",
     []),
    ("ua", "consumer",
     "Ukraine consumer spending retail market purchasing power 2026",
     []),
    ("ua", "labor",
     "Ukraine unemployment employment wages labor market 2026",
     []),
    ("ua", "trade",
     "Ukraine trade imports exports customs 2026",
     []),
    ("ua", "energy",
     "Ukraine electricity gas tariffs energy prices retailers 2026",
     []),
]

_TOPIC_UA = {
    "macro":    "Макроекономічні показники",
    "fiscal":   "Бюджетна та податкова політика",
    "consumer": "Споживчі настрої та купівельна спроможність",
    "labor":    "Ринок праці",
    "trade":    "Імпорт/експорт та логістика",
    "energy":   "Енергетика та вплив на ритейл",
}

_SYSTEM = """\
You are a macroeconomic analyst for Aurora Multimarket, a Ukrainian discount retailer expanding in Romania.
Synthesize the source data into a concise executive snapshot for the retail strategy team.

GROUNDING RULE: Only use numbers, facts, and claims that appear explicitly in the provided source snippets.
Do not use training knowledge, inference, or extrapolation to fill in missing values.
If a field has no supporting data in the sources, set it to "—" (dash). Never invent figures.

CRITICAL RULES — follow exactly:
- ALWAYS include the exact number/percentage if it appears anywhere in the source data
- Format: "X% — короткий контекст" (e.g. "5.2% — зростання", "15% — незмінна", "3.2% — низька")
- Search every snippet carefully for numbers before writing a descriptive phrase
- A vague phrase like "висока безробіття" is WRONG if the source contains "10.5%" — use "10.5% — висока"
- Write in Ukrainian
- NEVER return null or an empty string; use "—" for fields with no source data
- If truly no number found: use a short phrase (max 5 words) describing the trend, only if supported by a source

Examples of CORRECT values: "5.1% — зростання", "15% — незмінна", "3.25% — низька", "-32 — песимізм"
Examples of WRONG values: "висока безробіття" (use "X% — висока"), "тарифи ростуть" (use "↑X% від дата" if available)
Examples of NO-DATA response: "—" (not a made-up phrase)

aurora_bullets: exactly 2 bullets (max 8 words each) on implications for Aurora's Romania expansion,
derived only from the source data provided.

Return valid JSON only, no markdown:
{
  "romania": {
    "inflation":          "<value in Ukrainian>",
    "bnr_rate":           "<value in Ukrainian>",
    "unemployment":       "<value in Ukrainian>",
    "consumer_sentiment": "<value in Ukrainian>",
    "energy":             "<value in Ukrainian>",
    "fiscal":             "<value in Ukrainian>"
  },
  "ukraine": {
    "inflation":    "<value in Ukrainian>",
    "nbu_rate":     "<value in Ukrainian>",
    "unemployment": "<value in Ukrainian>",
    "wages":        "<value in Ukrainian>",
    "energy":       "<value in Ukrainian>",
    "fiscal":       "<value in Ukrainian>"
  },
  "aurora_bullets": ["<bullet 1 in Ukrainian>", "<bullet 2 in Ukrainian>"]
}"""


def _call_tavily(query: str, domains: list[str], days: int = 7) -> list[dict]:
    if not TAVILY_API_KEY:
        return []
    payload = {
        "api_key":      TAVILY_API_KEY,
        "query":        query,
        "search_depth": "basic",
        "max_results":  _MAX_RESULTS,
        "days":         days,
    }
    if domains:
        payload["include_domains"] = domains

    try:
        resp = requests.post(_TAVILY_URL, json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        raw = [
            {
                "title":          r.get("title", ""),
                "url":            r.get("url", ""),
                "snippet":        (r.get("content") or r.get("snippet", ""))[:500],
                "published_date": r.get("published_date", ""),
            }
            for r in resp.json().get("results", []) if r.get("url")
        ]
        return validate_results(raw)
    except Exception as e:
        logger.warning(f"Tavily macro query failed '{query[:50]}': {e}")
        return []


def _fetch_all() -> dict[str, dict[str, list[dict]]]:
    """Run all queries. Tries last 7 days first, falls back to 30 days."""
    data: dict[str, dict[str, list[dict]]] = {"ro": {}, "ua": {}}
    for country, topic, query, domains in _QUERIES:
        # Try last 7 days first — prefer fresh data
        hits = _call_tavily(query, domains, days=7)
        if not hits:
            # Widen to last 30 days
            hits = _call_tavily(query, domains, days=30)
        if not hits:
            # Last resort: no domain restriction, last 30 days
            hits = _call_tavily(query, [], days=30)
        data[country].setdefault(topic, []).extend(hits)
        time.sleep(_QUERY_DELAY)
    total = sum(len(v) for c in data.values() for v in c.values())
    logger.info(f"Macro intel: {total} Tavily hits across {len(_QUERIES)} queries")
    return data


def _build_prompt_block(data: dict[str, dict[str, list[dict]]]) -> str:
    lines: list[str] = []
    for country, label in [("ro", "ROMANIA"), ("ua", "UKRAINE")]:
        lines.append(f"=== {label} ===")
        for topic, ua_label in _TOPIC_UA.items():
            hits = data.get(country, {}).get(topic, [])
            lines.append(f"[{ua_label}]")
            if hits:
                for h in hits[:_MAX_RESULTS]:
                    snippet = (h.get("snippet") or "")[:_SNIPPET_LEN]
                    lines.append(f"  Title: {h['title']}")
                    lines.append(f"  URL: {h['url']}")
                    lines.append(f"  Snippet: {snippet}")
            else:
                lines.append("  No data found.")
            lines.append("")
    return "\n".join(lines)


def run_macro_intelligence(today: str = None) -> dict:
    """
    Fetch authoritative macro data via Tavily and synthesize via GPT-4o-mini.
    Returns dict with 'romania', 'ukraine', 'aurora_implication' keys.
    """
    today = today or date.today().isoformat()

    if not TAVILY_API_KEY:
        logger.warning("TAVILY_API_KEY not set — macro intelligence skipped")
        return {}

    data = _fetch_all()
    prompt_block = _build_prompt_block(data)

    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set — macro AI synthesis skipped")
        return {}

    user_msg = (
        f"Date: {today}\n\nMacroeconomic source data:\n\n{prompt_block}\n\n"
        "Synthesize into concise Ukrainian analytical intelligence. Return JSON only."
    )

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=1200,
            response_format={"type": "json_object"},
        )
        result = json.loads(resp.choices[0].message.content)

        # Build per-country source lists so the formatter can show which country has backing
        ro_sources, ua_sources = [], []
        seen_urls: set = set()
        for hits in data.get("ro", {}).values():
            for h in hits[:1]:
                if h.get("url") and h["url"] not in seen_urls:
                    seen_urls.add(h["url"])
                    ro_sources.append({"title": h.get("title", ""), "url": h["url"]})
        for hits in data.get("ua", {}).values():
            for h in hits[:1]:
                if h.get("url") and h["url"] not in seen_urls:
                    seen_urls.add(h["url"])
                    ua_sources.append({"title": h.get("title", ""), "url": h["url"]})

        result["_sources_ro"] = ro_sources[:4]
        result["_sources_ua"] = ua_sources[:4]
        result["_sources"]    = (ro_sources + ua_sources)[:6]  # backwards compat

        # If a country has no source hits, mark its fields as unverified
        if not ua_sources:
            logger.warning("Macro intel: no verified sources for Ukraine — marking fields unverified")
            for key in list(result.get("ukraine", {}).keys()):
                val = result["ukraine"].get(key)
                if val and "⚠️" not in str(val):
                    result["ukraine"][key] = f"{val} ⚠️"
        if not ro_sources:
            logger.warning("Macro intel: no verified sources for Romania — marking fields unverified")
            for key in list(result.get("romania", {}).keys()):
                val = result["romania"].get(key)
                if val and "⚠️" not in str(val):
                    result["romania"][key] = f"{val} ⚠️"

        ro_fields = sum(1 for v in result.get("romania", {}).values() if v)
        ua_fields = sum(1 for v in result.get("ukraine", {}).values() if v)
        logger.info(
            f"Macro intel: Romania {ro_fields}/6 ({len(ro_sources)} src), "
            f"Ukraine {ua_fields}/6 ({len(ua_sources)} src)"
        )
        return result
    except Exception as e:
        logger.error(f"Macro intel AI synthesis failed: {e}")
        return {}
