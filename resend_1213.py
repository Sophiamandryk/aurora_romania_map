#!/usr/bin/env python3
"""
Format and send sections 1.2 + 1.3 using data already in DB.
- Catalogue: re-scrape (fast, ~30s)
- News: load from DB (already scraped)
- Tavily results: load from DB → AI competitor intelligence synthesis (primary source for 1.2)
- Social posts: load today's from DB → re-run AI analysis (no Apify call)
- Send via Telegram HTML
"""
import re
import sqlite3
import json
from datetime import date

_TODAY = date.today().isoformat()
_MAX_MSG = 4000


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Pattern matching all inline markdown constructs
_INLINE_RE = re.compile(
    r"\[\[([^\]]*)\]\(([^)]*)\)\]"   # [[text](url)]
    r"|\[([^\]]+)\]\(([^)]+)\)"       # [text](url)
    r"|\*\*([^*\n]+?)\*\*"            # **bold**
    r"|(?<!\*)\*([^*\n]+?)\*(?!\*)"   # *bold*
    r"|_([^_\n]+?)_"                  # _italic_
    r"|`([^`\n]+?)`"                  # `code`
)


def _inline_to_html(text: str) -> str:
    """Convert inline markdown to HTML, escaping all plain text segments."""
    result: list[str] = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        result.append(_esc(text[pos : m.start()]))
        pos = m.end()
        g = m.groups()
        if g[0] is not None:   # [[text](url)]
            result.append(f'<a href="{g[1]}">{_esc(g[0])}</a>')
        elif g[2] is not None: # [text](url)
            result.append(f'<a href="{g[3]}">{_esc(g[2])}</a>')
        elif g[4] is not None: # **bold**
            result.append(f"<b>{_esc(g[4])}</b>")
        elif g[5] is not None: # *bold*
            result.append(f"<b>{_esc(g[5])}</b>")
        elif g[6] is not None: # _italic_
            result.append(f"<i>{_esc(g[6])}</i>")
        elif g[7] is not None: # `code`
            result.append(f"<code>{_esc(g[7])}</code>")
    result.append(_esc(text[pos:]))
    return "".join(result)


