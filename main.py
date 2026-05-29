#!/usr/bin/env python3
"""
Aurora Romania Expansion Monitor — Main Orchestrator
Runs the full daily pipeline: scrape → diff → analyze → alert → report.
"""
import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

from src.config import setup_logging, SNAPSHOTS_DIR, DATA_DIR

logger = setup_logging("main")


# ── Telegram section formatters ───────────────────────────────────────────────

def _fmt_retail_news(data: list, today: str) -> str:
    lines = [f"📰 *2.2 Retail News — {today}*\n"]
    for s in data:
        n    = s.get("results_count", 0)
        days = s.get("days_used", 1)
        lines.append(f"*{s.get('label', '')}* ({n} джерел, {days}д)")
        summary = (s.get("summary") or "").strip()
        if summary:
            lines.append(summary[:350])
        for src in s.get("sources", [])[:3]:
            title = (src.get("title") or "")[:60]
            url   = src.get("url", "")
            if url:
                lines.append(f"📎 [{title}]({url})")
        lines.append("")
    return "\n".join(lines)[:4090]


def _fmt_industry_research(data: list, today: str) -> str:
    lines = [f"🔬 *2.3 Industry Research — {today}*\n"]
    for s in data:
        n = s.get("results_count", 0)
        lines.append(f"*{s.get('label', '')}* ({n} джерел)")
        summary = (s.get("summary") or "").strip()
        if summary:
            lines.append(summary[:350])
        for src in s.get("sources", [])[:3]:
            title = (src.get("title") or "")[:60]
            url   = src.get("url", "")
            if url:
                lines.append(f"📎 [{title}]({url})")
        lines.append("")
    return "\n".join(lines)[:4090]


def _fmt_corporate_news(section: dict, today: str) -> str:
    items = section.get("items", [])
    if not items:
        return f"📊 *3.1 Корпоративні новини — {today}*\n\nНовин за день не знайдено."
    lines = [f"📊 *3.1 Корпоративні новини — {today}*\n"]
    for i, item in enumerate(items, 1):
        link   = item.get("telegram_link") or item.get("url", "")
        source = item.get("source_name", "")
        lines.append(f"{i}. {link}" + (f" — _{source}_" if source else ""))
        summary = (item.get("summary_uk") or "").strip()[:250]
        if summary:
            lines.append(summary)
        lines.append("")
    return "\n".join(lines)[:4090]


def _fmt_network_expansion(section: dict, today: str) -> str:
    if not section.get("changes_detected"):
        return (
            f"🗺 *3.2 Мережа Румунії — {today}*\n\n"
            + section.get("message", "Змін не виявлено.")
        )
    _UA = {"opened": "відкрито", "closed": "закрито",
           "relocated": "переміщено", "rebranded": "ребрендинг"}
    lines = [f"🗺 *3.2 Мережа Румунії — {today}*\n"]
    for brand, counts in section.get("by_brand", {}).items():
        parts = []
        if counts.get("opened"):    parts.append(f"+{counts['opened']} відкрито")
        if counts.get("closed"):    parts.append(f"{counts['closed']} закрито")
        if counts.get("relocated"): parts.append(f"{counts['relocated']} переміщено")
        if counts.get("rebranded"): parts.append(f"{counts['rebranded']} ребрендинг")
        if parts:
            lines.append(f"*{brand}:* " + " • ".join(parts))
    details = section.get("details", [])[:8]
    if details:
        lines.append("\n📍 *Деталі:*")
        for d in details:
            dtype = _UA.get(d.get("type", ""), d.get("type", ""))
            loc   = d.get("location") or d.get("to") or d.get("from", "")
            lines.append(f"• {d.get('brand', '')} {dtype} — {loc}")
    return "\n".join(lines)[:4090]


