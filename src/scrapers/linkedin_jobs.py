"""
LinkedIn and Romanian job board scraper for Aurora expansion signals.
Detects hiring patterns that predict future store openings.
"""
import re
import time
from datetime import date
from urllib.parse import urlencode, quote_plus, urlparse, parse_qs, urlunparse

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import (
    LINKEDIN_COOKIE, JOB_KEYWORDS, ROMANIAN_JOB_BOARDS,
    HEADERS, REQUEST_TIMEOUT, MAX_RETRIES, REQUEST_DELAY,
    HEADLESS, setup_logging,
)

logger = setup_logging("scraper.linkedin_jobs")

_LI_TRACKING_PARAMS = frozenset({"position", "pageNum", "refId", "trackingId", "trk", "trkInfo"})
_LI_JOB_ID_RE = re.compile(r"/jobs/view/[^/?#]*?-?(\d{7,})/?")


def _canonical_linkedin_url(url: str) -> str:
    """Strip LinkedIn tracking params, returning a stable URL for deduplication."""
    if not url:
        return url
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query, keep_blank_values=False)
        clean = {k: v for k, v in params.items() if k not in _LI_TRACKING_PARAMS}
        new_query = urlencode(clean, doseq=True)
        return urlunparse(parsed._replace(query=new_query))
    except Exception:
        return url


def _extract_linkedin_job_id(url: str) -> str:
    """Extract numeric job ID from a LinkedIn /jobs/view/... URL."""
    m = _LI_JOB_ID_RE.search(url)
    return m.group(1) if m else ""


ROMANIAN_CITIES = [
    "București", "Cluj-Napoca", "Cluj", "Timișoara", "Iași", "Constanța",
    "Craiova", "Brașov", "Galați", "Ploiești", "Oradea", "Brăila", "Arad",
    "Pitești", "Sibiu", "Bacău", "Târgu Mureș", "Baia Mare", "Buzău",
    "Satu Mare", "Botoșani", "Râmnicu Vâlcea", "Suceava", "Piatra Neamț",
    "Deva", "Bistrița", "Alba Iulia", "Tulcea", "Giurgiu", "Alexandria",
    "Zalău", "Focșani", "Câmpina", "Turda", "Dej", "Roman", "Slobozia",
    "Sfântu Gheorghe", "Reșița", "Drobeta-Turnu Severin",
]

_CITY_RE = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in ROMANIAN_CITIES) + r")\b",
    re.IGNORECASE,
)

HIGH_SIGNAL_TITLES = {
    "store manager": 3,
    "manager magazin": 3,
    "manager de magazin": 3,
    "regional manager": 2,
    "sales assistant": 1,
    "asistent vanzari": 1,
    "asistent de vânzări": 1,
    "expansion": 3,
    "extindere": 3,
    "logistics coordinator": 2,
    "supply chain": 2,
    "warehouse": 1,
    "depozit": 1,
    "visual merchandiser": 1,
    "district manager": 3,
}


def _score_job(title: str, description: str = "") -> int:
    combined = f"{title} {description}".lower()
    score = 0
    for kw, weight in HIGH_SIGNAL_TITLES.items():
        if kw in combined:
            score += weight
    return score


def _extract_cities(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0) for m in _CITY_RE.finditer(text)))


