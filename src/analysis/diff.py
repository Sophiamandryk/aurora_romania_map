"""
Snapshot comparison engine.
Detects NEW_STORE, REMOVED_STORE, RELOCATED_STORE, STORE_UPDATED, etc.
"""
import math
from datetime import date
from typing import Optional

from src.config import setup_logging

logger = setup_logging("analysis.diff")

# Meters — stores within this distance are considered the same location
SAME_LOCATION_THRESHOLD_M = 300
# Meters — if store moved more than this, flag as RELOCATED
RELOCATION_THRESHOLD_M = 500

CHANGE_TYPES = [
    "NEW_STORE",
    "REMOVED_STORE",
    "RELOCATED_STORE",
    "STORE_UPDATED",
    "POSSIBLE_FUTURE_OPENING",
    "POSSIBLE_REBRANDING",
    "NEW_STORE_FORMAT",
]


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in meters between two lat/lon points."""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _coords_valid(store: dict) -> bool:
    return (
        store.get("latitude") is not None
        and store.get("longitude") is not None
        and store["latitude"] != 0
        and store["longitude"] != 0
    )


def _location_key(store: dict) -> str:
    if _coords_valid(store):
        return f"{store['latitude']:.4f},{store['longitude']:.4f}"
    return f"{store.get('city', '').lower().strip()}::{store.get('address', '').lower().strip()[:40]}"


def _name_key(store: dict) -> str:
    return f"{store.get('city', '').lower().strip()}::{store.get('name', '').lower().strip()}"


def _find_nearest(store: dict, candidates: list[dict], threshold_m: float) -> Optional[dict]:
    """Find the nearest candidate store within threshold_m meters."""
    if not _coords_valid(store):
        return None
    best = None
    best_dist = float("inf")
    for c in candidates:
        if not _coords_valid(c):
            continue
        dist = haversine(store["latitude"], store["longitude"], c["latitude"], c["longitude"])
        if dist < best_dist:
            best_dist = dist
            best = c
    if best and best_dist <= threshold_m:
        return best
    return None


def _stores_same_location(a: dict, b: dict) -> bool:
    if _coords_valid(a) and _coords_valid(b):
        return haversine(a["latitude"], a["longitude"], b["latitude"], b["longitude"]) <= SAME_LOCATION_THRESHOLD_M
    return _location_key(a) == _location_key(b)


def compare_snapshots(previous: list[dict], current: list[dict]) -> list[dict]:
    """
    Compare two store snapshots and return a list of change events.
    Each event has: change_type, store, previous_store (if applicable), details.
    """
    today = date.today().isoformat()
    changes = []

    prev_by_key = {_location_key(s): s for s in previous}
    curr_by_key = {_location_key(s): s for s in current}

    prev_keys = set(prev_by_key.keys())
    curr_keys = set(curr_by_key.keys())

    new_keys = curr_keys - prev_keys
    removed_keys = prev_keys - curr_keys

    # NEW stores
    for key in new_keys:
        store = curr_by_key[key]
        # Check if this might be a RELOCATED store (removed one is nearby)
        relocated_from = None
        if removed_keys:
            removed_candidates = [prev_by_key[k] for k in removed_keys]
            nearby = _find_nearest(store, removed_candidates, RELOCATION_THRESHOLD_M * 5)
            if nearby:
                relocated_from = nearby

        if relocated_from:
            dist = haversine(
                store["latitude"], store["longitude"],
                relocated_from["latitude"], relocated_from["longitude"],
            ) if _coords_valid(store) and _coords_valid(relocated_from) else None

            changes.append({
                "change_type": "RELOCATED_STORE",
                "detected_date": today,
                "store": store,
                "previous_store": relocated_from,
                "details": {
                    "distance_m": round(dist) if dist else None,
                    "old_address": relocated_from.get("address", ""),
                    "new_address": store.get("address", ""),
                },
            })
            removed_keys.discard(_location_key(relocated_from))
        else:
            changes.append({
                "change_type": "NEW_STORE",
                "detected_date": today,
                "store": store,
                "previous_store": None,
                "details": {
                    "city": store.get("city", ""),
                    "address": store.get("address", ""),
                },
            })

    # REMOVED stores
    for key in removed_keys:
        store = prev_by_key[key]
        changes.append({
            "change_type": "REMOVED_STORE",
            "detected_date": today,
            "store": store,
            "previous_store": None,
            "details": {
                "city": store.get("city", ""),
                "address": store.get("address", ""),
                "note": "Store disappeared from map. Verify manually.",
            },
        })

    # STORE_UPDATED — same location, different name or address
    for key in prev_keys & curr_keys:
        prev = prev_by_key[key]
        curr = curr_by_key[key]

        updates = {}
        for field in ("name", "address", "city"):
            pv = prev.get(field, "")
            cv = curr.get(field, "")
            if pv and cv and pv.lower().strip() != cv.lower().strip():
                updates[field] = {"from": pv, "to": cv}

        if updates:
            # Distinguish rebranding from plain update
            if "name" in updates:
                prev_name = updates["name"]["from"].lower()
                curr_name = updates["name"]["to"].lower()
                # If names diverge significantly, flag as rebranding
                if not any(w in curr_name for w in prev_name.split()):
                    changes.append({
                        "change_type": "POSSIBLE_REBRANDING",
                        "detected_date": today,
                        "store": curr,
                        "previous_store": prev,
                        "details": updates,
                    })
                    continue
            changes.append({
                "change_type": "STORE_UPDATED",
                "detected_date": today,
                "store": curr,
                "previous_store": prev,
                "details": updates,
            })

    summary = {}
    for c in changes:
        ct = c["change_type"]
        summary[ct] = summary.get(ct, 0) + 1
    logger.info(f"Diff complete: {len(previous)} → {len(current)} stores. Changes: {summary}")
    return changes


# ── Signal classification ────────────────────────────────────────────────────

AURORA_KEYWORDS = {"aurora", "multimarket", "aurora multimarket", "aurora retail"}

COMPETITOR_KEYWORDS = {"pepco", "tedi", "kik", "action"}

GENERIC_RETAIL_KEYWORDS = {
    "lidl", "kaufland", "carrefour", "mega image", "penny", "aldi",
    "primark", "h&m", "zara", "inditex", "bershka", "pull&bear",
    "mango", "c&a", "normal a/s", "normal as", "hm", "h & m",
    "istyle", "levi strauss", "harding", "dodo pizza", "froo",
    "undelucram", "interbrands", "opella", "henkel", "twyford",
}

AURORA_SOURCE_DOMAINS = {"aurora-retail.com", "instagram.com/aurora", "aurora.multimarket"}


def classify_signal(signal: dict) -> str:
    """
    Maps any signal to one of the 4 prediction-engine classes:
      aurora_confirmed | competitor | generic_market | weak

    The 9-class source taxonomy (aurora_confirmed, aurora_mentioned,
    competitor_expansion, retail_park, mall_leasing, local_news,
    influencer_signal, generic_market, noise) is preserved on each signal
    as signal["signal_class"] and used in reporting, but the prediction
    engine only needs these 4 classes.
    """
    company = signal.get("company", "").lower()
    source = signal.get("source", "").lower()
    url = signal.get("url", "").lower()

    # Use pre-computed 9-class label when available (from web/retail intel)
    nine_class = signal.get("signal_class") or signal.get("signal_category") or \
                 (signal.get("signals") or {}).get("signal_category")

    if nine_class:
        if nine_class in ("aurora_confirmed",):
            logger.debug(f"aurora_confirmed via 9-class: {signal.get('title','')[:60]}")
            return "aurora_confirmed"
        if nine_class == "aurora_mentioned":
            # aurora_mentioned — treat as aurora_confirmed for prediction purposes
            logger.debug(f"aurora_confirmed via aurora_mentioned: {signal.get('title','')[:60]}")
            return "aurora_confirmed"
        if nine_class in ("competitor_expansion",):
            return "competitor"
        if nine_class == "noise":
            return "weak"
        # retail_park, mall_leasing, local_news, influencer_signal, generic_market
        return "generic_market"

    # Fallback: derive from source/company fields

    # Aurora official sources
    if source in ("aurora_news", "instagram", "aurora_map"):
        logger.debug(f"aurora_confirmed via source={source}: {signal.get('title','')[:60]}")
        return "aurora_confirmed"
    if any(d in url for d in AURORA_SOURCE_DOMAINS):
        logger.debug(f"aurora_confirmed via URL: {url[:60]}")
        return "aurora_confirmed"
    if any(kw in company for kw in AURORA_KEYWORDS):
        logger.debug(f"aurora_confirmed via company '{company}': {signal.get('title','')[:60]}")
        return "aurora_confirmed"

    # Competitor signals
    if any(kw in company for kw in COMPETITOR_KEYWORDS):
        logger.debug(f"competitor signal [{company}]: {signal.get('title','')[:60]}")
        return "competitor"

    # Generic retail market signals
    if any(kw in company for kw in GENERIC_RETAIL_KEYWORDS):
        logger.debug(f"generic_market signal [{company}]: {signal.get('title','')[:60]}")
        return "generic_market"

    logger.debug(f"weak signal (unclassified company='{company}'): {signal.get('title','')[:60]}")
    return "weak"


def _signal_evidence_entry(signal: dict, sig_class: str) -> dict:
    return {
        "title": signal.get("title", ""),
        "company": signal.get("company", ""),
        "url": signal.get("url", ""),
        "source": signal.get("source", ""),
        "signal_class": sig_class,
        "cities_mentioned": signal.get("cities_mentioned", []),
    }


def merge_intelligence_signals(
    map_changes: list[dict],
    jobs: list[dict],
    news_articles: list[dict],
    instagram_posts: list[dict],
) -> list[dict]:
    """
    Combine signals to generate predictions.

    Rules:
    - POSSIBLE_FUTURE_OPENING: requires at least one aurora_confirmed signal.
    - MARKET_ACTIVITY_SIGNAL: competitor or generic market signals with no Aurora evidence.
    - Cities already confirmed on the map are skipped.

    Each prediction carries full evidence with source company, URL, and classification.
    """
    from src.scrapers.aurora_instagram import _extract_cities
    today = date.today().isoformat()
    results = []

    # ── Classify every signal upfront ────────────────────────────────────────
    classified_jobs = [(j, classify_signal(j)) for j in jobs]
    classified_news = [(a, classify_signal(a)) for a in news_articles]
    _AURORA_IG_ACTIONABLE = {"confirmed_opening_signal", "possible_store_location_signal"}
    classified_ig = []
    for p in instagram_posts:
        if p.get("brand"):
            # Competitor account — always competitor signal
            classified_ig.append((p, "competitor"))
        elif p.get("signal_type") in _AURORA_IG_ACTIONABLE:
            # Aurora account with strong signal — supports predictions
            classified_ig.append((p, "aurora_confirmed"))
        else:
            # Aurora account with weak signal — context only, not a prediction driver
            classified_ig.append((p, "weak"))

    # ── Build city → signals index ───────────────────────────────────────────
    # city_key → { signal_class: [evidence_entry, ...] }
    CitySignals = dict[str, dict[str, list[dict]]]

    def _add_to_index(index: CitySignals, cities: list[str], entry: dict, sig_class: str):
        for city in cities:
            ck = city.lower().strip()
            index.setdefault(ck, {}).setdefault(sig_class, []).append(entry)

    city_index: CitySignals = {}

    for job, cls in classified_jobs:
        entry = _signal_evidence_entry(job, cls)
        _add_to_index(city_index, job.get("cities_mentioned", []), entry, cls)

    for article, cls in classified_news:
        cities = _extract_cities(f"{article.get('title','')} {article.get('excerpt','')}")
        entry = _signal_evidence_entry(article, cls)
        _add_to_index(city_index, cities, entry, cls)

    for post, cls in classified_ig:
        entry = _signal_evidence_entry(post, cls)
        _add_to_index(city_index, post.get("cities_mentioned", []), entry, cls)

    # ── Cities already confirmed on the map (skip them) ──────────────────────
    confirmed_cities = set()
    for change in map_changes:
        city = (change.get("store") or {}).get("city", "").lower().strip()
        if city:
            confirmed_cities.add(city)

    # ── Generate predictions ──────────────────────────────────────────────────
    aurora_predictions = 0
    market_signals = 0

    for city_key, signals_by_class in city_index.items():
        if city_key in confirmed_cities:
            continue

        aurora_signals = signals_by_class.get("aurora_confirmed", [])
        competitor_signals = signals_by_class.get("competitor", [])
        generic_signals = signals_by_class.get("generic_market", [])
        weak_signals = signals_by_class.get("weak", [])

        has_aurora = len(aurora_signals) > 0
        has_support = len(competitor_signals) + len(generic_signals) > 0

        if not aurora_signals and not competitor_signals and not generic_signals:
            continue  # only weak signals — skip entirely

        # ── POSSIBLE_FUTURE_OPENING: must have Aurora-specific evidence ───────
        if has_aurora:
            # Confidence: Aurora map > Aurora news > Instagram > weak
            score = 0.0
            score += len(aurora_signals) * 0.25
            score += len(competitor_signals) * 0.05   # supporting context
            score += len(generic_signals) * 0.02
            score = min(score, 0.90)

            aurora_predictions += 1
            logger.info(
                f"POSSIBLE_FUTURE_OPENING [{city_key.title()}] "
                f"aurora={len(aurora_signals)} comp={len(competitor_signals)} "
                f"generic={len(generic_signals)} score={score:.2f}"
            )
            results.append({
                "change_type": "POSSIBLE_FUTURE_OPENING",
                "detected_date": today,
                "city": city_key.title(),
                "aurora_specific": True,
                "raw_confidence": round(score, 3),
                "evidence": {
                    "aurora_signals": aurora_signals,
                    "competitor_signals": competitor_signals,
                    "generic_signals": generic_signals,
                    "job_count": len(aurora_signals),
                    "job_titles": list({e["title"] for e in aurora_signals}),
                    "news_count": sum(
                        1 for e in aurora_signals if e["source"] in ("aurora_news",)
                    ),
                },
                "store": None,
                "previous_store": None,
                "details": {},
            })

        # ── MARKET_ACTIVITY_SIGNAL: competitor/generic activity, no Aurora evidence
        elif has_support:
            market_signals += 1
            all_support = competitor_signals + generic_signals
            companies = list({e["company"] for e in all_support if e["company"]})
            logger.info(
                f"MARKET_ACTIVITY_SIGNAL [{city_key.title()}] "
                f"companies={companies[:4]} (no Aurora evidence)"
            )
            results.append({
                "change_type": "MARKET_ACTIVITY_SIGNAL",
                "detected_date": today,
                "city": city_key.title(),
                "aurora_specific": False,
                "raw_confidence": 0.0,
                "evidence": {
                    "aurora_signals": [],
                    "competitor_signals": competitor_signals,
                    "generic_signals": generic_signals,
                    "companies": companies,
                },
                "store": None,
                "previous_store": None,
                "details": {"note": "Competitor/generic retail activity — no Aurora evidence"},
            })

    logger.info(
        f"Signals: {aurora_predictions} Aurora predictions, "
        f"{market_signals} market activity signals"
    )
    return results
