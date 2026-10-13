import hmac
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

import admin_db  # noqa: E402
import auth  # noqa: E402
import database as db  # noqa: E402
import member  # noqa: E402
import member_db  # noqa: E402
from web import (  # noqa: E402
    MAX_BATCH_ITEMS, NotLoggedIn, back, days_since, flash, parse_card_batch,
    redirect, render, require_admin, templates,
)

ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
SESSION_SECRET = os.environ["SESSION_SECRET"]

# 側邊選單：照 bot 的 /指令說明 分組，首頁也用這份清單
MENU = [
    ("記錄", [
        ("/record", "📥", "借牌／借出／紀錄他人", "記錄一筆借牌：選牌主、借用人、卡名"),
        ("/batch", "📦", "批次紀錄", "同一個牌主借給同一個人多張牌，一次記錄"),
    ]),
    ("歸還與轉借", [
        ("/return", "✅", "還牌", "借用人把牌還回去（可部分歸還）"),
        ("/reclaim", "📬", "已取回", "牌主自行標記牌已取回（可部分取回）"),
        ("/transfer", "🔁", "轉借", "把借到的牌轉給另一個人，牌主不變"),
    ]),
    ("查詢", [
        ("/overview", "📋", "借牌總覽", "伺服器所有未還紀錄"),
        ("/borrowed", "📥", "借進來的牌", "某人借進來、還沒還的牌"),
        ("/lent", "📤", "借出去的牌", "某人借出去、還沒還的牌"),
        ("/between", "🔍", "查借牌", "兩個人之間的借貸狀況"),
        ("/history", "🗂️", "歸還紀錄", "最近歸還的紀錄"),
    ]),
    ("設定", [
        ("/reminders", "🔔", "提醒設定", "每個人的到期提醒開關與天數"),
        ("/users", "👤", "使用者", "使用者名單、手動新增使用者"),
    ]),
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 不跑 db.init_db()：bot 的資料表與 migration 交給 bot。
    # GUI 只額外建自己用的 return_requests 表（還牌申請），不動 bot 的表。
    await db.get_pool()
    await member_db.ensure_schema()
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
app.include_router(auth.router)
app.include_router(member.router)
templates.env.globals["MENU"] = MENU


@app.exception_handler(NotLoggedIn)
async def _redirect_to_login(request: Request, exc: NotLoggedIn):
    return redirect("/login")


admin = [Depends(require_admin)]


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("admin") or request.session.get("user_id"):
        return redirect("/")
    return render(
        request, "login.html",
        discord_enabled=auth.DISCORD_ENABLED,
        dev_users=await admin_db.list_users() if auth.DEV_LOGIN else None,
    )


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    if hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode()):
        request.session.clear()
        request.session["admin"] = True
        return redirect("/")
    flash(request, "密碼錯誤", "error")
    return redirect("/login")


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return redirect("/login")


# ── 首頁（/指令說明）────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if not request.session.get("admin"):
        return redirect("/me" if request.session.get("user_id") else "/login")
    loans = await admin_db.search_active_loans(None, None, None)
    overdue = [l for l in loans if days_since(l["borrowed_at"]) >= 14]
    return render(
        request, "home.html",
        pending_count=len(await member_db.pending_loan_ids()),
        active_count=len(loans),
        active_qty=sum(l["quantity"] for l in loans),
        overdue=overdue,
    )


# ── /借牌總覽 ─────────────────────────────────────────────────────────────────
@app.get("/overview", response_class=HTMLResponse, dependencies=admin)
async def overview(request: Request, guild: str = "", user: str = "", card: str = ""):
    users = await admin_db.list_users()
    guilds = await admin_db.list_guilds()
    loans = await admin_db.search_active_loans(guild or None, user or None, card.strip() or None)
    return render(
        request, "overview.html",
        loans=loans, users=users, guilds=guilds,
        filters={"guild": guild, "user": user, "card": card},
        total_qty=sum(l["quantity"] for l in loans),
    )


# ── /借進來的牌、/借出去的牌、/查借牌 ─────────────────────────────────────────
@app.get("/borrowed", response_class=HTMLResponse, dependencies=admin)
async def borrowed(request: Request, user: str = ""):
    users = await admin_db.list_users()
    loans = await db.get_active_loans_by_borrower(user) if user else None
    return render(request, "by_user.html", mode="borrowed", users=users, user=user, loans=loans)


@app.get("/lent", response_class=HTMLResponse, dependencies=admin)
async def lent(request: Request, user: str = ""):
    users = await admin_db.list_users()
    loans = await db.get_active_loans_by_lender(user) if user else None
    return render(request, "by_user.html", mode="lent", users=users, user=user, loans=loans)


