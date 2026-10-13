"""一般成員（Discord 登入）的頁面。

權限跟 bot 一致：
- 記錄（借牌、借出、紀錄他人、批次）：誰都可以記，直接寫入，不用牌主同意
- 還牌：只有借用人能申請，牌主同意後才寫入歸還
- 已取回：只有牌主能操作，直接寫入
- 轉借：只有目前的借用人能操作，直接寫入
- 不能新增使用者、不能改別人的提醒設定
"""
import os

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

import admin_db
import database as db
import member_db
from web import (
    MAX_BATCH_ITEMS, back, flash, parse_card_batch, redirect, render as _render,
    require_member,
)

router = APIRouter(prefix="/me")

MEMBER_MENU = [
    ("記錄", [
        ("/me/borrow", "📥", "借牌", "記錄你跟某人借了牌"),
        ("/me/lend", "📤", "借出", "記錄你借出牌給某人"),
        ("/me/record-others", "🙋", "紀錄他人", "代替別人記錄他們之間的借牌"),
        ("/me/batch", "📦", "批次紀錄", "同一個牌主借給同一個人多張牌，一次記錄"),
    ]),
    ("歸還與轉借", [
        ("/me/return", "✅", "還牌", "申請歸還你借的牌，牌主同意後才算還"),
        ("/me/approvals", "📨", "待我確認", "別人還你牌的申請，同意或拒絕"),
        ("/me/reclaim", "📬", "已取回", "你借出去的牌已經拿回來了"),
        ("/me/transfer", "🔁", "轉借", "把你借到的牌轉給另一個人"),
    ]),
    ("查詢", [
        ("/me", "🏠", "我的借牌", "借進來、借出去的牌"),
        ("/me/overview", "📋", "借牌總覽", "伺服器所有未還紀錄"),
        ("/me/between", "🔍", "查借牌", "你跟某人之間的借貸狀況"),
    ]),
    ("設定", [
        ("/me/settings", "⚙️", "個人設定", "改你在網站上顯示的名稱"),
    ]),
]


async def render(request: Request, name: str, me: str, **context):
    approvals = await member_db.pending_for_lender(me)
    user = await db.get_user(me)
    return _render(
        request, name,
        menu=MEMBER_MENU, me=me, me_name=user["username"] if user else "",
        approval_count=len(approvals), **context,
    )


async def _form_context():
    guilds = await admin_db.list_guilds()
    return {
        "users": await admin_db.list_users(),
        "guilds": guilds,
        "default_guild": os.getenv("DEFAULT_GUILD_ID") or (guilds[0] if guilds else ""),
        "card_names": await admin_db.list_card_names(),
    }


# ── 我的借牌 ──────────────────────────────────────────────────────────────────
@router.get("", response_class=HTMLResponse)
async def home(request: Request, me: str = Depends(require_member)):
    return await render(
        request, "me_home.html", me,
        borrowed=await db.get_active_loans_by_borrower(me),
        lent=await db.get_active_loans_by_lender(me),
        approvals=await member_db.pending_for_lender(me),
        my_requests=await member_db.pending_by_borrower(me),
        pending_ids=await member_db.pending_loan_ids(),
    )


@router.get("/overview", response_class=HTMLResponse)
async def overview(request: Request, card: str = "", me: str = Depends(require_member)):
    loans = await admin_db.search_active_loans(None, None, card.strip() or None)
    return await render(
        request, "me_overview.html", me, loans=loans, card=card,
        total_qty=sum(l["quantity"] for l in loans),
        pending_ids=await member_db.pending_loan_ids(),
    )


@router.get("/between", response_class=HTMLResponse)
async def between(request: Request, other: str = "", me: str = Depends(require_member)):
    users = [u for u in await admin_db.list_users() if u["discord_id"] != me]
    loans = await db.get_loans_between(me, other) if other and other != me else None
    return await render(
        request, "me_between.html", me, users=users, other=other, loans=loans,
        pending_ids=await member_db.pending_loan_ids(),
    )


# ── 記錄 ──────────────────────────────────────────────────────────────────────
RECORD_MODES = {
    "borrow": ("📥 借牌", "記錄你跟某人借了牌。直接寫入，不用牌主同意。"),
    "lend": ("📤 借出", "記錄你借出牌給某人。直接寫入，不用對方同意。"),
    "record-others": ("🙋 紀錄他人", "代替別人記錄他們之間的借牌關係。"),
}


@router.get("/borrow", response_class=HTMLResponse)
@router.get("/lend", response_class=HTMLResponse)
@router.get("/record-others", response_class=HTMLResponse)
async def record_page(request: Request, me: str = Depends(require_member)):
    mode = request.url.path.rsplit("/", 1)[1]
    title, desc = RECORD_MODES[mode]
    return await render(
        request, "me_record.html", me, mode=mode, title=title, desc=desc,
        **await _form_context(),
    )


