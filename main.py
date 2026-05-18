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

from src.config import setup_logging, SNAPSHOTS_DIR

logger = setup_logging("main")


def run_pipeline(
    skip_map: bool = False,
    skip_jobs: bool = False,
    skip_news: bool = False,
    skip_instagram: bool = False,
    skip_competitors: bool = False,
    skip_alerts: bool = False,
    skip_sheets: bool = False,
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
    )

    if not skip_alerts:
        from src.alerts.telegram_alerts import send_daily_summary
        from src.analysis.executive_summary import generate_executive_summary
        exec_summary = generate_executive_summary(
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
        send_daily_summary(exec_summary)

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
        choices=["run", "schedule", "test-telegram", "report", "weekly", "init", "debug-competitors"],
        help="Command to execute",
    )
    parser.add_argument("--skip-map", action="store_true", help="Skip store map scraping")
    parser.add_argument("--skip-jobs", action="store_true", help="Skip job board scraping")
    parser.add_argument("--skip-news", action="store_true", help="Skip news scraping")
    parser.add_argument("--skip-instagram", action="store_true", help="Skip Instagram scraping")
    parser.add_argument("--skip-competitors", action="store_true", help="Skip competitor scraping")
    parser.add_argument("--skip-alerts", action="store_true", help="Skip Telegram alerts")
    parser.add_argument("--skip-sheets", action="store_true", help="Skip Google Sheets export")
    parser.add_argument("--dry-run", action="store_true", help="No DB writes, no alerts")
    parser.add_argument("--baseline", action="store_true", help="Save snapshot without sending any alerts (use on first real run)")

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
        summary = run_pipeline(
            skip_map=args.skip_map,
            skip_jobs=args.skip_jobs,
            skip_news=args.skip_news,
            skip_instagram=args.skip_instagram,
            skip_competitors=args.skip_competitors,
            skip_alerts=args.skip_alerts,
            skip_sheets=args.skip_sheets,
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


if __name__ == "__main__":
    main()