def _fmt_competitor_intel(result: dict, today: str, sources: list = None) -> str:
    if not result or not result.get("brands"):
        return f"🏪 *1.2 Конкурентна розвідка — {today}*\n\n_Даних сьогодні не зібрано._"
    lines = [f"🏪 *1.2 Конкурентна розвідка — {today}*\n"]
    pattern     = (result.get("market_pattern") or "").strip()
    aurora_impl = (result.get("aurora_implication") or "").strip()
    if pattern:
        lines.append(f"_{pattern}_\n")
    if aurora_impl:
        lines.append(f"*Aurora:* {aurora_impl}\n")
    for brand, bdata in result.get("brands", {}).items():
        key_insight = (bdata.get("key_insight") or "").strip()
        bullets     = bdata.get("bullets") or []
        expansion   = (bdata.get("expansion") or "").strip()
        if not key_insight and not bullets:
            continue
        lines.append(f"*{brand}*")
        if key_insight:
            lines.append(key_insight)
        for b in bullets[:3]:
            lines.append(f"• {b}")
        if expansion:
            lines.append(f"📍 {expansion}")
        lines.append("")
    if sources:
        lines.append("📎 *Джерела:*")
        for src in sources[:5]:
            title = (src.get("title") or "")[:60]
            url   = src.get("url", "")
            if url:
                lines.append(f"[{title}]({url})")
    return "\n".join(lines)[:4090]


def _fmt_commercial_activity(social_analysis: dict, today: str) -> str:
    import re as _re
    header = f"📸 *1.3 Комерційна активність — {today}*\n"
    if not social_analysis:
        return header + "\n_Instagram-аналіз сьогодні недоступний._"

    lines = [header]

    # AI narrative — covers Aurora + all tracked competitors
    raw_narrative = (social_analysis.get("daily_narrative") or "").strip()
    if raw_narrative:
        narrative = _re.sub(r'^\s*\[[\d./:,\s]+\][^\n]*\n*', '', raw_narrative).strip()
        narrative = _re.sub(r'\n+', '\n\n', narrative)
        narrative = _re.sub(r'\((https?://[^\s)]+)\)', r'([пост](\1))', narrative)
        narrative = _re.sub(r'(?<!\]\()(https?://[^\s)]+)', r'[пост](\1)', narrative)
        lines.append(narrative[:700])
        lines.append("")

    digest   = social_analysis.get("commercial_digest") or {}
    promos   = digest.get("promos",   [])
    products = digest.get("products", [])
    openings = digest.get("openings", [])

    # Per-brand one-line summaries for competitor visibility
    brand_summary = social_analysis.get("brand_summary") or {}
    active_brands = {b: s.strip() for b, s in brand_summary.items() if s and s.strip()}
    if active_brands:
        lines.append("*Активність брендів:*")
        for brand, summary in active_brands.items():
            lines.append(f"• *{brand}:* {summary[:160]}")
        lines.append("")

    if not raw_narrative and not active_brands and not promos and not products and not openings:
        return header + "\n_Значущої комерційної активності сьогодні не виявлено._"

    if promos:
        lines.append("*Акції:*")
        for item in promos[:6]:
            lines.append(f"• {str(item)[:160]}")
        lines.append("")
    if products:
        lines.append("*Нові продукти / Категорії:*")
        for item in products[:4]:
            lines.append(f"• {str(item)[:160]}")
        lines.append("")
    if openings:
        lines.append("*Відкриття (підтверджено):*")
        for item in openings[:4]:
            lines.append(f"• {str(item)[:160]}")
        lines.append("")

    posts = social_analysis.get("posts") or []
    linked = [p for p in posts if p.get("post_url") and p.get("is_relevant")][:8]
    if linked:
        lines.append("📎 *Пости:*")
        for p in linked:
            brand = p.get("brand") or p.get("competitor") or "Aurora"
            url   = p.get("post_url", "")
            lines.append(f"• [{brand}]({url})")
    return "\n".join(lines)[:4090]


def _fmt_macro_environment(result: dict, today: str) -> str:
    if not result:
        return f"📊 *2.1 Макросередовище — {today}*\n\n_Даних сьогодні не зібрано._"
    _RO = {"inflation": "Інфляція", "bnr_rate": "Ставка BNR",
           "unemployment": "Безробіття", "consumer_sentiment": "Споживач",
           "energy": "Енергетика", "fiscal": "Фіскальна"}
    _UA = {"inflation": "Інфляція", "nbu_rate": "Ставка НБУ",
           "unemployment": "Безробіття", "wages": "Зарплати",
           "energy": "Енергетика", "fiscal": "Фіскальна"}
    lines = [f"📊 *2.1 Макросередовище — {today}*\n"]
    ro = result.get("romania") or {}
    if ro:
        lines.append("🇷🇴 *Румунія:*")
        for key, label in _RO.items():
            val = (ro.get(key) or "").strip()
            if val:
                lines.append(f"• {label}: {val}")
        lines.append("")
    ua = result.get("ukraine") or {}
    if ua:
        lines.append("🇺🇦 *Україна:*")
        for key, label in _UA.items():
            val = (ua.get(key) or "").strip()
            if val:
                lines.append(f"• {label}: {val}")
        lines.append("")
    bullets = result.get("aurora_bullets") or []
    if bullets:
        lines.append("*Aurora:*")
        for b in bullets[:2]:
            lines.append(f"• {b.strip()}")
    sources = result.get("_sources") or []
    if sources:
        lines.append("\n📎 *Джерела:*")
        for src in sources[:6]:
            title = (src.get("title") or "")[:60]
            url   = src.get("url", "")
            if url:
                lines.append(f"[{title}]({url})")
    return "\n".join(lines)[:4090]


