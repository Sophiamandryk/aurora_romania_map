"""
Daily markdown report generator — Ukrainian localization.
"""
import json
from datetime import date
from pathlib import Path
from collections import defaultdict

from src.config import REPORTS_DIR, setup_logging

logger = setup_logging("reports")

EMOJI = {
    "NEW_STORE": "🟢",
    "REMOVED_STORE": "🔴",
    "RELOCATED_STORE": "🔄",
    "STORE_UPDATED": "🔵",
    "POSSIBLE_FUTURE_OPENING": "🟡",
    "MARKET_ACTIVITY_SIGNAL": "📊",
    "POSSIBLE_REBRANDING": "🟠",
    "NEW_STORE_FORMAT": "🟣",
    "HIGH": "🔥",
    "MEDIUM": "📊",
    "LOW": "💡",
}

_CONF_UA = {"HIGH": "ВИСОКА", "MEDIUM": "СЕРЕДНЯ", "LOW": "НИЗЬКА"}

_CHANGE_UA = {
    "NEW_STORE": "Новий магазин",
    "REMOVED_STORE": "Закриття магазину",
    "RELOCATED_STORE": "Переїзд магазину",
    "STORE_UPDATED": "Оновлення магазину",
    "POSSIBLE_REBRANDING": "Можливий ребрендинг",
    "NEW_STORE_FORMAT": "Новий формат магазину",
}


def _confidence_badge(level: str) -> str:
    ua = _CONF_UA.get(level, level)
    return f"{EMOJI.get(level, '')} **{ua}**"


def _format_competitors_md(comp_analysis: dict) -> str:
    nearest = comp_analysis.get("nearest_competitors", {})
    if not nearest:
        return ""
    lines = ["**Найближчі конкуренти:**"]
    for brand, stores in nearest.items():
        if stores:
            dist = stores[0].get("distance_km", "?")
            addr = stores[0].get("address", "")
            lines.append(f"  - {brand}: {dist} км{f' ({addr})' if addr else ''}")
    density = comp_analysis.get("density", {})
    for radius, counts in density.items():
        if counts:
            count_str = ", ".join(f"{b}: {n}" for b, n in counts.items())
            lines.append(f"  - Щільність {radius}: {count_str}")
    return "\n".join(lines)


def _section_map_changes(changes: list[dict]) -> str:
    from src.data.ro_counties import display_city as _display_city
    map_types = {"NEW_STORE", "REMOVED_STORE", "RELOCATED_STORE", "STORE_UPDATED",
                 "POSSIBLE_REBRANDING", "NEW_STORE_FORMAT"}
    relevant = [c for c in changes if c.get("change_type") in map_types]
    if not relevant:
        return "Змін на мапі сьогодні не виявлено.\n"

    lines = []
    for c in relevant:
        ct = c.get("change_type", "")
        store = c.get("store") or {}
        confidence = c.get("confidence", {})
        comp = c.get("competitor_analysis", {})

        emoji = EMOJI.get(ct, "•")
        city = _display_city(store.get("city", "Невідомо"))
        address = store.get("address", "")
        conf_level = confidence.get("level", "")
        ct_ua = _CHANGE_UA.get(ct, ct.replace("_", " ").title())

        lines.append(f"### {emoji} {ct_ua} — {city}")
        if address:
            lines.append(f"- **Адреса:** {address}")
        if store.get("store_id"):
            lines.append(f"- **ID магазину:** `{store['store_id']}`")
        if conf_level:
            lines.append(f"- **Впевненість:** {_confidence_badge(conf_level)} ({confidence.get('score', '')})")
        if store.get("source_url"):
            lines.append(f"- **Джерело:** {store['source_url']}")

        details = c.get("details", {})
        if details and ct == "STORE_UPDATED":
            for field, diff in details.items():
                if isinstance(diff, dict):
                    lines.append(f"- **{field.title()} змінено:** `{diff.get('from', '')}` → `{diff.get('to', '')}`")

        if ct == "RELOCATED_STORE":
            prev = c.get("previous_store") or {}
            if prev.get("address"):
                lines.append(f"- **Попередня адреса:** {prev['address']}")
            dist = details.get("distance_m")
            if dist:
                lines.append(f"- **Відстань переїзду:** {dist}м")

        comp_str = _format_competitors_md(comp)
        if comp_str:
            lines.append("")
            lines.append(comp_str)

        lines.append("")

    return "\n".join(lines)


def _fmt_evidence_row(sig: dict) -> str:
    title = sig.get("title", "")
    company = sig.get("company", "") or ""
    source = sig.get("source", "")
    url = sig.get("url", "")
    sig_class = sig.get("signal_class", "")
    cities = ", ".join(sig.get("cities_mentioned", []))
    link = f" — [посилання]({url})" if url else ""
    city_tag = f" | Міста: {cities}" if cities else ""
    co_tag = f" | Компанія: _{company}_" if company else ""
    return f"  - **{title}** | джерело: `{source}` | клас: `{sig_class}`{co_tag}{city_tag}{link}"


