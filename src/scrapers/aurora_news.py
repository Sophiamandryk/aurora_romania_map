"""
Aurora official news page scraper.
Extracts announcements about new stores, openings, expansions, rebranding.
"""
import re
import time
from datetime import date
from typing import Optional

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import (
    AURORA_NEWS_URL, HEADERS, REQUEST_TIMEOUT,
    MAX_RETRIES, REQUEST_DELAY, HEADLESS, setup_logging,
)

logger = setup_logging("scraper.aurora_news")

EXPANSION_KEYWORDS = [
    "deschid", "deschidere", "nou magazin", "new store", "opening",
    "inaugurare", "extindere", "expansion", "relocare", "relocation",
    "rebranding", "rebrand", "format", "concept", "inaugurăm",
    "vă anunțăm", "vom deschide", "prelegere",
]


def _extract_expansion_signals(text: str) -> list[str]:
    signals = []
    text_lower = text.lower()
    for kw in EXPANSION_KEYWORDS:
        if kw in text_lower:
            signals.append(kw)
    return signals


class AuroraNewsScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    @retry(stop=stop_after_attempt(MAX_RETRIES), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _fetch(self, url: str) -> str:
        resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.text

    def _parse_articles(self, html: str, base_url: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        articles = []

        # Common article container selectors
        containers = (
            soup.find_all("article")
            or soup.find_all(class_=re.compile(r"news[-_]?item|post[-_]?item|article[-_]?card", re.I))
            or soup.find_all(class_=re.compile(r"blog[-_]?post|news[-_]?card|entry", re.I))
        )

        if not containers:
            # Fallback: find any linked heading within content area
            content = soup.find(id=re.compile(r"content|main|news", re.I)) or soup
            containers = content.find_all(["h2", "h3"], limit=50)

        today = date.today().isoformat()

        for el in containers:
            try:
                # Title
                title_tag = (
                    el.find(["h1", "h2", "h3", "h4"])
                    or el.find(class_=re.compile(r"title|heading", re.I))
                )
                title = title_tag.get_text(strip=True) if title_tag else el.get_text(strip=True)[:120]

                # Link
                link_tag = el.find("a", href=True) or (el if el.name == "a" else None)
                link = ""
                if link_tag:
                    href = link_tag["href"]
                    if href.startswith("http"):
                        link = href
                    elif href.startswith("/"):
                        link = "https://aurora-retail.com" + href

                # Date
                date_tag = (
                    el.find("time")
                    or el.find(class_=re.compile(r"date|time|published", re.I))
                )
                pub_date = today
                if date_tag:
                    pub_date = date_tag.get("datetime", date_tag.get_text(strip=True))[:10] or today

                # Body / excerpt
                body_tag = el.find(class_=re.compile(r"excerpt|summary|desc|body|content", re.I))
                body = body_tag.get_text(strip=True) if body_tag else ""

                full_text = f"{title} {body}"
                signals = _extract_expansion_signals(full_text)

                articles.append({
                    "title": title,
                    "url": link,
                    "published_date": pub_date,
                    "excerpt": body[:400],
                    "expansion_signals": signals,
                    "source": "aurora_news",
                    "scraped_date": today,
                })
            except Exception as e:
                logger.debug(f"Article parse error: {e}")
                continue

        return articles

    def _playwright_scrape(self) -> list[dict]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error("Playwright not installed")
            return []

        logger.info("Using Playwright for news page")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=HEADLESS)
            page = browser.new_page(extra_http_headers=HEADERS)
            try:
                page.goto(AURORA_NEWS_URL, timeout=60000, wait_until="networkidle")
                time.sleep(2)
                html = page.content()
            finally:
                browser.close()
        return self._parse_articles(html, AURORA_NEWS_URL)

    def scrape(self) -> list[dict]:
        logger.info(f"Scraping Aurora news: {AURORA_NEWS_URL}")
        try:
            html = self._fetch(AURORA_NEWS_URL)
            articles = self._parse_articles(html, AURORA_NEWS_URL)
        except Exception as e:
            logger.warning(f"Static news fetch failed: {e}, trying Playwright")
            articles = []

        if not articles:
            articles = self._playwright_scrape()

        logger.info(f"Scraped {len(articles)} news articles")
        time.sleep(REQUEST_DELAY)
        return articles


def scrape_aurora_news() -> list[dict]:
    return AuroraNewsScraper().scrape()