def run_pipeline(
    skip_map: bool = False,
    skip_jobs: bool = False,
    skip_news: bool = False,
    skip_instagram: bool = False,
    skip_competitors: bool = False,
    skip_alerts: bool = False,
    skip_sheets: bool = False,
    skip_brief: bool = False,
    skip_presentation: bool = False,
    dry_run: bool = False,
    baseline: bool = False,
) -> dict:
    today = date.today().isoformat()
    logger.info(f"=== Aurora Pipeline Start: {today} ===")

    # ── Storage init ──────────────────────────────────────────────────────────
    from src.storage.sqlite_store import (
        init_db, save_snapshot, load_previous_snapshot, load_snapshot,
        save_changes, save_jobs, save_news, save_instagram_posts,
        save_competitor_stores, load_competitor_stores,
        load_unalerted_changes, mark_changes_alerted,
        load_recent_jobs, load_recent_news, load_recent_instagram,
        purge_old_snapshots,
    )
    init_db()

    # ── Step 1: Scrape Aurora store map ───────────────────────────────────────
    current_stores = []
    if not skip_map:
        logger.info("[1/7] Scraping Aurora store map")
        try:
            from src.scrapers.aurora_map import scrape_aurora_map
            from src.data.ro_counties import enrich_stores
            current_stores = enrich_stores(scrape_aurora_map())
            logger.info(f"Store map: {len(current_stores)} stores")
            if not dry_run:
                save_snapshot(current_stores)
                _save_json_snapshot(current_stores, today, "stores")
        except Exception as e:
            logger.error(f"Store map scraping failed: {e}")
            current_stores = load_snapshot()
            logger.info(f"Using latest DB snapshot: {len(current_stores)} stores")
    else:
        current_stores = load_snapshot()

    # ── Step 2: Scrape jobs ───────────────────────────────────────────────────
    jobs = []
    if not skip_jobs:
        logger.info("[2/7] Scraping job boards")
        try:
            from src.scrapers.linkedin_jobs import scrape_jobs
            jobs = scrape_jobs()
            logger.info(f"Jobs: {len(jobs)} postings")
            if not dry_run:
                save_jobs(jobs)
        except Exception as e:
            logger.error(f"Job scraping failed: {e}")
    jobs = jobs or load_recent_jobs()

    # ── Step 3: Scrape news + broad web intelligence ──────────────────────────
    news_articles = []
    if not skip_news:
        logger.info("[3/7] Scraping retail news + broad web intelligence")
        try:
            from src.scrapers.aurora_news import scrape_aurora_news
            from src.scrapers.retail_intelligence import scrape_retail_intelligence
            from src.scrapers.web_intelligence import scrape_web_intelligence
            aurora_news = scrape_aurora_news()
            retail_intel = scrape_retail_intelligence()
            web_intel = scrape_web_intelligence()
            news_articles = aurora_news + retail_intel + web_intel
            logger.info(
                f"News: {len(news_articles)} articles "
                f"({len(aurora_news)} Aurora, {len(retail_intel)} retail intel, {len(web_intel)} web)"
            )
            if not dry_run:
                save_news(news_articles)
        except Exception as e:
            logger.error(f"News scraping failed: {e}")
    news_articles = news_articles or load_recent_news()

    # ── Step 4: Scrape Instagram (Aurora + competitors) ───────────────────────
    instagram_posts = []
    if not skip_instagram:
        logger.info("[4/7] Scraping Instagram (Aurora + Pepco, KiK, Action)")
        try:
            from src.scrapers.aurora_instagram import (
                scrape_aurora_instagram, scrape_competitor_instagram,
            )
            aurora_ig = scrape_aurora_instagram()
            competitor_ig = scrape_competitor_instagram()
            instagram_posts = aurora_ig + competitor_ig
            logger.info(
                f"Instagram: {len(aurora_ig)} Aurora posts, "
                f"{len(competitor_ig)} competitor posts"
            )
            if not dry_run:
                save_instagram_posts(instagram_posts)
        except Exception as e:
            logger.error(f"Instagram scraping failed: {e}")
    instagram_posts = instagram_posts or load_recent_instagram()

    # ── Step 5: Scrape competitors ────────────────────────────────────────────
    competitor_stores = {}
    if not skip_competitors:
        logger.info("[5/7] Scraping competitor stores")
        try:
            from src.scrapers.competitor_scraper import scrape_competitors
            competitor_stores = scrape_competitors()
            total_comp = sum(len(v) for v in competitor_stores.values())
            logger.info(f"Competitors: {total_comp} stores")
            if not dry_run:
                save_competitor_stores(competitor_stores)
        except Exception as e:
            logger.error(f"Competitor scraping failed: {e}")
    competitor_stores = competitor_stores or load_competitor_stores()

    # ── Step 5c: Competitor catalogue / promo pages ───────────────────────────
    catalogue_data: list[dict] = []
    if not skip_competitors:
        logger.info("[5c] Scraping competitor catalogue/promo pages")
        try:
            from src.scrapers.competitor_catalogue import scrape_competitor_catalogues
            catalogue_data = scrape_competitor_catalogues()
            logger.info(f"Catalogue: {len(catalogue_data)} brands scraped")
        except Exception as e:
            logger.error(f"Catalogue scraping failed: {e}")

    # ── Step 5b: Apify Instagram scrape + AI batch analysis ──────────────────
    social_analysis: dict = {}
    social_posts: list[dict] = []
    if not skip_instagram:
        from src.config import APIFY_TOKEN
        if not APIFY_TOKEN:
            logger.warning("APIFY_TOKEN not set — skipping Apify Instagram scrape")
        else:
            logger.info("[5b] Scraping Instagram via Apify + AI analysis")
            try:
                from datetime import date as _date
                from src.scrapers.instagram_scraper import scrape_instagram_apify
                from src.storage.sqlite_store import get_known_post_urls, save_social_posts
                social_posts = scrape_instagram_apify()
                if social_posts:
                    # Deduplication: snapshot known URLs before saving (bypass on 2026-05-20)
                    _DEDUP_START = "2026-05-21"
                    if _date.today().isoformat() >= _DEDUP_START:
                        known_urls = get_known_post_urls()
                        new_social_posts = [p for p in social_posts if p["post_url"] not in known_urls]
                    else:
                        new_social_posts = social_posts  # testing day — no dedup

                    if not dry_run:
                        new_count = save_social_posts(social_posts)
                        logger.info(f"Apify Instagram: {len(social_posts)} fetched, {new_count} new")
                    else:
                        logger.info(f"Apify Instagram: {len(social_posts)} fetched (dry-run)")

                    if not new_social_posts:
                        logger.info("Instagram: no new posts since last scrape — skipping analysis (re-run will load from DB)")
                    else:
                        # Analyze ALL of today's posts for a comprehensive daily digest
                        # (not just new ones — competitor posts need to be included)
                        from src.analysis.social_analyzer import analyze_social_batch
                        social_analysis = analyze_social_batch(social_posts)
                        if social_analysis:
                            if not dry_run:
                                from src.storage.sqlite_store import save_batch_analysis, update_social_posts_ai
                                save_batch_analysis(social_analysis)
                                update_social_posts_ai(social_analysis.get("posts", []))
            except Exception as e:
                logger.error(f"Apify Instagram + AI analysis failed: {e}")

    # ── Step 6: Diff & Analysis ───────────────────────────────────────────────
    logger.info("[6/7] Running diff and analysis")
    previous_stores = load_previous_snapshot()
    is_first_run = len(previous_stores) == 0
    if is_first_run:
        logger.info("First run detected — establishing baseline. No change alerts will be sent.")
    logger.info(f"Comparing: {len(previous_stores)} previous vs {len(current_stores)} current")

    from src.analysis.diff import compare_snapshots, merge_intelligence_signals
    from src.analysis.competitor_analysis import (
        enrich_change_with_competitor_data,
        cities_competitors_expanded_before_aurora,
    )
    from src.analysis.confidence import enrich_change_with_confidence
    from src.analysis.market_intelligence import compute_city_market_scores

    map_changes = compare_snapshots(previous_stores, current_stores)
    future_openings = merge_intelligence_signals(map_changes, jobs, news_articles, instagram_posts)

    all_changes = map_changes + future_openings

    # Enrich with competitor data and confidence
    enriched = []
    for change in all_changes:
        change = enrich_change_with_competitor_data(change, competitor_stores)
        change = enrich_change_with_confidence(change, jobs, news_articles, instagram_posts)
        enriched.append(change)

    # Competitor opportunity analysis
    competitor_opportunities = cities_competitors_expanded_before_aurora(
        current_stores, competitor_stores
    )

    # City-level market intelligence aggregation
    city_market_scores = compute_city_market_scores(
        news_articles, jobs, map_changes, competitor_stores
    )

    if not dry_run:
        save_changes(enriched)

    # ── Step 7: Alerts & Reports ──────────────────────────────────────────────
    logger.info("[7/7] Sending alerts and generating report")

    # Load unalerted changes (includes ones just saved)
    if not dry_run:
        unalerted = load_unalerted_changes()
    else:
        unalerted = enriched

    alerted_ids = []
    if not skip_alerts and unalerted and not is_first_run and not baseline:
        from src.alerts.telegram_alerts import send_alerts
        alerted_ids = send_alerts(unalerted, jobs=jobs, news=news_articles,
                                  instagram_posts=instagram_posts)
        if not dry_run:
            mark_changes_alerted(alerted_ids)
    elif is_first_run or baseline:
        # Silently mark all baseline changes as already alerted so they don't fire tomorrow
        if not dry_run:
            baseline_ids = [c["id"] for c in unalerted if c.get("id")]
            if baseline_ids:
                mark_changes_alerted(baseline_ids)
        logger.info(f"Baseline: marked {len(unalerted)} stores as seen (no alerts sent)")

    # Trend and white-space analysis
    from src.analysis.trends import (
        monthly_opening_counts, city_growth_ranking,
        expansion_velocity, region_activity_summary,
    )
    from src.analysis.whitespace import whitespace_opportunities

    trend_data = {
        "monthly": monthly_opening_counts(months_back=3),
        "velocity": expansion_velocity(),
        "city_growth": city_growth_ranking(months_back=2),
        "region_activity": region_activity_summary(months_back=2),
    }
    whitespace_opps = whitespace_opportunities(current_stores, competitor_stores)

    # Generate report
    from src.reports import generate_daily_report, compute_stats
    report_md, report_path = generate_daily_report(
        changes=enriched,
        future_openings=future_openings,
        jobs=jobs,
        news_articles=news_articles,
        current_stores=current_stores,
        competitor_opportunities=competitor_opportunities,
        city_market_scores=city_market_scores,
        competitor_stores=competitor_stores,
        whitespace_opps=whitespace_opps,
        trend_data=trend_data,
        report_date=today,
        is_baseline=is_first_run or baseline,
        instagram_posts=instagram_posts,
        catalogue_data=catalogue_data,
        social_analysis=social_analysis,
    )

    if not skip_alerts:
        from src.analysis.executive_summary import generate_executive_summary
        generate_executive_summary(
            data={
                "changes": enriched,
                "future_openings": future_openings,
                "jobs": jobs,
                "news": news_articles,
                "instagram_posts": instagram_posts,
                "current_stores": current_stores,
                "competitor_stores": competitor_stores,
                "city_market_scores": city_market_scores,
                "whitespace_opps": whitespace_opps,
                "trend_data": trend_data,
            },
            report_path=str(report_path),
        )
        # daily executive summary Telegram send disabled — Instagram digest is the only daily message

    # ── Tavily daily brief ────────────────────────────────────────────────────
    if not skip_brief:
        from src.config import TAVILY_API_KEY
        if not TAVILY_API_KEY:
            logger.info("TAVILY_API_KEY not set — skipping daily brief")
        else:
            logger.info("[7b] Running Tavily daily brief")
            try:
                from src.analysis.daily_brief import run_daily_brief
                # skip_alerts=True: sections 1.2–3.2 below send the content individually
                run_daily_brief(dry_run=dry_run, skip_alerts=True)
            except Exception as e:
                logger.error(f"Daily brief failed: {e}")

    # ── Sections 1.2 / 1.3 / 2.1 / 2.2 / 2.3 / 3.1 / 3.2 — each → Telegram ──
    from src.config import TAVILY_API_KEY as _TV_KEY
    from src.alerts.telegram_alerts import TelegramBot as _TGBot
    _bot    = _TGBot()
    _output: dict = {}

    # Load today's aurora_output early so we can detect re-runs and load saved Instagram data
    _ao_path = DATA_DIR / f"aurora_output_{today}.json"
    _ao_existing: dict = {}
    if _ao_path.exists() and not dry_run:
        try:
            _ao_existing = json.loads(_ao_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Gate section Telegram sends: skip on re-runs to prevent duplicate delivery
    _tg_sections_done = not dry_run and bool(_ao_existing.get("_tg_sent"))
    if _tg_sections_done and not skip_alerts:
        logger.info(
            "Section Telegram sends already completed today — skipping to avoid duplicates "
            "(delete aurora_output_%s.json to force re-send)", today
        )

    # Validate Telegram credentials before attempting any section sends
    if not skip_alerts and not _tg_sections_done:
        if _bot.test_connection():
            logger.info("Telegram startup check: OK")
        else:
            logger.warning(
                "Telegram startup check failed — TELEGRAM_BOT_TOKEN may be invalid or "
                "Telegram API is temporarily unreachable; section sends will proceed anyway"
            )

    # [7c] 1.2 Competitor Intelligence
    logger.info("[7c] Section 1.2: Competitor Intelligence")
    try:
        from src.storage.sqlite_store import load_recent_web_search
        from src.analysis.competitor_intelligence import synthesize_competitor_intel
        _tavily = load_recent_web_search(days=7)
        _ci = synthesize_competitor_intel(
            _tavily, catalogue_data, news_articles, social_analysis, today,
        )
        _output["1.2_competitor_intelligence"] = _ci
        if not skip_alerts and not _tg_sections_done:
            _ci_sources = [{"title": r["title"], "url": r["url"]}
                           for r in _tavily[:5] if r.get("url")]
            _bot._send(_fmt_competitor_intel(_ci, today, sources=_ci_sources),
                       disable_preview=True)
    except Exception as e:
        logger.error(f"Competitor intelligence failed: {e}")

    # [7d] 1.3 Commercial Activity (Instagram digest)
    logger.info("[7d] Section 1.3: Commercial Activity")
    try:
        # On re-runs social_analysis is empty (dedup left no new posts).
        # Fall back to the daily_narrative saved in DB by the first run.
        _social_13 = social_analysis
        if not _social_13:
            try:
                import sqlite3 as _sqlite3
                from src.config import DB_PATH as _DB_PATH
                _conn13 = _sqlite3.connect(str(_DB_PATH))
                _row13 = _conn13.execute(
                    "SELECT daily_narrative FROM batch_analyses "
                    "WHERE run_date = ? ORDER BY id DESC LIMIT 1",
                    (today,),
                ).fetchone()
                # Load today's relevant posts with their URLs for the links section
                _posts13 = _conn13.execute(
                    "SELECT competitor, post_url FROM social_posts "
                    "WHERE DATE(scraped_at) = ? AND is_relevant = 1 "
                    "ORDER BY relevance_score DESC LIMIT 8",
                    (today,),
                ).fetchall()
                _conn13.close()
                if _row13 or _posts13:
                    _social_13 = {
                        "daily_narrative": (_row13[0] if _row13 else ""),
                        "posts": [
                            {"brand": r[0] or "Aurora", "post_url": r[1], "is_relevant": True}
                            for r in _posts13
                        ],
                    }
                    logger.info(f"1.3: loaded from DB — narrative + {len(_posts13)} posts (re-run)")
            except Exception as _e13:
                logger.debug(f"1.3 DB load failed: {_e13}")
        _digest = (_social_13 or {}).get("commercial_digest") or {}
        _ca = {
            "promos":   _digest.get("promos",   []),
            "products": _digest.get("products", []),
            "openings": _digest.get("openings", []),
        }
        _output["1.3_commercial_activity"] = _ca
        if not skip_alerts and not _tg_sections_done:
            _bot._send(_fmt_commercial_activity(_social_13 or {}, today), disable_preview=True)
    except Exception as e:
        logger.error(f"Commercial activity failed: {e}")

    # [7e] 2.1 Macro Environment
    if not skip_brief and _TV_KEY:
        logger.info("[7e] Section 2.1: Macro Environment")
        try:
            from src.analysis.macro_intelligence import run_macro_intelligence
            _me = run_macro_intelligence(today)
            _output["2.1_macro_environment"] = _me
            if not skip_alerts and not _tg_sections_done:
                _bot._send(_fmt_macro_environment(_me, today), disable_preview=True)
        except Exception as e:
            logger.error(f"Macro environment failed: {e}")

    # [7f] 2.2 Retail News
    if not skip_brief and _TV_KEY:
        logger.info("[7f] Section 2.2: Retail News")
        try:
            from modules.retail_news import run as _run_retail_news
            _rn = _run_retail_news()
            _output["2.2_retail_news"] = _rn
            if not skip_alerts and not _tg_sections_done:
                _bot._send(_fmt_retail_news(_rn, today), disable_preview=True)
        except Exception as e:
            logger.error(f"Retail news failed: {e}")

    # [7g] 2.3 Industry Research
    if not skip_brief and _TV_KEY:
        logger.info("[7g] Section 2.3: Industry Research")
        try:
            from modules.industry_research import run as _run_industry_research
            _ir = _run_industry_research()
            _output["2.3_industry_research"] = _ir
            if not skip_alerts and not _tg_sections_done:
                _bot._send(_fmt_industry_research(_ir, today), disable_preview=True)
        except Exception as e:
            logger.error(f"Industry research failed: {e}")

    # [7h] 3.1 Corporate News
    if not skip_brief and _TV_KEY:
        logger.info("[7h] Section 3.1: Corporate News")
        try:
            from modules.corporate_news import run as _run_corporate_news
            _cn = _run_corporate_news(today=today)
            _output["3.1_corporate_news"] = _cn
            if not skip_alerts and not _tg_sections_done:
                _bot._send(_fmt_corporate_news(_cn, today), disable_preview=True)
        except Exception as e:
            logger.error(f"Corporate news failed: {e}")

    # [7i] 3.2 Network Expansion (no Tavily — always runs)
    logger.info("[7i] Section 3.2: Network Expansion")
    try:
        from modules.network_expansion_ro import run as _run_network_expansion
        _ne = _run_network_expansion(today=today)
        _output["3.2_network_expansion_ro"] = _ne
        if not skip_alerts and not _tg_sections_done:
            _bot._send(_fmt_network_expansion(_ne, today), disable_preview=True)
    except Exception as e:
        logger.error(f"Network expansion diff failed: {e}")

    # [7j] Write final aurora_output JSON (consolidates all sections)
    if _output and not dry_run:
        _ao_existing.update(_output)
        if not skip_alerts and not _tg_sections_done:
            _ao_existing["_tg_sent"] = True  # prevents re-sends on subsequent runs today
        _ao_path.write_text(
            json.dumps(_ao_existing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"aurora_output saved: {_ao_path}")

    # ── Daily presentation (.pptx) ────────────────────────────────────────────
    _pptx_path = None
    if not skip_presentation:
        logger.info("[7k] Generating daily presentation")
        try:
            from src.presentation_export import generate_presentation
            _pptx_path = generate_presentation(today_str=today, dry_run=dry_run)
        except Exception as e:
            logger.error(f"Presentation generation failed: {e}")

    # Google Sheets export
    if not skip_sheets and not dry_run:
        try:
            from src.storage.google_sheets import export_to_sheets
            export_to_sheets(current_stores, enriched, jobs, future_openings)
        except Exception as e:
            logger.warning(f"Google Sheets export failed: {e}")

    # Purge old snapshots
    if not dry_run:
        purge_old_snapshots()

    summary = {
        "date": today,
        "stores_total": len(current_stores),
        "map_changes": len(map_changes),
        "future_openings": len(future_openings),
        "jobs": len(jobs),
        "news": len(news_articles),
        "alerts_sent": len(alerted_ids),
        "report": str(report_path),
    }
    if _pptx_path:
        summary["presentation"] = str(_pptx_path)
    logger.info(f"=== Pipeline complete: {summary} ===")
    return summary


def _save_json_snapshot(data: list[dict], today: str, name: str) -> None:
    path = SNAPSHOTS_DIR / f"{name}_{today}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.debug(f"Saved JSON snapshot: {path}")


def run_scheduler() -> None:
    """Run the pipeline on a daily schedule."""
    import schedule

    logger.info("Starting scheduler (daily at 07:00)")
    schedule.every().day.at("07:00").do(run_pipeline)

    while True:
        schedule.run_pending()
        time.sleep(60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aurora Romania Expansion Monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run full pipeline once
  python main.py run

  # Dry run (no DB writes, no alerts)
  python main.py run --dry-run

  # Only scrape map and competitors
  python main.py run --skip-jobs --skip-news --skip-instagram

  # Start scheduler (runs daily at 07:00)
  python main.py schedule

  # Test Telegram connection
  python main.py test-telegram

  # Generate report from existing data
  python main.py report
        """,
    )
    parser.add_argument(
        "command",
        choices=["run", "schedule", "test-telegram", "report", "weekly", "init",
                 "debug-competitors", "brief"],
        help="Command to execute",
    )
    parser.add_argument("--skip-map", action="store_true", help="Skip store map scraping")
    parser.add_argument("--skip-jobs", action="store_true", help="Skip job board scraping")
    parser.add_argument("--skip-news", action="store_true", help="Skip news scraping")
    parser.add_argument("--skip-instagram", action="store_true", help="Skip Instagram scraping")
    parser.add_argument("--skip-competitors", action="store_true", help="Skip competitor scraping")
    parser.add_argument("--skip-alerts", action="store_true", help="Skip Telegram alerts")
    parser.add_argument("--skip-sheets", action="store_true", help="Skip Google Sheets export")
    parser.add_argument("--skip-brief", action="store_true", help="Skip Tavily daily brief")
    parser.add_argument("--skip-presentation", action="store_true", help="Skip .pptx generation")
    parser.add_argument("--presentation-only", action="store_true",
                        help="Skip all scraping; generate presentation from existing DB data")
    parser.add_argument("--dry-run", action="store_true", help="No DB writes, no alerts")
    parser.add_argument("--baseline", action="store_true", help="Save snapshot without sending any alerts (use on first real run)")
    parser.add_argument("--daily", action="store_true", help="Run the daily brief (used with 'brief' command)")

    args = parser.parse_args()

    if args.command == "init":
        from src.storage.sqlite_store import init_db
        init_db()
        logger.info("Database initialized successfully")

    elif args.command == "test-telegram":
        from src.alerts.telegram_alerts import TelegramBot
        bot = TelegramBot()
        ok = bot.test_connection()
        if ok:
            bot._send("🤖 Aurora Monitor: Telegram connection test successful!")
            logger.info("Telegram test: PASSED")
        else:
            logger.error("Telegram test: FAILED — check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
            sys.exit(1)

    elif args.command == "run":
        pres_only = getattr(args, "presentation_only", False)
        summary = run_pipeline(
            skip_map=args.skip_map        or pres_only,
            skip_jobs=args.skip_jobs      or pres_only,
            skip_news=args.skip_news      or pres_only,
            skip_instagram=args.skip_instagram or pres_only,
            skip_competitors=args.skip_competitors or pres_only,
            skip_alerts=args.skip_alerts  or pres_only,
            skip_sheets=args.skip_sheets  or pres_only,
            skip_brief=args.skip_brief    or pres_only,
            skip_presentation=args.skip_presentation,
            dry_run=args.dry_run,
            baseline=args.baseline,
        )
        print(json.dumps(summary, indent=2))

    elif args.command == "schedule":
        run_scheduler()

    elif args.command == "weekly":
        from src.reports import generate_weekly_report
        _, path = generate_weekly_report()
        print(f"Weekly report: {path}")

    elif args.command == "debug-competitors":
        from src.scrapers.competitor_scraper import debug_competitors
        debug_competitors()

    elif args.command == "report":
        from src.storage.sqlite_store import (
            init_db, load_snapshot, load_recent_changes,
            load_recent_jobs, load_recent_news,
        )
        init_db()
        current_stores = load_snapshot()
        changes = load_recent_changes(days=1)
        jobs = load_recent_jobs()
        news = load_recent_news()
        from src.reports import generate_daily_report
        _, path = generate_daily_report(
            changes=changes, future_openings=[],
            jobs=jobs, news_articles=news,
            current_stores=current_stores,
            city_market_scores=[],
        )
        print(f"Report generated: {path}")

    elif args.command == "brief":
        from src.storage.sqlite_store import init_db
        init_db()
        from src.analysis.daily_brief import run_daily_brief
        msg = run_daily_brief(
            dry_run=args.dry_run,
            skip_alerts=args.skip_alerts,
        )
        if msg:
            print(msg)
        else:
            print("Brief skipped — check TAVILY_API_KEY in .env")



if __name__ == "__main__":
    main()
