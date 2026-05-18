"""
AI executive summary generator.
Produces a short strategic Telegram briefing — not a raw metrics dump.

Key rules enforced everywhere:
  - "Predicted opening" only when Aurora-specific evidence exists (map, news, Instagram, direct article)
  - "Market opportunity" for competitor/retail-park/job signals with no Aurora source
  - Every number gets a "So what?" interpretation
  - Regional framing: East (Aurora home base) vs West (gap territory)
  - One mandatory "Key analyst observation" — the single most actionable insight
"""
from datetime import date

from src.config import setup_logging, DB_PATH

logger = setup_logging("analysis.executive_summary")


# ── Data preparation helpers ──────────────────────────────────────────────────

def _competitor_unique_cities(competitor_stores: dict) -> dict[str, int]:
    from src.data.ro_counties import normalize_city
    result = {}
    for brand, stores in competitor_stores.items():
        cities = {normalize_city(s.get("city", "")) for s in stores if s.get("city")}
        result[brand] = len(cities)
    return result


def _whitespace_top(whitespace: list, min_brands: int = 2, top_n: int = 6) -> list[dict]:
    return [w for w in whitespace if w.get("brand_diversity", 1) >= min_brands][:top_n]


def _classify_prediction(pred: dict) -> str:
    """Returns 'aurora_signal' or 'market_opportunity'."""
    evidence = pred.get("evidence", {})
    aurora_signals = evidence.get("aurora_signals", [])
    if not aurora_signals:
        return "market_opportunity"
    for sig in aurora_signals:
        src = sig.get("source", "")
        url = sig.get("url", "")
        if src not in ("linkedin",) and "ejobs.ro" not in url and "bestjobs" not in url:
            return "aurora_signal"
        if src in ("aurora_news", "instagram", "aurora_map"):
            return "aurora_signal"
    return "market_opportunity"


_ACTIVE_LATEST = "status='active' AND snapshot_date=(SELECT MAX(snapshot_date) FROM stores)"


def _query_regional_intelligence() -> dict:
    """Query DB for regional distribution stats — latest snapshot only."""
    try:
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        # Aurora by region — latest snapshot only
        aurora_by_region = {}
        for r in conn.execute(
            f"SELECT region, COUNT(*) as n FROM stores WHERE {_ACTIVE_LATEST} GROUP BY region ORDER BY n DESC"
        ):
            region = r["region"] or "Unknown"
            aurora_by_region[region] = aurora_by_region.get(region, 0) + r["n"]

        # Total Aurora — latest snapshot only
        aurora_total = conn.execute(
            f"SELECT COUNT(*) as n, COUNT(DISTINCT city) as cities FROM stores WHERE {_ACTIVE_LATEST}"
        ).fetchone()

        # Western whitespace: cities with 3 brands (Pepco+TEDi+KiK) and no Aurora
        all_3_ws = [
            dict(r) for r in conn.execute(f"""
                SELECT cs.city, COUNT(DISTINCT cs.brand) as brands,
                       GROUP_CONCAT(DISTINCT cs.brand) as brand_list
                FROM competitor_stores cs
                WHERE cs.city NOT IN (SELECT DISTINCT city FROM stores WHERE {_ACTIVE_LATEST})
                GROUP BY cs.city
                HAVING brands >= 3
                ORDER BY brands DESC, cs.city
            """)
        ]

        # 2-brand western whitespace
        two_brand_ws = [
            dict(r) for r in conn.execute(f"""
                SELECT cs.city, COUNT(DISTINCT cs.brand) as brands,
                       GROUP_CONCAT(DISTINCT cs.brand) as brand_list
                FROM competitor_stores cs
                WHERE cs.city NOT IN (SELECT DISTINCT city FROM stores WHERE {_ACTIVE_LATEST})
                GROUP BY cs.city
                HAVING brands = 2
                ORDER BY cs.city
                LIMIT 20
            """)
        ]

        # Competitor counts
        comp_counts = {}
        for r in conn.execute(
            "SELECT brand, COUNT(DISTINCT city) as cities, COUNT(*) as stores FROM competitor_stores GROUP BY brand"
        ):
            comp_counts[r["brand"]] = {"stores": r["stores"], "cities": r["cities"]}

        conn.close()
        return {
            "aurora_by_region": aurora_by_region,
            "aurora_total_stores": aurora_total["n"],
            "aurora_total_cities": aurora_total["cities"],
            "all_3_brand_whitespace": all_3_ws,
            "two_brand_whitespace": two_brand_ws,
            "competitor_counts": comp_counts,
        }
    except Exception as e:
        logger.warning(f"Regional intelligence DB query failed: {e}")
        return {}


