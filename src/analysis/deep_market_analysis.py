"""
Deep market analysis engine.
Single OpenAI call synthesizing Aurora map, competitor networks, news, jobs,
Instagram, retail parks, and historical snapshots into structured Ukrainian intelligence.

Returns a dict with 10 analysis sections. Cached per pipeline run.
"""
import json
from collections import defaultdict
from datetime import date

from src.config import setup_logging, DB_PATH

logger = setup_logging("analysis.deep_market")

_CACHE: dict = {}

# Western Romania cities — used across multiple modules
WESTERN_CITY_SET = {
    "timișoara", "timisoara", "arad", "reșița", "resita", "deta", "lipova",
    "oradea", "cluj-napoca", "cluj", "baia mare", "satu mare", "zalău", "zalau",
    "bistrița", "bistrita", "brașov", "brasov", "sibiu", "târgu mureș", "targu mures",
    "alba iulia", "deva", "hunedoara", "miercurea ciuc", "sfântu gheorghe",
    "sfantu gheorghe", "aiud", "turda", "mediaș", "medias", "reghin",
    "sfântu gheorghe", "odorheiu secuiesc",
}


_LATEST_SNAP = "(SELECT MAX(snapshot_date) FROM stores)"
_ACTIVE_LATEST = f"status='active' AND snapshot_date={_LATEST_SNAP}"


def _query_db() -> dict:
    """Fresh DB query for regional + competitor state — latest snapshot only."""
    try:
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        aurora_by_region: dict[str, int] = {}
        for r in conn.execute(
            f"SELECT region, COUNT(*) as n FROM stores WHERE {_ACTIVE_LATEST} "
            "GROUP BY region ORDER BY n DESC"
        ):
            aurora_by_region[r["region"] or "Unknown"] = r["n"]

        totals = conn.execute(
            f"SELECT COUNT(*) as n, COUNT(DISTINCT city) as cities "
            f"FROM stores WHERE {_ACTIVE_LATEST}"
        ).fetchone()

        top_cities = [
            dict(r) for r in conn.execute(
                f"SELECT city, COUNT(*) as n FROM stores WHERE {_ACTIVE_LATEST} "
                "GROUP BY city ORDER BY n DESC LIMIT 12"
            )
        ]

        all_3_ws = [
            dict(r) for r in conn.execute(f"""
                SELECT cs.city,
                       COUNT(DISTINCT cs.brand) as brands,
                       GROUP_CONCAT(DISTINCT cs.brand) as brand_list
                FROM competitor_stores cs
                WHERE cs.city NOT IN (
                    SELECT DISTINCT city FROM stores WHERE {_ACTIVE_LATEST}
                )
                GROUP BY cs.city
                HAVING brands >= 3
                ORDER BY brands DESC, cs.city
            """)
        ]

        comp_counts: dict[str, dict] = {}
        for r in conn.execute(
            "SELECT brand, COUNT(DISTINCT city) as cities, COUNT(*) as stores "
            "FROM competitor_stores GROUP BY brand"
        ):
            comp_counts[r["brand"]] = {"stores": r["stores"], "cities": r["cities"]}

        comp_top: dict[str, list] = defaultdict(list)
        for r in conn.execute(
            "SELECT brand, city, COUNT(*) as n FROM competitor_stores "
            "GROUP BY brand, city ORDER BY brand, n DESC"
        ):
            if len(comp_top[r["brand"]]) < 6:
                comp_top[r["brand"]].append(r["city"])

        recent_new = [
            dict(r) for r in conn.execute("""
                SELECT city, COUNT(*) as n FROM changes
                WHERE change_type='NEW_STORE'
                  AND detected_date >= date('now', '-30 days')
                GROUP BY city ORDER BY n DESC LIMIT 8
            """)
        ]

        conn.close()
        return {
            "aurora_by_region": aurora_by_region,
            "n_stores": totals["n"],
            "n_cities": totals["cities"],
            "top_cities": top_cities,
            "all_3_ws": all_3_ws,
            "comp_counts": comp_counts,
            "comp_top": dict(comp_top),
            "recent_new": recent_new,
        }
    except Exception as e:
        logger.warning(f"DB query failed: {e}")
        return {}


