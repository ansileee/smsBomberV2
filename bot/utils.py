"""Shared utilities — formatting helpers, admin check, duration formatter."""

from bot.config import ADMIN_ID

PM = "HTML"


def b(t: str) -> str:
    return f"<b>{hEsc(t)}</b>"


def i(t: str) -> str:
    return f"<i>{hEsc(t)}</i>"


def c(t: str) -> str:
    return f"<code>{hEsc(t)}</code>"


def hEsc(t: str) -> str:
    if not isinstance(t, str):
        t = str(t)
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def mdEsc(t: str) -> str:
    return hEsc(t)


def isAdmin(userId: int) -> bool:
    return userId == ADMIN_ID


def formatDuration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = seconds // 60, seconds % 60
        return f"{m}m {s}s" if s else f"{m}m"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}h {m}m" if m else f"{h}h"