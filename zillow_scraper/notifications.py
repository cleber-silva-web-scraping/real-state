#!/usr/bin/env python3
"""Notificacao via Telegram (best-effort; silencia se sem credenciais)."""
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from zillow_scraper.config import TELEGRAM_BOT_KEY, TELEGRAM_CHAT_ID


def send_telegram_message(message):
    if TELEGRAM_BOT_KEY == "" or TELEGRAM_CHAT_ID == "":
        return False, "missing BOT_KEY or BOT_CHAT_ID"

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_KEY}/sendMessage"
    body = urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": message}).encode("utf-8")
    request = Request(
        url=url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            status = response.getcode()
            if status < 200 or status >= 300:
                return False, f"telegram status={status}"
        return True, ""
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return False, str(exc)
