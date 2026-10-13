"""管理員頁面與使用者頁面共用的工具：樣板、提示訊息、登入檢查。"""
import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Taipei"))

# 跟 bot 的 cogs/loans.py 保持一致
MAX_BATCH_ITEMS = 20
_BATCH_LINE_RE = re.compile(r"^(.+?)(?:\s+[xX](\d+))?$")


def parse_card_batch(raw: str) -> list[tuple[str, int]]:
    items: list[tuple[str, int]] = []
    for line in raw.splitlines():
        for part in line.split(","):
            part = part.strip()
            if not part:
                continue
            match = _BATCH_LINE_RE.match(part)
            name = match.group(1).strip()
            quantity = int(match.group(2)) if match.group(2) else 1
            items.append((name, quantity))
    return items


templates = Jinja2Templates(directory="templates")


def fmt_time(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(TZ).strftime("%Y-%m-%d %H:%M")


def days_since(value: datetime) -> int:
    return (datetime.now(timezone.utc) - value).days


templates.env.filters["fmt_time"] = fmt_time
templates.env.filters["days_since"] = days_since


class NotLoggedIn(Exception):
    pass


def require_admin(request: Request):
    if not request.session.get("admin"):
        raise NotLoggedIn()


def require_member(request: Request) -> str:
    """回傳登入中使用者的 Discord ID。"""
    user_id = request.session.get("user_id")
    if not user_id:
        raise NotLoggedIn()
    return user_id


def flash(request: Request, message: str, kind: str = "ok"):
    request.session.setdefault("flash", []).append({"kind": kind, "message": message})


def render(request: Request, name: str, **context):
    messages = request.session.pop("flash", [])
    return templates.TemplateResponse(request, name, {"messages": messages, **context})


def redirect(url: str):
    return RedirectResponse(url, status_code=303)


def back(request: Request, fallback: str = "/") -> str:
    """表單送出後回到原本的頁面（只接受站內路徑）。"""
    ref = request.headers.get("referer", "")
    base = str(request.base_url)
    return "/" + ref[len(base):] if ref.startswith(base) else fallback
