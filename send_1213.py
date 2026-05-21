#!/usr/bin/env python3
"""
Gather fresh data and send sections 1.2 + 1.3 to Telegram.
Steps: catalogue scrape → competitor news → Instagram Apify + AI → Tavily → format → send.
"""
import re
import sys
from datetime import date

_TODAY = date.today().isoformat()
_MAX_MSG = 4000  # Telegram hard limit with headroom


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _md_to_html(text: str) -> str:
    """Convert report markdown to Telegram HTML (more robust than MarkdownV1)."""
    lines_out: list[str] = []
    for line in text.splitlines():
        # Strip HR
        if re.match(r"^---+$", line):
            continue
        # H2-H4 → <b>
        m = re.match(r"^#{2,6}\s+(.+)$", line)
        if m:
            lines_out.append(f"<b>{_escape_html(m.group(1))}</b>")
            continue
        # > blockquote → plain italic
        m = re.match(r"^>\s*(.+)$", line)
        if m:
            lines_out.append(f"<i>{_escape_html(m.group(1))}</i>")
            continue
        # Convert inline markdown
        # [[text](url)] or [text](url) → <a href="url">text</a>
        line = re.sub(
            r"\[([^\]]*)\]\(([^)]+)\)",
            lambda m: f'<a href="{m.group(2)}">{_escape_html(m.group(1))}</a>',
            line,
        )
        # **bold** → <b>
        line = re.sub(r"\*\*(.+?)\*\*", lambda m: f"<b>{_escape_html(m.group(1))}</b>", line)
        # *bold* → <b> (Telegram-style; only if not already converted)
        line = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", lambda m: f"<b>{_escape_html(m.group(1))}</b>", line)
        # _italic_ → <i>
        line = re.sub(r"_(.+?)_", lambda m: f"<i>{_escape_html(m.group(1))}</i>", line)
        # `code` → <code>
        line = re.sub(r"`([^`]+)`", lambda m: f"<code>{_escape_html(m.group(1))}</code>", line)
        # Escape remaining raw & < > in plain-text portions (already done per-token above;
        # any remaining raw chars that weren't in a tag need escaping)
        lines_out.append(line)
    text = "\n".join(lines_out)
    # Collapse excess blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _send_chunks(bot, label: str, header: str, body: str) -> None:
    html_body = _md_to_html(body)
    full = f"{header}\n\n{html_body}"
    chunks = [full[i : i + _MAX_MSG] for i in range(0, len(full), _MAX_MSG)]
    for i, chunk in enumerate(chunks, 1):
        suffix = f"\n<i>({label} — {i}/{len(chunks)})</i>" if len(chunks) > 1 else ""
        bot._send(chunk + suffix, parse_mode="HTML", disable_preview=True)


def run() -> None:
    from src.config import setup_logging, APIFY_TOKEN, TAVILY_API_KEY
    logger = setup_logging("send_1213")

    from src.storage.sqlite_store import (
        init_db, save_news, save_social_posts, save_batch_analysis,
        update_social_posts_ai, get_known_post_urls, load_recent_news,
        save_competitor_stores, load_competitor_stores,
    )
    init_db()

    # ── 1. Competitor catalogue pages ─────────────────────────────────────────
    logger.info("[1/4] Scraping competitor catalogue/promo pages")
    catalogue_data: list[dict] = []
    try:
        from src.scrapers.competitor_catalogue import scrape_competitor_catalogues
        catalogue_data = scrape_competitor_catalogues()
        logger.info(f"Catalogues: {len(catalogue_data)} brands scraped")
    except Exception as e:
        logger.error(f"Catalogue scraping failed: {e}")

    # ── 2. Competitor news ────────────────────────────────────────────────────
    logger.info("[2/4] Scraping competitor news (retail + web intel)")
    news_articles: list[dict] = []
    try:
        from src.scrapers.retail_intelligence import scrape_retail_intelligence
        from src.scrapers.web_intelligence import scrape_web_intelligence
        retail = scrape_retail_intelligence()
        web    = scrape_web_intelligence()
        news_articles = retail + web
        save_news(news_articles)
        logger.info(f"News: {len(news_articles)} articles")
    except Exception as e:
        logger.error(f"News scraping failed: {e}")

    news_articles = news_articles or load_recent_news()

    # ── 3. Instagram Apify + AI analysis ─────────────────────────────────────
    logger.info("[3/4] Instagram Apify + AI batch analysis")
    social_analysis: dict = {}
    if APIFY_TOKEN:
        try:
            from src.scrapers.instagram_scraper import scrape_instagram_apify
            from src.analysis.social_analyzer import analyze_social_batch

            posts = scrape_instagram_apify()
            if posts:
                known_urls  = get_known_post_urls()
                new_posts   = [p for p in posts if p["post_url"] not in known_urls]
                save_social_posts(posts)
                logger.info(f"Instagram: {len(posts)} fetched, {len(new_posts)} new")
                target = new_posts if new_posts else posts  # always analyze today's batch
                social_analysis = analyze_social_batch(target)
                if social_analysis:
                    save_batch_analysis(social_analysis)
                    update_social_posts_ai(social_analysis.get("posts", []))
        except Exception as e:
            logger.error(f"Instagram/AI failed: {e}")
    else:
        logger.warning("APIFY_TOKEN not set — Instagram skipped")

    # ── 4. Tavily daily brief (sends its own Telegram message) ───────────────
    if TAVILY_API_KEY:
        logger.info("[4/4] Tavily daily brief")
        try:
            from src.analysis.daily_brief import run_daily_brief
            run_daily_brief(dry_run=False, skip_alerts=False)
        except Exception as e:
            logger.error(f"Tavily brief failed: {e}")

    # ── 5. Format sections 1.2 + 1.3 ─────────────────────────────────────────
    from src.reports import (
        _section_12_competitor_intelligence,
        _section_13_commercial_activity,
    )

    s12 = _section_12_competitor_intelligence(
        news_articles, catalogue_data, social_analysis
    )
    s13 = _section_13_commercial_activity(social_analysis)

    # ── 6. Send to Telegram ───────────────────────────────────────────────────
    from src.alerts.telegram_alerts import TelegramBot
    bot = TelegramBot()

    _send_chunks(
        bot,
        label="1.2",
        header=f"🏪 <b>Aurora Romania {_TODAY} — 1.2 Публічні інфоприводи конкурентів</b>",
        body=s12,
    )

    _send_chunks(
        bot,
        label="1.3",
        header=f"🛒 <b>Aurora Romania {_TODAY} — 1.3 Комерційна активність: акції, нові продукти, відкриття</b>",
        body=s13,
    )

    logger.info("Sections 1.2 and 1.3 sent.")


if __name__ == "__main__":
    run()