def _section_aurora_predictions(predictions: list[dict]) -> str:
    aurora_preds = [
        p for p in predictions
        if p.get("change_type") == "POSSIBLE_FUTURE_OPENING" and p.get("aurora_specific")
    ]
    if not aurora_preds:
        return "Сигналів майбутніх відкриттів Aurora сьогодні не виявлено.\n"

    sorted_preds = sorted(aurora_preds, key=lambda x: x.get("confidence", {}).get("score", 0), reverse=True)
    lines = []
    for p in sorted_preds:
        city = p.get("city", "Невідомо")
        confidence = p.get("confidence", {})
        evidence = p.get("evidence", {})
        conf_level = confidence.get("level", "LOW")
        conf_score = confidence.get("score", 0)

        lines.append(f"### 🟡 {city} — {_confidence_badge(conf_level)} (оцінка: {conf_score})")
        lines.append(f"- **Aurora-специфічний:** ✅ Так")

        aurora_signals = evidence.get("aurora_signals", [])
        if aurora_signals:
            lines.append(f"- **Докази Aurora ({len(aurora_signals)} сигналів):**")
            for sig in aurora_signals[:6]:
                lines.append(_fmt_evidence_row(sig))

        comp_signals = evidence.get("competitor_signals", [])
        if comp_signals:
            lines.append(f"- **Контекст — активність конкурентів ({len(comp_signals)}):**")
            for sig in comp_signals[:3]:
                lines.append(_fmt_evidence_row(sig))

        generic_signals = evidence.get("generic_signals", [])
        if generic_signals:
            lines.append(f"- **Контекст — ритейл-активність ({len(generic_signals)}):**")
            for sig in generic_signals[:3]:
                lines.append(_fmt_evidence_row(sig))

        lines.append("")

    return "\n".join(lines)


def _section_market_activity(signals: list[dict]) -> str:
    market_sigs = [
        p for p in signals
        if p.get("change_type") == "MARKET_ACTIVITY_SIGNAL"
        or (p.get("change_type") == "POSSIBLE_FUTURE_OPENING" and not p.get("aurora_specific"))
    ]
    if not market_sigs:
        return "Сигналів ринкової активності сьогодні не виявлено.\n"

    lines = []
    for s in market_sigs:
        city = s.get("city", "Невідомо")
        evidence = s.get("evidence", {})

        comp_signals = evidence.get("competitor_signals", [])
        generic_signals = evidence.get("generic_signals", [])
        companies = evidence.get("companies", [])
        all_evidence = comp_signals + generic_signals

        lines.append(f"### 📊 {city}")
        lines.append(f"- **Aurora-специфічний:** ❌ Ні")
        if companies:
            lines.append(f"- **Активні компанії:** {', '.join(companies[:8])}")
        if all_evidence:
            lines.append(f"- **Докази ({len(all_evidence)} сигналів):**")
            for sig in all_evidence[:6]:
                lines.append(_fmt_evidence_row(sig))

        details = s.get("details", {})
        note = details.get("note", "")
        if note:
            lines.append(f"- _{note}_")

        lines.append("")

    return "\n".join(lines)


def _section_competitor_opportunities(opportunities: list[dict]) -> str:
    if not opportunities:
        return "Нових можливостей розширення конкурентів не виявлено.\n"

    lines = []
    for opp in opportunities[:10]:
        city = opp.get("city", "Невідомо")
        brands = opp.get("competitor_brands", {})
        score = opp.get("opportunity_score", 0)
        brand_str = ", ".join(f"{b} ({n})" for b, n in brands.items())
        lines.append(f"- **{city}** — Конкуренти: {brand_str} | Оцінка можливості: {score:.2f}")

    return "\n".join(lines) + "\n"


def _section_job_signals(jobs: list[dict]) -> str:
    if not jobs:
        return "Відповідних вакансій сьогодні не знайдено.\n"

    high_signal = [j for j in jobs if j.get("signal_score", 0) >= 2]
    lines = []
    for j in sorted(high_signal, key=lambda x: x.get("signal_score", 0), reverse=True)[:15]:
        cities = ", ".join(j.get("cities_mentioned", []))
        company = j.get("company", "") or ""
        co_str = f" @ {company}" if company else ""
        lines.append(
            f"- **{j['title']}**{co_str} — "
            f"{j.get('location', '')} {f'[{cities}]' if cities else ''} "
            f"| Сигнал: {j.get('signal_score', 0)} | [{j.get('source', '')}]({j.get('url', '')})"
        )
    if not lines:
        lines.append("Вакансій з високим сигналом сьогодні немає.")
    return "\n".join(lines) + "\n"


_INTEL_EMOJI = {
    "aurora_confirmed":     "🟢",
    "aurora_mentioned":     "🟡",
    "aurora_direct":        "🟡",
    "competitor_expansion": "🔵",
    "retail_park":          "🏗️",
    "mall_leasing":         "🏬",
    "shopping_center":      "🛒",
    "local_news":           "📰",
    "generic_market":       "📊",
    "generic_retail":       "📦",
    "influencer_signal":    "📱",
}

_INTEL_LABEL_UA = {
    "aurora_confirmed":     "Aurora (підтверджено)",
    "aurora_mentioned":     "Aurora (згадується)",
    "aurora_direct":        "Aurora (прямий сигнал)",
    "competitor_expansion": "Розширення конкурентів",
    "retail_park":          "Ритейл-парки",
    "mall_leasing":         "ТЦ / оренда",
    "shopping_center":      "Торгові центри",
    "local_news":           "Локальні новини",
    "generic_market":       "Загальний ринок",
    "generic_retail":       "Роздрібна торгівля",
    "influencer_signal":    "Соціальні мережі",
}


_REGION_CATEGORY_UA = {
    "aurora_stronghold":         "База Aurora",
    "competitor_dense_gap":      "Щільні конкуренти — відсутня Aurora",
    "retail_park_opportunity":   "Можливість через ритейл-парки",
    "zero_aurora":               "Нульова присутність Aurora",
    "growing":                   "Зростаючий ринок",
}

_ENTRY_UA = {
    "flagship_city":      "флагманське місто",
    "retail_park":        "вхід через ритейл-парк",
    "small_town_cluster": "кластер малих міст",
    "cluster_expansion":  "розширення кластеру",
    "not_recommended":    "не пріоритет",
}

_DEEP_SIG_EMOJI = {
    "aurora_specific":      "📰",
    "competitor_expansion": "🔵",
    "retail_park":          "🏗️",
    "market_trend":         "📊",
    "weak_signal":          "💡",
}


