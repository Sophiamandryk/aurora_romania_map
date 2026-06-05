"""
Interactive Telegram bot for Aurora Romania Expansion Monitor.
Responds to /commands with on-demand database queries.

Runs alongside the existing push-alert system (telegram_alerts.py) —
start it with: python bot.py
"""
import asyncio
import os
import sys
from pathlib import Path

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from src.config import TELEGRAM_BOT_TOKEN, setup_logging
import src.storage.sqlite_store as db

logger = setup_logging("bot.interactive")

EMOJI = {
    "NEW_STORE": "🟢",
    "REMOVED_STORE": "🔴",
    "RELOCATED_STORE": "🔄",
    "STORE_UPDATED": "🔵",
    "POSSIBLE_FUTURE_OPENING": "🟡",
    "HIGH": "🔥",
    "MEDIUM": "📊",
    "LOW": "💡",
}

HELP_TEXT = (
    "<b>Aurora Monitor Bot — Команди</b>\n\n"
    "📍 /stores [місто] — Список магазинів Aurora\n"
    "🔄 /changes [N] — Зміни за N днів (за замовч. 7)\n"
    "💼 /jobs — Вакансії (14 днів)\n"
    "📰 /news — Новини (7 днів)\n"
    "🏪 /competitors — Магазини конкурентів\n"
    "📊 /status — Стан системи\n"
    "📋 /report — Останній щоденний звіт\n"
    "📦 /digest — Надіслати всі секції сьогоднішнього pipeline\n"
    "🚀 /run — Запустити pipeline\n"
    "❓ /help — Ця довідка"
)


