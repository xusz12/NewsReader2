from __future__ import annotations

from datetime import datetime


def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