def _section_executive_analysis(deep: dict) -> str:
    """Executive analysis section for top of markdown report."""
    if not deep:
        return ""

    lines: list[str] = []

    key = (deep.get("key_insight") or "").strip()
    if key:
        lines.append(f"> **{key}**")
        lines.append("")

    aurora_net = (deep.get("aurora_network") or "").strip()
    if aurora_net:
        lines.append(f"**Мережа Aurora:** {aurora_net}")
        lines.append("")

    comp = (deep.get("competitor_analysis") or "").strip()
    if comp:
        lines.append(f"**Рух конкурентів:** {comp}")
        lines.append("")

    # Regional gap table
    regional_gaps = deep.get("regional_gaps") or []
    if regional_gaps:
        lines.append("**Регіональний аналіз:**")
        lines.append("")
        lines.append("| Регіон | Категорія | Аналіз | Рекомендований вхід |")
        lines.append("|--------|-----------|--------|---------------------|")
        for g in regional_gaps:
            cat   = _REGION_CATEGORY_UA.get(g.get("category",""), g.get("category",""))
            entry = _ENTRY_UA.get(g.get("entry_type",""), g.get("entry_type",""))
            lines.append(
                f"| {g.get('region','')} | {cat} | {g.get('analysis','')} | {entry} |"
            )
        lines.append("")

    # Retail signals
    retail_sigs = deep.get("retail_signals") or []
    if retail_sigs:
        lines.append("**Ключові ринкові сигнали:**")
        lines.append("")
        for sig in retail_sigs:
            emoji  = _DEEP_SIG_EMOJI.get(sig.get("classification",""), "•")
            summary = (sig.get("summary_ua") or "").strip()
            why     = (sig.get("why_matters") or "").strip()
            city    = sig.get("city")
            action  = (sig.get("action") or "").strip()
            if not summary:
                continue
            lines.append(f"**{emoji} {summary}**")
            if why:
                lines.append(f"_Чому важливо:_ {why}")
            if action:
                lines.append(f"_Перевірити:_ {action}")
            lines.append("")

    # Hiring + Instagram
    hiring = (deep.get("hiring_analysis") or "").strip()
    ig     = (deep.get("instagram_analysis") or "").strip()
    if hiring:
        lines.append(f"**Вакансії:** {hiring}")
    if ig:
        lines.append(f"**Instagram:** {ig}")
    if hiring or ig:
        lines.append("")

    # Whitespace cities table
    ws = deep.get("whitespace_cities") or []
    if ws:
        lines.append("**Пріоритетні міста для перевірки:**")
        lines.append("")
        lines.append("| Місто | Впевненість | Докази | Відсутнє | Наступний крок |")
        lines.append("|-------|-------------|--------|----------|----------------|")
        def _safe_cell(v) -> str:
            if isinstance(v, list):
                v = ", ".join(str(x) for x in v)
            return (str(v) if v else "—").replace("|", "\\|")

        _conf_lbl = {"aurora_signal": "сигнал Aurora", "market_only": "ринковий", "weak": "слабкий"}
        for c in ws:
            city    = c.get("city","")
            conf    = _conf_lbl.get(c.get("confidence",""), c.get("confidence",""))
            ev      = _safe_cell(c.get("evidence"))
            missing = _safe_cell(c.get("missing"))
            nxt     = _safe_cell(c.get("next_check"))
            lines.append(f"| {city} | {conf} | {ev} | {missing} | {nxt} |")
        lines.append("")

    # Risks
    risks = (deep.get("risks") or "").strip()
    if risks:
        lines.append(f"**Обмеження аналізу:** _{risks}_")
        lines.append("")

    # Next investigations
    tasks = deep.get("next_investigations") or []
    if tasks:
        lines.append("**Що перевірити:**")
        for t in tasks:
            lines.append(f"- {t}")
        lines.append("")

    return "\n".join(lines)


def _section_retail_intelligence(articles: list[dict], synthesis: dict = None) -> str:
    if not articles:
        return "Сигналів ритейл-аналітики сьогодні не знайдено.\n"

    lines = []

    # Synthesis insights at the top
    if synthesis:
        market_narrative = synthesis.get("market_narrative", "")
        aurora_insights  = synthesis.get("aurora_insights", "")
        comp_insights    = synthesis.get("competitor_insights", "")
        infra_insights   = synthesis.get("infrastructure_insights")

        if market_narrative:
            lines.append(f"> {market_narrative}")
            lines.append("")
        if aurora_insights:
            lines.append(f"**Aurora:** {aurora_insights}")
            lines.append("")
        if comp_insights:
            lines.append(f"**Конкуренти:** {comp_insights}")
            lines.append("")
        if infra_insights:
            lines.append(f"**Інфраструктура:** {infra_insights}")
            lines.append("")

    lines.append(
        f"_Усього {len(articles)} сигналів — "
        "джерела: Retail.ro, Economica.net, Profit.ro, ZF.ro, Business Review_"
    )
    lines.append("")

    # Group by category
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for a in articles:
        by_cat[a.get("signal_category", "generic_retail")].append(a)

    cat_order = [
        "aurora_confirmed", "aurora_mentioned", "aurora_direct",
        "competitor_expansion", "retail_park", "mall_leasing",
        "shopping_center", "local_news", "generic_market", "generic_retail",
        "influencer_signal",
    ]

    for cat in cat_order:
        items = sorted(by_cat.get(cat, []), key=lambda x: -x.get("confidence", 0))
        if not items:
            continue
        emoji = _INTEL_EMOJI.get(cat, "•")
        label = _INTEL_LABEL_UA.get(cat, cat)
        lines.append(f"#### {emoji} {label} ({len(items)})")
        for a in items[:6]:
            translated = a.get("translated_title") or a.get("title", "")
            orig_title = a.get("title", "")
            url = a.get("url", "")
            source = a.get("source", "")
            pub = a.get("published_date", "")
            conf = a.get("confidence", 0)
            cities = ", ".join((a.get("cities_mentioned") or [])[:3])
            why = a.get("why_this_matters", "")

            city_tag = f" | 📍 {cities}" if cities else ""
            title_link = f"[{translated}]({url})" if url else translated

            lines.append(
                f"- {title_link} — _{source}_ ({pub}){city_tag} | conf: `{conf}`"
            )
            if why:
                lines.append(f"  > _{why}_")
        lines.append("")

    return "\n".join(lines)


