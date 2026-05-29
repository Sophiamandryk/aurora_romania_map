#!/usr/bin/env python3
"""
Aurora Romania — daily full digest sender.
Runs all sections in order and sends each as a separate Telegram message.

Sections (in send order):
  1.2  Публічні інфоприводи конкурентів
  1.3  Комерційна активність (Instagram)
  2.1  Макроекономічне середовище
  2.2  Retail news
  2.3  Industry research
  3.1  Корпоративні новини
  3.2  Розширення мережі (diff)

Usage:
  python send_daily.py
  python send_daily.py --dry-run           # print to stdout, no Telegram
  python send_daily.py --skip 2.2,2.3      # skip slow research modules
"""
import argparse
import re
import sqlite3
import sys
from datetime import date

_TODAY = date.today().isoformat()
_MAX_MSG = 4000


# ── HTML helpers (identical to resend_1213.py) ────────────────────────────────

def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_INLINE_RE = re.compile(
    r"\[\[([^\]]*)\]\(([^)]*)\)\]"
    r"|\[([^\]]+)\]\(([^)]+)\)"
    r"|\*\*([^*\n]+?)\*\*"
    r"|(?<!\*)\*([^*\n]+?)\*(?!\*)"
    r"|_([^_\n]+?)_"
    r"|`([^`\n]+?)`"
)


def _inline_to_html(text: str) -> str:
    result: list[str] = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        result.append(_esc(text[pos: m.start()]))
        pos = m.end()
        g = m.groups()
        if   g[0] is not None: result.append(f'<a href="{g[1]}">{_esc(g[0])}</a>')
        elif g[2] is not None: result.append(f'<a href="{g[3]}">{_esc(g[2])}</a>')
        elif g[4] is not None: result.append(f"<b>{_esc(g[4])}</b>")
        elif g[5] is not None: result.append(f"<b>{_esc(g[5])}</b>")
        elif g[6] is not None: result.append(f"<i>{_esc(g[6])}</i>")
        elif g[7] is not None: result.append(f"<code>{_esc(g[7])}</code>")
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
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines_out)).strip()


def _send(bot, label: str, header: str, body: str, dry_run: bool) -> None:
    html_body = _md_to_html(body)
    full = f"{header}\n\n{html_body}"
    chunks = [full[i: i + _MAX_MSG] for i in range(0, len(full), _MAX_MSG)]
    for i, chunk in enumerate(chunks, 1):
        suffix = f"\n<i>({label} — {i}/{len(chunks)})</i>" if len(chunks) > 1 else ""
        msg = chunk + suffix
        if dry_run:
            print(f"\n{'='*60}\n{msg}\n")
        else:
            bot._send(msg, parse_mode="HTML", disable_preview=True)


# ── Section formatters ────────────────────────────────────────────────────────

def _fmt_22_retail_news(items: list[dict]) -> str:
    if not items:
        return "_Новин не знайдено._"
    parts = []
    for item in items:
        days = item.get("days_used", 1)
        n    = item.get("results_count", 0)
        note = f"_(за {days} дн., {n} джерел)_" if n else ""
        parts.append(f"**{item['label']}** {note}\n{item['summary']}")
    return "\n\n".join(parts)


def _fmt_23_industry_research(items: list[dict]) -> str:
    if not items:
        return "_Досліджень не знайдено._"
    parts = []
    for item in items:
        days = item.get("days_used", 7)
        n    = item.get("results_count", 0)
        note = f"_(за {days} дн., {n} джерел)_" if n else ""
        parts.append(f"**{item['label']}** {note}\n{item['summary']}")
    return "\n\n".join(parts)


def _fmt_31_corporate_news(section: dict) -> str:
    items = section.get("items", [])
    if not items:
        return "_Корпоративних новин не знайдено._"
    days = section.get("actual_days_searched", 1)
    lines = [f"_Пошук за {days} дн._\n"]
    for i, item in enumerate(items, 1):
        src     = item.get("source_name", "")
        summary = item.get("summary_uk", "")
        link    = item.get("telegram_link", "")
        lines.append(f"{i}. {link}\n_{src}_ — {summary}")
    return "\n\n".join(lines)