def _build_context(data: dict, db: dict) -> str:
    """Build compact but complete context string for the OpenAI prompt."""
    today = date.today().isoformat()

    stores  = data.get("current_stores", []) or []
    news    = data.get("news", []) or []
    jobs    = data.get("jobs", []) or []
    ig      = data.get("instagram_posts", []) or []
    ws      = data.get("whitespace_opps", []) or []
    fo      = data.get("future_openings", []) or []
    chg     = data.get("changes", []) or []

    aurora_by_region = db.get("aurora_by_region", {})
    n_stores  = db.get("n_stores") or len(stores)
    n_cities  = db.get("n_cities") or len({s.get("city","") for s in stores if s.get("city")})
    top_cities = db.get("top_cities", [])
    all_3_ws   = db.get("all_3_ws", [])
    comp_counts = db.get("comp_counts", {})
    comp_top    = db.get("comp_top", {})
    recent_new  = db.get("recent_new", [])

    known   = sum(v for k, v in aurora_by_region.items() if k != "Unknown")
    eastern = aurora_by_region.get("Nord-Est", 0) + aurora_by_region.get("Sud-Est", 0)
    e_pct   = round(eastern / known * 100) if known else 0

    # Validate store count: DB must match pipeline data
    pipeline_stores = len(stores)
    if n_stores != pipeline_stores:
        logger.warning(
            f"Store count mismatch: DB={n_stores} vs pipeline={pipeline_stores}. "
            "Using pipeline count."
        )
        n_stores = pipeline_stores
        n_cities = len({s.get("city","") for s in stores if s.get("city")})

    new_today     = [c for c in chg if c.get("change_type") == "NEW_STORE"]
    removed_today = [c for c in chg if c.get("change_type") == "REMOVED_STORE"]

    out = [f"Дата: {today}", ""]

    # ── CRITICAL FACTUAL CONSTRAINTS FOR GPT ───────────────────────────────────
    out.append("=== НЕЗМІННІ ФАКТИ (не перекручувати) ===")
    out.append(f"Активних магазинів Aurora СЬОГОДНІ (тільки поточний знімок): {n_stores}")
    out.append(f"Нові магазини на карті сьогодні: {len(new_today)}")
    out.append(f"Видалені магазини сьогодні: {len(removed_today)}")
    if not new_today and not removed_today:
        out.append("ПРАВИЛО: Карта без змін — НЕ писати про нові відкриття Aurora у висновку.")
    out.append("")
    out.append("ВАЖЛИВО — розрізняти ці сутності:")
    out.append("  'Aurora Multimarket' = украïнський дискаунтер (АНАЛІЗ ЦЬОГо ЗВІТУ)")
    out.append("  'Aurora Retail Park' = назва торгового центру (власність Cometex або інших)")
    out.append("  Статті про 'Aurora Retail Park' = сигнали ТЦ/нерухомості, НЕ відкриття Aurora Multimarket")
    out.append("")

    # ── Aurora network ──────────────────────────────────────────────────────────
    out.append("=== МЕРЕЖА AURORA ===")
    out.append(f"Загалом: {n_stores} магазинів у {n_cities} містах")
    for reg in ["Nord-Est","Sud-Est","Sud-Muntenia","București-Ilfov",
                "Nord-Vest","Centru","Vest","Sud-Vest Oltenia"]:
        n = aurora_by_region.get(reg, 0)
        flag = " ← НУЛЬ" if n == 0 and reg in ("Vest","Sud-Vest Oltenia") else (
               " ← BASE" if reg == "Nord-Est" else "")
        out.append(f"  {reg}: {n}{flag}")
    out.append(f"Схід (Nord-Est+Sud-Est): {e_pct}% відомих магазинів")
    if top_cities:
        out.append("Топ-міста: " + ", ".join(f"{c['city']}({c['n']})" for c in top_cities[:10]))
    if recent_new:
        out.append("Нові за 30 днів: " + ", ".join(f"{r['city']}(×{r['n']})" for r in recent_new[:5]))

    out.append(f"Зміни сьогодні: +{len(new_today)} нових, -{len(removed_today)} видалено")
    if new_today:
        out.append("  Нові: " + str([(c.get("store") or {}).get("city","") for c in new_today[:4]]))
    out.append("")

    # ── Competitor network ──────────────────────────────────────────────────────
    out.append("=== МЕРЕЖА КОНКУРЕНТІВ ===")
    for brand in ["Pepco","KiK","TEDi","Action"]:
        bc  = comp_counts.get(brand, {})
        top = ", ".join(comp_top.get(brand, [])[:5])
        out.append(f"{brand}: {bc.get('stores',0)} магазинів / {bc.get('cities',0)} міст | топ: {top}")
    pepco_c = comp_counts.get("Pepco", {}).get("cities", 0)
    gap = pepco_c - n_cities
    out.append(f"Розрив Pepco vs Aurora: {gap} додаткових міст")
    western_ws = [r for r in all_3_ws if r["city"].lower() in WESTERN_CITY_SET]
    other_ws   = [r for r in all_3_ws if r["city"].lower() not in WESTERN_CITY_SET]
    out.append(f"Міста з Pepco+KiK+TEDi та ZERO Aurora: {len(all_3_ws)} загалом, {len(western_ws)} на Заході")
    if western_ws:
        out.append("  Захід: " + ", ".join(r["city"] for r in western_ws[:10]))
    if other_ws:
        out.append("  Інші: " + ", ".join(r["city"] for r in other_ws[:8]))
    out.append("")

    # ── News signals ────────────────────────────────────────────────────────────
    aurora_news = [a for a in news if a.get("signal_category") in
                   ("aurora_direct","aurora_confirmed","aurora_mentioned")]
    comp_news   = [a for a in news if a.get("signal_category") == "competitor_expansion"]
    infra_news  = [a for a in news if a.get("signal_category") in
                   ("retail_park","mall_leasing","shopping_center")]
    market_news = [a for a in news if a.get("signal_category") in
                   ("generic_market","generic_retail","local_news","influencer_signal")]

    out.append("=== СИГНАЛИ НОВИН ===")
    out.append(f"Aurora: {len(aurora_news)} | Конкуренти: {len(comp_news)} | Інфра: {len(infra_news)} | Ринок: {len(market_news)}")
    for a in aurora_news[:5]:
        t = (a.get("translated_title") or a.get("title",""))[:70]
        c = str((a.get("cities_mentioned") or [])[:2])
        s = a.get("source","")
        # Flag "Aurora Retail Park" articles so GPT doesn't treat them as Aurora Multimarket openings
        is_retail_park = "retail park" in (a.get("title","") + a.get("url","")).lower()
        tag = "[AURORA_RETAIL_PARK — ТЦ, не Aurora Multimarket]" if is_retail_park else "[AURORA]"
        out.append(f"  {tag} {t} | {s} | {c}")
    for a in comp_news[:5]:
        t  = a.get("title","")[:65]
        co = a.get("company","") or ""
        c  = str((a.get("cities_mentioned") or [])[:2])
        out.append(f"  [COMP/{co}] {t} | {c}")
    for a in infra_news[:4]:
        t = a.get("title","")[:65]
        c = str((a.get("cities_mentioned") or [])[:2])
        out.append(f"  [INFRA] {t} | {c}")
    out.append("")

    # ── Jobs ────────────────────────────────────────────────────────────────────
    out.append("=== ВАКАНСІЇ ===")
    aurora_jobs = [j for j in jobs if "aurora" in
                   (j.get("company","") + j.get("title","")).lower()]
    comp_jobs   = [j for j in jobs if any(b in
                   (j.get("company","") + j.get("title","")).lower()
                   for b in ["pepco","kik","tedi","action"])]
    generic_n   = len(jobs) - len(aurora_jobs) - len(comp_jobs)
    out.append(f"Aurora: {len(aurora_jobs)} | Конкуренти: {len(comp_jobs)} | Generic: {generic_n}")
    for j in aurora_jobs[:3]:
        locs = j.get("cities_mentioned",[])
        out.append(f"  [AURORA] {j.get('title','')[:50]} @ {j.get('location','')} | міста: {locs[:2]}")
    city_job: dict[str,int] = defaultdict(int)
    for j in jobs:
        for c in (j.get("cities_mentioned") or []):
            city_job[c] += 1
    top_jc = sorted(city_job.items(), key=lambda x: -x[1])[:6]
    if top_jc:
        out.append("Кластеризація по містах: " + ", ".join(f"{c}({n})" for c,n in top_jc))
    out.append("")

    # ── Instagram ───────────────────────────────────────────────────────────────
    out.append("=== INSTAGRAM ===")
    aurora_ig     = [p for p in ig if not p.get("brand")]
    ig_opening    = [p for p in aurora_ig if p.get("signal_type") == "confirmed_opening_signal"]
    ig_location   = [p for p in aurora_ig if p.get("signal_type") == "possible_store_location_signal"]
    comp_ig_act   = [p for p in ig if p.get("brand") and p.get("signal_score",0) >= 30 and
                     p.get("signal_type") in ("confirmed_opening_signal",
                                               "possible_store_location_signal",
                                               "mall_or_retail_park_signal")]
    out.append(f"Aurora відкриття: {len(ig_opening)} | Aurora location: {len(ig_location)} | Конкуренти actionable: {len(comp_ig_act)}")
    for p in ig_opening[:3]:
        cities  = (p.get("cities_mentioned") or [])
        caption = (p.get("caption") or "")[:80]
        out.append(f"  [AURORA OPEN] has_city={bool(cities)} | міста={cities[:2]} | {caption}")
    for p in comp_ig_act[:2]:
        out.append(f"  [COMP/{p.get('brand','')}] міста={p.get('cities_mentioned',[])[:2]} score={p.get('signal_score',0)}")
    out.append("")

    # ── Aurora predictions ──────────────────────────────────────────────────────
    out.append("=== AURORA-СПЕЦИФІЧНІ ПРОГНОЗИ ===")
    aurora_preds = sorted(
        [c for c in fo if c.get("change_type") == "POSSIBLE_FUTURE_OPENING" and c.get("aurora_specific")],
        key=lambda x: x.get("confidence",{}).get("score",0), reverse=True,
    )
    market_sigs = [c for c in fo if not c.get("aurora_specific")]
    out.append(f"Підтверджених Aurora-прогнозів: {len(aurora_preds)} | Ринкових сигналів (без Aurora): {len(market_sigs)}")
    for p in aurora_preds:
        city  = p.get("city","")
        conf  = p.get("confidence",{})
        ev    = p.get("evidence",{})
        n_a   = len(ev.get("aurora_signals",[]))
        n_j   = ev.get("job_count",0)
        out.append(f"  {city} | {conf.get('level','')} | score={conf.get('score',0):.2f} | aurora_signals={n_a} | jobs={n_j}")
    out.append(f"Ринкові міста: {[p.get('city','') for p in market_sigs[:8]]}")
    out.append("")

    # ── Top whitespace ──────────────────────────────────────────────────────────
    out.append("=== ТОП РИНКОВІ НІШІ ===")
    for opp in (ws or [])[:10]:
        city   = opp.get("city","")
        brands = "+".join(opp.get("competitor_brands",{}).keys())
        score  = opp.get("opportunity_score",0)
        region = opp.get("region","")
        is_w   = city.lower() in WESTERN_CITY_SET
        out.append(f"  {city} ({region}) {'[WEST]' if is_w else ''} — {brands} — score: {score:.0f}")

    return "\n".join(out)