class LinkedInJobScraper:
    def __init__(self):
        self.session = requests.Session()
        # LinkedIn's public job search returns full data without auth.
        # The li_at cookie can cause redirect loops if LinkedIn detects automation;
        # we use it only as optional enrichment, falling back to unauthenticated if it fails.
        self.session.headers.update(HEADERS)
        self._cookie_ok = True  # flip to False if we see redirect loops

    def _fetch(self, url: str) -> str:
        if LINKEDIN_COOKIE and self._cookie_ok:
            self.session.cookies.set("li_at", LINKEDIN_COOKIE, domain=".linkedin.com")

        resp = self.session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=False)

        # Redirect loop = cookie rejected by LinkedIn — drop it and retry without
        if resp.status_code in (301, 302, 303) and self._cookie_ok:
            logger.warning("LinkedIn cookie caused redirect — switching to unauthenticated mode")
            self._cookie_ok = False
            self.session.cookies.clear()
            resp = self.session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)

        if resp.status_code == 429:
            raise Exception("LinkedIn rate-limited (429)")
        if resp.status_code >= 400:
            raise Exception(f"LinkedIn HTTP {resp.status_code}")

        # Follow single redirect if not a loop
        if resp.status_code in (301, 302, 303):
            location = resp.headers.get("Location", url)
            if location != url:
                resp = self.session.get(location, timeout=REQUEST_TIMEOUT, allow_redirects=True)

        return resp.text

    def _search_linkedin(self, keyword: str, location: str = "Romania") -> list[dict]:
        params = {
            "keywords": keyword,
            "location": location,
            "f_TPR": "r2592000",  # last 30 days
            "sortBy": "DD",
        }
        url = f"https://www.linkedin.com/jobs/search/?{urlencode(params)}"
        logger.info(f"LinkedIn search: {url}")
        try:
            html = self._fetch(url)
            return self._parse_linkedin_jobs(html, url)
        except Exception as e:
            logger.warning(f"LinkedIn fetch failed for '{keyword}': {e}")
            return []

    def _parse_linkedin_jobs(self, html: str, source_url: str) -> list[dict]:
        """
        Parse LinkedIn job listings from two possible formats:
        1. Authenticated (<code> JSON tags with 'included' array) — logged-in view
        2. Public HTML (.base-card divs) — unauthenticated view
        """
        import json as _json
        soup = BeautifulSoup(html, "lxml")
        jobs = []
        today = date.today().isoformat()

        def _add(title, company, location_text, link):
            title = title.rstrip("\xa0").strip()
            if not title:
                return
            cities = _extract_cities(f"{title} {company} {location_text}")
            score = _score_job(title, company)
            combined = f"{title} {company}".lower()
            is_aurora = any(kw in combined for kw in ["aurora", "multimarket"])
            is_signal = score >= 2
            if not is_aurora and not is_signal:
                return
            jobs.append({
                "title": title,
                "company": company,
                "location": location_text,
                "url": link or source_url,
                "cities_mentioned": cities,
                "signal_score": score,
                "is_aurora_company": is_aurora,
                "source": "linkedin",
                "scraped_date": today,
                "published_date": today,
            })

        # Path 1: JSON in <code> tags (authenticated view)
        for tag in soup.find_all("code"):
            txt = tag.get_text().strip()
            if not txt or txt[0] != "{":
                continue
            try:
                data = _json.loads(txt)
                for item in data.get("included", []):
                    if "JobPostingCard" not in item.get("$type", ""):
                        continue
                    title = (item.get("title") or {}).get("text", "")
                    company = (item.get("primaryDescription") or {}).get("text", "")
                    location_text = (item.get("secondaryDescription") or {}).get("text", "")
                    link = item.get("jobPostingUrl") or item.get("navigationUrl") or ""
                    _add(title, company, location_text, link)
            except (_json.JSONDecodeError, TypeError):
                continue

        # Path 2: HTML .base-card divs (public/unauthenticated view)
        if not jobs:
            for card in soup.select("div.base-card, li.result-card"):
                try:
                    title_el = (card.select_one("h3.base-search-card__title")
                                or card.select_one("h3.job-search-card__title")
                                or card.find("h3"))
                    company_el = (card.select_one("h4.base-search-card__subtitle")
                                  or card.select_one(".job-search-card__company-name")
                                  or card.find("h4"))
                    location_el = (card.select_one(".job-search-card__location")
                                   or card.select_one(".base-search-card__metadata span"))
                    link_el = card.select_one("a[href]")

                    title = title_el.get_text(strip=True) if title_el else ""
                    company = company_el.get_text(strip=True) if company_el else ""
                    location_text = location_el.get_text(strip=True) if location_el else ""
                    link = link_el["href"] if link_el else source_url
                    _add(title, company, location_text, link)
                except Exception:
                    continue

        # Deduplicate within this page
        seen = set()
        unique = []
        for j in jobs:
            key = f"{j['title'].lower()}::{j.get('company','').lower()}"
            if key not in seen:
                seen.add(key)
                unique.append(j)
        return unique

    def _playwright_linkedin(self, keyword: str) -> list[dict]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return []

        logger.info(f"Playwright LinkedIn search: {keyword}")
        params = {
            "keywords": f"aurora {keyword}",
            "location": "Romania",
            "f_TPR": "r604800",
        }
        url = f"https://www.linkedin.com/jobs/search/?{urlencode(params)}"

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=HEADLESS)
            context = browser.new_context(user_agent=HEADERS["User-Agent"])
            if LINKEDIN_COOKIE:
                context.add_cookies([{
                    "name": "li_at",
                    "value": LINKEDIN_COOKIE,
                    "domain": ".linkedin.com",
                    "path": "/",
                }])
            page = context.new_page()
            try:
                page.goto(url, timeout=60000, wait_until="networkidle")
                time.sleep(3)
                html = page.content()
                return self._parse_linkedin_jobs(html, url)
            except Exception as e:
                logger.error(f"Playwright LinkedIn error: {e}")
                return []
            finally:
                browser.close()

    def _scrape_company_jobs_page(self) -> list[dict]:
        """Scrape Aurora's own LinkedIn company jobs page — most reliable source."""
        # Aurora Multimarket Romania company page
        urls = [
            "https://www.linkedin.com/company/aurora-multimarket-romania/jobs/",
            "https://www.linkedin.com/jobs/search/?keywords=aurora+multimarket&location=Romania&f_TPR=r2592000",
        ]
        all_jobs = []
        for url in urls:
            try:
                jobs = self._playwright_linkedin_url(url)
                all_jobs.extend(jobs)
            except Exception as e:
                logger.warning(f"Company jobs page failed: {e}")
        return all_jobs

    def _playwright_linkedin_url(self, url: str) -> list[dict]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return []

        logger.info(f"Playwright LinkedIn: {url}")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=HEADLESS)
            context = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                locale="en-US",
            )
            page = context.new_page()
            if LINKEDIN_COOKIE:
                # Navigate to LinkedIn first, then inject cookie to avoid redirect loops
                page.goto("https://www.linkedin.com", timeout=30000, wait_until="domcontentloaded")
                context.add_cookies([
                    {"name": "li_at", "value": LINKEDIN_COOKIE,
                     "domain": ".linkedin.com", "path": "/",
                     "httpOnly": True, "secure": True, "sameSite": "None"},
                    {"name": "lang", "value": "v=2&lang=en-us",
                     "domain": ".linkedin.com", "path": "/"},
                ])
            try:
                page.goto(url, timeout=60000, wait_until="domcontentloaded")
                time.sleep(4)
                # Scroll to load more jobs
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)
                html = page.content()
                return self._parse_linkedin_jobs(html, url)
            except Exception as e:
                logger.error(f"Playwright LinkedIn URL error: {e}")
                return []
            finally:
                browser.close()

    def scrape(self) -> list[dict]:
        all_jobs = []

        # Requests-only: Playwright triggers redirect loops on LinkedIn auth
        for term in ["store manager", "manager magazin", "aurora multimarket"]:
            jobs = self._search_linkedin(term)
            all_jobs.extend(jobs)
            time.sleep(REQUEST_DELAY + 2)  # extra gap to avoid rate-limit

        # Deduplicate by title+company
        seen = set()
        unique = []
        for j in all_jobs:
            key = f"{j['title'].lower()}::{j.get('company','').lower()}"
            if key not in seen:
                seen.add(key)
                unique.append(j)

        logger.info(f"LinkedIn: {len(unique)} unique jobs found")
        return unique


class RomanianJobBoardScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    @retry(stop=stop_after_attempt(MAX_RETRIES), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _fetch(self, url: str) -> str:
        resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.text

    def _search_ejobs(self) -> list[dict]:
        url = "https://www.ejobs.ro/user/locuri-de-munca/aurora"
        logger.info(f"Searching eJobs: {url}")
        try:
            html = self._fetch(url)
            return self._parse_generic_jobs(html, url, "ejobs")
        except Exception as e:
            logger.warning(f"eJobs failed: {e}")
            return []

    def _search_bestjobs(self) -> list[dict]:
        url = f"https://www.bestjobs.eu/ro/locuri-de-munca?q=aurora+multimarket"
        logger.info(f"Searching BestJobs: {url}")
        try:
            html = self._fetch(url)
            return self._parse_generic_jobs(html, url, "bestjobs")
        except Exception as e:
            logger.warning(f"BestJobs failed: {e}")
            return []

    def _search_hipo(self) -> list[dict]:
        url = "https://www.hipo.ro/locuri-de-munca/cauta/aurora"
        logger.info(f"Searching Hipo: {url}")
        try:
            html = self._fetch(url)
            return self._parse_generic_jobs(html, url, "hipo")
        except Exception as e:
            logger.warning(f"Hipo failed: {e}")
            return []

    def _parse_generic_jobs(self, html: str, source_url: str, source: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        today = date.today().isoformat()
        jobs = []

        job_cards = (
            soup.find_all("div", class_=re.compile(r"job[-_]item|job[-_]card|result[-_]item|offer[-_]item", re.I))
            or soup.find_all("article")
            or soup.find_all("li", class_=re.compile(r"job|result|offer", re.I))
        )

        for card in job_cards:
            try:
                title_el = card.find(["h2", "h3", "h4"]) or card.find(class_=re.compile(r"title|job[-_]name", re.I))
                title = title_el.get_text(strip=True) if title_el else card.get_text(strip=True)[:80]

                link_el = card.find("a", href=True)
                link = link_el["href"] if link_el else source_url
                if not link.startswith("http"):
                    from urllib.parse import urljoin
                    link = urljoin(source_url, link)

                company_el = card.find(class_=re.compile(r"company|employer|angajator", re.I))
                company = company_el.get_text(strip=True) if company_el else ""

                location_el = card.find(class_=re.compile(r"location|city|oras", re.I))
                location_text = location_el.get_text(strip=True) if location_el else ""

                full_text = f"{title} {company} {location_text}"
                cities = _extract_cities(full_text)
                score = _score_job(title)

                # Filter: must be Aurora-related
                if "aurora" not in full_text.lower() and "multimarket" not in full_text.lower():
                    continue

                jobs.append({
                    "title": title,
                    "company": company,
                    "location": location_text,
                    "url": link,
                    "cities_mentioned": cities,
                    "signal_score": score,
                    "source": source,
                    "scraped_date": today,
                    "published_date": today,
                })
            except Exception as e:
                logger.debug(f"Job parse error ({source}): {e}")
                continue

        logger.info(f"{source}: parsed {len(jobs)} jobs")
        return jobs

    def scrape(self) -> list[dict]:
        all_jobs = []
        all_jobs.extend(self._search_ejobs())
        time.sleep(REQUEST_DELAY)
        all_jobs.extend(self._search_bestjobs())
        time.sleep(REQUEST_DELAY)
        all_jobs.extend(self._search_hipo())
        time.sleep(REQUEST_DELAY)
        logger.info(f"Romanian job boards: {len(all_jobs)} jobs total")
        return all_jobs


def scrape_jobs() -> list[dict]:
    li_jobs = LinkedInJobScraper().scrape()
    ro_jobs = RomanianJobBoardScraper().scrape()
    all_jobs = li_jobs + ro_jobs

    seen: set[str] = set()
    unique: list[dict] = []
    dupes_removed = 0
    for j in all_jobs:
        raw_url = j.get("url", "")
        canonical_url = _canonical_linkedin_url(raw_url)
        job_id = _extract_linkedin_job_id(canonical_url)

        if job_id:
            dedup_key = f"li_id:{job_id}"
        else:
            title_norm = j.get("title", "").lower().strip()
            company_norm = j.get("company", "").lower().strip()
            loc_norm = j.get("location", "").lower().strip()[:30]
            dedup_key = f"{title_norm}::{company_norm}::{loc_norm}"

        if dedup_key not in seen:
            seen.add(dedup_key)
            j["canonical_url"] = canonical_url
            j["canonical_job_id"] = job_id or dedup_key
            j["url"] = canonical_url or raw_url
            unique.append(j)
        else:
            dupes_removed += 1

    logger.info(f"Total unique jobs: {len(unique)} ({dupes_removed} duplicates removed)")
    return unique
