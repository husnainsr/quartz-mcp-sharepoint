from __future__ import annotations

import os
from datetime import datetime

_RESET = "\033[0m"
_DIM = "\033[90m"
_RED = "\033[31m"
_TAG_COLORS = {
    "[mirror]":  "\033[36m",   # cyan
    "[quartz-v2]": "\033[32m", # green
}
_ERROR_WORDS = ("fail", "error", "not ready", "exception", "unauthorized")
_USE_COLOR = os.environ.get("NO_COLOR", "") == ""


def _color_for(message: str) -> str:
    low = message.lower()
    if any(w in low for w in _ERROR_WORDS):
        return _RED
    for tag, color in _TAG_COLORS.items():
        if tag in message:
            return color
    return ""


def log(message: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    if not _USE_COLOR:
        print(f"{ts} {message}", flush=True)
        return
    color = _color_for(message)
    body = f"{color}{message}{_RESET}" if color else message
    print(f"{_DIM}{ts}{_RESET} {body}", flush=True)


def configure() -> None:
    import logging
    logging.getLogger("httpx").setLevel(logging.WARNING)
