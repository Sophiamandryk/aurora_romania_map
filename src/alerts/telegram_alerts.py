"""
Telegram alert system.
Sends formatted alerts for store changes and daily summaries.
"""
import json
import re
import time
from datetime import date
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    REQUEST_TIMEOUT, MAX_RETRIES, setup_logging,
)

logger = setup_logging("alerts.telegram")

EMOJI = {
    "NEW_STORE": "🟢",
    "REMOVED_STORE": "🔴",
    "RELOCATED_STORE": "🔄",
    "STORE_UPDATED": "🔵",
    "POSSIBLE_FUTURE_OPENING": "🟡",
    "POSSIBLE_REBRANDING": "🟠",
    "NEW_STORE_FORMAT": "🟣",
    "HIGH": "🔥",
    "MEDIUM": "📊",
    "LOW": "💡",
}


def _classify_prediction_local(change: dict) -> str:
    """'aurora_signal' or 'market_opportunity' — local copy to avoid circular import."""
    evidence = change.get("evidence", {})
    for sig in evidence.get("aurora_signals", []):
        src = sig.get("source", "")
        url = sig.get("url", "")
        if src not in ("linkedin",) and "ejobs.ro" not in url and "bestjobs" not in url:
            return "aurora_signal"
        if src in ("aurora_news", "instagram", "aurora_map"):
            return "aurora_signal"
    return "market_opportunity"


def _should_send_future_opening(change: dict) -> tuple[bool, str]:
    """
    Returns (should_send, suppression_reason).
    Individual POSSIBLE_FUTURE_OPENING alerts only for HIGH-confidence Aurora-specific
    signals with real non-job evidence.
    All others are batched into the daily executive summary.
    """
    city       = change.get("city", "?")
    confidence = change.get("confidence", {})
    level      = (confidence.get("level") or "").strip()
    score      = confidence.get("score", 0)

    if not level:
        return False, f"Confidence empty for {city} — suppressed"

    if not change.get("aurora_specific"):
        return False, f"{city}: market-only signal — batched into daily summary"

    if level != "HIGH":
        return False, f"{city}: confidence {level} (score {score:.2f}) below HIGH threshold"

    # Must have Aurora-specific evidence beyond job listings
    if _classify_prediction_local(change) != "aurora_signal":
        return False, f"{city}: aurora_specific=True but evidence is jobs-only"

    aurora_sigs = change.get("evidence", {}).get("aurora_signals", [])
    if not aurora_sigs:
        return False, f"{city}: no aurora_signals in evidence dict"

    return True, ""


def _format_competitors(comp_analysis: dict) -> str:
    nearest = comp_analysis.get("nearest_competitors", {})
    if not nearest:
        return ""
    lines = ["*Найближчі конкуренти:*"]
    for brand, stores in nearest.items():
        if stores:
            dist = stores[0].get("distance_km", "?")
            lines.append(f"  • {brand}: {dist} км")
    return "\n".join(lines)


def _format_new_store(change: dict) -> str:
    store         = change.get("store") or {}
    confidence    = change.get("confidence", {})
    comp_analysis = change.get("competitor_analysis", {})

    city       = store.get("city", "Невідомо")
    address    = store.get("address", "")
    conf_level = (confidence.get("level") or "").strip()
    conf_emoji = EMOJI.get(conf_level, "")
    conf_str   = f"{conf_emoji} {conf_level}" if conf_level else "підтверджено (мапа)"

    msg  = f"{EMOJI['NEW_STORE']} *Новий магазин Aurora виявлено*\n"
    msg += f"📍 *Місто:* {city}\n"
    if address:
        msg += f"🏠 *Адреса:* {address}\n"
    msg += f"📡 *Джерело:* карта магазинів\n"
    msg += f"🎯 *Впевненість:* {conf_str}\n"
    comp_str = _format_competitors(comp_analysis)
    if comp_str:
        msg += f"\n{comp_str}\n"
    if store.get("source_url"):
        msg += f"\n🔗 [Переглянути на мапі]({store['source_url']})"
    return msg


def _format_removed_store(change: dict) -> str:
    store   = change.get("store") or {}
    city    = store.get("city", "Невідомо")
    address = store.get("address", "")

    msg  = f"{EMOJI['REMOVED_STORE']} *Можливе закриття магазину*\n"
    msg += f"📍 *Місто:* {city}\n"
    if address:
        msg += f"🏠 *Адреса:* {address}\n"
    msg += "⚠️ *Магазин зник з мапи. Потрібна ручна перевірка.*\n"
    return msg


