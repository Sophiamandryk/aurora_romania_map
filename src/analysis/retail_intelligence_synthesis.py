"""
Retail intelligence synthesizer.
- Translates article titles (Romanian/English → Ukrainian) in batch
- Synthesizes news signals into Ukrainian analyst-style insights
- Generates "Why this matters for Aurora" explanations (rule-based)

Called once per pipeline run; result cached in memory to avoid duplicate API calls.
"""
import json
from collections import defaultdict
from src.config import setup_logging

logger = setup_logging("analysis.retail_intelligence")

# ── Module-level cache (keyed on frozenset of article URLs) ──────────────────
_CACHE: dict = {}

# ── Category config ────────────────────────────────────────────────────────────
_UA_CATEGORY_LABEL = {
    "aurora_confirmed":    "Aurora (підтверджено)",
    "aurora_mentioned":    "Aurora (згадується)",
    "aurora_direct":       "Aurora (прямий сигнал)",
    "competitor_expansion": "Розширення конкурентів",
    "retail_park":         "Ритейл-парки",
    "mall_leasing":        "ТЦ / оренда",
    "shopping_center":     "Торгові центри",
    "local_news":          "Локальні новини",
    "generic_market":      "Загальний ринок",
    "generic_retail":      "Роздрібна торгівля",
    "influencer_signal":   "Соціальні мережі",
}

_AURORA_CATS = {"aurora_confirmed", "aurora_mentioned", "aurora_direct"}
_COMP_CATS   = {"competitor_expansion"}
_INFRA_CATS  = {"retail_park", "mall_leasing", "shopping_center"}


# ── "Why this matters" — rule-based, no AI needed ────────────────────────────

def why_this_matters(article: dict, aurora_city_set: set[str]) -> str:
    cat    = article.get("signal_category", "")
    cities = article.get("cities_mentioned") or []
    in_aurora  = [c for c in cities if c.lower() in aurora_city_set]
    not_aurora = [c for c in cities if c.lower() not in aurora_city_set]

    if cat in _AURORA_CATS:
        if cities:
            return f"Прямий сигнал Aurora у {', '.join(cities[:2])} — потребує перевірки деталей."
        return "Офіційний сигнал Aurora — підтвердити відповідність на мапі."

    if cat in _COMP_CATS:
        if not_aurora:
            locs = ', '.join(not_aurora[:2])
            return f"Конкурент розширюється у {locs}, де Aurora ще відсутня — ризик втрати ринку."
        if in_aurora:
            locs = ', '.join(in_aurora[:2])
            return f"Конкурент активний у {locs} — Aurora вже представлена, але потрібен моніторинг."
        return "Конкурент розширює мережу по Румунії — зростає тиск на незайняті ринки Aurora."

    if cat in _INFRA_CATS:
        if not_aurora:
            city = not_aurora[0]
            return f"Новий ритейл-парк у {city}: Aurora відсутня, конкуренти вже активні — потенційна локація."
        if cities:
            city = cities[0]
            return f"Розвиток ритейл-інфраструктури у {city} — можливість для розміщення Aurora."
        return "Новий ритейл-парк або ТЦ — перевірити наявність Aurora серед орендарів."

    if not_aurora:
        return f"Ринкова активність у {', '.join(not_aurora[:2])} — Aurora відсутня у цих містах."
    return "Загальний сигнал ринкової активності в Румунії."


# ── OpenAI synthesis ──────────────────────────────────────────────────────────

_SYSTEM_UA = """Ти старший аналітик ритейлу для Aurora Romania. Пишеш виключно українською мовою.
Твоє завдання: перекласти заголовки статей та синтезувати ринкові сигнали у стислі аналітичні спостереження.

Правила:
- Зберігати назви компаній без змін: Pepco, TEDi, KiK, Action, Aurora Multimarket.
- Зберігати назви румунських міст без перекладу: Arad, Sibiu, Cluj-Napoca, etc.
- НЕ вигадувати розширення Aurora, якщо прямих доказів немає.
- Якщо сигналів Aurora немає — написати: "Прямих сигналів про розширення Aurora сьогодні не виявлено."
- Уникати фраз: "необхідно розглянути", "стратегічне значення", "важливо відзначити", "є можливість".
- Відповідай ТІЛЬКИ валідним JSON, без markdown-блоків."""

_SYNTHESIS_USER_TEMPLATE = """Проаналізуй такі новини з румунського ритейлу за сьогодні:

AURORA-СПЕЦИФІЧНІ СТАТТІ ({n_aurora}):
{aurora_block}

РОЗШИРЕННЯ КОНКУРЕНТІВ ({n_comp}):
{comp_block}

РИТЕЙЛ-ПАРКИ / ТЦ ({n_infra}):
{infra_block}

ЗАГАЛЬНИЙ РИНОК ({n_market}):
{market_block}

Поверни JSON з такою структурою:
{{
  "translations": {{
    "Original title 1": "Переклад 1",
    "Original title 2": "Переклад 2"
  }},
  "market_narrative": "2-3 речення про загальний стан румунського ритейлу сьогодні. Конкретні факти, назви брендів і міст.",
  "aurora_insights": "1-3 речення про Aurora-специфічні сигнали. Якщо їх немає — 'Прямих сигналів про розширення Aurora сьогодні не виявлено.'",
  "competitor_insights": "1-3 речення про розширення конкурентів. Що це означає для Aurora.",
  "infrastructure_insights": "1-2 речення про ритейл-парки / ТЦ або null якщо немає."
}}
Відповідай ТІЛЬКИ JSON."""


