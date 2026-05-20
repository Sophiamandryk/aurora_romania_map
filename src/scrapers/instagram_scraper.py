"""
Apify-based Instagram scraper for Aurora + competitor profiles.

Uses the apify/instagram-scraper actor. Requires APIFY_TOKEN in .env.
Falls back gracefully (logs a warning, returns []) if the token is missing.

Flow:
  1. POST run to Apify → get runId
  2. Poll until status == SUCCEEDED (10s interval, 5-minute timeout)
  3. Fetch dataset items
  4. Normalise → social_post dicts
  5. Run keyword signal detection
"""
import re
import time
from datetime import date, datetime, timedelta
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import APIFY_TOKEN, REQUEST_TIMEOUT, MAX_RETRIES, setup_logging

logger = setup_logging("scraper.instagram")

# ── Accounts to monitor ───────────────────────────────────────────────────────

AURORA_PROFILE = {
    "name":     "Aurora",
    "username": "aurora.multimarket",
    "is_own":   True,
}

COMPETITOR_PROFILES = [
    {"name": "Pepco",  "username": "pepco_ro"},
    {"name": "Penny",  "username": "pennyromania"},
    {"name": "Profi",  "username": "profi.ro"},
    {"name": "KiK",    "username": "kik.romania"},
    {"name": "TEDi",   "username": "tedi_romania_"},
    {"name": "Action", "username": "actionromania"},
    {"name": "MrDIY",  "username": "mrdiyRO"},
]

ALL_PROFILES = [AURORA_PROFILE] + COMPETITOR_PROFILES

# username → profile metadata (for result enrichment)
_USERNAME_MAP: dict[str, dict] = {p["username"]: p for p in ALL_PROFILES}

# ── Signal keywords ───────────────────────────────────────────────────────────

_SIGNAL_KEYWORDS = [
    # Romanian
    "deschidere", "nou magazin", "inaugurare", "reducere",
    "oferta", "promotie", "aplicatie", "livrare",
    "deschid", "deschidem", "s-a deschis", "am deschis",
    "extindere", "locatie noua", "locație nouă",
    # English
    "opening", "new store", "discount", "offer", "delivery", "app",
    "grand opening", "coming soon",
]

_SIGNAL_RE = re.compile(
    r"(" + "|".join(re.escape(k) for k in _SIGNAL_KEYWORDS) + r")",
    re.IGNORECASE,
)


def _match_keywords(caption: str) -> list[str]:
    return list(dict.fromkeys(m.group(0).lower() for m in _SIGNAL_RE.finditer(caption)))


# ── Apify API helpers ─────────────────────────────────────────────────────────

_APIFY_BASE = "https://api.apify.com/v2"
_ACTOR_ID   = "apify~instagram-scraper"
_POLL_INTERVAL_S = 10
_POLL_TIMEOUT_S  = 300  # 5 minutes


def _apify_headers() -> dict:
    return {
        "Authorization": f"Bearer {APIFY_TOKEN}",
        "Content-Type": "application/json",
    }


