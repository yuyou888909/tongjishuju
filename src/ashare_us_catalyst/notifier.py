from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional, Union

import requests


def load_env(path: Union[str, Path] = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def send_telegram(markdown: str, *, token: Optional[str] = None, chat_id: Optional[str] = None) -> dict[str, Any]:
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("缺少 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID")
    text = _telegram_excerpt(markdown)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True}, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _telegram_excerpt(markdown: str, max_len: int = 3800) -> str:
    text = markdown.replace("|", " ")
    if len(text) <= max_len:
        return text
    return text[: max_len - 40].rstrip() + "\n\n[已截断，完整报告见本地 reports 目录]"
