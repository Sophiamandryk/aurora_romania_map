"""
Romanian retail news scraper.
Monitors Retail.ro, Economica.net, ZF.ro, Profit.ro for Aurora mentions,
retail park announcements, and expansion news.
"""
import re
import time
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import (
    RETAIL_NEWS_SOURCES, HEADERS, REQUEST_TIMEOUT,
    MAX_RETRIES, REQUEST_DELAY, HEADLESS, setup_logging,
)

logger = setup_logging("scraper.retail_news")

AURORA_KEYWORDS = [
    "aurora", "multimarket", "aurora retail",
]

EXPANSION_KEYWORDS = [
    "deschidere", "deschid", "inaugurare", "opening",
    "extindere", "expansion", "retail park", "shopping center",
    "parc comercial", "mall", "chirias", "tenant",
    "inchiriere", "lease", "coming soon", "in curand",
    "relocare", "rebranding",
]

COMPETITOR_KEYWORDS = ["pepco", "tedi", "kik", "action", "primark", "h&m", "zara"]


def _is_relevant(text: str) -> bool:
    text_lower = text.lower()
    has_aurora = any(kw in text_lower for kw in AURORA_KEYWORDS)
    has_expansion = any(kw in text_lower for kw in EXPANSION_KEYWORDS)
    has_competitor = any(kw in text_lower for kw in COMPETITOR_KEYWORDS)
    return has_aurora or (has_expansion and has_competitor)


def _extract_signals(text: str) -> dict:
    text_lower = text.lower()
    return {
        "aurora_mentioned": any(kw in text_lower for kw in AURORA_KEYWORDS),
        "expansion_keywords": [kw for kw in EXPANSION_KEYWORDS if kw in text_lower],
        "competitors_mentioned": [kw for kw in COMPETITOR_KEYWORDS if kw in text_lower],
    }


class RetailNewsScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    @retry(stop=stop_after_attempt(MAX_RETRIES), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _fetch(self, url: str) -> str:
        resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.text

    def _search_site(self, site_config: dict) -> list[dict]:
        name = site_config["name"]
        base_url = site_config["url"]
        search_term = site_config["search_term"]
        today = date.today().isoformat()

        # Try common search URL patterns
        search_urls = [
            f"{base_url}?s={search_term.replace(' ', '+')}",
            f"{base_url}search/{search_term.replace(' ', '-')}/",
            f"{base_url}cautare/?q={search_term.replace(' ', '+')}",
            f"{base_url}tag/aurora/",
            f"{base_url}tag/aurora-retail/",
        ]

        # Site-specific URLs
        if "retail.ro" in base_url:
            search_urls = [
                "https://www.retail.ro/?s=aurora",
                "https://www.retail.ro/tag/aurora/",
                "https://www.retail.ro/tag/aurora-retail/",
            ]
        elif "economica.net" in base_url:
            search_urls = [
                "https://economica.net/search/aurora+retail",
                "https://economica.net/?s=aurora",
            ]
        elif "zf.ro" in base_url:
            search_urls = [
                "https://www.zf.ro/search/?q=aurora+multimarket",
                "https://www.zf.ro/tag/aurora/",
            ]
        elif "profit.ro" in base_url:
            search_urls = [
                "https://www.profit.ro/cautare?q=aurora",
                "https://www.profit.ro/tags/aurora/",
            ]

        articles = []
        for url in search_urls:
            try:
                html = self._fetch(url)
                parsed = self._parse_articles(html, base_url, name, url)
                if parsed:
                    articles.extend(parsed)
                    break  # Found results, stop trying other URLs
            except Exception as e:
                logger.debug(f"{name} fetch failed for {url}: {e}")
                continue

        logger.info(f"{name}: {len(articles)} relevant articles")
        return articles

    def _parse_articles(self, html: str, base_url: str, source_name: str, source_url: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        today = date.today().isoformat()
        articles = []

        # Common article selectors
        containers = (
            soup.find_all("article")
            or soup.find_all("div", class_=re.compile(r"article|post|news[-_]?item|story", re.I))
            or soup.find_all("li", class_=re.compile(r"article|post|news", re.I))
        )

        for el in containers:
            try:
                title_tag = el.find(["h1", "h2", "h3", "h4"])
                if not title_tag:
                    continue
                title = title_tag.get_text(strip=True)

                link_tag = title_tag.find("a") or el.find("a", href=True)
                link = ""
                if link_tag and link_tag.get("href"):
                    href = link_tag["href"]
                    link = href if href.startswith("http") else urljoin(base_url, href)

                date_tag = el.find("time") or el.find(class_=re.compile(r"date|time|published", re.I))
                pub_date = today
                if date_tag:
                    pub_date = (date_tag.get("datetime") or date_tag.get_text(strip=True))[:10] or today

                excerpt_tag = el.find(class_=re.compile(r"excerpt|summary|desc|lead|intro", re.I))
                excerpt = excerpt_tag.get_text(strip=True)[:400] if excerpt_tag else ""

                full_text = f"{title} {excerpt}"
                if not _is_relevant(full_text):
                    continue

                signals = _extract_signals(full_text)

                articles.append({
                    "title": title,
                    "url": link,
                    "published_date": pub_date,
                    "excerpt": excerpt,
                    "signals": signals,
                    "source": source_name,
                    "source_url": source_url,
                    "scraped_date": today,
                })
            except Exception as e:
                logger.debug(f"Article parse error: {e}")
                continue

        return articles

    def _playwright_scrape(self, url: str, source_name: str, base_url: str) -> list[dict]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return []

        logger.info(f"Playwright for {source_name}: {url}")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=HEADLESS)
            page = browser.new_page(extra_http_headers=HEADERS)
            try:
                page.goto(url, timeout=60000, wait_until="networkidle")
                time.sleep(2)
                html = page.content()
                return self._parse_articles(html, base_url, source_name, url)
            except Exception as e:
                logger.error(f"Playwright {source_name} error: {e}")
                return []
            finally:
                browser.close()

    def scrape(self) -> list[dict]:
        all_articles = []
        for source in RETAIL_NEWS_SOURCES:
            articles = self._search_site(source)
            if not articles:
                # Playwright fallback
                search_url = f"{source['url']}?s={source['search_term'].replace(' ', '+')}"
                articles = self._playwright_scrape(search_url, source["name"], source["url"])
            all_articles.extend(articles)
            time.sleep(REQUEST_DELAY)

        # Deduplicate by URL
        seen = set()
        unique = [a for a in all_articles if not (a["url"] in seen or seen.add(a["url"]))]
        logger.info(f"Retail news: {len(unique)} unique articles")
        return unique


def scrape_retail_news() -> list[dict]:
    return RetailNewsScraper().scrape()
