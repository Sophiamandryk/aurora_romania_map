"""
Explainable confidence scoring engine.

Point values are named and transparent. Each signal type has a fixed base value.
Multiple signals of the same type give diminishing returns (each extra adds 25%).
The point total determines HIGH / MEDIUM / LOW.

HIGH   >= 100 pts   (map confirmation, or strong multi-source convergence)
MEDIUM >=  40 pts   (Aurora hiring + 1 corroborating signal)
LOW    <   40 pts   (single weak signal)
"""

POINTS: dict[str, int] = {
    "aurora_map_confirmed":          100,
    "aurora_official_announcement":   80,
    "aurora_hiring_store_manager":    50,
    "aurora_instagram_signal":        30,
    "retail_park_announced":          25,
    "aurora_hiring_generic":          15,
    "competitor_expansion_city":      15,
    "shopping_center_news":           10,
    "competitor_store_in_city":        8,
    "generic_retail_jobs":             5,
}

LABELS: dict[str, str] = {
    "aurora_map_confirmed":          "Store confirmed on Aurora map",
    "aurora_official_announcement":  "Aurora official announcement",
    "aurora_hiring_store_manager":   "Aurora store-manager hiring",
    "aurora_instagram_signal":       "Aurora Instagram signal",
    "retail_park_announced":         "Retail park announced in city",
    "aurora_hiring_generic":         "Aurora hiring in city",
    "competitor_expansion_city":     "Competitor expanding in city",
    "shopping_center_news":          "Shopping centre / mall news",
    "competitor_store_in_city":      "Competitor store already in city",
    "generic_retail_jobs":           "Retail hiring in city",
}

HIGH_THRESHOLD = 160
MEDIUM_THRESHOLD = 100
_EXTRA_FACTOR = 0.25  # each additional signal of the same type adds 25% of base


def level_for_points(points: int) -> str:
    if points >= HIGH_THRESHOLD:
        return "HIGH"
    if points >= MEDIUM_THRESHOLD:
        return "MEDIUM"
    return "LOW"


def build_score(evidence: dict[str, int]) -> dict:
    """
    Build an explainable score from named evidence counts.

    Args:
        evidence: {factor_key: signal_count, ...}

    Returns:
        {
            "points": int,
            "level": "HIGH"|"MEDIUM"|"LOW",
            "score": float 0-1  (normalized, kept for DB/backward-compat),
            "factors": [{"key", "points", "count", "label"}, ...] sorted desc,
        }
    """
    total = 0
    factors = []

    for key, count in evidence.items():
        if not count:
            continue
        base = POINTS.get(key, 0)
        if not base:
            continue
        pts = round(base * (1.0 + _EXTRA_FACTOR * (count - 1)))
        total += pts
        label = LABELS.get(key, key)
        if count > 1:
            label = f"{label} ×{count}"
        factors.append({"key": key, "points": pts, "count": count, "label": label})

    factors.sort(key=lambda x: -x["points"])

    return {
        "points": total,
        "level": level_for_points(total),
        "score": round(min(total / 200.0, 1.0), 3),
        "factors": factors,
    }


def format_score_label(score: dict) -> str:
    """Human-readable one-liner: 'HIGH (175 pts)'."""
    pts = score.get("points", 0)
    level = score.get("level", "LOW")
    return f"{level} ({pts} pts)"
