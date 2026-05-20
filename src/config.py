import os
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(override=True)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
REPORTS_DIR = BASE_DIR / "reports"
LOGS_DIR = BASE_DIR / "logs"
DB_PATH = DATA_DIR / "aurora.db"

for d in [DATA_DIR, SNAPSHOTS_DIR, REPORTS_DIR, LOGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID", "")
GOOGLE_SHEETS_CREDENTIALS_JSON = os.getenv("GOOGLE_SHEETS_CREDENTIALS_JSON", "credentials.json")

LINKEDIN_COOKIE = os.getenv("LINKEDIN_COOKIE", "")
INSTAGRAM_SESSION = os.getenv("INSTAGRAM_SESSION", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "2"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))

CONFIDENCE_HIGH_THRESHOLD = float(os.getenv("CONFIDENCE_HIGH_THRESHOLD", "0.75"))
CONFIDENCE_MEDIUM_THRESHOLD = float(os.getenv("CONFIDENCE_MEDIUM_THRESHOLD", "0.4"))

SNAPSHOT_RETENTION_DAYS = int(os.getenv("SNAPSHOT_RETENTION_DAYS", "90"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

AURORA_STORE_MAP_URL = "https://aurora-retail.com/en/pages/store_map/"
AURORA_NEWS_URL = "https://aurora-retail.com/en/news/"
AURORA_INSTAGRAM_URL = "https://www.instagram.com/aurora.multimarket/"
AURORA_LINKEDIN_URL = "https://www.linkedin.com/company/aurora-multimarket-romania/"

COMPETITOR_INSTAGRAM_PROFILES: dict[str, str] = {
    "Pepco":  "pepco_ro",
    "KiK":    "kik.romania",
    "Action": "actionromania",
    "Penny":  "pennyromania",
    "Profi":  "profi.ro",
    "TEDi":   "tedi_romania_",
    "MrDIY":  "mrdiyRO",
}

COMPETITOR_URLS = {
    "Pepco":  "https://pepco.ro/store-locator",
    "TEDi":   "https://www.tedi.com/ro/cautare-filiala",
    "KiK":    "https://companie.kik.ro/localizare-magazin",
    "Action": "https://www.action.com/nl-nl/winkels/",
    "Profi":  "https://www.profi.ro/magazine",        # Cloudflare-blocked; scraped via OpenStreetMap Overpass
    "Penny":  "https://www.penny.ro/magazinul-meu",   # JSON API: penny.ro/api/stores
    "MrDIY":  "https://www.mrdiy.com/ro/storelocator",  # Stores embedded in HTML page
}

ROMANIAN_JOB_BOARDS = [
    "https://www.ejobs.ro/",
    "https://www.bestjobs.eu/",
    "https://www.hipo.ro/",
]

JOB_KEYWORDS = [
    "store manager", "manager magazin", "sales assistant",
    "asistent vanzari", "regional manager", "manager regional",
    "expansion", "extindere", "logistics", "logistica",
    "warehouse", "depozit", "aurora", "multimarket",
]

RETAIL_NEWS_SOURCES = [
    {"name": "Retail.ro", "url": "https://www.retail.ro/", "search_term": "aurora"},
    {"name": "Economica.net", "url": "https://economica.net/", "search_term": "aurora retail"},
    {"name": "ZF.ro", "url": "https://www.zf.ro/", "search_term": "aurora multimarket"},
    {"name": "Profit.ro", "url": "https://www.profit.ro/", "search_term": "aurora"},
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
}


def setup_logging(name: str = "aurora") -> logging.Logger:
    log_level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
    log_file = LOGS_DIR / f"{name}.log"

    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    if not logger.handlers:
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        logger.addHandler(fh)
        logger.addHandler(ch)

    return logger
