"""
City-level market intelligence aggregation.

Combines retail intelligence articles, job signals, Aurora map changes, and
competitor presence into a single city_market_score.  This score is used for
prioritisation in reports only — it does NOT generate Aurora predictions.
"""
from collections import defaultdict

from src.config import setup_logging

logger = setup_logging("analysis.market_intelligence")

# Score weights
_W = {
    "aurora_direct": 30,       # article explicitly mentions Aurora
    "competitor_expansion": 15, # named competitor opening in that city
    "retail_park": 12,          # new retail park being built / announced
    "mall_leasing": 10,         # leasing / tenant announcement
    "shopping_center": 8,       # shopping centre news
    "generic_retail": 5,        # generic opening / expansion news
    "aurora_job": 20,           # Aurora hiring in that city
    "high_signal_job": 8,       # store-manager-level job in that city
    "competitor_store": 4,      # confirmed competitor store in that city
    "aurora_map_change": 50,    # Aurora actually opened / changed in that city
}


def _norm(city: str) -> str:
    return city.lower().strip()


def compute_city_market_scores(
    intel_articles: list[dict],
    jobs: list[dict],
    map_changes: list[dict],
    competitor_stores: dict[str, list[dict]],
) -> list[dict]:
    """
    Return a list of dicts sorted by score desc:
      { city, score, breakdown, aurora_specific }
    """
    scores: dict[str, float] = defaultdict(float)
    breakdown: dict[str, dict] = defaultdict(lambda: defaultdict(int))

    # ── Retail intelligence articles ──────────────────────────────────────────
    for a in intel_articles:
        cat = a.get("signal_category", "generic_retail")
        w = _W.get(cat, 5)
        for city in a.get("cities_mentioned", []):
            ck = _norm(city)
            scores[ck] += w
            breakdown[ck][cat] += 1

    # ── Job signals ───────────────────────────────────────────────────────────
    for job in jobs:
        is_aurora = job.get("is_aurora_company") or any(
            kw in (job.get("company", "") + job.get("title", "")).lower()
            for kw in ("aurora", "multimarket")
        )
        job_w = _W["aurora_job"] if is_aurora else (
            _W["high_signal_job"] if job.get("signal_score", 0) >= 2 else 0
        )
        if not job_w:
            continue
        for city in job.get("cities_mentioned", []):
            ck = _norm(city)
            scores[ck] += job_w
            breakdown[ck]["aurora_job" if is_aurora else "high_signal_job"] += 1

    # ── Aurora map changes (strongest signal) ────────────────────────────────
    for change in map_changes:
        store = change.get("store") or {}
        city = store.get("city", change.get("city", ""))
        if city:
            ck = _norm(city)
            scores[ck] += _W["aurora_map_change"]
            breakdown[ck]["aurora_map_change"] += 1

    # ── Competitor stores ────────────────────────────────────────────────────
    for brand_stores in competitor_stores.values():
        for s in brand_stores:
            city = s.get("city", "")
            if city:
                ck = _norm(city)
                scores[ck] += _W["competitor_store"]
                breakdown[ck]["competitor_store"] += 1

    # ── Build output ──────────────────────────────────────────────────────────
    result = []
    for city_key, score in sorted(scores.items(), key=lambda x: -x[1]):
        bd = dict(breakdown[city_key])
        has_aurora = (
            bd.get("aurora_map_change", 0) > 0
            or bd.get("aurora_direct", 0) > 0
            or bd.get("aurora_job", 0) > 0
        )
        result.append({
            "city": city_key.title(),
            "score": round(score, 1),
            "aurora_specific": has_aurora,
            "breakdown": bd,
        })

    logger.info(
        f"City market scores: {len(result)} cities, "
        f"top={result[0]['city']}({result[0]['score']:.0f})" if result else "no cities"
    )
    return result