def _format_relocated(change: dict) -> str:
    store   = change.get("store") or {}
    prev    = change.get("previous_store") or {}
    details = change.get("details", {})

    city     = store.get("city", "Невідомо")
    msg      = f"{EMOJI['RELOCATED_STORE']} *Переміщення магазину виявлено*\n"
    msg     += f"📍 *Місто:* {city}\n"
    old_addr = details.get("old_address") or prev.get("address", "")
    new_addr = details.get("new_address") or store.get("address", "")
    if old_addr:
        msg += f"📤 *Стара адреса:* {old_addr}\n"
    if new_addr:
        msg += f"📥 *Нова адреса:* {new_addr}\n"
    dist = details.get("distance_m")
    if dist:
        msg += f"📏 *Відстань переміщення:* {dist}м\n"
    return msg


def _format_future_opening(change: dict) -> str:
    """
    Ukrainian format for HIGH-confidence Aurora-specific future openings.
    Only called when _should_send_future_opening returns True.
    """
    city       = change.get("city", "Невідомо")
    confidence = change.get("confidence", {})
    evidence   = change.get("evidence", {})

    level      = (confidence.get("level") or "невідомо").strip()
    score      = confidence.get("score", 0)
    conf_emoji = EMOJI.get(level, "💡")
    conf_str   = f"{conf_emoji} {level.upper()} (score: {score:.2f})"

    aurora_sigs = evidence.get("aurora_signals", [])
    n_aurora    = len(aurora_sigs)
    n_jobs      = evidence.get("job_count", 0)
    n_news      = evidence.get("news_count", 0)

    ev_parts = []
    if n_aurora:
        ev_parts.append(f"Aurora-статей: {n_aurora}")
    if n_jobs:
        ev_parts.append(f"вакансій: {n_jobs}")
    if n_news:
        ev_parts.append(f"новин: {n_news}")

    # What's missing
    missing = []
    if not n_aurora:
        missing.append("Aurora-специфічне джерело")
    if not n_jobs:
        missing.append("Aurora-вакансія")
    missing_str = ", ".join(missing) if missing else "—"

    msg  = f"🟡 *Сигнал Aurora — {city}*\n"
    msg += f"🎯 *Впевненість:* {conf_str}\n"
    msg += f"📋 *Докази:* {', '.join(ev_parts) if ev_parts else 'немає'}\n"
    msg += f"❓ *Відсутнє:* {missing_str}\n"
    if aurora_sigs:
        top_title = aurora_sigs[0].get("title","")[:70]
        if top_title:
            msg += f"📰 _Топ-джерело: {top_title}_\n"
    msg += f"💡 *Перевірити:* дату відкриття та офіційне підтвердження на aurora-retail.com\n"
    return msg


def _format_rebranding(change: dict) -> str:
    store   = change.get("store") or {}
    details = change.get("details", {})
    city    = store.get("city", "Невідомо")

    msg  = f"{EMOJI['POSSIBLE_REBRANDING']} *Можливий ребрендинг виявлено*\n"
    msg += f"📍 *Місто:* {city}\n"
    if "name" in details:
        msg += f"📛 *Було:* {details['name']['from']}\n"
        msg += f"📛 *Стало:* {details['name']['to']}\n"
    return msg


def _format_updated(change: dict) -> str:
    store   = change.get("store") or {}
    details = change.get("details", {})
    city    = store.get("city", "Невідомо")

    msg  = f"{EMOJI['STORE_UPDATED']} *Оновлення даних магазину*\n"
    msg += f"📍 *Місто:* {city}\n"
    for field, diff in details.items():
        if isinstance(diff, dict):
            msg += f"  • *{field}:* {diff.get('from','')} → {diff.get('to','')}\n"
    return msg


FORMATTERS = {
    "NEW_STORE": _format_new_store,
    "REMOVED_STORE": _format_removed_store,
    "RELOCATED_STORE": _format_relocated,
    "STORE_UPDATED": _format_updated,
    "POSSIBLE_FUTURE_OPENING": _format_future_opening,
    "POSSIBLE_REBRANDING": _format_rebranding,
    "NEW_STORE_FORMAT": _format_new_store,
    # Market activity signals are report-only; suppress Telegram alerts
    "MARKET_ACTIVITY_SIGNAL": None,
}


