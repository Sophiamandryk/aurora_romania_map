"""
2.3 Industry Research — weekly research firm reports for Ukraine and Romania.
3 sub-topics × 2 countries → GPT-4o-mini paragraph summary.
Fallback: 7 days → 30 days. Discards results without a parseable published_date.
"""
from src.config import OPENAI_API_KEY, setup_logging
from modules._tavily import collect

logger = setup_logging("modules.industry_research")

_FALLBACK = [7]

_CONSUMER_DOMAINS  = ["gradus.app", "kantar.com", "nielseniq.com", "euromonitor.com"]
_REALESTATE_DOMAINS = ["jll.com", "cushmanwakefield.com", "cbre.com", "colliers.com"]
_FORECAST_DOMAINS  = [
    "mckinsey.com", "deloitte.com", "pwc.com", "ey.com", "kpmg.com",
    "oliverwyman.com", "kse.ua", "case-ukraine.com.ua", "bruegel.org",
]

_SYSTEM = (
    "You are a retail intelligence analyst writing a daily briefing. "
    "Given fresh research snippets on a topic and country, write a 3–5 sentence summary in Ukrainian: "
    "state the key finding, name specific companies/reports/figures, "
    "highlight any CEE or UA–RO comparison if present. "
    "Start with the date range covered "
    "(e.g. 'За останній тиждень...' or 'За останні 30 днів...'). "
    "Neutral tone, no bullets, plain paragraph."
)

_SUBTOPICS = [
    # ── Ukraine ──────────────────────────────────────────────────────────────
    {
        "id": "consumer_ua", "country": "ua",
        "label": "Retail & споживча поведінка — Україна",
        "queries": [
            "GRADUS Kantar NielsenIQ retail consumer Ukraine report 2026",
            "Euromonitor NielsenIQ retail consumer behavior Ukraine 2026",
            "NielsenIQ consumer trends retail Ukraine 2026",
        ],
        "domains": _CONSUMER_DOMAINS,
    },
    {
        "id": "realestate_ua", "country": "ua",
        "label": "Комерційна нерухомість — Україна",
        "queries": [
            "JLL Cushman Wakefield retail real estate Ukraine 2026",
            "JLL commercial real estate shopping centers Ukraine report",
            "CBRE Colliers retail property Ukraine 2026",
        ],
        "domains": _REALESTATE_DOMAINS,
    },
    {
        "id": "forecast_ua", "country": "ua",
        "label": "Галузеві прогнози & CEE — Україна",
        "queries": [
            "McKinsey Deloitte PwC EY retail Ukraine forecast 2026",
            "KSE CASE Ukraine retail industry outlook 2026",
            "KPMG Oliver Wyman CEE Ukraine retail forecast 2026",
        ],
        "domains": _FORECAST_DOMAINS,
    },
    # ── Romania ───────────────────────────────────────────────────────────────
    {
        "id": "consumer_ro", "country": "ro",
        "label": "Retail & споживча поведінка — Румунія",
        "queries": [
            "Kantar NielsenIQ Euromonitor retail consumer Romania report 2026",
            "NielsenIQ consumer trends retail Romania 2026",
            "Euromonitor retail consumer behavior Romania CEE 2026",
        ],
        "domains": _CONSUMER_DOMAINS,
    },
    {
        "id": "realestate_ro", "country": "ro",
        "label": "Комерційна нерухомість — Румунія",
        "queries": [
            "JLL Cushman Wakefield retail real estate Romania 2026",
            "JLL commercial real estate shopping centers Romania CEE",
            "CBRE Colliers retail property Romania 2026",
        ],
        "domains": _REALESTATE_DOMAINS,
    },
    {
        "id": "forecast_ro", "country": "ro",
        "label": "Галузеві прогнози & CEE — Румунія",
        "queries": [
            "McKinsey Deloitte PwC EY Romania retail forecast 2026",
            "Deloitte PwC Romania CEE retail outlook report 2026",
            "Bruegel KPMG Oliver Wyman Romania CEE industry forecast 2026",
        ],
        "domains": _FORECAST_DOMAINS,
    },
]


def _summarize(label: str, results: list[dict], days_used: int) -> str:
    if not results:
        return f"За останні {days_used} дн. відповідних звітів не знайдено."
    if not OPENAI_API_KEY:
        return f"OPENAI_API_KEY не налаштовано — резюме недоступне ({len(results)} джерел)."

    snippets = "\n\n".join(
        f"Title: {r['title']}\nURL: {r['url']}\n"
        f"Date: {r.get('published_date') or 'n/a'}\n{r['snippet']}"
        for r in results[:8]
    )
    user_msg = f"Topic: {label}\nDays searched: {days_used}\n\n{snippets}"

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
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"AI summary '{label}': {e}")
        return f"AI недоступний ({len(results)} джерел знайдено)."


def run() -> list[dict]:
    """Run all 6 subtopics (3 UA + 3 RO). Returns list of result dicts."""
    output = []
    for st in _SUBTOPICS:
        logger.info(f"Industry research: {st['label']}")

        # Stage 1: target domains + require published_date
        results, days = collect(
            st["queries"], _FALLBACK, min_n=2,
            domains=st["domains"], require_date=True,
        )
        # Stage 2: drop domain restriction, keep require_date
        if len(results) < 2:
            results, days = collect(
                st["queries"], _FALLBACK, min_n=2, require_date=True,
            )
        # Stage 3: drop require_date (Tavily often omits published_date for research PDFs)
        if len(results) < 2:
            results, days = collect(
                st["queries"], _FALLBACK, min_n=2,
            )

        logger.info(f"  {len(results)} results (days={days})")
        summary = _summarize(st["label"], results, days)
        output.append({
            "id":            st["id"],
            "label":         st["label"],
            "summary":       summary,
            "results_count": len(results),
            "days_used":     days,
        })
    return output