def _section_competitor_network(
    competitor_stores: dict[str, list[dict]],
    aurora_stores: list[dict],
) -> str:
    if not competitor_stores or not any(v for k, v in competitor_stores.items() if k != "Action"):
        return "Дані про мережі конкурентів ще не зібрано.\n"

    from collections import Counter
    from src.data.ro_counties import normalize_city

    aurora_cities = {normalize_city(s.get("city", "")) for s in aurora_stores if s.get("city")}

    lines = ["| Бренд | Магазини | Міста | Топ-3 міста |",
             "|-------|----------|-------|-------------|"]
    for brand, stores in competitor_stores.items():
        if brand == "Action":
            lines.append("| Action | — | — | Cloudflare-блокування |")
            continue
        if not stores:
            lines.append(f"| {brand} | 0 | — | — |")
            continue
        city_counts = Counter(normalize_city(s.get("city", "")) for s in stores if s.get("city"))
        top = ", ".join(c.title() for c, _ in city_counts.most_common(3))
        lines.append(f"| {brand} | {len(stores)} | {len(city_counts)} | {top} |")

    # Co-presence
    comp_cities: dict[str, set] = {}
    for brand, stores in competitor_stores.items():
        if brand == "Action":
            continue
        for s in stores:
            c = normalize_city(s.get("city", ""))
            if c:
                comp_cities.setdefault(c, set()).add(brand)

    shared = {c: brands for c, brands in comp_cities.items() if c in aurora_cities}
    if shared:
        lines += ["", f"**Aurora + конкуренти в одному місті: {len(shared)} міст**", ""]
        lines += ["| Місто | Присутні бренди |", "|-------|-----------------|"]
        for city in sorted(shared)[:15]:
            lines.append(f"| {city.title()} | {', '.join(sorted(shared[city]))} |")

    return "\n".join(lines) + "\n"


def _section_whitespace(opportunities: list[dict], limit: int = 15) -> str:
    if not opportunities:
        return "Ринкових ніш не виявлено (потрібні дані конкурентів).\n"

    lines = [
        f"_Топ {min(len(opportunities), limit)} міст за оцінкою можливості "
        f"(щільність конкурентів × різноманітність брендів × розмір міста)_",
        "",
        "| Місто | Район | Регіон | Конкуренти | Магазини | Оцінка | Пріоритет |",
        "|-------|-------|--------|------------|----------|--------|-----------|",
    ]
    for opp in opportunities[:limit]:
        brands = ", ".join(
            f"{b}({n})" for b, n in sorted(opp["competitor_brands"].items())
        )
        lines.append(
            f"| {opp['city']} | {opp.get('county', '—')} | {opp.get('region', '—')} "
            f"| {brands} | {opp['total_competitor_stores']} "
            f"| {opp['opportunity_score']:.0f} | {opp['gap_label'].split(' —')[0]} |"
        )
    return "\n".join(lines) + "\n"


def _section_trend_summary(trends: dict) -> str:
    monthly = trends.get("monthly", [])
    velocity = trends.get("velocity", {})
    city_growth = trends.get("city_growth", [])
    region_activity = trends.get("region_activity", [])

    lines = []

    if velocity:
        if velocity.get("insufficient_data"):
            reason = velocity.get("reason", "Потрібно щонайменше 2 щоденні знімки.")
            lines.append(
                f"_Трендовий аналіз ще недоступний. {reason} "
                f"Розрахунок швидкості активується після 2+ щоденних запусків._"
            )
        else:
            rate = velocity.get("stores_per_month", 0)
            total = velocity.get("total_new", 0)
            months = velocity.get("months_observed", 0)
            lines.append(f"**Швидкість розширення:** {rate} магазинів/міс "
                         f"({total} нових магазинів за {months} місяців)")
        lines.append("")

    if monthly:
        lines.append("**Щомісячні відкриття:**")
        lines.append("")
        lines.append("| Місяць | Відкриття | Закриття |")
        lines.append("|--------|-----------|----------|")
        for m in monthly:
            lines.append(f"| {m['month']} | {m['openings']} | {m['closures']} |")
        lines.append("")

    if region_activity:
        lines.append("**Найактивніші регіони (нещодавно):**")
        for r in region_activity[:5]:
            lines.append(f"- **{r['region']}**: {r['openings']} відкриттів "
                         f"(топ-місто: {r['top_city']})")
        lines.append("")

    if city_growth:
        lines.append("**Міста з найшвидшим зростанням (нещодавно):**")
        for c in city_growth[:8]:
            county = f" ({c['county']})" if c.get("county") else ""
            lines.append(f"- **{c['city']}**{county}: {c['openings']} нових магазинів")
        lines.append("")

    return "\n".join(lines) if lines else "Трендових даних поки немає.\n"


