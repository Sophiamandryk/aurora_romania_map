# Aurora Romania Expansion Monitor

A production-ready Python monitoring bot that tracks Aurora Romania's retail network expansion and generates operational intelligence.

## What it tracks

| Signal | Source |
|--------|--------|
| New store openings | Aurora store map |
| Possible closures | Store map diff |
| Relocations | Coordinate comparison |
| Rebranding | Name change detection |
| Future openings | LinkedIn + job boards |
| Competitor expansion | Pepco, TEDi, KiK, Action |
| Official announcements | Aurora news page |
| Social signals | Instagram |
| Retail press | Retail.ro, ZF.ro, Profit.ro, Economica.net |

---

## Architecture

```
Aurora Romania Monitor/
├── main.py                      # CLI entry point + orchestrator
├── src/
│   ├── config.py                # All config, env vars, constants
│   ├── reports.py               # Markdown report generator
│   ├── scrapers/
│   │   ├── aurora_map.py        # Store map (HTML + Playwright + KML)
│   │   ├── aurora_news.py       # Official news page
│   │   ├── aurora_instagram.py  # Instagram posts
│   │   ├── linkedin_jobs.py     # LinkedIn + eJobs + BestJobs + Hipo
│   │   ├── competitor_scraper.py # Pepco / TEDi / KiK / Action
│   │   └── retail_news.py       # Retail.ro, ZF.ro, Profit.ro, Economica.net
│   ├── analysis/
│   │   ├── diff.py              # Snapshot comparison engine
│   │   ├── competitor_analysis.py # Proximity + density analysis
│   │   └── confidence.py        # Multi-signal confidence scoring
│   ├── storage/
│   │   ├── sqlite_store.py      # SQLite persistence layer
│   │   └── google_sheets.py     # Optional Google Sheets export
│   └── alerts/
│       └── telegram_alerts.py   # Telegram bot alerts
├── data/
│   ├── aurora.db                # SQLite database (auto-created)
│   └── snapshots/               # Daily JSON snapshots
├── reports/                     # Generated daily reports
├── logs/                        # Rotating log files
├── .env.example                 # Environment template
└── requirements.txt
```

### Pipeline flow

```
Daily run (07:00)
     │
     ├─ [1] Scrape Aurora store map → save snapshot
     ├─ [2] Scrape LinkedIn + job boards → save jobs
     ├─ [3] Scrape Aurora news + retail press → save articles
     ├─ [4] Scrape Aurora Instagram → save posts
     ├─ [5] Scrape Pepco/TEDi/KiK/Action → save competitors
     │
     ├─ [6] Diff: previous vs current snapshot
     │        ├─ NEW_STORE / REMOVED_STORE / RELOCATED_STORE
     │        ├─ STORE_UPDATED / POSSIBLE_REBRANDING
     │        └─ POSSIBLE_FUTURE_OPENING (off-map signals)
     │
     │   Enrich each change with:
     │        ├─ Competitor proximity (nearest stores + density)
     │        └─ Confidence scoring (HIGH / MEDIUM / LOW)
     │
     └─ [7] Output
              ├─ Telegram alerts (per change)
              ├─ Telegram daily summary
              ├─ Markdown report → reports/daily_report_YYYY-MM-DD.md
              └─ Google Sheets export (optional)
```

---

## Setup

### 1. Python environment

```bash
python3 -m venv venv
source venv/bin/activate        # Mac/Linux
# venv\Scripts\activate         # Windows
pip install -r requirements.txt
```

### 2. Playwright (for JS-rendered pages)

```bash
playwright install chromium
# If you get OS dependency errors on Linux:
playwright install-deps chromium
```

### 3. Environment variables

```bash
cp .env.example .env
# Edit .env with your credentials
```

Key variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes (for alerts) | BotFather token |
| `TELEGRAM_CHAT_ID` | Yes (for alerts) | Your chat/channel ID |
| `LINKEDIN_COOKIE` | Optional | `li_at` session cookie |
| `INSTAGRAM_SESSION` | Optional | `sessionid` cookie |
| `GOOGLE_SHEETS_ID` | Optional | Spreadsheet ID |
| `HEADLESS` | Optional | `true` (default) |

### 4. Initialize database

```bash
python main.py init
```

### 5. Test Telegram

```bash
python main.py test-telegram
```

---

## Running

### One-time full run

```bash
python main.py run
```

### Dry run (no writes, no alerts — for testing)

```bash
python main.py run --dry-run
```

### Partial runs

```bash
# Skip slow scrapers for quick test
python main.py run --skip-jobs --skip-instagram

# Only map + competitors, no alerts
python main.py run --skip-jobs --skip-news --skip-instagram --skip-alerts

# Regenerate today's report from existing DB data
python main.py report
```

### Start the scheduler (runs daily at 07:00)

```bash
python main.py schedule
```

---

## Cron setup (Linux/Mac)

Edit crontab:

```bash
crontab -e
```

Add:

```cron
# Run Aurora monitor every day at 07:00
0 7 * * * cd /path/to/Aurora_Romania && /path/to/venv/bin/python main.py run >> logs/cron.log 2>&1
```

Or use a systemd timer for more robust scheduling.

---

## Telegram setup