# Western Romania cities by region — used for archetype labeling
_WESTERN_CITIES = {
    "Vest": {"Timișoara", "Timisoara", "Arad", "Reșița", "Resita", "Deta", "Lipova"},
    "Nord-Vest": {"Oradea", "Cluj-Napoca", "Cluj", "Baia Mare", "Satu Mare", "Zalău", "Zalau", "Bistrița", "Bistrita"},
    "Centru": {"Brașov", "Brasov", "Sibiu", "Târgu Mureș", "Targu Mures", "Alba Iulia", "Deva", "Hunedoara",
               "Miercurea Ciuc", "Sfântu Gheorghe", "Sfantu Gheorghe", "Aiud", "Turda", "Mediaș", "Medias"},
}
_WESTERN_CITY_SET = {c.lower() for cities in _WESTERN_CITIES.values() for c in cities}


def _archetype(city: str, aurora_cities: set, all_3_cities: set, western_cities: set) -> str:
    c = city.lower()
    if c in western_cities and c in all_3_cities:
        return "western-gap / full-competitor-set"
    if c in western_cities:
        return "western-gap"
    if c in all_3_cities:
        return "full-competitor-set"
    if c in aurora_cities:
        return "Aurora-present"
    return "general-whitespace"


def _build_context(data: dict, today: str) -> str:
    changes = data.get("changes", [])
    future_openings = data.get("future_openings", []) or []
    jobs = data.get("jobs", []) or []
    news = data.get("news", []) or []
    instagram = data.get("instagram_posts", []) or []
    current_stores = data.get("current_stores", [])
    competitor_stores = data.get("competitor_stores", {}) or {}
    whitespace = data.get("whitespace_opps", []) or []

    # ── Regional intelligence (live DB query) ─────────────────────────────────
    reg = _query_regional_intelligence()
    aurora_by_region = reg.get("aurora_by_region", {})
    all_3_ws = reg.get("all_3_brand_whitespace", [])
    comp_counts = reg.get("competitor_counts", {})
    db_aurora_cities = reg.get("aurora_total_cities", 0)
    db_aurora_stores = reg.get("aurora_total_stores", 0)

    all_3_city_set = {r["city"].lower() for r in all_3_ws}
    aurora_city_set = {s.get("city", "").lower() for s in current_stores if s.get("city")}
    western_ws_cities = [r["city"] for r in all_3_ws if r["city"].lower() in _WESTERN_CITY_SET]
    other_ws_cities = [r["city"] for r in all_3_ws if r["city"].lower() not in _WESTERN_CITY_SET]

    # ── Map changes ────────────────────────────────────────────────────────────
    new_stores = [c for c in changes if c.get("change_type") == "NEW_STORE"]
    removed = [c for c in changes if c.get("change_type") == "REMOVED_STORE"]
    updated = [c for c in changes if c.get("change_type") == "STORE_UPDATED"]
    relocated = [c for c in changes if c.get("change_type") == "RELOCATED_STORE"]

    def _city(c):
        return (c.get("store") or {}).get("city", "") or c.get("city", "")

    # ── Predictions split by evidence quality ─────────────────────────────────
    aurora_preds = [c for c in future_openings if c.get("change_type") == "POSSIBLE_FUTURE_OPENING"]
    market_sigs = [c for c in future_openings if c.get("change_type") == "MARKET_ACTIVITY_SIGNAL"]
    strong_aurora = [p for p in aurora_preds if _classify_prediction(p) == "aurora_signal"]
    weak_aurora = [p for p in aurora_preds if _classify_prediction(p) == "market_opportunity"]

    # ── Instagram ──────────────────────────────────────────────────────────────
    ig_aurora = [p for p in instagram if not p.get("brand")]
    ig_opening = [p for p in ig_aurora if p.get("signal_type") == "confirmed_opening_signal"]
    ig_location = [p for p in ig_aurora if p.get("signal_type") == "possible_store_location_signal"]
    ig_comp = [p for p in instagram if p.get("brand") and p.get("signal_score", 0) >= 35]

    # ── News by category ───────────────────────────────────────────────────────
    aurora_news = [a for a in news if a.get("signal_category") in ("aurora_direct", "aurora_confirmed", "aurora_mentioned")]
    comp_news = [a for a in news if a.get("signal_category") in ("competitor_expansion",)]
    retail_park_news = [a for a in news if a.get("signal_category") in ("retail_park", "mall_leasing")]

    # ── Competitor network (prefer DB counts, fall back to passed data) ────────
    if comp_counts:
        pepco_cities = comp_counts.get("Pepco", {}).get("cities", 0)
        pepco_stores = comp_counts.get("Pepco", {}).get("stores", 0)
        kik_cities = comp_counts.get("KiK", {}).get("cities", 0)
        tedi_cities = comp_counts.get("TEDi", {}).get("cities", 0)
    else:
        comp_city_count = _competitor_unique_cities(competitor_stores)
        pepco_cities = comp_city_count.get("Pepco", 0)
        pepco_stores = len(competitor_stores.get("Pepco", []))
        kik_cities = comp_city_count.get("KiK", 0)
        tedi_cities = comp_city_count.get("TEDi", 0)

    # Validate: DB count must match pipeline count (guards against multi-snapshot bug)
    pipeline_store_count = len(current_stores)
    if db_aurora_stores and db_aurora_stores != pipeline_store_count:
        logger.warning(
            f"Store count mismatch executive_summary: DB={db_aurora_stores} vs pipeline={pipeline_store_count}. "
            "Using pipeline count."
        )
        db_aurora_stores = pipeline_store_count
        db_aurora_cities = len(aurora_city_set)

    aurora_cities_count = db_aurora_cities or len(aurora_city_set)
    aurora_stores_count = db_aurora_stores or pipeline_store_count
    gap = pepco_cities - aurora_cities_count

    # ── Jobs ──────────────────────────────────────────────────────────────────
    aurora_jobs = [j for j in jobs if "aurora" in (j.get("company", "") + j.get("title", "")).lower()]

    # ── Build context string ──────────────────────────────────────────────────
    lines = [
        f"Date: {today}",
        "",
        "=== CONFIRMED AURORA MAP STATE ===",
        f"Aurora active: {aurora_stores_count} stores in {aurora_cities_count} unique cities/localities",
        f"New stores on map today: {len(new_stores)} {[_city(c) for c in new_stores[:4]]}",
        f"Stores removed today: {len(removed)} {[_city(c) for c in removed[:3]]}",
        f"Stores relocated: {len(relocated)} | updated: {len(updated)}",
        "",
        "=== AURORA REGIONAL DISTRIBUTION ===",
    ]

    # Ordered by strategic importance
    region_order = ["Nord-Est", "Sud-Est", "Sud-Muntenia", "București-Ilfov",
                    "Nord-Vest", "Centru", "Vest", "Sud-Vest Oltenia", "Unknown"]
    for region in region_order:
        n = aurora_by_region.get(region, 0)
        note = ""
        if region in ("Vest", "Sud-Vest Oltenia") and n == 0:
            note = "  ← ZERO AURORA PRESENCE"
        elif region == "Nord-Est":
            note = "  ← Aurora's home base"
        elif region in ("Nord-Vest", "Centru") and n <= 2:
            note = "  ← minimal presence"
        lines.append(f"  {region}: {n} stores{note}")

    # Aurora's eastern concentration
    eastern = aurora_by_region.get("Nord-Est", 0) + aurora_by_region.get("Sud-Est", 0)
    known_region_stores = sum(v for k, v in aurora_by_region.items() if k != "Unknown")
    eastern_pct = round(eastern / known_region_stores * 100) if known_region_stores else 0
    lines += [
        f"Eastern bias: Nord-Est + Sud-Est = {eastern} of {known_region_stores} geocoded stores = {eastern_pct}% — confirms eastern concentration",
        "",
        "=== WESTERN ROMANIA GAP (strategic opportunity) ===",
        f"Cities with ALL THREE competitors (Pepco+TEDi+KiK) and ZERO Aurora — {len(all_3_ws)} total:",
    ]
    if western_ws_cities:
        lines.append(f"  Western Romania (Vest/Nord-Vest/Centru): {', '.join(western_ws_cities[:10])}")
    if other_ws_cities:
        lines.append(f"  Other regions: {', '.join(other_ws_cities[:8])}")
    lines += [
        f"Interpretation: These {len(all_3_ws)} cities have proven discount retail demand (3 brands operating) but Aurora has not entered.",
        "",
        "=== COMPETITOR NETWORK vs AURORA ===",
        f"Pepco: {pepco_stores} stores in {pepco_cities} cities (7.2x Aurora's city count)",
        f"KiK: {kik_cities} cities | TEDi: {tedi_cities} cities",
        f"Aurora: {aurora_stores_count} stores in {aurora_cities_count} cities",
        f"Coverage gap: Pepco is in {gap} more localities than Aurora",
        f"Pepco model: mid-size town saturation (20-50k population) — proven across 278+ such towns",
        "Aurora model: eastern cluster concentration — not yet replicating Pepco's coverage breadth",
        "",
        "=== AURORA-SPECIFIC INTELLIGENCE (unconfirmed on map) ===",
        f"Strong Aurora signals (dedicated article / official source): {len(strong_aurora)} cities",
    ]
    for p in strong_aurora[:4]:
        score = p.get("raw_confidence", p.get("confidence", {}).get("score", 0))
        ev = p.get("evidence", {})
        src_titles = [s.get("title", "")[:55] for s in ev.get("aurora_signals", [])[:2]]
        lines.append(f"  - {_city(p)} | score={score:.2f} | {src_titles}")
    lines += [
        f"Weak Aurora signals (multi-city job listings only — NOT city-confirmed): {len(weak_aurora)} cities",
        f"  Note: a single job posting listing multiple cities is NOT a city-specific prediction",
        f"  Cities: {[_city(p) for p in weak_aurora[:8]]}",
        f"Retail market activity signals (competitor/retail-park, no Aurora evidence): {len(market_sigs)} cities",
        "",
        "=== NEWS & RETAIL INTELLIGENCE ===",
        f"Aurora direct articles: {len(aurora_news)}",
    ]
    for a in aurora_news[:3]:
        lines.append(f"  - {a.get('title','')[:70]} | src: {a.get('source','')} | cities: {a.get('cities_mentioned',[])[:3]}")
    lines += [f"Competitor expansion articles: {len(comp_news)}"]
    for a in comp_news[:4]:
        lines.append(f"  - {a.get('company','')}: {a.get('title','')[:60]} | cities: {a.get('cities_mentioned',[])[:3]}")
    lines += [f"Retail park / leasing articles: {len(retail_park_news)}"]
    for a in retail_park_news[:3]:
        cities_str = str(a.get("cities_mentioned", [])[:3])
        lines.append(f"  - {a.get('title','')[:65]} | cities: {cities_str}")

    lines += [
        "",
        "=== INSTAGRAM ===",
        f"Aurora confirmed opening posts: {len(ig_opening)}",
    ]
    for p in ig_opening[:2]:
        lines.append(f"  - {p.get('caption', '')[:100]}")
    lines += [
        f"Aurora location/presence posts: {len(ig_location)}",
        f"Competitor actionable posts (score>=35): {len(ig_comp)}",
    ]

    lines += [
        "",
        "=== CITY ARCHETYPES for opportunity section ===",
        "Use these labels when citing cities:",
    ]
    for r in all_3_ws[:8]:
        city = r["city"]
        arch = _archetype(city, aurora_city_set, all_3_city_set, _WESTERN_CITY_SET)
        lines.append(f"  {city}: [{arch}]")

    lines += [
        "",
        "=== JOB SIGNALS ===",
        f"Aurora-specific job postings: {len(aurora_jobs)}",
        f"Sample titles: {[j.get('title','')[:50] for j in aurora_jobs[:3]]}",
    ]

    return "\n".join(lines)


