"""
QA source log — one JSONL entry per source URL used in (or rejected from) a report.

Schema per entry:
  date           YYYY-MM-DD
  section        "2.2" | "2.3" | "3.1" | "2.1" | "1.2"
  url            https://…
  fetch_method   "rss" | "tavily" | "playwright" | "apify" | "failed"
  status         "fetched" | "failed"
  published_date YYYY-MM-DD or ""
  used_in_report true | false
  content_chars  int
"""
import json
import threading
from datetime import date
from pathlib import Path

from src.config import DATA_DIR, setup_logging

logger = setup_logging("modules.qa_log")

_LOCK = threading.Lock()


def _log_path(today: str = None) -> Path:
    today = today or date.today().isoformat()
    return DATA_DIR / f"qa_sources_{today}.jsonl"


def write_entry(
    section: str,
    url: str,
    fetch_method: str,
    status: str,
    published_date: str = "",
    used_in_report: bool = False,
    content_chars: int = 0,
    today: str = None,
) -> None:
    """Append one QA log entry (thread-safe)."""
    today = today or date.today().isoformat()
    entry = {
        "date":           today,
        "section":        section,
        "url":            url,
        "fetch_method":   fetch_method,
        "status":         status,
        "published_date": published_date,
        "used_in_report": used_in_report,
        "content_chars":  content_chars,
    }
    try:
        with _LOCK:
            with open(_log_path(today), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug(f"QA log write failed: {e}")


def write_batch(entries: list[dict], today: str = None) -> None:
    """Write multiple entries at once."""
    for e in entries:
        write_entry(today=today, **e)