@app.get("/between", response_class=HTMLResponse, dependencies=admin)
async def between(request: Request, a: str = "", b: str = ""):
    users = await admin_db.list_users()
    loans = await db.get_loans_between(a, b) if a and b and a != b else None
    return render(request, "between.html", users=users, a=a, b=b, loans=loans)


# ── /借牌、/借出、/紀錄他人（單筆）與 /批次紀錄 ────────────────────────────────
async def _form_context():
    guilds = await admin_db.list_guilds()
    return {
        "users": await admin_db.list_users(),
        "guilds": guilds,
        "default_guild": os.getenv("DEFAULT_GUILD_ID") or (guilds[0] if guilds else ""),
        "card_names": await admin_db.list_card_names(),
    }


@app.get("/record", response_class=HTMLResponse, dependencies=admin)
async def record_page(request: Request):
    return render(request, "record.html", **await _form_context())


@app.get("/batch", response_class=HTMLResponse, dependencies=admin)
async def batch_page(request: Request):
    return render(request, "batch.html", **await _form_context())


def _check_parties(request: Request, guild_id: str, lender_id: str, borrower_id: str) -> bool:
    if not guild_id.strip():
        flash(request, "請填伺服器 ID", "error")
        return False
    if lender_id == borrower_id:
        flash(request, "牌主跟借用人不能是同一人", "error")
        return False
    return True


@app.post("/record", dependencies=admin)
async def create_record(
    request: Request,
    guild_id: str = Form(...),
    lender_id: str = Form(...),
    borrower_id: str = Form(...),
    card_name: str = Form(...),
    edition: str = Form(""),
    quantity: int = Form(1),
    note: str = Form(""),
):
    if not _check_parties(request, guild_id, lender_id, borrower_id):
        return redirect("/record")
    if not card_name.strip():
        flash(request, "請輸入卡名", "error")
        return redirect("/record")
    if quantity < 1:
        flash(request, "數量至少為 1", "error")
        return redirect("/record")

    ids = await db.add_loans_bulk(
        guild_id.strip(), lender_id, borrower_id, [(card_name.strip(), quantity)],
        edition.strip() or None, note.strip() or None,
    )
    flash(request, f"已新增紀錄 #{ids[0]}：{card_name.strip()} x{quantity}")
    return redirect("/record")


@app.post("/batch", dependencies=admin)
async def create_batch(
    request: Request,
    guild_id: str = Form(...),
    lender_id: str = Form(...),
    borrower_id: str = Form(...),
    cards: str = Form(...),
    edition: str = Form(""),
    note: str = Form(""),
):
    if not _check_parties(request, guild_id, lender_id, borrower_id):
        return redirect("/batch")

    items = parse_card_batch(cards)
    if not items:
        flash(request, "至少要輸入一張卡", "error")
        return redirect("/batch")
    if len(items) > MAX_BATCH_ITEMS:
        flash(request, f"一次最多只能記錄 {MAX_BATCH_ITEMS} 筆，請分批輸入", "error")
        return redirect("/batch")
    if any(qty < 1 for _, qty in items):
        flash(request, "數量至少為 1", "error")
        return redirect("/batch")

    ids = await db.add_loans_bulk(
        guild_id.strip(), lender_id, borrower_id, items,
        edition.strip() or None, note.strip() or None,
    )
    flash(request, f"已新增 {len(ids)} 筆紀錄：" + "、".join(f"#{i}" for i in ids))
    return redirect("/batch")


# ── /還牌、/已取回、/轉借 ─────────────────────────────────────────────────────
@app.get("/return", response_class=HTMLResponse, dependencies=admin)
async def return_page(request: Request, id: int | None = None):
    loans = await admin_db.search_active_loans(None, None, None)
    return render(request, "close_loan.html", mode="return", loans=loans, selected=id)


@app.get("/reclaim", response_class=HTMLResponse, dependencies=admin)
async def reclaim_page(request: Request, id: int | None = None):
    loans = await admin_db.search_active_loans(None, None, None)
    return render(request, "close_loan.html", mode="reclaim", loans=loans, selected=id)


@app.post("/return", dependencies=admin)
@app.post("/reclaim", dependencies=admin)
async def close_loan(request: Request, loan_id: int = Form(...), quantity: str = Form("")):
    action = "取回" if request.url.path == "/reclaim" else "歸還"
    target = back(request, request.url.path)

    loan = await admin_db.get_loan_any(loan_id)
    if loan is None or loan["returned_at"] is not None:
        flash(request, f"找不到 #{loan_id}，或已經{action}了", "error")
        return redirect(target)

    qty: int | None = None
    if quantity.strip():
        if not quantity.strip().isdigit() or int(quantity) < 1:
            flash(request, "數量必須是大於 0 的整數", "error")
            return redirect(target)
        qty = int(quantity)
        if qty > loan["quantity"]:
            flash(request, f"數量不可超過剩餘的 {loan['quantity']} 張", "error")
            return redirect(target)

    # 還牌與已取回對資料庫的效果一樣，都用牌主身分走 reclaim_loan（跟 bot 同一段邏輯）
    result = await db.reclaim_loan(loan_id, loan["lender_id"], qty)
    if result is None:
        flash(request, f"#{loan_id} 已經{action}了", "error")
    elif result["fully_returned"]:
        flash(request, f"#{loan_id} {result['card_name']} 已全部{action}（{loan['borrower_name']} → {loan['lender_name']}）")
    else:
        flash(request, f"#{loan_id} {result['card_name']} {action} {result['returned_qty']} 張，剩 {result['remaining_qty']} 張未還")
    return redirect(target)