def _h(text: str) -> str:
    """Escape HTML special chars for Telegram HTML parse mode."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 <b>Aurora Romania Expansion Monitor</b>\n\n"
        "Відстежую мережу Aurora та конкурентів у Румунії.\n\n"
        + HELP_TEXT,
        parse_mode=ParseMode.HTML,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        stores = db.load_snapshot()
        changes_30 = db.load_recent_changes(days=30)
        jobs = db.load_recent_jobs(days=14)
        news = db.load_recent_news(days=7)
        competitors = db.load_competitor_stores()
        unalerted = db.load_unalerted_changes()
        latest_date = db.get_latest_snapshot_date() or "—"
        competitor_total = sum(len(v) for v in competitors.values())

        lines = [
            "<b>📊 Стан системи Aurora Monitor</b>\n",
            f"🗓 <b>Останній знімок:</b> {_h(latest_date)}",
            f"📍 <b>Магазини Aurora:</b> {len(stores)}",
            f"🔄 <b>Зміни (30д):</b> {len(changes_30)}",
            f"⚠️ <b>Не оповіщено:</b> {len(unalerted)}",
            f"💼 <b>Вакансії (14д):</b> {len(jobs)}",
            f"📰 <b>Новини (7д):</b> {len(news)}",
            f"🏪 <b>Конкуренти — магазини:</b> {competitor_total}",
        ]
        if competitors:
            lines.append("\n<b>Конкуренти:</b>")
            for brand in sorted(competitors):
                lines.append(f"  • {_h(brand)}: {len(competitors[brand])}")

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"/status error: {e}")
        await update.message.reply_text(f"❌ Помилка: {_h(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_stores(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    city_filter = " ".join(context.args).strip().lower() if context.args else ""
    try:
        stores = db.load_snapshot()
        if city_filter:
            stores = [s for s in stores if city_filter in (s.get("city") or "").lower()]

        if not stores:
            note = f" у місті «{_h(city_filter)}»" if city_filter else ""
            await update.message.reply_text(
                f"ℹ️ Магазини не знайдено{note}.", parse_mode=ParseMode.HTML
            )
            return

        by_city: dict[str, list] = {}
        for s in stores:
            city = s.get("city") or "Невідомо"
            by_city.setdefault(city, []).append(s)

        lines = [f"<b>📍 Магазини Aurora</b> ({len(stores)} всього)\n"]
        for city in sorted(by_city):
            city_stores = by_city[city]
            if len(city_stores) == 1:
                addr = city_stores[0].get("address") or ""
                lines.append(
                    f"• <b>{_h(city)}</b>" + (f" — {_h(addr)}" if addr else "")
                )
            else:
                lines.append(f"• <b>{_h(city)}</b> ({len(city_stores)} шт.)")
                for s in city_stores[:2]:
                    addr = s.get("address") or ""
                    if addr:
                        lines.append(f"  — {_h(addr)}")

        text = "\n".join(lines)
        # Split long responses
        for i in range(0, len(text), 4000):
            await update.message.reply_text(text[i:i + 4000], parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"/stores error: {e}")
        await update.message.reply_text(f"❌ Помилка: {_h(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_changes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        days = int(context.args[0]) if context.args else 7
        days = max(1, min(days, 90))
    except (ValueError, IndexError):
        days = 7

    try:
        changes = db.load_recent_changes(days=days)
        stale_note = ""
        if not changes:
            changes = db.load_recent_changes(days=9999)
            if not changes:
                await update.message.reply_text("ℹ️ Змін не знайдено. Запустіть /run щоб зібрати дані.")
                return
            last_date = max(c.get("detected_date") or "" for c in changes)
            stale_note = f"\n⚠️ <i>Немає нових змін за {days}д — показую останні з {_h(last_date)}. Запустіть /run.</i>"

        lines = [f"<b>🔄 Зміни</b> ({len(changes)} всього)\n"]
        for c in changes[:15]:
            ct = c.get("change_type", "")
            emoji = EMOJI.get(ct, "•")
            city = _h(c.get("city") or "?")
            det_date = c.get("detected_date") or ""
            conf = c.get("confidence_level") or ""
            conf_emoji = EMOJI.get(conf, "")
            ct_label = _h(ct.replace("_", " ").title())

            line = f"{emoji} <b>{ct_label}</b> — {city}"
            if det_date:
                line += f" <i>{_h(det_date)}</i>"
            if conf:
                line += f" {conf_emoji}<i>{_h(conf)}</i>"
            lines.append(line)

        if len(changes) > 15:
            lines.append(f"\n<i>...і ще {len(changes) - 15} змін</i>")
        if stale_note:
            lines.append(stale_note)

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"/changes error: {e}")
        await update.message.reply_text(f"❌ Помилка: {_h(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        jobs = db.load_recent_jobs(days=14)
        stale_note = ""
        if not jobs:
            jobs = db.load_recent_jobs(days=9999)
            if not jobs:
                await update.message.reply_text("ℹ️ База вакансій порожня. Запустіть /run щоб зібрати дані.")
                return
            last_date = max(j.get("scraped_date") or "" for j in jobs)
            stale_note = f"\n⚠️ <i>Дані застарілі — останнє оновлення {_h(last_date)}. Запустіть /run.</i>"

        jobs.sort(key=lambda j: j.get("signal_score", 0), reverse=True)
        last_date = max(j.get("scraped_date") or "" for j in jobs)
        lines = [f"<b>💼 Вакансії</b> ({len(jobs)}, останнє оновлення {_h(last_date)})\n"]
        for j in jobs[:10]:
            title = _h(j.get("title", "")[:55])
            cities = ", ".join(_h(c) for c in j.get("cities_mentioned", [])[:3])
            score = j.get("signal_score", 0)
            source = _h(j.get("source", ""))
            score_icon = "🔴" if score >= 3 else "🟡" if score >= 2 else "🟢"

            lines.append(f"{score_icon} <b>{title}</b>")
            if cities:
                lines.append(f"   📍 {cities}")
            if source:
                lines.append(f"   <i>{source}</i>")
            lines.append("")

        if len(jobs) > 10:
            lines.append(f"<i>...і ще {len(jobs) - 10} вакансій</i>")
        if stale_note:
            lines.append(stale_note)

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"/jobs error: {e}")
        await update.message.reply_text(f"❌ Помилка: {_h(str(e))}", parse_mode=ParseMode.HTML)


async def _translate_titles(titles: list[str]) -> list[str]:
    """Batch-translate a list of titles to Ukrainian via GPT-4o-mini."""
    openai_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_key or not titles:
        return titles
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=openai_key)
        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": (
                    "Translate these news article titles to Ukrainian. "
                    "Return only the translations, one per line, numbered the same way. "
                    "Keep proper nouns and brand names as-is.\n\n" + numbered
                ),
            }],
            temperature=0.1,
            max_tokens=600,
        )
        lines = resp.choices[0].message.content.strip().split("\n")
        translated = []
        for line in lines:
            line = line.strip()
            if line and line[0].isdigit() and ". " in line[:5]:
                line = line.split(". ", 1)[1]
            if line:
                translated.append(line)
        if len(translated) == len(titles):
            return translated
    except Exception as e:
        logger.warning(f"Title translation failed: {e}")
    return titles


async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        news = db.load_recent_news(days=7)
        stale_note = ""

        # No news in last 7 days — fall back to all available news and warn user
        if not news:
            news = db.load_recent_news(days=9999)
            if not news:
                await update.message.reply_text("ℹ️ База новин порожня. Запустіть /run щоб зібрати дані.")
                return
            last_date = max(a.get("scraped_date") or "" for a in news)
            stale_note = f"\n⚠️ <i>Дані застарілі — останнє оновлення {_h(last_date)}. Запустіть /run.</i>"

        articles = news[:10]
        raw_titles = [a.get("title", "")[:120] for a in articles]
        translated = await _translate_titles(raw_titles)

        last_date = max(a.get("scraped_date") or "" for a in news)
        lines = [f"<b>📰 Новини</b> ({len(news)} статей, останнє оновлення {_h(last_date)})\n"]
        for a, title_ua in zip(articles, translated):
            title = _h(title_ua[:100])
            source = _h(a.get("source", ""))
            url = a.get("url", "")
            pub = _h(a.get("published_date") or a.get("scraped_date") or "")

            if url:
                lines.append(
                    f'• <a href="{url}">{title}</a>'
                    + (f" — <i>{source}</i>" if source else "")
                )
            else:
                lines.append(f"• <b>{title}</b>" + (f" — <i>{source}</i>" if source else ""))
            if pub:
                lines.append(f"  <i>{pub}</i>")

        if len(news) > 10:
            lines.append(f"\n<i>...і ще {len(news) - 10} новин</i>")

        if stale_note:
            lines.append(stale_note)

        await update.message.reply_text(
            "\n".join(lines)[:4090],
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"/news error: {e}")
        await update.message.reply_text(f"❌ Помилка: {_h(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_competitors(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        competitors = db.load_competitor_stores()
        if not competitors:
            await update.message.reply_text("ℹ️ Даних про конкурентів немає.")
            return

        total = sum(len(v) for v in competitors.values())
        lines = [f"<b>🏪 Магазини конкурентів</b> ({total} всього)\n"]

        for brand in sorted(competitors):
            stores = competitors[brand]
            cities: dict[str, int] = {}
            for s in stores:
                city = s.get("city") or "?"
                cities[city] = cities.get(city, 0) + 1
            top = sorted(cities.items(), key=lambda x: x[1], reverse=True)[:5]
            city_str = ", ".join(f"{_h(c)}({n})" for c, n in top)
            lines.append(f"<b>{_h(brand)}</b>: {len(stores)}")
            if city_str:
                lines.append(f"  📍 {city_str}")

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"/competitors error: {e}")
        await update.message.reply_text(f"❌ Помилка: {_h(str(e))}", parse_mode=ParseMode.HTML)


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reports_dir = Path(__file__).parent.parent.parent / "reports"
    try:
        report_files = sorted(reports_dir.glob("daily_report_*.md"), reverse=True)
        if not report_files:
            await update.message.reply_text("ℹ️ Звітів не знайдено.")
            return

        latest = report_files[0]
        content = latest.read_text(encoding="utf-8")

        header = f"📋 Звіт: {latest.stem}\n\n"
        body = content[:3700]
        text = header + body
        if len(content) > 3700:
            text += "\n\n...звіт скорочено"

        await update.message.reply_text(text[:4090])
    except Exception as e:
        logger.error(f"/report error: {e}")
        await update.message.reply_text(f"❌ Помилка: {str(e)}")


_PIPELINE_STEPS = [
    ("[1/7]", "📡 Крок 1/7 — Мапа магазинів Aurora"),
    ("[2/7]", "💼 Крок 2/7 — Вакансії та сигнали найму"),
    ("[3/7]", "📰 Крок 3/7 — Новини та веб-розвідка"),
    ("[4/7]", "📸 Крок 4/7 — Instagram"),
    ("[5/7]", "🏪 Крок 5/7 — Конкуренти"),
    ("[6/7]", "🔍 Крок 6/7 — Аналіз та оцінка змін"),
    ("[7/7]", "📤 Крок 7/7 — Алерти та звіти"),
]
_TOTAL_STEPS = len(_PIPELINE_STEPS)


def _bar(active: int) -> str:
    """active = 0-indexed step currently running. -1 = not started. _TOTAL_STEPS = all done."""
    if active < 0:
        return "⬜️" * _TOTAL_STEPS
    if active >= _TOTAL_STEPS:
        return "🟩" * _TOTAL_STEPS
    return "🟩" * active + "🔵" + "⬜️" * (_TOTAL_STEPS - active - 1)


async def _monitor_pipeline(bot, chat_id: int, message_id: int) -> None:
    project_root = Path(__file__).parent.parent.parent
    active = -1

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "main.py", "run",
            cwd=str(project_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        logger.info(f"Pipeline started via /run (PID {proc.pid})")

        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace")

            # Detect step start
            for i, (marker, label) in enumerate(_PIPELINE_STEPS):
                if marker in line and i >= active:
                    active = i
                    try:
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=(
                                f"🔄 <b>Aurora Pipeline</b>\n"
                                f"{_bar(active)}\n\n"
                                f"⏳ {label}"
                            ),
                            parse_mode=ParseMode.HTML,
                        )
                    except Exception:
                        pass
                    break

            # Detect completion
            if "Pipeline complete" in line:
                try:
                    summary = line.split("Pipeline complete:")[1].split("===")[0].strip()
                except Exception:
                    summary = ""
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=(
                            f"✅ <b>Pipeline завершено!</b>\n"
                            f"{_bar(_TOTAL_STEPS)}\n\n"
                            + (f"<i>{_h(summary)}</i>" if summary else "Алерти відправлені.")
                        ),
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass

        await proc.wait()

        if proc.returncode != 0:
            await bot.send_message(
                chat_id=chat_id,
                text=f"❌ <b>Pipeline завершився з помилкою</b> (код {proc.returncode})\nДив. logs/main.log",
                parse_mode=ParseMode.HTML,
            )
    except Exception as e:
        logger.error(f"Pipeline monitor error: {e}")
        await bot.send_message(
            chat_id=chat_id,
            text=f"❌ <b>Помилка моніторингу:</b> {_h(str(e))}",
            parse_mode=ParseMode.HTML,
        )


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text(
        f"🚀 <b>Aurora Pipeline</b>\n"
        f"{_bar(-1)}\n\n"
        f"⏳ Запускаю...",
        parse_mode=ParseMode.HTML,
    )
    asyncio.create_task(
        _monitor_pipeline(context.bot, update.effective_chat.id, msg.message_id)
    )


async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send all sections from today's (or latest) aurora_output JSON."""
    from datetime import date as _date
    import sys as _sys

    data_dir = Path(__file__).parent.parent.parent / "data"
    today = _date.today().isoformat()

    # Find today's file, fall back to most recent
    output_file = data_dir / f"aurora_output_{today}.json"
    if not output_file.exists():
        candidates = sorted(data_dir.glob("aurora_output_*.json"), reverse=True)
        if not candidates:
            await update.message.reply_text("ℹ️ Даних pipeline ще немає. Запустіть /run.")
            return
        output_file = candidates[0]

    try:
        import json as _json
        data = _json.loads(output_file.read_text(encoding="utf-8"))
    except Exception as e:
        await update.message.reply_text(f"❌ Не вдалося прочитати файл: {_h(str(e))}")
        return

    file_date = output_file.stem.replace("aurora_output_", "")
    await update.message.reply_text(
        f"📦 <b>Дайджест Aurora — {_h(file_date)}</b>\n"
        f"Надсилаю {len([k for k in data if not k.startswith('_')])} секцій...",
        parse_mode=ParseMode.HTML,
    )

    # Import the formatters from main.py
    _sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    try:
        from main import (
            _fmt_competitor_intel, _fmt_commercial_activity,
            _fmt_macro_environment, _fmt_retail_news,
            _fmt_industry_research, _fmt_corporate_news,
            _fmt_network_expansion,
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Помилка імпорту форматерів: {_h(str(e))}")
        return

    sections = [
        ("1.2_competitor_intelligence",  lambda d: _fmt_competitor_intel(d, file_date)),
        ("1.3_commercial_activity",      lambda d: _fmt_commercial_activity(d, file_date)),
        ("2.1_macro_environment",        lambda d: _fmt_macro_environment(d, file_date)),
        ("2.2_retail_news",              lambda d: _fmt_retail_news(d, file_date)),
        ("2.3_industry_research",        lambda d: _fmt_industry_research(d, file_date)),
        ("3.1_corporate_news",           lambda d: _fmt_corporate_news(d, file_date)),
        ("3.2_network_expansion_ro",     lambda d: _fmt_network_expansion(d, file_date)),
    ]

    sent = 0
    for key, fmt in sections:
        section_data = data.get(key)
        if section_data is None:
            continue
        try:
            result = fmt(section_data)
            # _fmt_industry_research returns a list of messages; others return a string
            texts = result if isinstance(result, list) else [result]
            texts = [t for t in texts if t and t.strip()]
            if not texts:
                continue
        except Exception as e:
            logger.warning(f"digest: formatter error {key}: {e}")
            continue

        for text in texts:
            text = text[:4090]
            try:
                await update.message.reply_text(
                    text, parse_mode="Markdown", disable_web_page_preview=True
                )
            except Exception:
                try:
                    await update.message.reply_text(text, disable_web_page_preview=True)
                except Exception as e2:
                    logger.warning(f"digest: send failed {key}: {e2}")
                    continue
            sent += 1
            await asyncio.sleep(0.8)

    await update.message.reply_text(f"✅ Надіслано {sent} секцій.")


def build_app(token: str) -> Application:
    app = Application.builder().token(token).build()
    for cmd, handler in [
        ("start", cmd_start),
        ("help", cmd_help),
        ("status", cmd_status),
        ("stores", cmd_stores),
        ("changes", cmd_changes),
        ("jobs", cmd_jobs),
        ("news", cmd_news),
        ("competitors", cmd_competitors),
        ("report", cmd_report),
        ("run", cmd_run),
        ("digest", cmd_digest),
    ]:
        app.add_handler(CommandHandler(cmd, handler))
    return app


def main() -> None:
    token = os.getenv("TELEGRAM_INTERACTIVE_BOT_TOKEN") or TELEGRAM_BOT_TOKEN
    if not token:
        raise SystemExit(
            "Set TELEGRAM_INTERACTIVE_BOT_TOKEN (or TELEGRAM_BOT_TOKEN) in .env"
        )
    app = build_app(token)
    logger.info("Aurora interactive bot polling...")
    app.run_polling(allowed_updates=["message"])