def _build_cluster_message(city: str, changes: list[dict], jobs: list[dict],
                            news: list[dict], comp_context: dict) -> str:
    """
    Single grouped intelligence message for a city where multiple signals converge.
    """
    msg = f"🔥 *Retail cluster detected — {city}*\n\n"

    for c in changes[:3]:
        ct = c.get("change_type", "")
        emoji = EMOJI.get(ct, "•")
        address = (c.get("store") or {}).get("address", "")
        msg += f"{emoji} {ct.replace('_', ' ').title()}"
        if address:
            msg += f" — {address}"
        conf = c.get("confidence", {})
        if conf.get("level"):
            msg += f" _{conf['level']}_"
        msg += "\n"

    if jobs:
        job_titles = list({j["title"][:40] for j in jobs[:3]})
        msg += f"💼 Hiring: {', '.join(job_titles)}\n"

    if news:
        for a in news[:2]:
            cat = a.get("signal_category", "")
            cat_label = {
                "aurora_direct": "Aurora news",
                "competitor_expansion": "Competitor expansion",
                "retail_park": "Retail park",
                "shopping_center": "Mall/centre",
            }.get(cat, "Retail news")
            msg += f"📰 {cat_label}: {a.get('title', '')[:60]}\n"

    for brand, dist_km in comp_context.items():
        msg += f"🏪 {brand}: {dist_km} km away\n"

    return msg


def detect_and_send_clusters(
    changes: list[dict],
    jobs: list[dict],
    news: list[dict],
    bot: "TelegramBot",
) -> set[str]:
    """
    Detect cities where 3+ signal types converge and send one grouped alert per city.
    Returns set of city names that were sent as cluster alerts.
    """
    from collections import defaultdict
    from src.data.ro_counties import normalize_city

    city_signals: dict[str, dict] = defaultdict(lambda: {
        "changes": [], "jobs": [], "news": [], "comp": {}
    })

    for c in changes:
        city = (c.get("store") or {}).get("city", "")
        if city:
            city_signals[normalize_city(city)]["changes"].append(c)

    for j in jobs:
        for city in j.get("cities_mentioned", []):
            city_signals[normalize_city(city)]["jobs"].append(j)

    for a in news:
        for city in a.get("cities_mentioned", []):
            city_signals[normalize_city(city)]["news"].append(a)

    for c in changes:
        comp = (c.get("competitor_analysis") or {}).get("nearest_competitors", {})
        city = (c.get("store") or {}).get("city", "")
        if city and comp:
            cn = normalize_city(city)
            for brand, stores in comp.items():
                if stores:
                    city_signals[cn]["comp"][brand] = stores[0].get("distance_km", "?")

    cluster_cities: set[str] = set()
    for city_norm, data in city_signals.items():
        signal_count = (
            bool(data["changes"]) +
            bool(data["jobs"]) +
            bool(data["news"]) +
            bool(data["comp"])
        )
        if signal_count >= 3:
            city_title = city_norm.title()
            msg = _build_cluster_message(
                city_title, data["changes"], data["jobs"], data["news"], data["comp"]
            )
            try:
                bot._send(msg)
                cluster_cities.add(city_norm)
                logger.info(f"Cluster alert sent: {city_title} ({signal_count} signal types)")
                time.sleep(0.5)
            except Exception as e:
                logger.error(f"Failed to send cluster alert for {city_title}: {e}")

    return cluster_cities