# ── OpenAI prompt ──────────────────────────────────────────────────────────────

_SYSTEM_DEEP = """Ти старший аналітик ринку ритейлу для Aurora Romania — українського дискаунтера.
Пишеш ВИКЛЮЧНО УКРАЇНСЬКОЮ МОВОЮ. Стиль: конкретний, фактологічний, аналітичний.

ОБОВ'ЯЗКОВІ ПРАВИЛА:
- Кожен висновок = конкретне місто або регіон + цифра
- Підтверджений Aurora сигнал = тільки з мапи/офіційного сайту/Instagram Aurora/статті про Aurora
- Ринкова можливість = конкуренти/ритейл-парки без Aurora-доказів
- ЗАБОРОНЕНО: "стратегічне значення", "необхідно розглянути", "важлива можливість", "ринкові можливості"
- Вакансія в кількох містах ≠ прогноз для конкретного міста
- Якщо щось невідомо — написати це прямо
- Відповідай ТІЛЬКИ валідним JSON без markdown-блоків

ПРАВИЛА ЩОДО ДАНИХ КОНКУРЕНТІВ:
- "Pepco: 517 магазинів", "KiK: 160 магазинів", "TEDi: 77 магазинів" — це НАЦІОНАЛЬНІ підсумки по Румунії.
- ЗАБОРОНЕНО використовувати національні підсумки як докази для конкретного міста.
- У полях evidence/whitespace_cities можна вказувати тільки кількість магазинів конкурента в конкретному місті.
- Приклад ✅: "Pepco має 10 магазинів у Timișoara" — це міський показник.
- Приклад ❌: "Pepco: 517 магазинів, KiK: 160 магазинів" у клітинці evidence міста — це ЗАБОРОНЕНО."""