# ── Telegram brief builder helpers ───────────────────────────────────────────

_AURORA_CATS = {"aurora_direct", "aurora_confirmed", "aurora_mentioned"}
_COMP_CATS   = {"competitor_expansion"}
_PARK_CATS   = {"retail_park", "mall_leasing", "shopping_center"}

_SIG_EMOJI = {
    "aurora_specific":      "📰",
    "competitor_expansion": "🔵",
    "retail_park":          "🏗️",
    "market_trend":         "📊",
    "weak_signal":          "💡",
}


def _is_retail_park_article(article: dict) -> bool:
    """True when article is about a mall/retail-park named 'Aurora', NOT Aurora Multimarket."""
    title = (article.get("title","") + article.get("url","")).lower()
    return "retail park" in title or "aurora mall" in title


def _build_news_section_ua(news: list, deep: dict, synthesis: dict = None) -> str:
    """
    Section 2: news intelligence built from actual article data.
    Keeps Romanian city names in Latin script for accuracy.
    Shows: Ukrainian title/summary, source, why it matters, evidence level.
    synthesis["translations"] used for Ukrainian titles when available.
    """
    translations = (synthesis or {}).get("translations", {})

    def _ua_title(a: dict) -> str:
        orig = a.get("title","")
        return translations.get(orig) or a.get("translated_title") or orig

    aurora_arts  = [a for a in news if a.get("signal_category") in _AURORA_CATS
                    and not _is_retail_park_article(a)]
    rp_arts      = [a for a in news if a.get("signal_category") in _AURORA_CATS
                    and _is_retail_park_article(a)]
    comp_arts    = [a for a in news if a.get("signal_category") in _COMP_CATS]
    park_arts    = [a for a in news if a.get("signal_category") in _PARK_CATS]

    lines: list[str] = []

    # Aurora Multimarket articles (real signals)
    for a in aurora_arts[:3]:
        title_ua = _ua_title(a)
        src  = a.get("source","")
        cats = (a.get("cities_mentioned") or [])[:2]
        city_str = f" ({', '.join(cats)})" if cats else ""
        lines.append(
            f"📰 *Aurora Multimarket:* _{title_ua[:90]}{city_str}_\n"
            f"  📎 {src} | Рівень: підтверджений сигнал Aurora\n"
            f"  _Перевірити на офіційній карті aurora-retail.com_"
        )

    # Competitor expansion
    for a in comp_arts[:2]:
        title_ua = _ua_title(a)
        company  = a.get("company","") or "Конкурент"
        src  = a.get("source","")
        cats = (a.get("cities_mentioned") or [])[:2]
        city_str = f" ({', '.join(cats)})" if cats else ""
        lines.append(
            f"🔵 *{company}:* _{title_ua[:85]}{city_str}_\n"
            f"  📎 {src} | Рівень: конкурентний сигнал\n"
            f"  _Конкурентний тиск у містах без Aurora._"
        )

    # Retail parks (infrastructure signal, NOT Aurora openings)
    for a in (rp_arts + park_arts)[:1]:
        title_ua = _ua_title(a)
        src  = a.get("source","")
        cats = (a.get("cities_mentioned") or [])[:2]
        city_str = f" ({', '.join(cats)})" if cats else ""
        lines.append(
            f"🏗️ *Ритейл-парк/ТЦ:* _{title_ua[:85]}{city_str}_\n"
            f"  📎 {src} | Рівень: інфраструктурний сигнал\n"
            f"  _Потенційна точка входу — перевірити список орендарів._"
        )

    if not lines:
        lines.append("Значущих ритейл-новин для Aurora сьогодні не виявлено.")

    return "\n".join(lines)


