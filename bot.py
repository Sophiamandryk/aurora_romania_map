#!/usr/bin/env python3
"""
Start the Aurora interactive Telegram bot.

Usage:
    python bot.py

The bot listens for /commands and queries the local database.
It runs independently from the push-alert pipeline (main.py run).

Required env var:
    TELEGRAM_INTERACTIVE_BOT_TOKEN  — token for the interactive bot
    (falls back to TELEGRAM_BOT_TOKEN if not set)
"""
from src.bot.interactive_bot import main

if __name__ == "__main__":
    main()
