"""Envio de relatorios. Telegram Bot API (oficial). WhatsApp Business Cloud API pode ser
adicionado aqui com a mesma interface quando houver numero e token aprovados pela Meta."""
from __future__ import annotations

import httpx

from .config import settings

_TG_LIMIT = 4000


def send_telegram(text: str) -> None:
    if not (settings.telegram_token and settings.telegram_chat_id):
        raise RuntimeError("Defina TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no arquivo .env")
    url = f"https://api.telegram.org/bot{settings.telegram_token}/sendMessage"
    for i in range(0, len(text), _TG_LIMIT):
        r = httpx.post(url, timeout=20, data={
            "chat_id": settings.telegram_chat_id, "text": text[i:i + _TG_LIMIT],
            "parse_mode": "HTML", "disable_web_page_preview": "true",
        })
        r.raise_for_status()
