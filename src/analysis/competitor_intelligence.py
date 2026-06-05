"""
AI synthesis of competitor intelligence for section 1.2.
Primary source: Tavily web search results. Supporting: catalogues, news, Instagram.
Produces per-brand Ukrainian strategic intelligence (pricing, campaign, categories, expansion).
"""
import json
from datetime import date

from src.config import OPENAI_API_KEY, setup_logging

logger = setup_logging("analysis.competitor_intel")

_BRANDS = ["Pepco", "TEDi", "KiK", "Action", "Penny", "Profi", "Mr.DIY"]

_SYSTEM = """\
You are a retail intelligence analyst for Aurora Multimarket, a Ukrainian variety/discount retailer expanding in Romania.
Your task: analyze multi-source competitor data and produce concise strategic intelligence in Ukrainian.

For each competitor brand with available data, write 2–4 bullet points on the most relevant of:
- Pricing/discount strategy (what discounts, how aggressive, seasonal pricing)
- Campaign/category focus (which product categories they're pushing this week)
- Seasonal strategy (summer/back-to-school/holiday themes visible in promotions)
- Confirmed expansion (ONLY if source explicitly states location + date — never speculate)

Rules:
- Write entirely in Ukrainian
- Max 4 bullets per brand — include only the most actionable insights
- Convert Romanian source text into strategic insight, NOT literal translation
- Deduplicate: if multiple sources say the same thing, write it once
- Expansion bullet only for confirmed, explicitly dated/located facts
- key_insight: single most important sentence for Aurora decision-making
- sources: list 1–3 URLs from the provided Tavily results that back up the bullets for this brand — copy URLs exactly as given, do not invent
- Omit brands with no meaningful data
- Return valid JSON only, no markdown fences

JSON schema:
{
  "brands": {
    "<BrandName>": {
      "key_insight": "<1 sentence>",
      "bullets": ["<bullet>", ...],
      "expansion": "<confirmed expansion text or null>",
      "sources": ["<url1>", "<url2>"]
    }
  },
  "market_pattern": "<1-2 sentences: what do competitors collectively signal this week>",
  "aurora_implication": "<1-2 sentences: what does this mean for Aurora's competitive positioning>"
}"""

_USER_TEMPLATE = """\
Date: {date}

=== TAVILY WEB SEARCH RESULTS (primary source) ===
{tavily_text}

=== CATALOGUE / PROMO PAGE SCRAPES (supporting) ===
{catalogue_text}

=== NEWS ARTICLES (supporting) ===
{news_text}

=== INSTAGRAM ACTIVITY (supporting) ===
{instagram_text}

Synthesize per-brand Ukrainian intelligence for these competitors: {brands}.
Return JSON only."""


def _fmt_tavily(results: list[dict]) -> str:
    if not results:
        return "No Tavily results available."
    lines = []
    for r in results[:70]:
        topic = r.get("query_topic", "")
        title = r.get("title", "")
        snippet = (r.get("snippet", "") or "")[:300]
        url = r.get("url", "")
        lines.append(f"[{topic}] {title} — {snippet} ({url})")
    return "\n".join(lines)


def _fmt_catalogue(catalogue_data: list[dict]) -> str:
    if not catalogue_data:
        return "No catalogue data."
    lines = []
    for c in catalogue_data:
        if c.get("error"):
            continue
        brand = c.get("brand", "")
        count = c.get("promo_count", 0)
        cats = ", ".join(c.get("page_categories", [])[:6])
        discs = ", ".join(c.get("page_discounts", [])[:5])
        promos = c.get("promos", [])
        parts = [f"{brand}: {count} promos"]
        if cats:
            parts.append(f"categories: {cats}")
        if discs:
            parts.append(f"discounts: {discs}")
        if promos:
            sample = "; ".join(p.get("title", "") for p in promos[:4] if p.get("title"))
            if sample:
                parts.append(f"sample items: {sample}")
        lines.append(" | ".join(parts))
    return "\n".join(lines) if lines else "No catalogue data."


def _fmt_news(news_articles: list[dict]) -> str:
    comp_news = []
    seen: set[str] = set()
    for a in (news_articles or []):
        full = (a.get("title", "") + " " + (a.get("excerpt", "") or "")).lower()
        for brand in _BRANDS:
            if brand.lower() in full:
                url = a.get("url", "")
                if url not in seen:
                    seen.add(url)
                    comp_news.append(a)
                break
    if not comp_news:
        return "No competitor news."
    lines = []
    for a in comp_news[:20]:
        src = a.get("source", "")
        pub = (a.get("published_date", "") or "")[:10]
        title = a.get("title", "")
        excerpt = (a.get("excerpt", "") or "")[:250]
        lines.append(f"[{src} {pub}] {title} — {excerpt}")
    return "\n".join(lines)


def _fmt_instagram(social_analysis: dict) -> str:
    if not social_analysis:
        return "No Instagram data."
    lines = []
    for brand, summary in (social_analysis.get("brand_summary") or {}).items():
        if summary and brand != "Aurora":
            lines.append(f"{brand}: {summary}")
    promos = (social_analysis.get("commercial_digest") or {}).get("promos", [])
    if promos:
        lines.append("Promos seen: " + "; ".join(promos[:8]))
    return "\n".join(lines) if lines else "No Instagram data."


def synthesize_competitor_intel(
    tavily_results: list[dict],
    catalogue_data: list[dict],
    news_articles: list[dict],
    social_analysis: dict,
    today: str = None,
) -> dict:
    """
    Call GPT-4o-mini to synthesize competitor intelligence from all sources.
    Returns a dict with 'brands', 'market_pattern', 'aurora_implication'.
    """
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set — competitor intel synthesis skipped")
        return {}

    today = today or date.today().isoformat()

    user_msg = _USER_TEMPLATE.format(
        date=today,
        tavily_text=_fmt_tavily(tavily_results),
        catalogue_text=_fmt_catalogue(catalogue_data),
        news_text=_fmt_news(news_articles),
        instagram_text=_fmt_instagram(social_analysis),
        brands=", ".join(_BRANDS),
    )

    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=1600,
            response_format={"type": "json_object"},
        )
        result = json.loads(resp.choices[0].message.content)
        logger.info(f"Competitor intel: {len(result.get('brands', {}))} brands synthesized")
        return result
    except Exception as e:
        logger.error(f"Competitor intel synthesis failed: {e}")
        return {}
