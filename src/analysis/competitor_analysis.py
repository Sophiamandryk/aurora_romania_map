"""
Competitor proximity and density analysis.
For each Aurora store change, finds nearest competitors and computes density.
"""
import math
from collections import defaultdict
from typing import Optional

from src.config import setup_logging
from src.analysis.diff import haversine, _coords_valid
from src.data.ro_counties import normalize_city as _normalize_city

logger = setup_logging("analysis.competitors")

PROXIMITY_RADIUS_KM = 5.0


def nearest_competitors(
    store: dict,
    competitor_stores: dict[str, list[dict]],
    radius_km: float = PROXIMITY_RADIUS_KM,
) -> dict[str, list[dict]]:
    """
    Return dict of brand -> list of competitor stores within radius_km,
    sorted by distance, each enriched with a 'distance_km' field.
    """
    if not _coords_valid(store):
        return {}

    result = {}
    for brand, stores in competitor_stores.items():
        nearby = []
        for cs in stores:
            if not _coords_valid(cs):
                continue
            dist_m = haversine(
                store["latitude"], store["longitude"],
                cs["latitude"], cs["longitude"],
            )
            dist_km = dist_m / 1000
            if dist_km <= radius_km:
                nearby.append({**cs, "distance_km": round(dist_km, 2)})
        nearby.sort(key=lambda x: x["distance_km"])
        if nearby:
            result[brand] = nearby

    return result


def competitor_density_summary(
    store: dict,
    competitor_stores: dict[str, list[dict]],
    radii_km: list[float] = [1.0, 2.0, 5.0],
) -> dict:
    """
    Counts how many competitor stores of each brand fall within each radius.
    """
    if not _coords_valid(store):
        return {}

    summary = {}
    for radius in radii_km:
        counts = {}
        for brand, stores in competitor_stores.items():
            count = 0
            for cs in stores:
                if not _coords_valid(cs):
                    continue
                dist_km = haversine(
                    store["latitude"], store["longitude"],
                    cs["latitude"], cs["longitude"],
                ) / 1000
                if dist_km <= radius:
                    count += 1
            if count:
                counts[brand] = count
        if counts:
            summary[f"within_{int(radius)}km"] = counts

    return summary


def city_competitor_presence(
    city: str,
    competitor_stores: dict[str, list[dict]],
) -> dict[str, int]:
    """Count how many competitor stores each brand has in the given city."""
    city_norm = _normalize_city(city)
    result = {}
    for brand, stores in competitor_stores.items():
        count = sum(
            1 for s in stores
            if _normalize_city(s.get("city", "")) == city_norm
        )
        if count:
            result[brand] = count
    return result


def identify_retail_clusters(
    aurora_stores: list[dict],
    competitor_stores: dict[str, list[dict]],
    cluster_radius_km: float = 1.0,
    min_brands: int = 2,
) -> list[dict]:
    """
    Identify locations where Aurora co-exists with multiple competitor brands
    within cluster_radius_km — these are "retail cluster" opportunities.
    """
    clusters = []
    for store in aurora_stores:
        if not _coords_valid(store):
            continue
        nearby = nearest_competitors(store, competitor_stores, radius_km=cluster_radius_km)
        if len(nearby) >= min_brands:
            brand_summary = {brand: stores[0]["distance_km"] for brand, stores in nearby.items()}
            clusters.append({
                "aurora_store": store,
                "cluster_brands": brand_summary,
                "brand_count": len(nearby),
                "city": store.get("city", ""),
                "note": f"Retail cluster: Aurora + {', '.join(nearby.keys())} within {cluster_radius_km}km",
            })
    logger.info(f"Identified {len(clusters)} retail clusters")
    return clusters


def cities_competitors_expanded_before_aurora(
    aurora_stores: list[dict],
    competitor_stores: dict[str, list[dict]],
) -> list[dict]:
    """
    Find cities where competitors have stores but Aurora does not yet.
    These are candidate markets for Aurora expansion.
    """
    aurora_cities = {_normalize_city(s.get("city", "")) for s in aurora_stores if s.get("city")}

    comp_city_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for brand, stores in competitor_stores.items():
        for s in stores:
            city = _normalize_city(s.get("city", ""))
            if city:
                comp_city_counts[city][brand] += 1

    result = []
    for city, brand_counts in comp_city_counts.items():
        if city not in aurora_cities and len(brand_counts) >= 2:
            result.append({
                "city": city.title(),
                "competitor_brands": dict(brand_counts),
                "total_competitor_stores": sum(brand_counts.values()),
                "opportunity_score": min(len(brand_counts) * 0.3 + sum(brand_counts.values()) * 0.05, 1.0),
                "note": "Competitors present, Aurora not yet in this city",
            })

    result.sort(key=lambda x: x["opportunity_score"], reverse=True)
    logger.info(f"Found {len(result)} cities with competitor presence but no Aurora")
    return result


def enrich_change_with_competitor_data(
    change: dict,
    competitor_stores: dict[str, list[dict]],
) -> dict:
    """Add competitor proximity data to a map change event."""
    store = change.get("store")
    if not store or not _coords_valid(store):
        return change

    nearby = nearest_competitors(store, competitor_stores, radius_km=PROXIMITY_RADIUS_KM)
    density = competitor_density_summary(store, competitor_stores)
    city_presence = city_competitor_presence(store.get("city", ""), competitor_stores)

    change["competitor_analysis"] = {
        "nearest_competitors": {
            brand: [
                {"name": s.get("name", brand), "address": s.get("address", ""), "distance_km": s["distance_km"]}
                for s in stores[:3]
            ]
            for brand, stores in nearby.items()
        },
        "density": density,
        "city_presence": city_presence,
        "in_retail_cluster": len(nearby) >= 2,
    }
    return change