def _article_block(articles: list[dict], max_n: int = 8) -> str:
    lines = []
    for a in articles[:max_n]:
        title = a.get("title", "")
        source = a.get("source", "")
        cities = ", ".join((a.get("cities_mentioned") or [])[:3])
        city_tag = f" | міста: {cities}" if cities else ""
        lines.append(f"- {title} (src: {source}{city_tag})")
    return "\n".join(lines) if lines else "(немає)"


def _openai_synthesis(news: list[dict]) -> dict:
    from openai import OpenAI
    from src.config import OPENAI_API_KEY

    by_cat: dict[str, list] = defaultdict(list)
    for a in news:
        cat = a.get("signal_category", "generic_market")
        by_cat[cat].append(a)

    aurora_articles = []
    for cat in _AURORA_CATS:
        aurora_articles.extend(by_cat.get(cat, []))
    comp_articles = list(by_cat.get("competitor_expansion", []))
    infra_articles = []
    for cat in _INFRA_CATS:
        infra_articles.extend(by_cat.get(cat, []))
    market_articles = []
    for cat in ("local_news", "generic_market", "generic_retail"):
        market_articles.extend(by_cat.get(cat, []))

    prompt = _SYNTHESIS_USER_TEMPLATE.format(
        n_aurora=len(aurora_articles),
        aurora_block=_article_block(aurora_articles),
        n_comp=len(comp_articles),
        comp_block=_article_block(comp_articles),
        n_infra=len(infra_articles),
        infra_block=_article_block(infra_articles),
        n_market=len(market_articles),
        market_block=_article_block(market_articles, max_n=5),
    )

    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=900,
        temperature=0.2,
        messages=[
            {"role": "system", "content": _SYSTEM_UA},
            {"role": "user", "content": prompt},
        ],
    )
    raw = response.choices[0].message.content.strip()
    # Strip markdown fences if GPT added them
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    result = json.loads(raw)
    logger.info(
        f"Synthesis: {len(result.get('translations', {}))} titles translated, "
        f"market_narrative={'yes' if result.get('market_narrative') else 'no'}"
    )
    return result


# ── Rule-based fallback (no API key) ─────────────────────────────────────────

def _rule_based_synthesis(news: list[dict]) -> dict:
    aurora = [a for a in news if a.get("signal_category") in _AURORA_CATS]
    comp   = [a for a in news if a.get("signal_category") in _COMP_CATS]
    infra  = [a for a in news if a.get("signal_category") in _INFRA_CATS]

    if aurora:
        aurora_text = (
            f"Зафіксовано {len(aurora)} Aurora-специфічних сигналів. "
            f"Заголовки: {'; '.join(a['title'][:60] for a in aurora[:3])}."
        )
    else:
        aurora_text = "Прямих сигналів про розширення Aurora сьогодні не виявлено."

    if comp:
        brands = list({a.get("signals", {}).get("company", "") or "" for a in comp} - {""})
        comp_text = (
            f"Конкуренти активно розширюються: {len(comp)} статей. "
            + (f"Бренди: {', '.join(brands[:4])}." if brands else "")
        )
    else:
        comp_text = "Явних сигналів розширення конкурентів сьогодні не виявлено."

    if infra:
        infra_text = (
            f"Зафіксовано {len(infra)} сигналів про ритейл-парки та ТЦ — "
            "перевірити список орендарів."
        )
    else:
        infra_text = None

    market_text = (
        f"Проаналізовано {len(news)} ринкових сигналів. "
        "Румунський ритейл-ринок демонструє активне будівництво нових торгових об'єктів."
    )

    return {
        "translations": {},
        "market_narrative": market_text,
        "aurora_insights": aurora_text,
        "competitor_insights": comp_text,
        "infrastructure_insights": infra_text,
    }


# ── Public API ────────────────────────────────────────────────────────────────

def synthesize(news: list[dict], current_stores: list[dict] = None) -> dict:
    """
    Synthesize news signals into Ukrainian retail intelligence.
    Result is cached for the current Python session (keyed on article URLs).

    Returns:
      translations: dict[original_title → ukrainian_title]
      market_narrative: str
      aurora_insights: str
      competitor_insights: str
      infrastructure_insights: str | None
    """
    from src.config import OPENAI_API_KEY

    cache_key = frozenset(a.get("url", a.get("title", "")) for a in news[:40])
    if cache_key in _CACHE:
        logger.debug("Synthesis: cache hit")
        return _CACHE[cache_key]

    if not OPENAI_API_KEY:
        result = _rule_based_synthesis(news)
    else:
        try:
            result = _openai_synthesis(news)
        except Exception as e:
            logger.warning(f"OpenAI synthesis failed ({e}) — using rule-based fallback")
            result = _rule_based_synthesis(news)

    # Add aurora_city_set for why_this_matters callers
    aurora_cities = {s.get("city", "").lower().strip()
                     for s in (current_stores or []) if s.get("city")}
    result["_aurora_city_set"] = aurora_cities

    # Enrich articles with translated_title and why_this_matters
    translations = result.get("translations", {})
    for a in news:
        orig = a.get("title", "")
        a["translated_title"] = translations.get(orig, orig)
        if "why_this_matters" not in a:
            a["why_this_matters"] = why_this_matters(a, aurora_cities)

    _CACHE[cache_key] = result
    return result
