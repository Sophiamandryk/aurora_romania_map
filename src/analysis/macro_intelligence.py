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

logger = setup_logging("analysis.macro_intel")

_TAVILY_URL = "https://api.tavily.com/search"
_MODEL = "gpt-4o-mini"
_QUERY_DELAY = 0.3
_MAX_RESULTS = 4
_SNIPPET_LEN = 300

# (country, topic_key, query_string, include_domains)
_QUERIES: list[tuple[str, str, str, list[str]]] = [
    # ── Romania ──────────────────────────────────────────────────────────────
    ("ro", "macro",
     "BNR rata dobanda inflatie PIB crestere economica Romania 2026",
     ["bnr.ro", "insse.ro"]),
    ("ro", "fiscal",
     "modificari fiscale impozite taxe reglementare lege Romania 2026",
     ["monitoruloficial.ro"]),
    ("ro", "consumer",
     "cheltuieli consum populatie putere cumparare incredere consumatori Romania 2026",
     ["insse.ro"]),
    ("ro", "labor",
     "piata muncii rata somaj salarii angajare migratie forta munca Romania 2026",
     ["ejobs.ro", "insse.ro"]),
    ("ro", "trade",
     "import export balanta comerciala vamala comert Romania 2026",
     ["customs.ro", "insse.ro"]),
    ("ro", "energy",
     "tarife energie electricitate gaz consumatori reglementare ANRE Romania 2026",
     ["anre.ro"]),
    # ── Ukraine ───────────────────────────────────────────────────────────────
    ("ua", "macro",
     "НБУ облікова ставка інфляція ВВП зростання економіка Україна 2026",
     ["bank.gov.ua", "ukrstat.gov.ua"]),
    ("ua", "fiscal",
     "зміни оподаткування закони регулювання бізнес Україна 2026",
     ["zakon.rada.gov.ua"]),
    ("ua", "consumer",
     "споживання населення купівельна спроможність роздрібна торгівля Україна 2026",
     ["ukrstat.gov.ua"]),
    ("ua", "labor",
     "ринок праці зайнятість зарплати міграція безробіття Україна 2026",
     ["work.ua", "ukrstat.gov.ua"]),
    ("ua", "trade",
     "митниця імпорт експорт торгівля зовнішньоекономічна діяльність Україна 2026",
     ["customs.gov.ua"]),
    ("ua", "energy",
     "тарифи електроенергія газ НКРЕКП енергетика вплив бізнес Україна 2026",
     ["nerc.gov.ua"]),
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
You are a macroeconomic data extractor for Aurora Multimarket, a Ukrainian discount retailer in Romania.
Extract the latest KEY NUMBERS and brief signals from the source data.
Output is a fast-scan executive snapshot — numbers first, ultra-short.

Rules:
- PRIORITIZE exact numbers (%, rates, figures) over descriptions
- Each field: max 1 short value or phrase — ideally just a number or "number + 1 word"
- NO sentences, NO explanations, NO analysis paragraphs
- If a number is available, use it; if not, use 2-3 words max
- null if truly no data found

Good field values: "5.1%", "6.5%", "слабкі", "тарифи ↑", "дефіцит кадрів", "стабільний"
Bad field values: long sentences, explanations, copied article text

aurora_bullets: exactly 2 short bullets (max 6 words each) on what these numbers mean for Aurora.

Return valid JSON only, no markdown:
{
  "romania": {
    "inflation":          "<% or null>",
    "bnr_rate":           "<% or null>",
    "unemployment":       "<% or null>",
    "consumer_sentiment": "<1-3 words or null>",
    "energy":             "<1-4 words or null>",
    "fiscal":             "<1-4 words or null>"
  },
  "ukraine": {
    "inflation":    "<% or null>",
    "nbu_rate":     "<% or null>",
    "unemployment": "<% or null>",
    "wages":        "<1-3 words or null>",
    "energy":       "<1-4 words or null>",
    "fiscal":       "<1-4 words or null>"
  },
  "aurora_bullets": ["<bullet 1>", "<bullet 2>"]
}"""


def _call_tavily(query: str, domains: list[str]) -> list[dict]:
    if not TAVILY_API_KEY:
        return []
    payload = {
        "api_key":      TAVILY_API_KEY,
        "query":        query,
        "search_depth": "basic",
        "max_results":  _MAX_RESULTS,
    }
    if domains:
        payload["include_domains"] = domains

    try:
        resp = requests.post(_TAVILY_URL, json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
            for r in resp.json().get("results", []) if r.get("url")
        ]
    except Exception as e:
        logger.warning(f"Tavily macro query failed '{query[:50]}': {e}")
        return []


def _fetch_all() -> dict[str, dict[str, list[dict]]]:
    """Run all queries. Returns {country: {topic: [hits]}}."""
    data: dict[str, dict[str, list[dict]]] = {"ro": {}, "ua": {}}
    for country, topic, query, domains in _QUERIES:
        hits = _call_tavily(query, domains)
        if not hits:
            # Retry without domain restriction
            hits = _call_tavily(query, [])
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
        ro_fields = sum(1 for v in result.get("romania", {}).values() if v)
        ua_fields = sum(1 for v in result.get("ukraine", {}).values() if v)
        bullets = len(result.get("aurora_bullets") or [])
        logger.info(f"Macro intel: Romania {ro_fields}/6, Ukraine {ua_fields}/6, Aurora {bullets} bullets")
        return result
    except Exception as e:
        logger.error(f"Macro intel AI synthesis failed: {e}")
        return {}