def _md_to_html(text: str) -> str:
    lines_out: list[str] = []
    for line in text.splitlines():
        if re.match(r"^---+$", line):
            continue
        m = re.match(r"^#{2,6}\s+(.+)$", line)
        if m:
            lines_out.append(f"<b>{_esc(m.group(1))}</b>")
            continue
        m = re.match(r"^>\s*(.*)$", line)
        if m:
            lines_out.append(f"<i>{_esc(m.group(1))}</i>")
            continue
        lines_out.append(_inline_to_html(line))

    text = "\n".join(lines_out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _send_chunks(bot, label: str, header: str, body: str) -> None:
    html_body = _md_to_html(body)
    full = f"{header}\n\n{html_body}"
    chunks = [full[i : i + _MAX_MSG] for i in range(0, len(full), _MAX_MSG)]
    for i, chunk in enumerate(chunks, 1):
        suffix = f"\n<i>({label} — {i}/{len(chunks)})</i>" if len(chunks) > 1 else ""
        bot._send(chunk + suffix, parse_mode="HTML", disable_preview=True)


# ── Main ─────────────────────────────────────────────────────────────────────

def run() -> None:
    from src.config import setup_logging, DB_PATH, OPENAI_API_KEY
    logger = setup_logging("resend_1213")

    from src.storage.sqlite_store import init_db, load_recent_news
    init_db()

    # 1. Catalogue (re-scrape; fast for non-Playwright sources)
    logger.info("[1/3] Re-scraping competitor catalogues")
    catalogue_data: list[dict] = []
    try:
        from src.scrapers.competitor_catalogue import scrape_competitor_catalogues
        catalogue_data = scrape_competitor_catalogues()
        logger.info(f"Catalogues: {len(catalogue_data)} brands")
    except Exception as e:
        logger.error(f"Catalogue scraping failed: {e}")

    # 2. News from DB
    news_articles = load_recent_news(days=1)
    logger.info(f"News from DB: {len(news_articles)} articles")

    # 3. Today's social posts from DB → AI analysis
    logger.info("[3/3] Loading today's posts from DB + running AI analysis")
    social_analysis: dict = {}
    if OPENAI_API_KEY:
        try:
            conn = sqlite3.connect(str(DB_PATH))
            rows = conn.execute(
                """
                SELECT competitor, post_url, caption, likes, comments, is_own
                FROM social_posts
                WHERE DATE(created_at) = ?
                ORDER BY created_at DESC
                """,
                (_TODAY,),
            ).fetchall()
            conn.close()

            posts = [
                {
                    "brand":    r[0] or "",
                    "post_url": r[1],
                    "caption":  r[2] or "",
                    "likes":    r[3] or 0,
                    "comments": r[4] or 0,
                    "is_own":   bool(r[5]),
                }
                for r in rows
            ]
            logger.info(f"Social posts from DB: {len(posts)}")
            if posts:
                from src.analysis.social_analyzer import analyze_social_batch
                social_analysis = analyze_social_batch(posts)
                logger.info(
                    f"AI analysis done: "
                    f"{len(social_analysis.get('commercial_digest', {}).get('promos', []))} promos, "
                    f"{len(social_analysis.get('commercial_digest', {}).get('openings', []))} openings"
                )
        except Exception as e:
            logger.error(f"Social analysis failed: {e}")
    else:
        logger.warning("OPENAI_API_KEY not set — skipping AI analysis")

    # 4. Macro-economic intelligence (fresh Tavily searches)
    logger.info("[4/5] Running macro-economic intelligence (Tavily → GPT-4o-mini)")
    macro_intel: dict = {}
    try:
        from src.analysis.macro_intelligence import run_macro_intelligence
        macro_intel = run_macro_intelligence()
    except Exception as e:
        logger.error(f"Macro intelligence failed: {e}")

    # 5. Load Tavily results from DB + synthesize competitor intelligence
    logger.info("[5/5] Loading Tavily results + synthesizing competitor intelligence")
    competitor_intel: dict = {}
    from src.storage.sqlite_store import load_recent_web_search
    tavily_results = load_recent_web_search(days=2)
    logger.info(f"Tavily results from DB: {len(tavily_results)}")
    if OPENAI_API_KEY and tavily_results:
        try:
            from src.analysis.competitor_intelligence import synthesize_competitor_intel
            competitor_intel = synthesize_competitor_intel(
                tavily_results=tavily_results,
                catalogue_data=catalogue_data,
                news_articles=news_articles,
                social_analysis=social_analysis,
            )
        except Exception as e:
            logger.error(f"Competitor intel synthesis failed: {e}")

    # 6. Format sections
    from src.reports import (
        _section_12_competitor_intelligence,
        _section_13_commercial_activity,
        _section_21_macro_environment,
    )
    s21 = _section_21_macro_environment(macro_intel)
    s12 = _section_12_competitor_intelligence(
        news_articles, catalogue_data, social_analysis,
        competitor_intel=competitor_intel,
    )
    s13 = _section_13_commercial_activity(social_analysis)

    # 7. Send
    from src.alerts.telegram_alerts import TelegramBot
    bot = TelegramBot()

    _send_chunks(
        bot,
        label="2.1",
        header=f"🌍 <b>Aurora Romania {_TODAY} — 2.1 Макроекономічне середовище</b>",
        body=s21,
    )
    logger.info("Sent section 2.1")

    _send_chunks(
        bot,
        label="1.2",
        header=f"🏪 <b>Aurora Romania {_TODAY} — 1.2 Публічні інфоприводи конкурентів</b>",
        body=s12,
    )
    logger.info("Sent section 1.2")

    _send_chunks(
        bot,
        label="1.3",
        header=f"🛒 <b>Aurora Romania {_TODAY} — 1.3 Комерційна активність: акції, нові продукти, відкриття</b>",
        body=s13,
    )
    logger.info("Sent section 1.3")


if __name__ == "__main__":
    run()