def _section_city_market_scores(scores: list[dict]) -> str:
    if not scores:
        return "Ринкові рейтинги міст сьогодні не розраховано.\n"

    lines = []
    aurora_cities = [s for s in scores if s.get("aurora_specific")]
    market_cities = [s for s in scores if not s.get("aurora_specific")]

    if aurora_cities:
        lines.append("**Міста з сигналами Aurora:**")
        for s in aurora_cities[:10]:
            bd = s.get("breakdown", {})
            tags = []
            if bd.get("aurora_map_change"):
                tags.append(f"мапа +{bd['aurora_map_change']}x")
            if bd.get("aurora_direct"):
                tags.append(f"новини +{bd['aurora_direct']}x")
            if bd.get("aurora_job"):
                tags.append(f"вакансії +{bd['aurora_job']}x")
            tag_str = f" ({', '.join(tags)})" if tags else ""
            lines.append(f"- **{s['city']}** — оцінка: {s['score']:.0f}{tag_str}")
        lines.append("")

    if market_cities:
        lines.append("**Міста лише з ринковою активністю:**")
        for s in market_cities[:10]:
            bd = s.get("breakdown", {})
            tags = []
            for k in ("competitor_expansion", "retail_park", "mall_leasing", "competitor_store"):
                if bd.get(k):
                    tags.append(f"{k.replace('_', ' ')}:{bd[k]}")
            tag_str = f" ({', '.join(tags[:3])})" if tags else ""
            lines.append(f"- {s['city']} — оцінка: {s['score']:.0f}{tag_str}")
        lines.append("")

    return "\n".join(lines)


def _section_instagram_summary(instagram_posts: list[dict]) -> str:
    if not instagram_posts:
        return "Instagram-пости сьогодні не зібрано.\n"

    from collections import Counter
    from src.scrapers.aurora_instagram import SIGNAL_TYPES

    aurora_posts = [p for p in instagram_posts if not p.get("brand")]
    competitor_posts = [p for p in instagram_posts if p.get("brand")]

    lines = []

    for label, posts in [("Aurora (@aurora.multimarket)", aurora_posts),
                          ("Конкуренти", competitor_posts)]:
        if not posts:
            continue
        counts = Counter(p.get("signal_type", "generic_promo") for p in posts)
        lines.append(f"**{label}** — {len(posts)} постів")
        for stype in SIGNAL_TYPES:
            n = counts.get(stype, 0)
            if n:
                lines.append(f"  - {stype.replace('_', ' ')}: {n}")
        lines.append("")

    actionable_types = {"confirmed_opening_signal", "possible_store_location_signal",
                        "mall_or_retail_park_signal"}
    actionable = [p for p in instagram_posts if p.get("signal_type") in actionable_types]
    if actionable:
        lines.append("**Actionable-пости:**")
        for p in sorted(actionable, key=lambda x: -x.get("signal_score", 0))[:8]:
            brand = p.get("brand") or "Aurora"
            stype = p.get("signal_type", "").replace("_", " ")
            cities = ", ".join(p.get("cities_mentioned", [])[:2])
            malls = ", ".join(p.get("detected_malls", [])[:2])
            score = p.get("signal_score", 0)
            url = p.get("url", "")
            detail = " | ".join(filter(None, [
                f"міста: {cities}" if cities else "",
                f"ТЦ: {malls}" if malls else "",
                f"оцінка: {score}",
            ]))
            lines.append(f"- [{brand}] **{stype}** — {detail} — [пост]({url})")
        lines.append("")

    return "\n".join(lines)


def _section_data_quality_warnings(
    jobs_raw: int,
    jobs_deduped: int,
    news_raw: int,
    news_filtered: int,
    velocity_insufficient: bool,
    is_baseline: bool,
    stores_no_city: int = 0,
    stores_no_coords: int = 0,
    competitor_partial: list[str] = None,
) -> str:
    lines = []
    if is_baseline:
        lines.append("- **Базовий запуск:** усі магазини проіндексовано; сповіщень не надіслано.")
        lines.append("- **Трендовий аналіз:** вимкнено на базовому запуску (немає попереднього знімка).")
    dupes = jobs_raw - jobs_deduped
    if dupes > 0:
        lines.append(f"- **Дедублікація вакансій:** видалено {dupes} дублікатів (збережено {jobs_deduped} унікальних).")
    filtered = news_raw - news_filtered
    if filtered > 0:
        lines.append(f"- **Фільтр ритейл-аналітики:** видалено {filtered} шумових статей (збережено {news_filtered}).")
    if velocity_insufficient and not is_baseline:
        lines.append("- **Трендова швидкість:** недостатньо даних — активується після 2+ щоденних запусків.")
    if stores_no_city:
        lines.append(f"- **Розбір міст:** {stores_no_city} магазинів без назви міста.")
    if stores_no_coords:
        lines.append(f"- **Координати:** {stores_no_coords} магазинів без lat/lon (аналіз відстаней пропущено).")
    if competitor_partial:
        lines.append(f"- **Часткові/неперевірені дані конкурентів:** {', '.join(competitor_partial)}.")
    if not lines:
        return "Проблем із якістю даних не виявлено.\n"
    return "\n".join(lines) + "\n"


def _generate_baseline_report(
    current_stores: list[dict],
    jobs: list[dict],
    news_articles: list[dict],
    competitor_stores: dict,
    today: str,
) -> str:
    from collections import Counter
    from src.data.ro_counties import display_city

    total = len(current_stores)
    city_counts = Counter(
        display_city(s.get("city", "Невідомо")) for s in current_stores
    )

    comp_summary = []
    for brand, stores in (competitor_stores or {}).items():
        if brand == "Action":
            comp_summary.append("Action: заблоковано")
        else:
            comp_summary.append(f"{brand}: {len(stores)} магазинів")

    lines = [
        "# Aurora Romania — Базовий Індексний Звіт",
        f"**Дата:** {today}",
        "",
        "---",
        "",
        "> **Це базовий запуск.** Усі магазини нижче є початковим проіндексованим знімком. "
        "Жоден магазин не класифіковано як новий, закритий або змінений. "
        "Реальне виявлення змін починається з наступного щоденного запуску.",
        "",
        "---",
        "",
        "## Базове Зведення",
        "",
        "| Показник | Значення |",
        "|----------|----------|",
        f"| Проіндексовано базових магазинів | {total} |",
        f"| Нових магазинів виявлено | 0 |",
        f"| Можливих закриттів | 0 |",
        f"| Змін на мапі | 0 |",
        f"| Надіслано сповіщень | 0 |",
        f"| Проаналізовано вакансій | {len(jobs)} |",
        f"| Сигналів ритейл-аналітики | {len(news_articles)} |",
        "",
        "---",
        "",
        "## Мережа Магазинів по Містах (Топ-15)",
        "",
    ]

    for city, count in city_counts.most_common(15):
        lines.append(f"- **{city}**: {count} магазин{'и' if count != 1 else ''}")
    lines.append("")

    if comp_summary:
        lines += [
            "---",
            "",
            "## Охоплення Конкурентів (базове)",
            "",
        ]
        for item in comp_summary:
            lines.append(f"- {item}")
        lines.append("")

    lines += [
        "---",
        "",
        "## Якість Даних",
        "",
        "- Базовий запуск: усі магазини відмічено як переглянуті; сповіщень не надіслано.",
        "- Трендовий аналіз: активується після 2+ щоденних знімків.",
        "",
        "---",
        "",
        f"_Базовий звіт згенеровано: {today} | Aurora Romania Expansion Monitor_",
    ]
    return "\n".join(lines)