1. Open [@BotFather](https://t.me/BotFather) in Telegram
2. Send `/newbot` and follow prompts
3. Copy the API token → `TELEGRAM_BOT_TOKEN` in `.env`
4. Start a chat with your bot (or add it to a channel)
5. Get your chat ID:
   - Send a message to the bot
   - Visit: `https://api.telegram.org/bot<TOKEN>/getUpdates`
   - Find `"chat":{"id":...}` → `TELEGRAM_CHAT_ID`
6. For a private channel: add the bot as admin, use the channel ID (starts with `-100`)

---

## LinkedIn cookie setup

LinkedIn requires authentication for job search. To get your session cookie:

1. Log in to LinkedIn in Chrome/Firefox
2. Open DevTools → Application → Cookies → `www.linkedin.com`
3. Find the cookie named `li_at`
4. Copy its value → `LINKEDIN_COOKIE` in `.env`

> **Note:** This cookie expires periodically. Refresh it every 2–4 weeks.

---

## Instagram session setup

1. Log in to Instagram in your browser
2. Open DevTools → Application → Cookies → `www.instagram.com`
3. Find `sessionid` cookie
4. Copy → `INSTAGRAM_SESSION` in `.env`

> Without this, Instagram falls back to Playwright (slower, may hit rate limits).

---

## Google Sheets setup

1. Create a Google Cloud project
2. Enable Sheets API and Drive API
3. Create a Service Account → download JSON key → save as `credentials.json`
4. Share your spreadsheet with the service account email
5. Copy spreadsheet ID from URL → `GOOGLE_SHEETS_ID`
6. Set `GOOGLE_SHEETS_CREDENTIALS_JSON=credentials.json`

The exporter creates/updates these sheets:
- **Stores** — current store network
- **Changes** — detected changes
- **Jobs** — job market signals
- **Future Openings** — predictions

---

## Confidence scoring

Each detected change gets a confidence score (0–1) and level:

| Level | Score | Meaning |
|-------|-------|---------|
| HIGH | ≥ 0.75 | Map marker + official source |
| MEDIUM | 0.40–0.74 | Multiple signals (jobs + news) |
| LOW | < 0.40 | Weak signal only |

Signal weights:

| Signal | Weight |
|--------|--------|
| Store on map | +0.50 |
| Official announcement | +0.35 |
| Building permit / retail park lease | +0.25–0.30 |
| Store Manager job posting | +0.10 bonus |
| Job posting (per posting, cap 0.32) | +0.08 |
| News mention (per article, cap 0.25) | +0.10 |
| Instagram post (per post, cap 0.15) | +0.05 |

---

## Output formats

### Telegram alerts

```
🟢 New Aurora store detected
📍 City: Iași
🏠 Address: Strada Palat 2
📡 Source: store map
🎯 Confidence: 🔥 HIGH

Nearest competitors:
  • Pepco: 0.7 km
  • TEDi: 1.1 km
```

```
🟡 Possible future opening
📍 City: Brașov
🎯 Confidence: 📊 MEDIUM
Signals:
  • LinkedIn/Job boards: 3 postings (Store Manager, Sales Assistant)
  • Retail news: 1 mention
```

```
🔴 Possible store closure
📍 City: Cluj-Napoca
⚠️ Store disappeared from map. Manual verification needed.
```

### Daily markdown report

Saved to: `reports/daily_report_YYYY-MM-DD.md`

Sections:
1. Executive Summary table
2. Map Changes (with competitor proximity)
3. Future Opening Predictions (by confidence)
4. Competitor Expansion Opportunities
5. Job Market Signals
6. Retail News
7. Current Store Network

---

## Competitor analysis

For each new or changed Aurora store, the system:
- Finds nearest Pepco / TEDi / KiK / Action stores within 5km
- Computes density (stores within 1km / 2km / 5km radius)
- Identifies retail cluster opportunities
- Flags cities where competitors have expanded but Aurora has not yet

---

## Troubleshooting

**Store map returns 0 stores**
- The Aurora map may be fully JavaScript-rendered. Playwright handles this automatically.
- Try: `HEADLESS=false` in `.env` to watch the browser scrape.
- Check `logs/aurora.log` for detailed error messages.

**LinkedIn returns no jobs**
- LinkedIn requires authentication for most searches.
- Set `LINKEDIN_COOKIE` with a fresh `li_at` cookie.
- The job board fallbacks (eJobs, BestJobs, Hipo) work without credentials.

**Playwright fails on Linux**
```bash
playwright install-deps chromium
sudo apt-get install -y libgbm1 libxkbcommon0
```

**Telegram not sending**
- Verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set.
- Run `python main.py test-telegram` to diagnose.
- For channels: bot must be an admin.

**Duplicate alerts**
- The system tracks `alerted=1` per change in SQLite.
- If you reset the DB, alerts may resend. Use `--skip-alerts` flag.

---

## Extending the system

### Add a new country (Bulgaria/Moldova)

1. Add country-specific URLs to `src/config.py`
2. Create country-specific city lists in scrapers
3. Add `--country` flag to `main.py`
4. The DB schema supports multiple countries via the `source_url` field

### Add AI summaries

```python
# In src/reports.py, add after generating report:
import anthropic
client = anthropic.Anthropic()
summary = client.messages.create(
    model="claude-opus-4-7",
    max_tokens=500,
    messages=[{"role": "user", "content": f"Summarize this retail expansion report:\n{report_md[:3000]}"}]
)
```

### Add TikTok monitoring

1. Use unofficial TikTok API or Playwright
2. Add `src/scrapers/tiktok.py` mirroring `aurora_instagram.py`
3. Add to pipeline in `main.py`

---

## Data retention

- SQLite snapshots: 90 days (configurable via `SNAPSHOT_RETENTION_DAYS`)
- JSON snapshots: `data/snapshots/` — manage manually or add a cron cleanup
- Reports: `reports/` — kept indefinitely

---

_Built for Aurora Romania retail expansion intelligence._