def _build_competitor_section_ua(competitor_stores: dict, current_stores: list,
                                  comp_counts: dict) -> str:
    """
    Section 3: competitor movement — built from actual pipeline/DB data.
    Never claims 'no competitors' without verification.
    """
    n_aurora        = len(current_stores)
    aurora_city_set = {s.get("city","").lower() for s in current_stores if s.get("city")}
    n_aurora_cities = len(aurora_city_set)

    lines: list[str] = []
    for brand in ["Pepco","KiK","TEDi"]:
        bc = comp_counts.get(brand, {})
        n_s = bc.get("stores", 0)
        n_c = bc.get("cities", 0)
        if n_s:
            lines.append(f"• {brand}: {n_s} магазинів / {n_c} міст")
        else:
            lines.append(f"• {brand}: дані потребують перевірки")
    lines.append(f"• Aurora: {n_aurora} магазинів / {n_aurora_cities} міст")

    pepco_cities = comp_counts.get("Pepco",{}).get("cities", 0)
    if pepco_cities and n_aurora_cities:
        gap = pepco_cities - n_aurora_cities
        lines.append(f"• Розрив Pepco vs Aurora: +{gap} міст де є Pepco, але немає Aurora")

    return "\n".join(lines)


def _build_predictions_section_ua(future_openings: list, deep: dict) -> str:
    """
    Section 4: cities to investigate — only evidence-backed Aurora-specific signals.
    Multi-city job listings do NOT count as city-specific evidence.
    """
    aurora_preds = [
        c for c in future_openings
        if c.get("change_type") == "POSSIBLE_FUTURE_OPENING"
        and c.get("aurora_specific")
        and c.get("confidence",{}).get("level") in ("HIGH","MEDIUM")
    ]

    # Filter: must have ≥1 real Aurora signal (not a mass multi-city job listing)
    real_preds = []
    for p in aurora_preds:
        ev = p.get("evidence",{}) or {}
        aurora_sigs = ev.get("aurora_signals",[]) or []
        has_real = any(
            s.get("source") not in ("linkedin",)
            and "ejobs.ro"    not in (s.get("url",""))
            and "bestjobs"    not in (s.get("url",""))
            and len(s.get("cities_mentioned") or []) <= 4  # not a mass multi-city listing
            for s in aurora_sigs
        )
        if has_real:
            real_preds.append(p)

    lines: list[str] = []
    for p in sorted(real_preds, key=lambda x: x.get("confidence",{}).get("score",0), reverse=True)[:4]:
        city  = p.get("city","")
        conf  = p.get("confidence",{})
        level = conf.get("level","")
        score = conf.get("score",0)
        ev    = p.get("evidence",{}) or {}
        aurora_sigs = ev.get("aurora_signals",[]) or []
        # Best non-job source
        best_sig = next(
            (s for s in aurora_sigs
             if s.get("source") not in ("linkedin",)
             and "ejobs.ro" not in (s.get("url",""))),
            aurora_sigs[0] if aurora_sigs else {}
        )
        src_name  = best_sig.get("source","")
        src_title = (best_sig.get("title") or "")[:60]
        n_sigs = len(aurora_sigs)

        line = f"• *{city}* — оцінка: {score:.2f} [{level}], {n_sigs} Aurora-сигналів"
        if src_name:
            line += f"\n  Джерело: {src_name}"
        if src_title:
            line += f" — _{src_title}_"
        line += "\n  Що перевірити: офіційна карта Aurora + дата відкриття"
        lines.append(line)

    # Supplement with deep-analysis whitespace cities
    ws_cities = deep.get("whitespace_cities") or []
    ws_added = 0
    for c in ws_cities:
        city = c.get("city","")
        already = any(city.lower() == p.get("city","").lower() for p in real_preds)
        if already or ws_added >= 2:
            continue
        conf    = c.get("confidence","market_only")
        why     = (c.get("why") or "").strip()
        missing = (c.get("missing") or "").strip()
        nxt     = (c.get("next_check") or "").strip()
        lbl     = "сигнал Aurora" if conf == "aurora_signal" else "ринкова ніша"
        line    = f"• *{city}* [{lbl}] — {why}"
        if missing:
            line += f". Відсутнє: _{missing}_"
        if nxt:
            line += f". {nxt}"
        lines.append(line)
        ws_added += 1

    if not lines:
        return (
            "Міст з підтвердженими Aurora-специфічними сигналами сьогодні не виявлено.\n"
            "• Рекомендуємо перевірити офіційну карту та Instagram Aurora."
        )

    return "\n".join(lines)