def generate_daily_report(
    changes: list[dict],
    future_openings: list[dict],
    jobs: list[dict],
    news_articles: list[dict],
    current_stores: list[dict],
    competitor_opportunities: list[dict] = None,
    city_market_scores: list[dict] = None,
    competitor_stores: dict = None,
    whitespace_opps: list[dict] = None,
    trend_data: dict = None,
    report_date: str = None,
    is_baseline: bool = False,
    instagram_posts: list[dict] = None,
) -> tuple[str, Path]:
    from collections import Counter
    from src.data.ro_counties import display_city

    today = report_date or date.today().isoformat()

    if is_baseline:
        report_md = _generate_baseline_report(
            current_stores, jobs, news_articles, competitor_stores or {}, today
        )
        path = REPORTS_DIR / f"daily_report_{today}.md"
        path.write_text(report_md, encoding="utf-8")
        logger.info(f"Baseline report saved: {path}")
        return report_md, path

    # Call synthesis early so translated_title / why_this_matters are enriched on articles
    synthesis: dict = {}
    if news_articles:
        try:
            from src.analysis.retail_intelligence_synthesis import synthesize
            synthesis = synthesize(news_articles, current_stores)
        except Exception as e:
            logger.warning(f"Synthesis unavailable for report: {e}")

    # Deep market analysis for the executive section (cached — free if already called)
    deep_analysis: dict = {}
    try:
        from src.analysis.deep_market_analysis import generate_deep_market_analysis
        data_for_deep = {
            "current_stores":    current_stores,
            "competitor_stores": competitor_stores or {},
            "news":              news_articles,
            "jobs":              jobs,
            "instagram_posts":   instagram_posts or [],
            "whitespace_opps":   whitespace_opps or [],
            "future_openings":   future_openings,
            "changes":           changes,
        }
        deep_analysis = generate_deep_market_analysis(data_for_deep)
    except Exception as e:
        logger.warning(f"Deep analysis unavailable for report: {e}")

    # Stats
    map_changes = [c for c in changes if c.get("change_type") not in
                   ("POSSIBLE_FUTURE_OPENING", "MARKET_ACTIVITY_SIGNAL")]
    stats: dict = {}
    for c in changes + future_openings:
        ct = c.get("change_type", "")
        stats[ct] = stats.get(ct, 0) + 1

    new_count = stats.get("NEW_STORE", 0)
    removed_count = stats.get("REMOVED_STORE", 0)
    aurora_pred_count = sum(
        1 for p in future_openings
        if p.get("change_type") == "POSSIBLE_FUTURE_OPENING" and p.get("aurora_specific")
    )
    market_signal_count = sum(
        1 for p in future_openings
        if p.get("change_type") == "MARKET_ACTIVITY_SIGNAL"
        or (p.get("change_type") == "POSSIBLE_FUTURE_OPENING" and not p.get("aurora_specific"))
    )
    total_stores = len(current_stores)

    velocity = (trend_data or {}).get("velocity", {})
    velocity_insufficient = bool(velocity.get("insufficient_data", False))
    stores_no_city = sum(1 for s in current_stores if not s.get("city"))
    stores_no_coords = sum(1 for s in current_stores
                           if not s.get("latitude") or not s.get("longitude"))
    comp_partial = [b for b, v in (competitor_stores or {}).items() if not v]

    exec_analysis_md = _section_executive_analysis(deep_analysis)

    lines = [
        "# Aurora Romania — Щоденний Звіт",
        f"**Дата:** {today}",
        "",
        "---",
        "",
        "## Аналітичний Огляд",
        "",
        exec_analysis_md,
        "---",
        "",
        "## Зведена Таблиця",
        "",
        "| Показник | Значення |",
        "|----------|----------|",
        f"| Активних магазинів Aurora | {total_stores} |",
        f"| Нових магазинів виявлено | {new_count} |",
        f"| Можливих закриттів | {removed_count} |",
        f"| Прогнозів відкриттів Aurora | {aurora_pred_count} |",
        f"| Сигналів ринкової активності | {market_signal_count} |",
        f"| Змін на мапі | {len(map_changes)} |",
        f"| Проаналізовано вакансій | {len(jobs)} |",
        f"| Сигналів ритейл-аналітики | {len(news_articles)} |",
        "",
        "---",
        "",
        "## Якість Даних",
        "",
        _section_data_quality_warnings(
            jobs_raw=len(jobs), jobs_deduped=len(jobs),
            news_raw=len(news_articles), news_filtered=len(news_articles),
            velocity_insufficient=velocity_insufficient,
            is_baseline=False,
            stores_no_city=stores_no_city,
            stores_no_coords=stores_no_coords,
            competitor_partial=comp_partial,
        ),
        "",
        "---",
        "",
        "## Зміни на Мапі",
        "",
        _section_map_changes(map_changes),
        "",
        "---",
        "",
        "## Прогнози Відкриттів Aurora",
        "",
        _section_aurora_predictions(future_openings),
        "",
        "---",
        "",
        "## Ринкова Активність",
        "_Активність конкурентів та загальний ритейл — без Aurora-специфічних доказів. Лише контекст ринку._",
        "",
        _section_market_activity(future_openings),
        "",
        "---",
        "",
        "## Можливості Розширення Конкурентів",
        "",
        _section_competitor_opportunities(competitor_opportunities or []),
        "",
        "---",
        "",
        "## Ринкові Ніші (White-Space)",
        "_Міста, де вже присутні Pepco / TEDi / KiK, але відсутня Aurora — ранжування за пріоритетом._",
        "",
        _section_whitespace(whitespace_opps or []),
        "",
        "---",
        "",
        "## Мережа Конкурентів",
        "_Підтверджена кількість магазинів конкурентів з живих локаторів._",
        "",
        _section_competitor_network(competitor_stores or {}, current_stores),
        "",
        "---",
        "",
        "## Ритейл-Аналітика",
        "",
        _section_retail_intelligence(news_articles, synthesis),
        "",
        "---",
        "",
        "## Рейтинг Міст",
        "_Зведена оцінка: сигнали Aurora + ритейл-аналітика + присутність конкурентів + вакансії._",
        "",
        _section_city_market_scores(city_market_scores or []),
        "",
        "---",
        "",
        "## Тенденції Розширення",
        "",
        _section_trend_summary(trend_data or {}),
        "",
        "---",
        "",
        "## Ринок Праці",
        "",
        _section_job_signals(jobs),
        "",
        "---",
        "",
        "## Instagram-Сигнали",
        "",
        _section_instagram_summary(instagram_posts or []),
        "",
        "---",
        "",
        "## Поточна Мережа (Зведення)",
        "",
        f"Активних магазинів Aurora: **{total_stores}**",
        "_Повний список магазинів з адресами та близькістю конкурентів — у додатку._",
        "",
    ]

    if current_stores:
        city_counts = Counter(
            display_city(s.get("city", "Невідомо")) for s in current_stores
        )
        lines.append("**Топ-міста за кількістю магазинів:**")
        for city, count in city_counts.most_common(10):
            lines.append(f"- {city}: {count}")
        lines.append("")

    lines += [
        "---",
        "",
        f"_Звіт згенеровано: {today} | Aurora Romania Expansion Monitor_",
    ]

    report_md = "\n".join(lines)
    path = REPORTS_DIR / f"daily_report_{today}.md"
    path.write_text(report_md, encoding="utf-8")
    logger.info(f"Daily report saved: {path}")

    _generate_appendix_report(current_stores, changes, competitor_stores or {}, today)

    return report_md, path