@router.post("/borrow")
@router.post("/lend")
@router.post("/record-others")
async def create_record(
    request: Request,
    guild_id: str = Form(...),
    lender_id: str = Form(""),
    borrower_id: str = Form(""),
    card_name: str = Form(...),
    edition: str = Form(""),
    quantity: int = Form(1),
    note: str = Form(""),
    me: str = Depends(require_member),
):
    mode = request.url.path.rsplit("/", 1)[1]
    if mode == "borrow":
        borrower_id = me
    elif mode == "lend":
        lender_id = me
    target = f"/me/{mode}"

    if not lender_id or not borrower_id:
        flash(request, "請選擇牌主和借用人", "error")
        return redirect(target)
    if lender_id == borrower_id:
        flash(request, "不能跟自己借牌" if me in (lender_id, borrower_id) else "牌主跟借用人不能是同一人", "error")
        return redirect(target)
    if not card_name.strip():
        flash(request, "請輸入卡名", "error")
        return redirect(target)
    if quantity < 1:
        flash(request, "數量至少為 1", "error")
        return redirect(target)

    ids = await db.add_loans_bulk(
        guild_id.strip(), lender_id, borrower_id, [(card_name.strip(), quantity)],
        edition.strip() or None, note.strip() or None,
    )
    flash(request, f"已記錄 #{ids[0]}：{card_name.strip()} x{quantity}")
    return redirect(target)


@router.get("/batch", response_class=HTMLResponse)
async def batch_page(request: Request, me: str = Depends(require_member)):
    return await render(request, "batch.html", me, **await _form_context())


@router.post("/batch")
async def create_batch(
    request: Request,
    guild_id: str = Form(...),
    lender_id: str = Form(...),
    borrower_id: str = Form(...),
    cards: str = Form(...),
    edition: str = Form(""),
    note: str = Form(""),
    me: str = Depends(require_member),
):
    if lender_id == borrower_id:
        flash(request, "牌主跟借用人不能是同一人", "error")
        return redirect("/me/batch")
    items = parse_card_batch(cards)
    if not items:
        flash(request, "至少要輸入一張卡", "error")
        return redirect("/me/batch")
    if len(items) > MAX_BATCH_ITEMS:
        flash(request, f"一次最多只能記錄 {MAX_BATCH_ITEMS} 筆，請分批輸入", "error")
        return redirect("/me/batch")
    if any(qty < 1 for _, qty in items):
        flash(request, "數量至少為 1", "error")
        return redirect("/me/batch")

    ids = await db.add_loans_bulk(
        guild_id.strip(), lender_id, borrower_id, items,
        edition.strip() or None, note.strip() or None,
    )
    flash(request, f"已記錄 {len(ids)} 筆：" + "、".join(f"#{i}" for i in ids))
    return redirect("/me/batch")


# ── 還牌（申請 → 牌主同意）────────────────────────────────────────────────────
def _parse_qty(request: Request, raw: str, max_qty: int) -> int | None | bool:
    """留空回傳 None（全部），格式錯誤回傳 False。"""
    raw = raw.strip()
    if not raw:
        return None
    if not raw.isdigit() or int(raw) < 1:
        flash(request, "數量必須是大於 0 的整數", "error")
        return False
    if int(raw) > max_qty:
        flash(request, f"數量不可超過剩餘的 {max_qty} 張", "error")
        return False
    return int(raw)


@router.get("/return", response_class=HTMLResponse)
async def return_page(request: Request, id: int | None = None, me: str = Depends(require_member)):
    pending_ids = await member_db.pending_loan_ids()
    loans = [l for l in await db.get_active_loans_by_borrower(me) if l["id"] not in pending_ids]
    return await render(
        request, "me_return.html", me, loans=loans, selected=id,
        my_requests=await member_db.pending_by_borrower(me),
    )


@router.post("/return")
async def request_return(
    request: Request,
    loan_id: int = Form(...),
    quantity: str = Form(""),
    me: str = Depends(require_member),
):
    target = back(request, "/me/return")
    loan = await db.get_loan(loan_id, me)
    if loan is None:
        flash(request, f"找不到 #{loan_id}，或這筆紀錄不是你借的", "error")
        return redirect(target)
    qty = _parse_qty(request, quantity, loan["quantity"])
    if qty is False:
        return redirect(target)

    request_id = await member_db.create_return_request(loan_id, me, qty or loan["quantity"])
    if request_id is None:
        flash(request, f"#{loan_id} 已經有一筆還牌申請在等 {loan['lender_name']} 確認", "error")
    else:
        flash(request, f"已送出還牌申請：#{loan_id} {loan['card_name']} x{qty or loan['quantity']}，等 {loan['lender_name']} 確認")
    return redirect(target)


@router.post("/return/{request_id}/cancel")
async def cancel_return(request: Request, request_id: int, me: str = Depends(require_member)):
    if await member_db.cancel(request_id, me):
        flash(request, "已取消還牌申請")
    else:
        flash(request, "這筆申請已經處理過了", "error")
    return redirect(back(request, "/me/return"))


@router.get("/approvals", response_class=HTMLResponse)
async def approvals_page(request: Request, me: str = Depends(require_member)):
    return await render(
        request, "me_approvals.html", me,
        approvals=await member_db.pending_for_lender(me),
    )


