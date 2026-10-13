import hmac
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

import admin_db  # noqa: E402
import database as db  # noqa: E402

ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
SESSION_SECRET = os.environ["SESSION_SECRET"]
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 不跑 db.init_db()：建表與 migration 交給 bot，GUI 只讀寫既有資料
    await db.get_pool()
    yield
    if db._pool is not None:
        await db._pool.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=bool(os.getenv("RAILWAY_ENVIRONMENT")),
    max_age=60 * 60 * 24 * 7,
)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def fmt_time(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(TZ).strftime("%Y-%m-%d %H:%M")


def days_since(value: datetime) -> int:
    return (datetime.now(timezone.utc) - value).days


templates.env.filters["fmt_time"] = fmt_time
templates.env.filters["days_since"] = days_since


# ── 登入 ──────────────────────────────────────────────────────────────────────
class NotLoggedIn(Exception):
    pass


@app.exception_handler(NotLoggedIn)
async def _redirect_to_login(request: Request, exc: NotLoggedIn):
    return RedirectResponse("/login", status_code=303)


def require_admin(request: Request):
    if not request.session.get("admin"):
        raise NotLoggedIn()


def flash(request: Request, message: str, kind: str = "ok"):
    request.session.setdefault("flash", []).append({"kind": kind, "message": message})


def render(request: Request, name: str, **context):
    messages = request.session.pop("flash", [])
    return templates.TemplateResponse(request, name, {"messages": messages, **context})


def redirect(url: str):
    return RedirectResponse(url, status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return render(request, "login.html")


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    if hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode()):
        request.session["admin"] = True
        return redirect("/")
    flash(request, "密碼錯誤", "error")
    return redirect("/login")


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return redirect("/login")


# ── 借牌總覽 ──────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
async def overview(request: Request, guild: str = "", user: str = "", user_b: str = "", card: str = ""):
    users = await admin_db.list_users()
    guilds = await admin_db.list_guilds()

    if user and user_b:
        # 跟 /查借牌 一樣：兩人之間雙向的未還紀錄
        loans = await db.get_loans_between(user, user_b)
        if guild:
            loans = [l for l in loans if l["guild_id"] == guild]
        if card:
            loans = [l for l in loans if card.lower() in l["card_name"].lower()]
    else:
        loans = await admin_db.search_active_loans(guild or None, user or None, card.strip() or None)

    return render(
        request, "overview.html",
        loans=loans, users=users, guilds=guilds,
        filters={"guild": guild, "user": user, "user_b": user_b, "card": card},
        total_qty=sum(l["quantity"] for l in loans),
    )


# ── 新增紀錄（對應 /借牌、/借出、/紀錄他人、/批次紀錄）──────────────────────
@app.get("/loans/new", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
async def new_loan_page(request: Request):
    users = await admin_db.list_users()
    guilds = await admin_db.list_guilds()
    default_guild = os.getenv("DEFAULT_GUILD_ID") or (guilds[0] if guilds else "")
    return render(request, "new_loan.html", users=users, guilds=guilds, default_guild=default_guild)


@app.post("/loans/new", dependencies=[Depends(require_admin)])
async def create_loans(
    request: Request,
    guild_id: str = Form(...),
    lender_id: str = Form(...),
    borrower_id: str = Form(...),
    cards: str = Form(...),
    edition: str = Form(""),
    note: str = Form(""),
):
    guild_id = guild_id.strip()
    if not guild_id:
        flash(request, "請填伺服器 ID", "error")
        return redirect("/loans/new")
    if lender_id == borrower_id:
        flash(request, "牌主跟借用人不能是同一人", "error")
        return redirect("/loans/new")

    items = parse_card_batch(cards)
    if not items:
        flash(request, "至少要輸入一張卡", "error")
        return redirect("/loans/new")
    if len(items) > MAX_BATCH_ITEMS:
        flash(request, f"一次最多只能記錄 {MAX_BATCH_ITEMS} 筆，請分批輸入", "error")
        return redirect("/loans/new")
    if any(qty < 1 for _, qty in items):
        flash(request, "數量至少為 1", "error")
        return redirect("/loans/new")

    ids = await db.add_loans_bulk(
        guild_id, lender_id, borrower_id, items,
        edition.strip() or None, note.strip() or None,
    )
    flash(request, f"已新增 {len(ids)} 筆紀錄：" + "、".join(f"#{i}" for i in ids))
    return redirect("/")


# ── 單筆紀錄：還牌 / 已取回 / 轉借 ────────────────────────────────────────────
@app.get("/loans/{loan_id}", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
async def loan_detail(request: Request, loan_id: int):
    loan = await admin_db.get_loan_any(loan_id)
    if loan is None:
        flash(request, f"找不到 #{loan_id}", "error")
        return redirect("/")
    users = await admin_db.list_users()
    return render(request, "loan_detail.html", loan=loan, users=users)


@app.post("/loans/{loan_id}/return", dependencies=[Depends(require_admin)])
async def return_loan(request: Request, loan_id: int, quantity: int = Form(...)):
    loan = await admin_db.get_loan_any(loan_id)
    if loan is None or loan["returned_at"] is not None:
        flash(request, f"#{loan_id} 不存在或已經歸還", "error")
        return redirect("/")
    if quantity < 1:
        flash(request, "數量至少為 1", "error")
        return redirect(f"/loans/{loan_id}")

    # 用牌主身分走 reclaim_loan，跟 bot 的 /已取回 同一段邏輯（部分歸還、FOR UPDATE 鎖）
    result = await db.reclaim_loan(loan_id, loan["lender_id"], quantity)
    if result is None:
        flash(request, f"#{loan_id} 已經被歸還了", "error")
        return redirect("/")
    if result["fully_returned"]:
        flash(request, f"#{loan_id} {result['card_name']} 已全部歸還")
        return redirect("/")
    flash(request, f"#{loan_id} {result['card_name']} 歸還 {result['returned_qty']} 張，剩 {result['remaining_qty']} 張未還")
    return redirect(f"/loans/{loan_id}")


@app.post("/loans/{loan_id}/transfer", dependencies=[Depends(require_admin)])
async def transfer_loan(request: Request, loan_id: int, new_borrower_id: str = Form(...)):
    loan = await admin_db.get_loan_any(loan_id)
    if loan is None or loan["returned_at"] is not None:
        flash(request, f"#{loan_id} 不存在或已經歸還", "error")
        return redirect("/")
    if new_borrower_id == loan["borrower_id"]:
        flash(request, "牌已經在這個人手上了", "error")
        return redirect(f"/loans/{loan_id}")
    if new_borrower_id == loan["lender_id"]:
        flash(request, "對方就是原本的牌主，請改用「還牌」", "error")
        return redirect(f"/loans/{loan_id}")

    moved = await db.transfer_loan(loan_id, loan["borrower_id"], new_borrower_id)
    if moved is None:
        flash(request, f"#{loan_id} 狀態已變更，請重新整理後再試", "error")
    else:
        flash(request, f"#{loan_id} {loan['card_name']} 已轉借")
    return redirect(f"/loans/{loan_id}")


# ── 歷史紀錄 ──────────────────────────────────────────────────────────────────
@app.get("/history", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
async def history(request: Request):
    loans = await admin_db.get_returned_loans()
    return render(request, "history.html", loans=loans)


# ── 使用者與提醒設定（對應 /借進來的牌、/借出去的牌、/提醒設定）──────────────
@app.get("/users", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
async def users_page(request: Request):
    users = await admin_db.list_users()
    return render(request, "users.html", users=users)


@app.post("/users", dependencies=[Depends(require_admin)])
async def create_user(request: Request, discord_id: str = Form(...), username: str = Form(...)):
    discord_id, username = discord_id.strip(), username.strip()
    if not discord_id.isdigit() or not username:
        flash(request, "Discord ID 必須是數字，名稱不可空白", "error")
    elif await admin_db.add_user(discord_id, username):
        flash(request, f"已新增使用者 {username}")
    else:
        flash(request, f"{discord_id} 已經存在", "error")
    return redirect("/users")


@app.get("/users/{discord_id}", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
async def user_detail(request: Request, discord_id: str):
    user = await db.get_user(discord_id)
    if user is None:
        flash(request, "找不到這個使用者", "error")
        return redirect("/users")
    borrowed = await db.get_active_loans_by_borrower(discord_id)
    lent = await db.get_active_loans_by_lender(discord_id)
    return render(request, "user_detail.html", user=user, borrowed=borrowed, lent=lent)


@app.post("/users/{discord_id}/reminder", dependencies=[Depends(require_admin)])
async def update_reminder(
    request: Request,
    discord_id: str,
    enabled: str = Form(""),
    days: int = Form(...),
):
    if days < 1:
        flash(request, "天數至少為 1", "error")
    else:
        await db.set_reminder(discord_id, enabled == "on", days)
        flash(request, "提醒設定已更新")
    return redirect(f"/users/{discord_id}")