_USER_TEMPLATE_DEEP = """Дані для аналізу:

{context}

Поверни JSON (всі значення — українською, конкретні факти + цифри):

{{
  "key_insight": "1-2 речення. Головний невочевидний інсайт — конкретне місто або регіон + цифра. НЕ банальності типу 'Aurora має розширюватись'.",

  "competitor_analysis": "2-3 речення. Стратегія Pepco/KiK/TEDi: малі міста чи великі? Схід/Захід? Де перекриваються? Де домінує один бренд? Конкретні цифри.",

  "aurora_network": "2 речення. Поточний патерн Aurora: кластерна чи національна? Яка глибина присутності на Заході/Центрі? Конкретні регіони.",

  "regional_gaps": [
    {{
      "region": "назва регіону",
      "category": "aurora_stronghold | competitor_dense_gap | retail_park_opportunity | zero_aurora | growing",
      "analysis": "1 конкретне речення — що відбувається в цьому регіоні",
      "entry_type": "flagship_city | retail_park | small_town_cluster | cluster_expansion | not_recommended"
    }}
  ],

  "retail_signals": [
    {{
      "summary_ua": "1 речення — суть сигналу (не заголовок, а що саме відбулось)",
      "why_matters": "1 речення — чому це важливо для Aurora конкретно",
      "city": "назва міста або null",
      "classification": "aurora_specific | competitor_expansion | retail_park | market_trend | weak_signal",
      "action": "що конкретно перевірити"
    }}
  ],

  "hiring_analysis": "1-2 речення. Aurora vs конкурент vs generic. Кластеризація по містах. Чи підкріплюють вакансії прогнози?",

  "instagram_analysis": "1-2 речення. Чи є міста у posts? Чи підтверджують реальні відкриття? Що потребує ручної перевірки?",

  "whitespace_cities": [
    {{
      "city": "назва міста",
      "why": "конкретна причина (не просто 'є конкуренти')",
      "evidence": "перелік конкретних доказів",
      "confidence": "aurora_signal | market_only | weak",
      "missing": "що відсутнє для підтвердження",
      "next_check": "конкретна дія"
    }}
  ],

  "risks": "1-2 речення. Що може бути неточним або помилковим в аналізі сьогодні?",

  "next_investigations": [
    "конкретне завдання 1 — місто + дія",
    "конкретне завдання 2",
    "конкретне завдання 3"
  ]
}}

Ліміти: retail_signals макс 5, whitespace_cities макс 4, regional_gaps макс 5, next_investigations макс 4.
Відповідай ТІЛЬКИ JSON."""