@retry(stop=stop_after_attempt(MAX_RETRIES), wait=wait_exponential(multiplier=1, min=2, max=10))
def _start_run(direct_urls: list[str]) -> str:
    """Trigger an Apify actor run and return the runId."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    payload = {
        "directUrls": direct_urls,
        "resultsLimit": 20,
        "onlyPostsNewerThan": yesterday,
        "proxy": {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"],
        },
    }
    resp = requests.post(
        f"{_APIFY_BASE}/acts/{_ACTOR_ID}/runs",
        json=payload,
        headers=_apify_headers(),
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    run_id = resp.json()["data"]["id"]
    logger.info(f"Apify run started: {run_id}")
    return run_id


def _poll_run(run_id: str) -> str:
    """Poll until the run reaches a terminal state. Returns final status."""
    deadline = time.time() + _POLL_TIMEOUT_S
    while time.time() < deadline:
        resp = requests.get(
            f"{_APIFY_BASE}/actor-runs/{run_id}",
            headers=_apify_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        status = resp.json()["data"]["status"]
        logger.debug(f"Apify run {run_id}: {status}")
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            return status
        time.sleep(_POLL_INTERVAL_S)

    logger.warning(f"Apify run {run_id} did not finish within {_POLL_TIMEOUT_S}s")
    return "TIMEOUT"


def _fetch_dataset(run_id: str) -> list[dict]:
    """Fetch all items from the run's default dataset."""
    resp = requests.get(
        f"{_APIFY_BASE}/actor-runs/{run_id}/dataset/items",
        params={"format": "json", "clean": "true"},
        headers=_apify_headers(),
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


# ── Result normalisation ──────────────────────────────────────────────────────

def _normalise(item: dict) -> Optional[dict]:
    """
    Map a raw Apify Instagram result to the canonical social_post format.
    Returns None if essential fields are missing.
    """
    post_url = item.get("url") or item.get("shortCode") and \
        f"https://www.instagram.com/p/{item['shortCode']}/"
    if not post_url:
        return None

    owner_username = (
        item.get("ownerUsername")
        or (item.get("owner") or {}).get("username", "")
    )
    profile = _USERNAME_MAP.get(owner_username, {})
    competitor = profile.get("name", owner_username)
    is_own     = profile.get("is_own", False)

    caption  = item.get("caption") or item.get("text") or ""
    likes    = item.get("likesCount") or item.get("likes") or 0
    comments = item.get("commentsCount") or item.get("comments") or 0

    # Normalise timestamp → ISO string
    raw_ts = item.get("timestamp") or item.get("takenAt") or ""
    try:
        posted_at = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00")).isoformat()
    except (ValueError, TypeError):
        posted_at = ""

    keywords = _match_keywords(caption)

    return {
        "competitor": competitor,
        "platform":   "instagram",
        "post_url":   post_url,
        "caption":    caption[:2000],
        "likes":      int(likes),
        "comments":   int(comments),
        "posted_at":  posted_at,
        "scraped_at": datetime.utcnow().isoformat(),
        "is_own":     bool(is_own),
        "keywords_matched": keywords,
    }


# ── Public entry point ────────────────────────────────────────────────────────

def scrape_instagram_apify(profiles: Optional[list[dict]] = None) -> list[dict]:
    """
    Scrape recent Instagram posts via Apify for all monitored profiles.

    Returns a list of social_post dicts. Returns [] if APIFY_TOKEN is not set.
    """
    if not APIFY_TOKEN:
        logger.warning(
            "APIFY_TOKEN not set — skipping Apify Instagram scrape. "
            "Add APIFY_TOKEN to .env to enable."
        )
        return []

    profiles = profiles or ALL_PROFILES
    direct_urls = [
        f"https://www.instagram.com/{p['username']}/" for p in profiles
    ]
    logger.info(
        f"Starting Apify Instagram scrape for {len(profiles)} profiles: "
        + ", ".join(p["username"] for p in profiles)
    )

    try:
        run_id = _start_run(direct_urls)
    except Exception as e:
        logger.error(f"Failed to start Apify run: {e}")
        return []

    status = _poll_run(run_id)
    if status != "SUCCEEDED":
        logger.error(f"Apify run {run_id} finished with status {status} — no results")
        return []

    try:
        raw_items = _fetch_dataset(run_id)
    except Exception as e:
        logger.error(f"Failed to fetch Apify dataset for run {run_id}: {e}")
        return []

    posts = []
    for item in raw_items:
        post = _normalise(item)
        if post:
            posts.append(post)

    # Counts per competitor
    from collections import Counter
    counts = Counter(p["competitor"] for p in posts)
    logger.info(
        f"Apify Instagram: {len(posts)} posts total — "
        + ", ".join(f"{k}: {v}" for k, v in counts.most_common())
    )
    return posts