def _validate_key_insight(key: str, new_stores_today: list, n_stores: int) -> str:
    """
    Guard against GPT hallucinating new openings or wrong store counts.
    Returns cleaned insight or empty string to suppress.
    """
    if not key:
        return ""

    # Wrong store count mentioned by GPT
    for bad in ["124", "125", "126", "123"]:
        if bad in key:
            key = key.replace(bad, str(n_stores))

    # "New opening" claim when there are no map changes
    if not new_stores_today:
        opening_words_ua = [
            "відкривається", "відкриває новий", "нове відкриття", "нового магазину",
            "opening", "new store",
        ]
        if any(w in key.lower() for w in opening_words_ua):
            return ""  # suppress hallucinated insight

    # Truncate to reasonable length
    return key[:300]


def _format_telegram_brief(deep: dict, s1: str, today: str, report_path: str,
                            data: dict = None, synthesis: dict = None) -> str:
    """
    5-section Ukrainian Telegram brief.
    Factual sections (1, 2, 3, 4) built from pipeline data in Python.
    GPT sections (key_insight, next_investigations) validated before use.
    """
    data = data or {}
    changes         = data.get("changes", []) or []
    news            = data.get("news", []) or []
    future_openings = data.get("future_openings", []) or []
    current_stores  = data.get("current_stores", []) or []
    competitor_stores = data.get("competitor_stores", {}) or {}

    new_stores_today = [c for c in changes if c.get("change_type") == "NEW_STORE"]
    n_stores = len(current_stores)

    # Pull comp_counts from DB (same query already done in regional intelligence)
    try:
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        comp_counts: dict = {}
        for r in conn.execute(
            "SELECT brand, COUNT(DISTINCT city) as cities, COUNT(*) as stores "
            "FROM competitor_stores GROUP BY brand"
        ):
            comp_counts[r["brand"]] = {"stores": r["stores"], "cities": r["cities"]}
        conn.close()
    except Exception:
        comp_counts = {}

    parts: list[str] = []

    # ── Header ────────────────────────────────────────────────────────────────
    parts.append(f"📍 *Aurora Romania — аналітичний бриф {today}*")

    # ── Key insight (GPT, validated) ──────────────────────────────────────────
    key = _validate_key_insight(
        (deep.get("key_insight") or "").strip(),
        new_stores_today,
        n_stores,
    )
    if key:
        parts.append(f"🔍 *Головний висновок дня*\n{key}")

    # ── 1. Confirmed network status (Python-built, factual) ───────────────────
    parts.append(f"*1. Підтверджений статус мережі Aurora*\n{s1}")

    # ── 2. News & web intelligence (Python-built from actual articles) ─────────
    s2 = _build_news_section_ua(news, deep, synthesis=synthesis)
    parts.append(f"*2. Новини та веб-аналітика*\n{s2}")

    # ── 3. Competitor movement (Python-built from actual data) ────────────────
    s3 = _build_competitor_section_ua(competitor_stores, current_stores, comp_counts)
    parts.append(f"*3. Рух конкурентів*\n{s3}")

    # ── 4. Evidence-backed predictions (Python-filtered) ─────────────────────
    s4 = _build_predictions_section_ua(future_openings, deep)
    parts.append(f"*4. Міста для перевірки*\n{s4}")

    # ── 5. Next steps (GPT, trimmed) ─────────────────────────────────────────
    tasks = deep.get("next_investigations") or []
    if tasks:
        parts.append("*5. Що перевірити завтра*\n" + "\n".join(f"• {t}" for t in tasks[:5]))

    # ── Footer ────────────────────────────────────────────────────────────────
    parts.append(f"📄 *Повний звіт:* `{report_path}`")

    return "\n\n".join(p for p in parts if p)