@router.post("/approvals/{request_id}/approve")
async def approve_return(request: Request, request_id: int, me: str = Depends(require_member)):
    result = await member_db.approve(request_id, me)
    if result is None:
        flash(request, "這筆申請已經處理過，或紀錄已經變更", "error")
    elif result["fully_returned"]:
        flash(request, f"已確認歸還：{result['card_name']} 全部還清")
    else:
        flash(request, f"已確認歸還：{result['card_name']} {result['returned_qty']} 張，還剩 {result['remaining_qty']} 張")
    return redirect(back(request, "/me/approvals"))


@router.post("/approvals/{request_id}/reject")
async def reject_return(request: Request, request_id: int, me: str = Depends(require_member)):
    req = await member_db.reject(request_id, me)
    if req is None:
        flash(request, "這筆申請已經處理過了", "error")
    else:
        flash(request, f"已拒絕 #{req['loan_id']} {req['card_name']} 的還牌申請")
    return redirect(back(request, "/me/approvals"))


# ── 已取回（牌主直接寫入）────────────────────────────────────────────────────
@router.get("/reclaim", response_class=HTMLResponse)
async def reclaim_page(request: Request, id: int | None = None, me: str = Depends(require_member)):
    return await render(
        request, "close_loan.html", me, mode="reclaim", action="/me/reclaim",
        loans=await db.get_active_loans_by_lender(me), selected=id,
    )


@router.post("/reclaim")
async def reclaim(
    request: Request,
    loan_id: int = Form(...),
    quantity: str = Form(""),
    me: str = Depends(require_member),
):
    target = back(request, "/me/reclaim")
    loan = await db.get_loan_by_lender(loan_id, me)
    if loan is None:
        flash(request, f"找不到 #{loan_id}，或這筆紀錄不是你借出的", "error")
        return redirect(target)
    qty = _parse_qty(request, quantity, loan["quantity"])
    if qty is False:
        return redirect(target)

    result = await db.reclaim_loan(loan_id, me, qty)
    if result is None:
        flash(request, f"#{loan_id} 已經取回了", "error")
    elif result["fully_returned"]:
        flash(request, f"#{loan_id} {result['card_name']} 已全部取回")
    else:
        flash(request, f"#{loan_id} {result['card_name']} 取回 {result['returned_qty']} 張，{loan['borrower_name']} 還欠 {result['remaining_qty']} 張")
    return redirect(target)


# ── 轉借（目前借用人直接寫入）────────────────────────────────────────────────
@router.get("/transfer", response_class=HTMLResponse)
async def transfer_page(request: Request, id: int | None = None, me: str = Depends(require_member)):
    pending_ids = await member_db.pending_loan_ids()
    loans = [l for l in await db.get_active_loans_by_borrower(me) if l["id"] not in pending_ids]
    users = [u for u in await admin_db.list_users() if u["discord_id"] != me]
    return await render(
        request, "transfer.html", me, action="/me/transfer",
        loans=loans, users=users, selected=id,
    )


@router.post("/transfer")
async def transfer(
    request: Request,
    loan_id: int = Form(...),
    new_borrower_id: str = Form(...),
    me: str = Depends(require_member),
):
    target = back(request, "/me/transfer")
    if new_borrower_id == me:
        flash(request, "牌已經在你這了", "error")
        return redirect(target)
    loan = await db.get_loan(loan_id, me)
    if loan is None:
        flash(request, f"找不到 #{loan_id}，或這筆紀錄不是你借的", "error")
        return redirect(target)
    if new_borrower_id == loan["lender_id"]:
        flash(request, "對方就是原本的牌主，請改用「還牌」", "error")
        return redirect(target)
    if await member_db.has_pending(loan_id):
        flash(request, f"#{loan_id} 有還牌申請在等確認，請先取消申請再轉借", "error")
        return redirect(target)
    new_user = await db.get_user(new_borrower_id)
    if new_user is None:
        flash(request, "找不到新借用人", "error")
        return redirect(target)

    if await db.transfer_loan(loan_id, me, new_borrower_id) is None:
        flash(request, f"#{loan_id} 狀態已變更，請重新整理後再試", "error")
    else:
        flash(request, f"#{loan_id} {loan['card_name']} 已轉借給 {new_user['username']}（牌主還是 {loan['lender_name']}）")
    return redirect(target)


# ── 個人設定 ──────────────────────────────────────────────────────────────────
@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, me: str = Depends(require_member)):
    user = await db.get_user(me)
    return await render(
        request, "me_settings.html", me, user=user, max_len=admin_db.MAX_NAME_LENGTH,
    )


@router.post("/settings")
async def update_settings(request: Request, username: str = Form(...), me: str = Depends(require_member)):
    error = await admin_db.rename_user(me, username)
    if error:
        flash(request, error, "error")
    else:
        user = await db.get_user(me)
        request.session["username"] = user["username"]
        flash(request, f"顯示名稱已改成「{user['username']}」")
    return redirect("/me/settings")