class TelegramBot:
    def __init__(self, token: str = TELEGRAM_BOT_TOKEN, chat_id: str = TELEGRAM_CHAT_ID):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.enabled = bool(token and chat_id)
        if not self.enabled:
            logger.warning("Telegram not configured — alerts will be logged only")

    @retry(stop=stop_after_attempt(MAX_RETRIES), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _send(self, text: str, parse_mode: str = "Markdown", disable_preview: bool = False) -> bool:
        if not self.enabled:
            logger.info(f"[Telegram MOCK] {text[:100]}...")
            return True
        resp = requests.post(
            f"{self.base_url}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": disable_preview,
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return True

    def send_change_alert(self, change: dict) -> bool:
        change_type = change.get("change_type", "")
        if change_type not in FORMATTERS:
            logger.warning(f"No formatter for change type: {change_type}")
            return False
        formatter = FORMATTERS[change_type]
        if formatter is None:
            return False

        # Gate: POSSIBLE_FUTURE_OPENING requires HIGH confidence + real Aurora evidence
        if change_type == "POSSIBLE_FUTURE_OPENING":
            should, reason = _should_send_future_opening(change)
            if not should:
                logger.info(f"Alert suppressed ({reason})")
                return False

        try:
            msg = formatter(change)
            result = self._send(msg)
            logger.info(f"Sent alert: {change_type}")
            return result
        except Exception as e:
            logger.error(f"Failed to send alert for {change_type}: {e}")
            return False

    def send_daily_summary(self, executive_summary: str) -> bool:
        """Send the AI-generated executive summary to Telegram."""
        try:
            return self._send(executive_summary)
        except Exception as e:
            logger.error(f"Failed to send daily summary: {e}")
            return False

    def send_alerts_batch(self, changes: list[dict], jobs: list[dict] = None,
                          news: list[dict] = None) -> list[int]:
        """
        Send alerts for all changes.
        Cities with 3+ signal types get one grouped cluster alert instead of N individual ones.
        Returns list of alerted change IDs.
        """
        from src.data.ro_counties import normalize_city

        # First, detect and send cluster alerts
        cluster_cities = set()
        if jobs or news:
            cluster_cities = detect_and_send_clusters(changes, jobs or [], news or [], self)

        alerted_ids = []
        for change in changes:
            city = normalize_city((change.get("store") or {}).get("city", ""))
            if city in cluster_cities:
                # Already covered by cluster alert — just mark as alerted
                if change.get("id"):
                    alerted_ids.append(change["id"])
                continue

            success = self.send_change_alert(change)
            if success and change.get("id"):
                alerted_ids.append(change["id"])
            time.sleep(0.5)

        logger.info(
            f"Sent {len(alerted_ids)}/{len(changes)} alerts "
            f"({len(cluster_cities)} cities via cluster alerts)"
        )
        return alerted_ids

    def test_connection(self) -> bool:
        try:
            resp = requests.get(f"{self.base_url}/getMe", timeout=10)
            data = resp.json()
            if data.get("ok"):
                bot_name = data["result"].get("username", "")
                logger.info(f"Telegram connected: @{bot_name}")
                return True
            return False
        except Exception as e:
            logger.error(f"Telegram connection test failed: {e}")
            return False


def _format_instagram_signal(post: dict) -> str:
    brand   = post.get("brand") or "Aurora"
    stype   = post.get("signal_type", "").replace("_", " ").title()
    cities  = ", ".join((post.get("cities_mentioned") or [])[:3])
    malls   = ", ".join((post.get("detected_malls") or [])[:2])
    score   = post.get("signal_score", 0)
    url     = post.get("url", "")
    reason  = post.get("reason", "")
    caption = (post.get("caption") or "")[:120]
    has_loc = bool(cities or malls)

    msg  = f"📸 *Instagram — {brand}*\n"
    msg += f"🔍 *Тип:* {stype} (score: {score})\n"
    if cities:
        msg += f"📍 *Міста:* {cities}\n"
    if malls:
        msg += f"🏬 *ТЦ/парк:* {malls}\n"
    if not has_loc:
        msg += "⚠️ _Контент про відкриття, але місце не визначено — потрібна ручна перевірка_\n"
    if reason:
        msg += f"💡 *Причина:* {reason}\n"
    if caption:
        msg += f"📝 _{caption}_\n"
    if url:
        msg += f"\n🔗 [Переглянути пост]({url})"
    return msg


def send_instagram_alerts(posts: list[dict]) -> None:
    from src.scrapers.aurora_instagram import SIGNAL_SCORE_THRESHOLD

    _ALERT_TYPES = {"confirmed_opening_signal", "possible_store_location_signal"}
    _ALERT_COMPETITOR_TYPES = {"confirmed_opening_signal", "mall_or_retail_park_signal"}

    bot = TelegramBot()
    sent = 0
    no_location_count = 0

    for post in posts:
        stype         = post.get("signal_type", "")
        score         = post.get("signal_score", 0)
        is_competitor = bool(post.get("brand"))
        has_location  = bool(post.get("cities_mentioned") or post.get("detected_malls"))

        should_alert = (
            (not is_competitor and stype in _ALERT_TYPES and score >= SIGNAL_SCORE_THRESHOLD)
            or (is_competitor and stype in _ALERT_COMPETITOR_TYPES and score >= SIGNAL_SCORE_THRESHOLD)
        )

        # Suppress individual alert if no location — it appears in executive summary instead
        if should_alert and not has_location and not is_competitor:
            no_location_count += 1
            logger.info(
                f"Instagram signal suppressed (no location): "
                f"{stype} score={score} url={post.get('url','')[:60]}"
            )
            continue

        if should_alert:
            try:
                bot._send(_format_instagram_signal(post))
                sent += 1
                time.sleep(0.5)
            except Exception as e:
                logger.error(f"Instagram alert failed: {e}")

    if sent:
        logger.info(f"Sent {sent} Instagram signal alerts")
    if no_location_count:
        logger.info(
            f"{no_location_count} Instagram opening signal(s) suppressed (no location) — "
            "included in daily executive summary"
        )


def send_alerts(changes: list[dict], jobs: list[dict] = None,
                news: list[dict] = None, instagram_posts: list[dict] = None) -> list[int]:
    bot = TelegramBot()
    alerted_ids = bot.send_alerts_batch(changes, jobs=jobs, news=news)
    # Per-post Instagram signal alerts disabled — only the daily digest is sent
    return alerted_ids


def send_daily_summary(executive_summary: str) -> bool:
    bot = TelegramBot()
    return bot.send_daily_summary(executive_summary)


def _format_narrative(narrative: str) -> str:
    # Strip AI-generated header prefixes like "[20.05.2026 15:50] АВРОРА РУМУНІЯ: "
    narrative = re.sub(r'^\s*\[[\d./:,\s]+\][^\n]*\n*', '', narrative).strip()

    # Ensure double newlines between paragraphs
    narrative = re.sub(r'\n+', '\n\n', narrative)

    # Convert (url) → ([пост](url))
    narrative = re.sub(r'\((https?://[^\s)]+)\)', r'([пост](\1))', narrative)
    # Convert any remaining bare url (not already inside a markdown link) → [пост](url)
    narrative = re.sub(r'(?<!\]\()(https?://[^\s)]+)', r'[пост](\1)', narrative)

    return narrative


def send_social_batch_alert(analysis: dict) -> bool:
    """
    Send one daily Telegram message with the Ukrainian narrative Instagram briefing.
    Skips if no narrative was generated (all posts were noise).
    """
    if not analysis:
        return False

    narrative = _format_narrative((analysis.get("daily_narrative") or "").strip())
    if not narrative:
        logger.info("Social batch alert skipped: no narrative generated (all posts noise)")
        return False

    msg = f"📸 *Instagram-дайджест — {date.today().isoformat()}*\n\n{narrative}"

    bot = TelegramBot()
    try:
        # disable_preview=True because the narrative contains multiple post URLs
        result = bot._send(msg, disable_preview=True)
        relevant_count = sum(1 for p in analysis.get("posts", []) if p.get("is_relevant"))
        total = analysis.get("post_count", len(analysis.get("posts", [])))
        logger.info(f"Daily Instagram narrative sent ({relevant_count} relevant of {total} posts)")
        return result
    except Exception as e:
        logger.error(f"Failed to send daily Instagram narrative: {e}")
        return False


def send_social_signal_alerts(posts: list[dict]) -> int:
    """
    Send a Telegram alert for each social post that matched signal keywords.
    Returns the number of alerts sent.
    """
    bot = TelegramBot()
    sent = 0
    for post in posts:
        keywords = post.get("keywords_matched", [])
        if not keywords:
            continue
        competitor = post.get("competitor", "?")
        caption    = (post.get("caption") or "")[:200]
        post_url   = post.get("post_url", "")
        kw_str     = ", ".join(keywords[:5])

        msg = (
            f"📸 *Instagram signal — {competitor}*\n"
            f"🔍 {caption}\n"
            f"🔗 {post_url}\n"
            f"❗ *Keywords matched:* {kw_str}"
        )
        try:
            bot._send(msg)
            sent += 1
            time.sleep(0.5)
        except Exception as e:
            logger.error(f"Failed to send social signal alert for {competitor}: {e}")

    logger.info(f"Social signal alerts sent: {sent}")
    return sent
