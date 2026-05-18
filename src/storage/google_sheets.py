"""
Optional Google Sheets export.
Writes store snapshots and changes to a Google Spreadsheet.
Requires GOOGLE_SHEETS_ID and a service account credentials JSON.
"""
import json
from datetime import date
from typing import Optional

from src.config import (
    GOOGLE_SHEETS_ID, GOOGLE_SHEETS_CREDENTIALS_JSON, setup_logging,
)

logger = setup_logging("storage.gsheets")


def _get_client():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        raise ImportError("gspread and google-auth are required: pip install gspread google-auth")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(GOOGLE_SHEETS_CREDENTIALS_JSON, scopes=scopes)
    return gspread.authorize(creds)


def _get_or_create_sheet(spreadsheet, title: str):
    try:
        return spreadsheet.worksheet(title)
    except Exception:
        return spreadsheet.add_worksheet(title=title, rows=2000, cols=30)


class GoogleSheetsExporter:
    def __init__(self):
        if not GOOGLE_SHEETS_ID:
            logger.warning("GOOGLE_SHEETS_ID not set — Google Sheets export disabled")
            self.enabled = False
            return
        try:
            self.client = _get_client()
            self.spreadsheet = self.client.open_by_key(GOOGLE_SHEETS_ID)
            self.enabled = True
            logger.info(f"Google Sheets connected: {self.spreadsheet.title}")
        except Exception as e:
            logger.error(f"Google Sheets init failed: {e}")
            self.enabled = False

    def _write_sheet(self, title: str, headers: list[str], rows: list[list]) -> None:
        if not self.enabled:
            return
        try:
            ws = _get_or_create_sheet(self.spreadsheet, title)
            ws.clear()
            ws.append_row(headers)
            if rows:
                ws.append_rows(rows, value_input_option="USER_ENTERED")
            logger.info(f"Wrote {len(rows)} rows to sheet '{title}'")
        except Exception as e:
            logger.error(f"Failed to write sheet '{title}': {e}")

    def export_stores(self, stores: list[dict]) -> None:
        if not self.enabled:
            return
        headers = [
            "store_id", "name", "city", "address",
            "latitude", "longitude", "status", "first_seen_date",
            "last_seen_date", "notes", "source_url",
        ]
        rows = [
            [
                s.get("store_id", ""), s.get("name", ""), s.get("city", ""),
                s.get("address", ""), s.get("latitude", ""), s.get("longitude", ""),
                s.get("status", "active"), s.get("first_seen_date", ""),
                s.get("last_seen_date", ""), s.get("notes", ""), s.get("source_url", ""),
            ]
            for s in stores
        ]
        self._write_sheet("Stores", headers, rows)

    def export_changes(self, changes: list[dict]) -> None:
        if not self.enabled:
            return
        headers = [
            "change_type", "detected_date", "city", "store_id",
            "confidence_level", "confidence_score", "details",
            "nearest_competitors",
        ]
        rows = []
        for c in changes:
            store = c.get("store") or {}
            confidence = c.get("confidence", {})
            comp = c.get("competitor_analysis", {})
            nearest = comp.get("nearest_competitors", {})
            nearest_str = "; ".join(
                f"{brand}: {v[0]['distance_km']}km" for brand, v in nearest.items() if v
            )
            rows.append([
                c.get("change_type", ""),
                c.get("detected_date", ""),
                store.get("city", c.get("city", "")),
                store.get("store_id", ""),
                confidence.get("level", ""),
                confidence.get("score", ""),
                json.dumps(c.get("details", {})),
                nearest_str,
            ])
        self._write_sheet("Changes", headers, rows)

    def export_jobs(self, jobs: list[dict]) -> None:
        if not self.enabled:
            return
        headers = [
            "title", "company", "location", "url",
            "cities_mentioned", "signal_score", "source", "scraped_date",
        ]
        rows = [
            [
                j.get("title", ""), j.get("company", ""), j.get("location", ""),
                j.get("url", ""),
                ", ".join(j.get("cities_mentioned", [])),
                j.get("signal_score", 0), j.get("source", ""), j.get("scraped_date", ""),
            ]
            for j in jobs
        ]
        self._write_sheet("Jobs", headers, rows)

    def export_future_openings(self, predictions: list[dict]) -> None:
        if not self.enabled:
            return
        headers = [
            "city", "confidence_level", "confidence_score",
            "job_count", "news_count", "instagram_count",
            "job_titles", "news_titles", "detected_date",
        ]
        rows = []
        for p in predictions:
            evidence = p.get("evidence", {})
            confidence = p.get("confidence", {})
            rows.append([
                p.get("city", ""),
                confidence.get("level", ""),
                confidence.get("score", p.get("raw_confidence", "")),
                evidence.get("job_count", 0),
                evidence.get("news_count", 0),
                evidence.get("instagram_count", 0),
                "; ".join(evidence.get("job_titles", [])),
                "; ".join(evidence.get("news_titles", [])),
                p.get("detected_date", ""),
            ])
        self._write_sheet("Future Openings", headers, rows)

    def full_export(
        self,
        stores: list[dict],
        changes: list[dict],
        jobs: list[dict],
        future_openings: list[dict],
    ) -> None:
        self.export_stores(stores)
        self.export_changes(changes)
        self.export_jobs(jobs)
        self.export_future_openings(future_openings)
        logger.info("Google Sheets full export complete")


def export_to_sheets(
    stores: list[dict],
    changes: list[dict],
    jobs: list[dict],
    future_openings: list[dict],
) -> None:
    GoogleSheetsExporter().full_export(stores, changes, jobs, future_openings)