def _call_openai(context: str) -> dict:
    from openai import OpenAI
    from src.config import OPENAI_API_KEY

    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=1500,
        temperature=0.15,
        messages=[
            {"role": "system", "content": _SYSTEM_DEEP},
            {"role": "user",   "content": _USER_TEMPLATE_DEEP.format(context=context)},
        ],
    )
    raw = resp.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    result = json.loads(raw)
    logger.info(
        f"Deep analysis: whitespace={len(result.get('whitespace_cities',[]))}, "
        f"signals={len(result.get('retail_signals',[]))}, "
        f"regions={len(result.get('regional_gaps',[]))}"
    )
    return result


# ── Rule-based fallback ────────────────────────────────────────────────────────

def _rule_based(data: dict, db: dict) -> dict:
    stores  = data.get("current_stores", []) or []
    news    = data.get("news", []) or []
    jobs    = data.get("jobs", []) or []
    ig      = data.get("instagram_posts", []) or []
    ws      = data.get("whitespace_opps", []) or []
    fo      = data.get("future_openings", []) or []

    aurora_by_region = db.get("aurora_by_region", {})
    n_stores  = db.get("n_stores") or len(stores)
    n_cities  = db.get("n_cities") or len({s.get("city","") for s in stores if s.get("city")})
    all_3_ws  = db.get("all_3_ws", [])
    comp_counts = db.get("comp_counts", {})

    pepco_c = comp_counts.get("Pepco", {}).get("cities", 0)
    kik_c   = comp_counts.get("KiK",  {}).get("cities", 0)
    tedi_c  = comp_counts.get("TEDi", {}).get("cities", 0)
    gap     = pepco_c - n_cities
    known   = sum(v for k, v in aurora_by_region.items() if k != "Unknown")
    eastern = aurora_by_region.get("Nord-Est", 0) + aurora_by_region.get("Sud-Est", 0)
    e_pct   = round(eastern / known * 100) if known else 0

    western_ws = [r["city"] for r in all_3_ws if r["city"].lower() in WESTERN_CITY_SET]

    key_insight = (
        f"Aurora відсутня у {len(western_ws)} містах Заходу (серед яких {', '.join(western_ws[:3])}), "
        f"де вже присутні Pepco, KiK і TEDi — найбільший структурний розрив відносно конкурентів."
        if western_ws else
        f"Pepco охоплює {pepco_c} міст, Aurora — лише {n_cities}. "
        f"Розрив у {gap} локацій, сконцентрований у регіонах без присутності Aurora."
    )

    comp_analysis = (
        f"Pepco ({pepco_c} міст) і KiK ({kik_c}) демонструють стратегію насичення малих та середніх міст: "
        f"обидва бренди присутні в містах з населенням 20–80 тис., де Aurora ще відсутня. "
        f"TEDi ({tedi_c} міст) активно зростає і планує досягти 100 магазинів у 2026 р. "
        f"Перетин усіх трьох брендів у {len(all_3_ws)} містах формує зони підтвердженого попиту на дискаунтери."
    )

    aurora_network = (
        f"Aurora концентрує {e_pct}% відомих магазинів у Пн-Схід + Пд-Схід — "
        f"кластерна, а не національна стратегія. "
        f"Захід (Vest, Nord-Vest, Centru) має нульову або мінімальну присутність: "
        f"{aurora_by_region.get('Vest',0)} + {aurora_by_region.get('Nord-Vest',0)} + "
        f"{aurora_by_region.get('Centru',0)} магазинів у трьох регіонах."
    )

    aurora_news = [a for a in news if a.get("signal_category") in
                   ("aurora_direct","aurora_confirmed","aurora_mentioned")]
    comp_news   = [a for a in news if a.get("signal_category") == "competitor_expansion"]
    infra_news  = [a for a in news if a.get("signal_category") in
                   ("retail_park","mall_leasing","shopping_center")]

    retail_signals = []
    for a in aurora_news[:2]:
        t     = a.get("translated_title") or a.get("title","")
        cities = (a.get("cities_mentioned") or [])
        city   = cities[0] if cities else None
        retail_signals.append({
            "summary_ua": t[:80],
            "why_matters": f"Прямий сигнал Aurora{' у ' + city if city else ''} — необхідно перевірити на офіційній мапі.",
            "city": city,
            "classification": "aurora_specific",
            "action": "Перевірити наявність на aurora-retail.com/store_map",
        })
    for a in comp_news[:2]:
        t      = a.get("title","")
        cities = (a.get("cities_mentioned") or [])
        city   = cities[0] if cities else None
        co     = a.get("company","") or "Конкурент"
        retail_signals.append({
            "summary_ua": f"{co} розширює мережу{' у ' + city if city else ' у Румунії'}.",
            "why_matters": "Підтверджує тиск конкурентів у містах без Aurora.",
            "city": city,
            "classification": "competitor_expansion",
            "action": f"Перевірити присутність Aurora у {city or 'цьому місті'}.",
        })
    for a in infra_news[:1]:
        t      = a.get("title","")
        cities = (a.get("cities_mentioned") or [])
        city   = cities[0] if cities else None
        retail_signals.append({
            "summary_ua": f"Новий ритейл-парк або ТЦ{' у ' + city if city else ''}.",
            "why_matters": f"Ритейл-парк — потенційна точка входу для Aurora{' у ' + city if city else ''}.",
            "city": city,
            "classification": "retail_park",
            "action": f"Перевірити список орендарів{' у ' + city if city else ''}.",
        })

    aurora_preds = sorted(
        [c for c in fo if c.get("change_type") == "POSSIBLE_FUTURE_OPENING" and c.get("aurora_specific")],
        key=lambda x: x.get("confidence",{}).get("score",0), reverse=True,
    )

    aurora_jobs = [j for j in jobs if "aurora" in
                   (j.get("company","") + j.get("title","")).lower()]
    hiring_analysis = (
        f"Виявлено {len(aurora_jobs)} Aurora-вакансій — вимагають перевірки конкретних міст. "
        "Масові вакансії з переліком кількох міст не підтверджують відкриття в жодному з них окремо."
    )

    ig_opening = [p for p in ig if not p.get("brand") and
                  p.get("signal_type") == "confirmed_opening_signal"]
    no_city_ig = [p for p in ig_opening if not p.get("cities_mentioned")]
    instagram_analysis = (
        f"Виявлено {len(ig_opening)} Aurora-постів про відкриття. "
        + (f"{len(no_city_ig)} без визначеного міста — потребують ручної перевірки." if no_city_ig else
           "Всі мають визначені міста.")
    )

    ws_cities = []
    for opp in (ws or [])[:4]:
        city   = opp.get("city","")
        brands = list((opp.get("competitor_brands") or {}).keys())
        is_w   = city.lower() in WESTERN_CITY_SET
        region = opp.get("region","")
        ws_cities.append({
            "city": city,
            "why": (
                f"{'Захід — ' if is_w else ''}{'+'.join(brands)} вже присутні, "
                f"Aurora відсутня у {region}."
            ),
            "evidence": f"Конкуренти: {', '.join(brands)}",
            "confidence": "market_only",
            "missing": "Aurora-специфічний сигнал (стаття/вакансія/Instagram)",
            "next_check": f"Перевірити вакансії Aurora у {city} та орендарів найближчого retail park.",
        })

    investigations = []
    for pred in aurora_preds[:2]:
        city  = pred.get("city","")
        level = pred.get("confidence",{}).get("level","")
        investigations.append(
            f"Верифікувати Aurora-сигнал у {city} [{level}] — знайти першоджерело (стаття vs вакансія)."
        )
    if western_ws:
        investigations.append(
            f"Перевірити наявність Aurora-вакансій або орендних оголошень у {western_ws[0]} — "
            f"всі три конкуренти вже є."
        )
    if len(investigations) < 3:
        investigations.append(
            "Порівняти повну карту Aurora з картою Pepco — виявити незайняті міста >30 тис. мешканців."
        )

    regional_gaps = []
    for reg, cat, entry, note in [
        ("Vest",       "zero_aurora",            "flagship_city",    f"Нульова присутність Aurora; Pepco+KiK+TEDi вже у Timișoara та Arad."),
        ("Nord-Vest",  "competitor_dense_gap",   "retail_park",      f"Aurora відсутня у більшості міст; кілька ключових з усіма конкурентами."),
        ("Centru",     "retail_park_opportunity", "retail_park",      "Sibiu і Brașov мають активну retail park інфраструктуру, але Aurora відсутня."),
        ("Nord-Est",   "aurora_stronghold",      "cluster_expansion", "База Aurora — кілька магазинів у більшості великих міст."),
        ("București-Ilfov", "growing",           "flagship_city",     "Кілька Aurora-сигналів і нові retail park проекти."),
    ]:
        regional_gaps.append({
            "region": reg, "category": cat,
            "analysis": note, "entry_type": entry,
        })

    return {
        "key_insight":         key_insight,
        "competitor_analysis": comp_analysis,
        "aurora_network":      aurora_network,
        "regional_gaps":       regional_gaps,
        "retail_signals":      retail_signals,
        "hiring_analysis":     hiring_analysis,
        "instagram_analysis":  instagram_analysis,
        "whitespace_cities":   ws_cities,
        "risks": (
            "Частина Aurora-сигналів може бути застарілими статтями (2023–2024), "
            "а не поточними планами. Вакансії без конкретного міста не підтверджують відкриттів."
        ),
        "next_investigations": investigations[:4],
    }


# ── Public API ─────────────────────────────────────────────────────────────────

def generate_deep_market_analysis(data: dict) -> dict:
    """
    Synthesize all pipeline sources into structured Ukrainian market intelligence.
    10 sections: key_insight, competitor_analysis, aurora_network, regional_gaps,
    retail_signals, hiring_analysis, instagram_analysis, whitespace_cities,
    risks, next_investigations.
    Cached per pipeline run.
    """
    from src.config import OPENAI_API_KEY

    news   = data.get("news",   []) or []
    stores = data.get("current_stores", []) or []
    cache_key = (
        frozenset(a.get("url", a.get("title","")) for a in news[:40]),
        len(stores),
    )
    if cache_key in _CACHE:
        logger.debug("Deep analysis: cache hit")
        return _CACHE[cache_key]

    db = _query_db()

    if not OPENAI_API_KEY:
        result = _rule_based(data, db)
    else:
        try:
            context = _build_context(data, db)
            result  = _call_openai(context)
        except Exception as e:
            logger.warning(f"Deep analysis OpenAI failed ({e}) — rule-based fallback")
            result = _rule_based(data, db)

    _CACHE[cache_key] = result
    return result