def _generate_appendix_report(
    current_stores: list[dict],
    changes: list[dict],
    competitor_stores: dict,
    today: str,
) -> None:
    from collections import Counter
    from src.data.ro_counties import display_city

    lines = [
        "# Aurora Romania — Повний Список Магазинів (Додаток)",
        f"**Дата:** {today}",
        "",
        f"_Цей додаток містить повний список магазинів ({len(current_stores)} магазинів). "
        "Використовуйте для довідки та перевірки даних, а не для щоденного огляду бізнесу._",
        "",
        "---",
        "",
        "## Усі Активні Магазини",
        "",
        "| Місто | Адреса | ID Магазину | Район | Регіон |",
        "|-------|--------|-------------|-------|--------|",
    ]

    for s in sorted(current_stores, key=lambda x: (x.get("city", ""), x.get("address", ""))):
        city = display_city(s.get("city", "—"))
        address = s.get("address", "—")
        store_id = s.get("store_id", "—")
        county = s.get("county", "—")
        region = s.get("region", "—")
        lines.append(f"| {city} | {address} | `{store_id}` | {county} | {region} |")

    lines += [
        "",
        "---",
        "",
        f"_Додаток згенеровано: {today} | Aurora Romania Expansion Monitor_",
    ]

    path = REPORTS_DIR / f"appendix_{today}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Appendix report saved: {path}")


def compute_stats(changes: list[dict], future_openings: list[dict]) -> dict:
    stats: dict = {}
    for c in changes + future_openings:
        ct = c.get("change_type", "")
        stats[ct] = stats.get(ct, 0) + 1
    stats["total_changes"] = len(changes)
    return stats