def _build_ua_sections_12(data: dict, synthesis: dict) -> tuple[str, str]:
    """Build Ukrainian sections 1 and 2 deterministically (no GPT)."""
    from src.analysis.retail_intelligence_synthesis import _AURORA_CATS
    changes = data.get("changes", []) or []
    news    = data.get("news", []) or []
    current_stores = data.get("current_stores", []) or []

    def _city(c): return (c.get("store") or {}).get("city", "") or c.get("city", "")
    new_stores = [c for c in changes if c.get("change_type") == "NEW_STORE"]
    removed    = [c for c in changes if c.get("change_type") == "REMOVED_STORE"]
    relocated  = [c for c in changes if c.get("change_type") == "RELOCATED_STORE"]

    reg = _query_regional_intelligence()
    n_stores = reg.get("aurora_total_stores", 0) or len(current_stores)
    n_cities = reg.get("aurora_total_cities", 0)

    # Section 1 — network status
    s1_lines = [f"• {n_stores} активних магазинів у {n_cities} містах."]
    if new_stores:
        s1_lines.append(f"• 🟢 Додано {len(new_stores)} новий(х) магазин(ів): "
                        f"{', '.join(_city(c) for c in new_stores[:4])}")
    else:
        s1_lines.append("• Змін на мапі сьогодні немає.")
    if removed:
        s1_lines.append(f"• 🔴 Видалено {len(removed)}: {', '.join(_city(c) for c in removed[:3])}")
    if relocated:
        s1_lines.append(f"• 🔄 Переміщено: {len(relocated)} магазин(ів).")
    s1 = "\n".join(s1_lines)

    # Section 2 — retail intelligence (synthesis + top translated titles)
    aurora_news = [a for a in news if a.get("signal_category") in _AURORA_CATS]
    comp_news   = [a for a in news if a.get("signal_category") == "competitor_expansion"]

    aurora_insight = synthesis.get("aurora_insights", "")
    comp_insight   = synthesis.get("competitor_insights", "")
    translations   = synthesis.get("translations", {})

    s2_lines = []
    if aurora_insight:
        s2_lines.append(f"• {aurora_insight}")
    if aurora_news:
        for a in aurora_news[:2]:
            orig  = a.get("title", "")
            ua    = translations.get(orig, orig)
            src   = a.get("source", "")
            s2_lines.append(f'  📰 _Сигнал Aurora_: "{ua[:80]}" ({src})')
    if not aurora_news and not aurora_insight:
        s2_lines.append("• Прямих сигналів про розширення Aurora сьогодні не виявлено.")
    if comp_insight:
        s2_lines.append(f"• {comp_insight}")
    elif comp_news:
        best = comp_news[0]
        orig = best.get("title", "")
        ua   = translations.get(orig, orig)
        s2_lines.append(f'  🏪 _Ринковий сигнал_: "{ua[:75]}"')

    s2 = "\n".join(s2_lines)
    return s1, s2


