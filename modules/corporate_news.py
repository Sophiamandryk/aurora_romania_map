"""
3.1 Corporate News — daily corporate news for Ukraine retail/FMCG audience.
Two sub-topics (financial results + strategic announcements) searched against
13 Ukrainian business/retail domains via Tavily advanced search.
GPT-4o-mini curates the 5 most relevant items and writes to aurora_output_YYYY-MM-DD.json.
"""
import json
import time
import requests
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from src.config import (
    TAVILY_API_KEY, OPENAI_API_KEY, REQUEST_TIMEOUT, DATA_DIR, setup_logging,
)

logger = setup_logging("modules.corporate_news")

_TAVILY_URL = "https://api.tavily.com/search"
_DELAY = 0.35
_DOMAINS = [
    "interfax.com.ua", "delo.ua", "speka.ua", "rau.ua", "allretail.ua",
    "ucsc.org.ua", "business.org.ua", "uazmi.org", "finteco.com.ua",
    "fixygen.ua", "thepage.ua", "trademaster.ua", "ua-retail.com",
]

_QUERIES = [
    "фінансові результати звітність виручка прибуток збиток ритейл FMCG Україна",
    "стратегічні оголошення плани розширення M&A злиття поглинання зміна керівництва ритейл Україна",
]

_SYSTEM = """\
You are an editor for Aurora, a daily retail intelligence digest for Ukraine and Romania.
From the provided news list, select the 5 most relevant items for retail business readers.
Prioritize: financial results of retail/FMCG companies, M&A, strategic expansion, leadership changes.
Ignore politics, sports, unrelated industries.
Return only a valid JSON array of 5 objects — no markdown, no explanation:
[{ "title": "...", "url": "...", "published_date": "...", "source_name": "...", "summary_uk": "...", "anchor_text": "..." }]
source_name: short name (e.g. RAU, Delo, Speka).
summary_uk: 2-3 sentences in Ukrainian on why this item matters for retail.
anchor_text: 4-6 word Ukrainian phrase suitable as a hyperlink label."""


def _search(query: str, days: int) -> list[dict]:
    if not TAVILY_API_KEY:
        return []
    payload = {
        "api_key":         TAVILY_API_KEY,
        "query":           query,
        "search_depth":    "advanced",
        "max_results":     5,
        "days":            days,
        "include_domains": _DOMAINS,
    }
    try:
        resp = requests.post(_TAVILY_URL, json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        out = []
        for r in resp.json().get("results", []):
            url = r.get("url", "")
            if not url:
                continue
            domain = urlparse(url).netloc.removeprefix("www.")
            out.append({
                "title":          r.get("title", ""),
                "url":            url,
                "published_date": r.get("published_date", ""),
                "snippet":        (r.get("content") or r.get("snippet", ""))[:500],
                "source_domain":  domain,
            })
        return out
    except Exception as e:
        logger.warning(f"Tavily '{query[:60]}': {e}")
        return []
    finally:
        time.sleep(_DELAY)


def _collect(days: int) -> list[dict]:
    seen: set[str] = set()
    results: list[dict] = []
    for q in _QUERIES:
        for r in _search(q, days):
            if r["url"] not in seen:
                seen.add(r["url"])
                results.append(r)
    return results


def _curate(results: list[dict]) -> list[dict]:
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set — curation skipped")
        return []

    snippets = "\n\n".join(
        f"[{i + 1}] {r['title']}\nURL: {r['url']}\n"
        f"Date: {r.get('published_date') or 'n/a'}\n"
        f"Source: {r['source_domain']}\n{r['snippet']}"
        for i, r in enumerate(results)
    )

    raw = ""
    try:
        from openai import OpenAI
        resp = OpenAI(api_key=OPENAI_API_KEY).chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": snippets},
            ],
            temperature=0.3,
            max_tokens=1500,
        )
        raw = resp.choices[0].message.content.strip()
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.error(f"Malformed JSON from OpenAI curation: {raw!r}")
        return []
    except Exception as e:
        logger.error(f"OpenAI curation failed: {e}")
        return []


def _save_output(today: str, data: dict) -> None:
    path: Path = DATA_DIR / f"aurora_output_{today}.json"
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing.update(data)
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


def run(today: str = None) -> dict:
    """
    Run section 3.1. Returns the section dict and persists to aurora_output_YYYY-MM-DD.json.
    """
    today = today or date.today().isoformat()

    results = _collect(days=1)
    actual_days = 1
    if len(results) < 5:
        logger.info(f"Only {len(results)} results for days=1, retrying with days=2")
        results = _collect(days=2)
        actual_days = 2

    logger.info(f"3.1 corporate news: {len(results)} raw results (days={actual_days})")

    curated = _curate(results)
    items = []
    for item in curated:
        anchor = item.get("anchor_text", item.get("title", ""))
        url    = item.get("url", "")
        items.append({
            "url":            url,
            "telegram_link":  f"[{anchor}]({url})",
            "published_date": item.get("published_date", ""),
            "source_name":    item.get("source_name", ""),
            "summary_uk":     item.get("summary_uk", ""),
        })

    section = {
        "actual_days_searched": actual_days,
        "items": items,
    }

    _save_output(today, {"3.1_corporate_news": section})
    logger.info(f"3.1 corporate news: {len(items)} curated items saved")
    return section
