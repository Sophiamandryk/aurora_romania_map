"""
Confidence scoring — wraps the explainable scoring engine.

Translates raw pipeline signals (map markers, jobs, news, Instagram) into
named evidence counts, then delegates to scoring.build_score() which returns
a transparent points breakdown instead of opaque floats.
"""
from src.config import setup_logging
from src.analysis.scoring import build_score, level_for_points
from src.data.ro_counties import normalize_city

logger = setup_logging("analysis.confidence")


def enrich_change_with_confidence(
    change: dict,
    jobs: list[dict],
    news: list[dict],
    instagram: list[dict],
) -> dict:
    """
    Attach explainable confidence scoring to a change event.
    Matches city from the change against job/news/social signals.
    """
    store = change.get("store") or {}
    change_type = change.get("change_type", "")
    city_raw = store.get("city", change.get("city", ""))
    city_norm = normalize_city(city_raw)

    # Market activity signals carry no Aurora evidence — confidence is not meaningful
    if change_type == "MARKET_ACTIVITY_SIGNAL":
        change["confidence"] = {"points": 0, "level": "LOW", "score": 0.0, "factors": []}
        return change

    # ── Gather matching signals for this city ─────────────────────────────────
    city_jobs = [
        j for j in jobs
        if city_norm and city_norm in [normalize_city(c) for c in j.get("cities_mentioned", [])]
    ]
    city_news = [
        a for a in news
        if city_norm and city_norm in (
            normalize_city(a.get("title", "") + " " + a.get("excerpt", ""))
            or normalize_city(" ".join(a.get("cities_mentioned", [])))
        )
    ]
    city_ig = [
        p for p in instagram
        if city_norm and city_norm in [normalize_city(c) for c in p.get("cities_mentioned", [])]
    ]

    # ── Build named evidence dict ─────────────────────────────────────────────
    evidence: dict[str, int] = {}

    # Map confirmation is the strongest single signal
    if change_type in ("NEW_STORE", "RELOCATED_STORE", "STORE_UPDATED"):
        evidence["aurora_map_confirmed"] = 1

    # Official Aurora announcement in news
    has_official = any(
        (a.get("signal_category") == "aurora_direct" or
         (a.get("signals") or {}).get("aurora_mentioned"))
        for a in city_news
    )
    if has_official:
        evidence["aurora_official_announcement"] = 1

    # Job signals — split store-manager level from generic
    store_mgr_jobs = [j for j in city_jobs if j.get("signal_score", 0) >= 3]
    generic_jobs = [j for j in city_jobs if j.get("signal_score", 0) < 3]
    if store_mgr_jobs:
        evidence["aurora_hiring_store_manager"] = len(store_mgr_jobs)
    if generic_jobs:
        evidence["aurora_hiring_generic"] = len(generic_jobs)

    # Instagram
    if city_ig:
        evidence["aurora_instagram_signal"] = len(city_ig)

    # News by category
    for article in city_news:
        cat = article.get("signal_category", "generic_retail")
        if cat == "retail_park":
            evidence["retail_park_announced"] = evidence.get("retail_park_announced", 0) + 1
        elif cat == "competitor_expansion":
            evidence["competitor_expansion_city"] = evidence.get("competitor_expansion_city", 0) + 1
        elif cat in ("shopping_center", "mall_leasing"):
            evidence["shopping_center_news"] = evidence.get("shopping_center_news", 0) + 1
        elif cat == "generic_retail":
            evidence["generic_retail_jobs"] = evidence.get("generic_retail_jobs", 0) + 1

    # Competitor stores already in city (from enriched competitor_analysis)
    comp = change.get("competitor_analysis", {})
    city_presence = comp.get("city_presence", {})
    if city_presence:
        evidence["competitor_store_in_city"] = sum(city_presence.values())

    # ── Score ─────────────────────────────────────────────────────────────────
    confidence = build_score(evidence)

    # For POSSIBLE_FUTURE_OPENING, blend with any pre-computed raw_confidence
    if change_type == "POSSIBLE_FUTURE_OPENING" and "raw_confidence" in change:
        existing_pts = round(change["raw_confidence"] * 200)
        if existing_pts > confidence["points"]:
            confidence = build_score({"aurora_map_confirmed": 0})  # reset then rebuild
            confidence["points"] = existing_pts
            confidence["score"] = round(min(existing_pts / 200.0, 1.0), 3)
            confidence["level"] = level_for_points(existing_pts)

    change["confidence"] = confidence
    logger.debug(
        f"Confidence [{city_raw}] {change_type}: "
        f"{confidence['level']} ({confidence['points']} pts) "
        f"factors={[f['key'] for f in confidence['factors']]}"
    )
    return change
