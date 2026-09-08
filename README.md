# Robinhood Chain Wallet Monitor

Local-first, open-source wallet intelligence agent. Monitors three
ingestion sources (hood.vantis.sh scraper, a Telegram channel, and
optional screen OCR), enriches wallets with on-chain data from Blockscout,
scores and categorizes them with a local LLM (Ollama), and delivers a
daily digest via Telegram. Data lives in Google Sheets, with daily CSV
rotation to Google Drive to keep it small.

No cloud AI costs, no VPS - everything runs on your own PC.

## Architecture

```
hood.vantis.sh --> hood_scraper.py -----\
Telegram channel -> telethon_collector.py -> Google Sheets -> blockscout_enricher.py -> score_calculator.py (Ollama) -> alert_bot.py / frontend
Screen (optional) -> screen_ocr_monitor.py -/
```

## Setup

1. **Install Ollama** and pull the models:
   ```bash
   ollama pull qwen2.5:7b
   ollama pull nomic-embed-text
   ollama pull llama3.2:3b
   curl http://localhost:11434/api/tags   # verify it's running
   ```

2. **Install Tesseract OCR** (only needed if you enable screen OCR):
   - Windows: https://github.com/UB-Mannheim/tesseract/wiki
   - Linux: `sudo apt install tesseract-ocr`
   - Mac: `brew install tesseract`

3. **Python environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate   # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   playwright install chromium
   ```

4. **Google Sheets/Drive:**
   - Create a Google Cloud project, enable the Sheets API and Drive API.
   - Create a service account, download its JSON key, save it as
     `service-account.json` in this folder.
   - Create a spreadsheet, note its ID from the URL, and share it with the
     service account's email (Editor access).
   - Create a Drive folder for daily CSV exports, note its ID.

5. **Configure `.env`:** copy `.env.example` to `.env` and fill in
   `GOOGLE_SHEET_ID`, `GOOGLE_DRIVE_FOLDER_ID`, your Telegram API
   credentials (from https://my.telegram.org), your bot token (from
   @BotFather), and your Telegram user id.

6. **Create the sheet tabs** (candidates, wallets, wallets_updated, events,
   labels, telegram_messages, ocr_results) with their header rows:
   ```bash
   python setup_sheets.py
   ```

7. **Authenticate the Telegram userbot** (one-time; creates
   `wallet_monitor_session.session`):
   ```bash
   python telethon_collector.py
   # enter the login code Telegram sends you, then Ctrl+C once "Listening..." appears
   ```

## Running

Always-on processes:
```bash
python telethon_collector.py
cd frontend && streamlit run app.py --server.port 8501   # http://localhost:8501
python screen_ocr_monitor.py   # optional, only if OCR_ENABLED=true
```

Or via Docker Compose (same three always-on services):
```bash
docker compose up -d
```

Scheduled jobs (cron on Linux/Mac, Task Scheduler on Windows):

| Script | Suggested schedule |
|---|---|
| `hood_scraper.py` | every 10 minutes |
| `blockscout_enricher.py` | every hour |
| `score_calculator.py` | daily, e.g. 2 AM |
| `alert_bot.py` | daily, e.g. 8 AM |
| `daily_export.py` | daily, e.g. 11 PM |

Example crontab:
```cron
*/10 * * * * cd /path/to/project && venv/bin/python hood_scraper.py >> logs/hood_scraper.log 2>&1
0 * * * *    cd /path/to/project && venv/bin/python blockscout_enricher.py >> logs/enricher.log 2>&1
0 2 * * *    cd /path/to/project && venv/bin/python score_calculator.py >> logs/scorer.log 2>&1
0 8 * * *    cd /path/to/project && venv/bin/python alert_bot.py >> logs/alert_bot.log 2>&1
0 23 * * *   cd /path/to/project && venv/bin/python daily_export.py >> logs/export.log 2>&1
```

## Project layout

```
config.py               # centralized .env / settings access
regex_utils.py           # shared address / tx-hash regexes
google_sheets.py          # Sheets & Drive API helpers
setup_sheets.py           # one-time: creates tabs + header rows
llm_utils.py               # Ollama chat/generate/embed + wallet categorization
telethon_collector.py      # Telegram channel reader (always-on)
hood_scraper.py            # hood.vantis.sh Playwright scraper (scheduled)
screen_ocr_monitor.py      # optional screen OCR fallback (always-on or scheduled)
blockscout_enricher.py     # on-chain enrichment (scheduled)
score_calculator.py        # Smart Score + label calculation via Ollama (scheduled)
alert_bot.py                # daily Telegram digest (scheduled)
daily_export.py             # CSV rotation to Google Drive (scheduled)
frontend/app.py              # Streamlit dashboard
```

## Resource requirements

- CPU: 6+ cores recommended
- RAM: 16 GB (32 GB recommended)
- GPU: 8+ GB VRAM recommended for Ollama (CPU-only works with smaller models
  like `qwen2.5:1.5b`, just slower)
- Storage: ~50 GB free for models + data

## Cost

Electricity only (~$5-15/month). No VPS, no per-token AI costs. Google
Sheets/Drive API usage stays within the free tier for personal use.
