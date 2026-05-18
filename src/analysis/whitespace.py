"""
White-space market opportunity analysis.

Identifies cities where competitors already operate but Aurora does not yet,
ranked by an opportunity score that weighs competitor density, brand diversity,
and city size (population tier).

This is the primary tool for answering: "Where should Aurora expand next?"
"""
from src.config import setup_logging
from src.data.ro_counties import normalize_city, county_for_city, region_for_city

logger = setup_logging("analysis.whitespace")

# Approximate city populations (used only for opportunity ranking tier)
_POPULATION: dict[str, int] = {
    "bucuresti":             2_000_000,
    "bucharest":             2_000_000,
    "iasi":                    380_000,
    "cluj napoca":             350_000,
    "timisoara":               330_000,
    "constanta":               310_000,
    "craiova":                 260_000,
    "brasov":                  280_000,
    "galati":                  250_000,
    "ploiesti":                225_000,
    "oradea":                  220_000,
    "arad":                    160_000,
    "pitesti":                 150_000,
    "sibiu":                   150_000,
    "targu mures":             145_000,
    "bacau":                   145_000,
    "baia mare":               120_000,
    "buzau":                   110_000,
    "botosani":                115_000,
    "suceava":                 130_000,
    "ramnicu valcea":           95_000,
    "focsani":                  95_000,
    "braila":                  190_000,
    "galati":                  250_000,
    "targu jiu":                82_000,
    "drobeta turnu severin":    90_000,
    "resita":                   70_000,
    "piatra neamt":            100_000,
    "alba iulia":               65_000,
    "deva":                     60_000,
    "zalau":                    55_000,
    "satu mare":               100_000,
    "sighetu marmatiei":        37_000,
    "miercurea ciuc":           38_000,
    "sfantu gheorghe":          55_000,
    "targu secuiesc":           20_000,
    "bistrita":                 85_000,
    "hunedoara":                60_000,
    "petrosani":                40_000,
    "medias":                   50_000,
    "lugoj":                    40_000,
    "targoviste":               75_000,
    "alexandria":               45_000,
    "slobozia":                 45_000,
    "calarasi":                 60_000,
    "giurgiu":                  55_000,
    "tulcea":                   70_000,
    "vaslui":                   60_000,
    "slatina":                  70_000,
    "turda":                    45_000,
    "odorheiu secuiesc":        33_000,
    "onesti":                   40_000,
    "moinesti":                 22_000,
    "caransebes":               25_000,
    "targu neamt":              22_000,
    "roman":                    65_000,
    "voluntari":                35_000,
    "otopeni":                  25_000,
    "popesti leordeni":         24_000,
    "bragadiru":                20_000,
    "chiajna":                  20_000,
}


def _pop_tier(city_norm: str) -> int:
    """Return a 0-4 tier based on population."""
    pop = _POPULATION.get(city_norm, 30_000)
    if pop >= 300_000:
        return 4
    if pop >= 100_000:
        return 3
    if pop >= 50_000:
        return 2
    if pop >= 20_000:
        return 1
    return 0


def whitespace_opportunities(
    aurora_stores: list[dict],
    competitor_stores: dict[str, list[dict]],
    min_brands: int = 1,
) -> list[dict]:
    """
    Identify cities where competitors operate but Aurora does not.

    Opportunity score formula:
      brand_diversity * 30
      + total_competitor_stores * 3
      + pop_tier * 20

    Returns list sorted by opportunity_score desc.
    Each entry: {city, county, region, competitor_brands, total_stores,
                 opportunity_score, population_tier, gap_label}
    """
    aurora_cities = {normalize_city(s.get("city", "")) for s in aurora_stores if s.get("city")}

    # Build city → {brand: count} from competitor stores
    from collections import defaultdict
    comp_by_city: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for brand, stores in competitor_stores.items():
        if brand == "Action":
            continue
        for s in stores:
            c = normalize_city(s.get("city", ""))
            if c:
                comp_by_city[c][brand] += 1

    results = []
    for city_norm, brand_counts in comp_by_city.items():
        if city_norm in aurora_cities:
            continue
        if len(brand_counts) < min_brands:
            continue

        brand_diversity = len(brand_counts)
        total_stores = sum(brand_counts.values())
        tier = _pop_tier(city_norm)
        score = round(brand_diversity * 30 + total_stores * 3 + tier * 20, 1)

        # Human-readable gap label
        if brand_diversity >= 3:
            gap_label = "High priority — 3+ brands present"
        elif brand_diversity == 2:
            gap_label = "Medium priority — 2 brands present"
        else:
            gap_label = "Watch — 1 brand present"

        results.append({
            "city": city_norm.title(),
            "city_norm": city_norm,
            "county": county_for_city(city_norm),
            "region": region_for_city(city_norm),
            "competitor_brands": dict(brand_counts),
            "total_competitor_stores": total_stores,
            "brand_diversity": brand_diversity,
            "population_tier": tier,
            "opportunity_score": score,
            "gap_label": gap_label,
        })

    results.sort(key=lambda x: -x["opportunity_score"])
    logger.info(
        f"White-space: {len(results)} cities with competitors but no Aurora "
        f"(min_brands={min_brands})"
    )
    return results


def whitespace_by_region(
    aurora_stores: list[dict],
    competitor_stores: dict[str, list[dict]],
) -> list[dict]:
    """
    Group white-space opportunities by development region.
    Returns [{region, city_count, top_opportunities}, ...] desc.
    """
    opps = whitespace_opportunities(aurora_stores, competitor_stores, min_brands=1)
    by_region: dict[str, list] = {}
    for opp in opps:
        region = opp.get("region") or "Unknown"
        by_region.setdefault(region, []).append(opp)

    result = []
    for region, cities in sorted(by_region.items(), key=lambda x: -len(x[1])):
        result.append({
            "region": region,
            "city_count": len(cities),
            "top_score": cities[0]["opportunity_score"] if cities else 0,
            "top_opportunities": cities[:5],
        })
    return sorted(result, key=lambda x: -x["top_score"])