@app.get("/transfer", response_class=HTMLResponse, dependencies=admin)
async def transfer_page(request: Request, id: int | None = None):
    loans = await admin_db.search_active_loans(None, None, None)
    users = await admin_db.list_users()
    return render(request, "transfer.html", loans=loans, users=users, selected=id)


@app.post("/transfer", dependencies=admin)
async def transfer(request: Request, loan_id: int = Form(...), new_borrower_id: str = Form(...)):
    target = back(request, "/transfer")
    loan = await admin_db.get_loan_any(loan_id)
    if loan is None or loan["returned_at"] is not None:
        flash(request, f"找不到 #{loan_id}，或已經歸還了", "error")
        return redirect(target)
    if new_borrower_id == loan["borrower_id"]:
        flash(request, "牌已經在這個人手上了", "error")
        return redirect(target)
    if new_borrower_id == loan["lender_id"]:
        flash(request, "對方就是原本的牌主，請改用「還牌」", "error")
        return redirect(target)

    moved = await db.transfer_loan(loan_id, loan["borrower_id"], new_borrower_id)
    if moved is None:
        flash(request, f"#{loan_id} 狀態已變更，請重新整理後再試", "error")
    else:
        new_user = await db.get_user(new_borrower_id)
        flash(request, f"#{loan_id} {loan['card_name']} 已從 {loan['borrower_name']} 轉借給 {new_user['username']}")
    return redirect(target)


# ── 單筆紀錄詳細頁 ────────────────────────────────────────────────────────────
@app.get("/loans/{loan_id}", response_class=HTMLResponse, dependencies=admin)
async def loan_detail(request: Request, loan_id: int):
    loan = await admin_db.get_loan_any(loan_id)
    if loan is None:
        flash(request, f"找不到 #{loan_id}", "error")
        return redirect("/overview")
    users = await admin_db.list_users()
    return render(request, "loan_detail.html", loan=loan, users=users)


# ── 歸還紀錄 ──────────────────────────────────────────────────────────────────
@app.get("/history", response_class=HTMLResponse, dependencies=admin)
async def history(request: Request):
    loans = await admin_db.get_returned_loans()
    return render(request, "history.html", loans=loans)


# ── /提醒設定 ─────────────────────────────────────────────────────────────────
@app.get("/reminders", response_class=HTMLResponse, dependencies=admin)
async def reminders_page(request: Request):
    users = await admin_db.list_users()
    return render(request, "reminders.html", users=users)


@app.post("/users/{discord_id}/reminder", dependencies=admin)
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
        user = await db.get_user(discord_id)
        state = f"開啟（{days} 天）" if enabled == "on" else "關閉"
        flash(request, f"{user['username']} 的提醒已{state}")
    return redirect(back(request, "/reminders"))


# ── 使用者 ────────────────────────────────────────────────────────────────────
@app.get("/users", response_class=HTMLResponse, dependencies=admin)
async def users_page(request: Request):
    users = await admin_db.list_users()
    return render(request, "users.html", users=users)


@app.post("/users", dependencies=admin)
async def create_user(request: Request, discord_id: str = Form(...), username: str = Form(...)):
    discord_id, username = discord_id.strip(), username.strip()
    if not discord_id.isdigit() or not username:
        flash(request, "Discord ID 必須是數字，名稱不可空白", "error")
    elif await admin_db.add_user(discord_id, username):
        flash(request, f"已新增使用者 {username}")
    else:
        flash(request, f"{discord_id} 已經存在", "error")
    return redirect("/users")


@app.get("/users/{discord_id}", response_class=HTMLResponse, dependencies=admin)
async def user_detail(request: Request, discord_id: str):
    user = await db.get_user(discord_id)
    if user is None:
        flash(request, "找不到這個使用者", "error")
        return redirect("/users")
    borrowed = await db.get_active_loans_by_borrower(discord_id)
    lent = await db.get_active_loans_by_lender(discord_id)
    return render(request, "user_detail.html", user=user, borrowed=borrowed, lent=lent)
