"""
2.2 Retail News — daily retail intelligence for Ukraine and Romania.
6 sub-topics × 3 queries (UA / RO / EN) → dedup → GPT-4o-mini paragraph summary.
"""
from src.config import OPENAI_API_KEY, setup_logging
from modules._tavily import collect

logger = setup_logging("modules.retail_news")

_FALLBACK = [1, 2, 7]

_SYSTEM = (
    "You are a retail intelligence analyst writing a daily briefing. "
    "Given fresh news snippets on a topic covering Ukraine and Romania, "
    "write a 3–5 sentence summary in Ukrainian: "
    "state the key finding of the day, name specific companies/reports/figures, "
    "highlight any UA–RO comparison if present. "
    "Start with the date range covered "
    "(e.g. 'За останню добу...' or 'За останні 2 дні...'). "
    "Neutral tone, no bullets, plain paragraph."
)

_SUBTOPICS = [
    {
        "id": "ma_investments",
        "label": "M&A, інвестиції, нові гравці ринку",
        "queries": [
            "M&A злиття поглинання інвестиції ритейл Україна Румунія",
            "fuziuni achizitii investitii retail piata Romania Ucraina",
            "retail M&A investment new market entrants Ukraine Romania",
        ],
    },
    {
        "id": "openings",
        "label": "Відкриття / закриття магазинів, розширення мережі",
        "queries": [
            "відкриття закриття магазинів розширення мережі ритейл Україна Румунія",
            "deschideri inchideri magazine extindere retea retail Romania Ucraina",
            "store openings closures network expansion retail Ukraine Romania",
        ],
    },
    {
        "id": "formats",
        "label": "Нові формати, концепції, приватні марки",
        "queries": [
            "новий формат магазину концепція приватна марка ритейл Україна Румунія",
            "format nou concept marca proprie retail Romania Ucraina",
            "new store format concept private label retail Ukraine Romania",
        ],
    },
    {
        "id": "ecommerce",
        "label": "E-commerce, маркетплейси, омніканальність",
        "queries": [
            "електронна торгівля маркетплейс омніканальність ритейл Україна Румунія",
            "comert electronic marketplace omnichannel retail Romania Ucraina",
            "e-commerce marketplace omnichannel retail Ukraine Romania",
        ],
    },
    {
        "id": "consumer",
        "label": "Споживчі тренди, поведінка покупців",
        "queries": [
            "споживчі тренди поведінка покупців торгівля Україна Румунія",
            "tendinte consum comportament cumparator retail Romania Ucraina",
            "consumer trends shopper behavior retail Ukraine Romania",
        ],
    },
    {
        "id": "logistics",
        "label": "Логістика, склади, ланцюги постачання",
        "queries": [
            "логістика склади ланцюги постачання ритейл Україна Румунія",
            "logistica depozite lant aprovizionare retail Romania Ucraina",
            "logistics warehouses supply chain retail Ukraine Romania",
        ],
    },
]


def _summarize(label: str, results: list[dict], days_used: int) -> str:
    if not results:
        return f"За останні {days_used} дн. релевантних новин не знайдено."
    if not OPENAI_API_KEY:
        return f"OPENAI_API_KEY не налаштовано — резюме недоступне ({len(results)} джерел)."

    snippets = "\n\n".join(
        f"Title: {r['title']}\nURL: {r['url']}\n"
        f"Date: {r.get('published_date') or 'n/a'}\n{r['snippet']}"
        for r in results[:10]
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
    """Run all 6 sub-topics. Returns list of result dicts."""
    output = []
    for st in _SUBTOPICS:
        logger.info(f"Retail news: {st['label']}")
        results, days = collect(st["queries"], _FALLBACK)
        logger.info(f"  {len(results)} results (days={days})")
        summary = _summarize(st["label"], results, days)
        output.append({
            "id":     st["id"],
            "label":  st["label"],
            "summary":       summary,
            "results_count": len(results),
            "days_used":     days,
        })
    return output