def _fmt_32_network_expansion(section: dict) -> str:
    if not section.get("changes_detected"):
        return section.get("message", "Змін не виявлено.")

    lines: list[str] = []
    for brand, counts in section.get("by_brand", {}).items():
        parts = []
        if counts.get("opened"):    parts.append(f"+{counts['opened']} відкрито")
        if counts.get("closed"):    parts.append(f"-{counts['closed']} закрито")
        if counts.get("relocated"): parts.append(f"{counts['relocated']} переїхало")
        if counts.get("rebranded"): parts.append(f"{counts['rebranded']} ребрендинг")
        if parts:
            lines.append(f"**{brand}**: {', '.join(parts)}")

    details = section.get("details", [])
    if details:
        lines.append("")
        for d in details:
            t     = d.get("type", "")
            brand = d.get("brand", "")
            if t == "opened":
                lines.append(f"• {brand} — відкрито: {d.get('location', '')}")
            elif t == "closed":
                lines.append(f"• {brand} — закрито: {d.get('location', '')}")
            elif t == "relocated":
                lines.append(f"• {brand} — переїхав: {d.get('from', '')} → {d.get('to', '')}")
            elif t == "rebranded":
                lines.append(f"• {brand} — ребрендинг: {d.get('from_name', '')} → {d.get('to_name', '')}")

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False, skip: set[str] = None) -> None:
    skip = skip or set()

    from src.config import setup_logging, OPENAI_API_KEY, TAVILY_API_KEY
    logger = setup_logging("send_daily")

    from src.storage.sqlite_store import init_db, load_recent_news, load_recent_web_search
    init_db()

    bot = None
    if not dry_run:
        from src.alerts.telegram_alerts import TelegramBot
        bot = TelegramBot()

    # ── Load shared data (fast — DB only) ─────────────────────────────────────
    logger.info("[data] Loading catalogue, news, social posts from DB")

    catalogue_data: list[dict] = []
    try:
        from src.scrapers.competitor_catalogue import scrape_competitor_catalogues
        catalogue_data = scrape_competitor_catalogues()
        logger.info(f"Catalogues: {len(catalogue_data)} brands")
    except Exception as e:
        logger.error(f"Catalogue scrape failed: {e}")

    news_articles = load_recent_news(days=1)
    logger.info(f"News from DB: {len(news_articles)} articles")

    social_analysis: dict = {}
    if OPENAI_API_KEY:
        try:
            conn = sqlite3.connect(str(__import__("src.config", fromlist=["DB_PATH"]).DB_PATH))
            rows = conn.execute(
                "SELECT competitor, post_url, caption, likes, comments, is_own "
                "FROM social_posts WHERE DATE(created_at) = ? ORDER BY created_at DESC",
                (_TODAY,),
            ).fetchall()
            conn.close()
            posts = [
                {"brand": r[0] or "", "post_url": r[1], "caption": r[2] or "",
                 "likes": r[3] or 0, "comments": r[4] or 0, "is_own": bool(r[5])}
                for r in rows
            ]
            logger.info(f"Social posts from DB: {len(posts)}")
            if posts:
                from src.analysis.social_analyzer import analyze_social_batch
                social_analysis = analyze_social_batch(posts)
        except Exception as e:
            logger.error(f"Social analysis failed: {e}")

    # ── 2.1 Macro environment ─────────────────────────────────────────────────
    if "2.1" not in skip:
        logger.info("[2.1] Macro intelligence")
        macro_intel: dict = {}
        try:
            from src.analysis.macro_intelligence import run_macro_intelligence
            macro_intel = run_macro_intelligence()
        except Exception as e:
            logger.error(f"Macro intelligence failed: {e}")

        from src.reports import _section_21_macro_environment
        _send(bot, "2.1",
              f"🌍 <b>Aurora Romania {_TODAY} — 2.1 Макроекономічне середовище</b>",
              _section_21_macro_environment(macro_intel), dry_run)
        logger.info("2.1 sent")

    # ── 1.2 Competitor intelligence ───────────────────────────────────────────
    if "1.2" not in skip:
        logger.info("[1.2] Competitor intelligence")
        competitor_intel: dict = {}
        tavily_results = load_recent_web_search(days=2)
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

        from src.reports import _section_12_competitor_intelligence
        _send(bot, "1.2",
              f"🏪 <b>Aurora Romania {_TODAY} — 1.2 Публічні інфоприводи конкурентів</b>",
              _section_12_competitor_intelligence(
                  news_articles, catalogue_data, social_analysis,
                  competitor_intel=competitor_intel,
              ), dry_run)
        logger.info("1.2 sent")

    # ── 1.3 Commercial activity ───────────────────────────────────────────────
    if "1.3" not in skip:
        logger.info("[1.3] Commercial activity")
        from src.reports import _section_13_commercial_activity
        _send(bot, "1.3",
              f"🛒 <b>Aurora Romania {_TODAY} — 1.3 Комерційна активність</b>",
              _section_13_commercial_activity(social_analysis), dry_run)
        logger.info("1.3 sent")

    # ── 2.2 Retail news ───────────────────────────────────────────────────────
    if "2.2" not in skip:
        if not TAVILY_API_KEY:
            logger.info("2.2 skipped — TAVILY_API_KEY not set")
        else:
            logger.info("[2.2] Retail news (Tavily × 6 sub-topics)")
            try:
                from modules.retail_news import run as run_retail_news
                retail_news_items = run_retail_news()
                _send(bot, "2.2",
                      f"📰 <b>Aurora Romania {_TODAY} — 2.2 Retail News</b>",
                      _fmt_22_retail_news(retail_news_items), dry_run)
                logger.info("2.2 sent")
            except Exception as e:
                logger.error(f"Retail news failed: {e}")

    # ── 2.3 Industry research ─────────────────────────────────────────────────
    if "2.3" not in skip:
        if not TAVILY_API_KEY:
            logger.info("2.3 skipped — TAVILY_API_KEY not set")
        else:
            logger.info("[2.3] Industry research (Tavily × 6 sub-topics)")
            try:
                from modules.industry_research import run as run_industry_research
                industry_items = run_industry_research()
                _send(bot, "2.3",
                      f"🔬 <b>Aurora Romania {_TODAY} — 2.3 Industry Research</b>",
                      _fmt_23_industry_research(industry_items), dry_run)
                logger.info("2.3 sent")
            except Exception as e:
                logger.error(f"Industry research failed: {e}")

    # ── 3.1 Corporate news ────────────────────────────────────────────────────
    if "3.1" not in skip:
        if not TAVILY_API_KEY:
            logger.info("3.1 skipped — TAVILY_API_KEY not set")
        else:
            logger.info("[3.1] Corporate news")
            try:
                from modules.corporate_news import run as run_corporate_news
                corp_section = run_corporate_news(today=_TODAY)
                _send(bot, "3.1",
                      f"📋 <b>Aurora Romania {_TODAY} — 3.1 Корпоративні новини</b>",
                      _fmt_31_corporate_news(corp_section), dry_run)
                logger.info("3.1 sent")
            except Exception as e:
                logger.error(f"Corporate news failed: {e}")

    # ── 3.2 Network expansion diff ────────────────────────────────────────────
    if "3.2" not in skip:
        logger.info("[3.2] Network expansion diff")
        try:
            from modules.network_expansion_ro import run as run_network_expansion
            net_section = run_network_expansion(today=_TODAY)
            _send(bot, "3.2",
                  f"🗺 <b>Aurora Romania {_TODAY} — 3.2 Розширення мережі</b>",
                  _fmt_32_network_expansion(net_section), dry_run)
            logger.info("3.2 sent")
        except Exception as e:
            logger.error(f"Network expansion diff failed: {e}")

    logger.info(f"Daily digest complete — {_TODAY}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send Aurora daily digest to Telegram")
    parser.add_argument("--dry-run", action="store_true", help="Print to stdout, no Telegram")
    parser.add_argument("--skip", default="", help="Comma-separated section IDs to skip (e.g. 2.2,2.3)")
    args = parser.parse_args()

    skip_set = {s.strip() for s in args.skip.split(",") if s.strip()}
    run(dry_run=args.dry_run, skip=skip_set)