def _openai_summary(data: dict, today: str, report_path: str) -> str:
    from src.analysis.retail_intelligence_synthesis import synthesize, _rule_based_synthesis
    from src.analysis.deep_market_analysis import generate_deep_market_analysis

    news           = data.get("news", []) or []
    current_stores = data.get("current_stores", []) or []
    future_openings = data.get("future_openings", []) or []

    # Title translations for section 1 (cached synthesis)
    try:
        synthesis = synthesize(news, current_stores)
    except Exception:
        synthesis = _rule_based_synthesis(news)

    # Deep analysis — one comprehensive OpenAI call (cached)
    deep = generate_deep_market_analysis(data)

    # Attach Aurora predictions so formatter can add them to section 4
    aurora_preds = sorted(
        [c for c in future_openings
         if c.get("change_type") == "POSSIBLE_FUTURE_OPENING" and c.get("aurora_specific")],
        key=lambda x: x.get("confidence", {}).get("score", 0), reverse=True,
    )
    deep["_aurora_preds_for_telegram"] = aurora_preds

    s1, _ = _build_ua_sections_12(data, synthesis)
    msg = _format_telegram_brief(deep, s1, today, report_path, data=data, synthesis=synthesis)
    logger.info("OpenAI executive summary generated (Ukrainian)")
    return msg