def generate_weekly_report(report_date: str = None) -> tuple[str, Path]:
    """
    Strategic weekly report: expansion summary, competitor pressure,
    white-space opportunities, regional trends, likely next regions.
    """
    from datetime import timedelta
    from collections import Counter, defaultdict
    from src.storage.sqlite_store import (
        init_db, load_snapshot, load_recent_changes,
        load_recent_jobs, load_recent_news, load_competitor_stores,
    )
    from src.analysis.trends import (
        monthly_opening_counts, city_growth_ranking,
        expansion_velocity, region_activity_summary,
        weekly_change_summary, competitor_store_counts,
    )
    from src.analysis.whitespace import whitespace_opportunities, whitespace_by_region
    from src.data.ro_counties import normalize_city, county_for_city, region_for_city

    today = report_date or date.today().isoformat()
    init_db()

    current_stores = load_snapshot()
    changes_7d = load_recent_changes(days=7)
    jobs = load_recent_jobs(days=7)
    news = load_recent_news(days=7)
    competitor_stores = load_competitor_stores()

    map_changes_7d = [c for c in changes_7d
                      if c.get("change_type") not in ("POSSIBLE_FUTURE_OPENING", "MARKET_ACTIVITY_SIGNAL")]
    new_stores_7d = [c for c in map_changes_7d if c.get("change_type") == "NEW_STORE"]
    closures_7d = [c for c in map_changes_7d if c.get("change_type") == "REMOVED_STORE"]

    velocity = expansion_velocity()
    monthly = monthly_opening_counts(months_back=3)
    city_growth = city_growth_ranking(months_back=2)
    region_act = region_activity_summary(months_back=2)
    week_summary = weekly_change_summary()
    comp_counts = competitor_store_counts()

    ws_opps = whitespace_opportunities(current_stores, competitor_stores)
    ws_regions = whitespace_by_region(current_stores, competitor_stores)

    city_signals: dict[str, set] = defaultdict(set)
    for c in new_stores_7d:
        city = (c.get("store") or {}).get("city", "")
        if city:
            city_signals[normalize_city(city)].add("aurora_map")
    for j in jobs:
        for city in j.get("cities_mentioned", []):
            city_signals[normalize_city(city)].add("jobs")
    for a in news:
        for city in a.get("cities_mentioned", []):
            city_signals[normalize_city(city)].add("news")
    for brand, stores in competitor_stores.items():
        for s in stores:
            city_signals[normalize_city(s.get("city", ""))].add(f"comp_{brand.lower()}")

    hot_clusters = [
        {"city": c.title(), "signals": sorted(sigs),
         "county": county_for_city(c), "region": region_for_city(c)}
        for c, sigs in sorted(city_signals.items(), key=lambda x: -len(x[1]))
        if len(sigs) >= 3
    ][:10]

    delta = week_summary.get("delta", {})
    new_delta = delta.get("NEW_STORE", 0)

    lines = [
        "# Aurora Romania — Щотижневий Стратегічний Звіт",
        f"**Тиждень до:** {today}",
        "",
        "---",
        "",
        "## Тиждень у Цифрах",
        "",
        "| Показник | Цей тиждень | vs минулий тиждень |",
        "|----------|-------------|---------------------|",
        f"| Нових магазинів | {len(new_stores_7d)} | {'+' if new_delta>=0 else ''}{new_delta} |",
        f"| Закриттів | {len(closures_7d)} | — |",
        f"| Всього магазинів Aurora | {len(current_stores)} | — |",
        f"| Швидкість розширення | {velocity.get('stores_per_month') or 'N/A'}/міс | — |",
        "",
        "---",
        "",
        "## Точки Зростання",
        "_Міста з найбільшою активністю Aurora за останні 2 тижні._",
        "",
    ]

    if city_growth:
        lines += ["| Місто | Район | Регіон | Нових магазинів |",
                  "|-------|-------|--------|-----------------|"]
        for c in city_growth[:10]:
            lines.append(
                f"| {c['city']} | {c.get('county','—')} | {c.get('region','—')} | {c['openings']} |"
            )
        lines.append("")
    else:
        lines.append("Даних про розширення ще немає.\n")

    lines += [
        "---",
        "",
        "## Кластери Ринкової Активності",
        "_Міста, де активність Aurora, присутність конкурентів та ритейл-сигнали збігаються._",
        "",
    ]

    if hot_clusters:
        lines += ["| Місто | Район | Регіон | Типи сигналів |",
                  "|-------|-------|--------|---------------|"]
        for cl in hot_clusters:
            sig_str = ", ".join(cl["signals"])
            lines.append(
                f"| {cl['city']} | {cl.get('county','—')} | {cl.get('region','—')} | {sig_str} |"
            )
        lines.append("")
    else:
        lines.append("Кластерів з високою конвергенцією цього тижня не виявлено.\n")

    lines += [
        "---",
        "",
        "## Цільові Ринкові Ніші",
        "_Міста, де є конкуренти, але немає Aurora — за регіонами._",
        "",
    ]

    for region_data in ws_regions[:5]:
        region_name = region_data["region"]
        lines.append(f"### {region_name} ({region_data['city_count']} міст)")
        top = region_data["top_opportunities"][:5]
        if top:
            lines += ["| Місто | Конкуренти | Оцінка |",
                      "|-------|------------|--------|"]
            for opp in top:
                brands = ", ".join(f"{b}({n})" for b, n in sorted(opp["competitor_brands"].items()))
                lines.append(f"| {opp['city']} | {brands} | {opp['opportunity_score']:.0f} |")
        lines.append("")

    lines += [
        "---",
        "",
        "## Мережа Конкурентів",
        "",
    ]

    if comp_counts:
        lines += ["| Бренд | Кількість магазинів |", "|-------|---------------------|"]
        for brand, cnt in sorted(comp_counts.items()):
            lines.append(f"| {brand} | {cnt} |")
        lines.append("")

    lines += [
        "---",
        "",
        "## Активність по Регіонах",
        "",
    ]

    if region_act:
        lines += ["| Регіон | Нових магазинів (2 тижні) | Топ-місто |",
                  "|--------|--------------------------|-----------|"]
        for r in region_act:
            lines.append(f"| {r['region']} | {r['openings']} | {r['top_city']} |")
        lines.append("")
    else:
        lines.append("Регіональних даних ще немає.\n")

    lines += [
        "---",
        "",
        "## Щомісячний Тренд",
        "",
    ]

    if monthly:
        lines += ["| Місяць | Відкриття | Закриття |",
                  "|--------|-----------|----------|"]
        for m in monthly:
            lines.append(f"| {m['month']} | {m['openings']} | {m['closures']} |")
        lines.append("")
    else:
        lines.append("Недостатньо даних поки що.\n")

    lines += [
        "---",
        "",
        f"_Звіт згенеровано: {today} | Aurora Romania Expansion Monitor — Щотижневий Стратегічний Звіт_",
    ]

    report_md = "\n".join(lines)
    path = REPORTS_DIR / f"weekly_report_{today}.md"
    path.write_text(report_md, encoding="utf-8")
    logger.info(f"Weekly report saved: {path}")
    return report_md, path
