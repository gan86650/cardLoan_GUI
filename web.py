"""管理員頁面與使用者頁面共用的工具：樣板、提示訊息、登入檢查。"""
import os
import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Taipei"))

MAX_BATCH_ITEMS = 20
# 「數量 卡名」，例如「2 Death's Shadow」；數量後面可加 x（「2x Death's Shadow」），省略數量就是 1 張
_BATCH_LINE_RE = re.compile(r"^(?:(\d+)\s*[xX]?\s+)?(.+)$")


def parse_card_batch(raw: str) -> list[tuple[str, int]]:
    """一行一張卡。不再用逗號分隔，因為很多卡名本身就有逗號（例如 Kozilek, Butcher of Truth）。"""
    items: list[tuple[str, int]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        match = _BATCH_LINE_RE.match(line)
        quantity = int(match.group(1)) if match.group(1) else 1
        items.append((match.group(2).strip(), quantity))
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


# 登入時效：從登入起最多 SESSION_MAX_HOURS 小時；超過 SESSION_IDLE_MINUTES 分鐘沒動作也會登出
SESSION_MAX_SECONDS = int(float(os.getenv("SESSION_MAX_HOURS", "12")) * 3600)
SESSION_IDLE_SECONDS = int(float(os.getenv("SESSION_IDLE_MINUTES", "120")) * 60)


def start_session(request: Request, **values):
    """登入：清掉舊的 session（避免沿用登入前的內容），寫入身分與登入時間。"""
    request.session.clear()
    now = int(time.time())
    request.session.update(values, login_at=now, seen_at=now)


def check_session_expiry(request: Request):
    """過期就登出；沒過期就更新最後活動時間（每分鐘最多更新一次）。"""
    session = request.session
    if not (session.get("admin") or session.get("user_id")):
        return
    now = int(time.time())
    if (now - session.get("login_at", 0) > SESSION_MAX_SECONDS
            or now - session.get("seen_at", 0) > SESSION_IDLE_SECONDS):
        session.clear()
        flash(request, "登入已過期，請重新登入", "error")
    elif now - session.get("seen_at", 0) > 60:
        session["seen_at"] = now