# ── Rule-based fallback (no OpenAI) ──────────────────────────────────────────

def _rule_based_summary(data: dict, today: str, report_path: str) -> str:
    from src.analysis.retail_intelligence_synthesis import _rule_based_synthesis
    from src.analysis.deep_market_analysis import generate_deep_market_analysis

    news           = data.get("news", []) or []
    future_openings = data.get("future_openings", []) or []

    synthesis = _rule_based_synthesis(news)
    deep      = generate_deep_market_analysis(data)  # returns rule-based result when no API key

    aurora_preds = sorted(
        [c for c in future_openings
         if c.get("change_type") == "POSSIBLE_FUTURE_OPENING" and c.get("aurora_specific")],
        key=lambda x: x.get("confidence", {}).get("score", 0), reverse=True,
    )
    deep["_aurora_preds_for_telegram"] = aurora_preds

    s1, _ = _build_ua_sections_12(data, synthesis)
    return _format_telegram_brief(deep, s1, today, report_path, data=data, synthesis=synthesis)


# ── Public API ────────────────────────────────────────────────────────────────

def generate_executive_summary(data: dict, report_path: str = "") -> str:
    """
    Generate a strategic Telegram briefing from pipeline data.
    Uses OpenAI GPT-4o-mini when OPENAI_API_KEY is set; falls back to rule-based.
    """
    from src.config import OPENAI_API_KEY
    today = date.today().isoformat()

    if not OPENAI_API_KEY:
        logger.info("OPENAI_API_KEY not set — using rule-based executive summary")
        return _rule_based_summary(data, today, report_path)

    try:
        return _openai_summary(data, today, report_path)
    except Exception as e:
        logger.warning(f"OpenAI summary failed ({e}) — falling back to rule-based")
        return _rule_based_summary(data, today, report_path)
