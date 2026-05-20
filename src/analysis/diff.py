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

# Signal classes used in city index (not stored as change_types)
# aurora_confirmed  — direct Aurora Multimarket source
# aurora_context    — Aurora is mentioned but signal is not city-specific or is a venue name
# competitor        — competitor brand signal
# generic_market    — general retail market signal
# weak              — noise / unclassifiable


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

# Venue names that happen to contain "Aurora" but are NOT the Aurora Multimarket brand
_AURORA_VENUE_KEYWORDS = {"aurora retail park", "aurora mall"}
# Keywords unambiguously referring to the Aurora Multimarket brand
_AURORA_BRAND_KEYWORDS = {"aurora multimarket", "avrora multimarket"}
# Companies that are never Aurora Multimarket signals
_NON_AURORA_BRAND_COMPANIES = {"altex", "cometex"}

# Job board domains/sources where multi-city listings are common
_JOB_BOARD_DOMAINS = ("ejobs.ro", "bestjobs", "hipo.ro", "linkedin.com")
_JOB_BOARD_SOURCES = {"linkedin"}
# Max cities on a single signal before it's treated as context, not city-specific evidence
_MULTI_CITY_THRESHOLD = 2


def _is_aurora_venue_false_positive(signal: dict) -> bool:
    """
    True when the signal is about a venue *named* Aurora (retail park, mall, Altex location)
    rather than the Aurora Multimarket brand itself.
    Returns False immediately if 'Aurora Multimarket' is explicitly present.
    """
    text = (
        f"{signal.get('title', '')} {signal.get('url', '')} {signal.get('excerpt', '')}"
    ).lower()
    company = signal.get("company", "").lower()

    # Explicit brand mention overrides venue heuristics
    if any(kw in text for kw in _AURORA_BRAND_KEYWORDS):
        return False
    # Company is a non-Aurora retailer (Altex store inside Aurora Retail Park, etc.)
    if any(kw in company for kw in _NON_AURORA_BRAND_COMPANIES):
        return True
    # URL or title refers to a venue named Aurora
    if any(kw in text for kw in _AURORA_VENUE_KEYWORDS):
        return True
    return False


def _is_multi_city_job(signal: dict, n_cities: int) -> bool:
    """True when a job board signal lists more than _MULTI_CITY_THRESHOLD cities."""
    if n_cities <= _MULTI_CITY_THRESHOLD:
        return False
    source = signal.get("source", "").lower()
    url = signal.get("url", "").lower()
    return source in _JOB_BOARD_SOURCES or any(d in url for d in _JOB_BOARD_DOMAINS)


def _is_qualifying_aurora_signal(sig: dict) -> bool:
    """
    True when an aurora_confirmed signal is city-specific enough to drive a prediction.

    Qualifying signals:
      - Official Aurora sources (aurora_news, instagram, aurora_map, aurora-retail.com)
      - Web articles that explicitly name Aurora Multimarket in title/excerpt
      - Job postings from job boards with <= MULTI_CITY_THRESHOLD cities mentioned
    Disqualified:
      - Job board search/result pages listing many Romanian cities at once
      - Venue false positives (handled upstream in classify_signal)
    """
    source = sig.get("source", "")
    url = sig.get("url", "").lower()
    n_cities = len(sig.get("cities_mentioned", []))

    if source in ("aurora_news", "instagram", "aurora_map"):
        return True
    if "aurora-retail.com" in url:
        return True

    is_job = source in _JOB_BOARD_SOURCES or any(d in url for d in _JOB_BOARD_DOMAINS)
    if is_job:
        return n_cities <= _MULTI_CITY_THRESHOLD

    # Other web signals: require explicit Aurora Multimarket brand mention
    text = f"{sig.get('title', '')} {sig.get('excerpt', '')}".lower()
    return any(kw in text for kw in _AURORA_BRAND_KEYWORDS)


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

    # Priority 0: venue false positives — "Aurora Retail Park", Altex, etc.
    if _is_aurora_venue_false_positive(signal):
        logger.debug(f"generic_market (Aurora venue false positive): {signal.get('title','')[:60]}")
        return "generic_market"

    # Use pre-computed 9-class label when available (from web/retail intel)
    nine_class = signal.get("signal_class") or signal.get("signal_category") or \
                 (signal.get("signals") or {}).get("signal_category")

    if nine_class:
        if nine_class == "aurora_confirmed":
            logger.debug(f"aurora_confirmed via 9-class: {signal.get('title','')[:60]}")
            return "aurora_confirmed"
        if nine_class == "aurora_mentioned":
            # aurora_mentioned: Aurora referenced but not city-specific expansion evidence.
            # Treated as context only — does NOT drive predictions on its own.
            logger.debug(f"aurora_context via aurora_mentioned: {signal.get('title','')[:60]}")
            return "aurora_context"
        if nine_class == "competitor_expansion":
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
        n_cities = len(cities)
        for city in cities:
            ck = city.lower().strip()
            # Downgrade multi-city job listings: they show national market context,
            # not city-specific Aurora expansion intent.
            effective = sig_class
            if sig_class == "aurora_confirmed" and _is_multi_city_job(entry, n_cities):
                effective = "aurora_context"
            index.setdefault(ck, {}).setdefault(effective, []).append(entry)

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

        aurora_signals   = signals_by_class.get("aurora_confirmed", [])
        context_signals  = signals_by_class.get("aurora_context", [])
        competitor_signals = signals_by_class.get("competitor", [])
        generic_signals  = signals_by_class.get("generic_market", [])

        has_support = len(competitor_signals) + len(generic_signals) > 0

        if not aurora_signals and not context_signals and not competitor_signals and not generic_signals:
            continue  # only weak signals — skip entirely

        # A prediction requires at least one qualifying aurora_confirmed signal:
        # - official source / direct article, OR
        # - city-specific job posting (<=2 cities mentioned)
        qualifying_aurora = [s for s in aurora_signals if _is_qualifying_aurora_signal(s)]
        has_qualifying_aurora = len(qualifying_aurora) > 0

        # ── POSSIBLE_FUTURE_OPENING: city needs manual verification ──────────
        if has_qualifying_aurora:
            score = 0.0
            score += len(qualifying_aurora) * 0.25
            score += len(aurora_signals) * 0.05       # non-qualifying Aurora context
            score += len(competitor_signals) * 0.05   # supporting context
            score += len(generic_signals) * 0.02
            score = min(score, 0.90)

            aurora_predictions += 1
            logger.info(
                f"POSSIBLE_FUTURE_OPENING [{city_key.title()}] "
                f"qualifying={len(qualifying_aurora)} aurora={len(aurora_signals)} "
                f"comp={len(competitor_signals)} generic={len(generic_signals)} score={score:.2f}"
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
                    "job_count": len(qualifying_aurora),
                    "job_titles": list({e["title"] for e in qualifying_aurora}),
                    "news_count": sum(
                        1 for e in qualifying_aurora if e["source"] in ("aurora_news",)
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
