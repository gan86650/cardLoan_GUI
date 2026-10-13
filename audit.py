"""稽核紀錄：誰在什麼時候新增、修改、歸還、轉借了哪筆借牌紀錄。

存在 GUI 自己的 audit_log 表。操作者名稱在寫入當下一起存下來，之後改名也看得出當時是誰。
"""
from fastapi import Request

import admin_db
import database as db
from database import get_pool
from web import flash

ACTIONS = {
    "create": "新增",
    "edit": "編輯",
    "return": "還牌",
    "reclaim": "取回",
    "transfer": "轉借",
    "return_request": "申請還牌",
    "return_approve": "同意還牌",
    "return_reject": "拒絕還牌",
    "return_cancel": "取消申請",
}


async def ensure_schema():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id         SERIAL PRIMARY KEY,
                actor_id   TEXT,
                actor_name TEXT NOT NULL,
                is_admin   BOOLEAN NOT NULL DEFAULT FALSE,
                action     TEXT NOT NULL,
                loan_id    INTEGER,
                summary    TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_audit_loan ON audit_log(loan_id);
            CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);
        """)


async def _actor(request: Request) -> tuple[str | None, str, bool]:
    user_id = request.session.get("user_id")
    is_admin = bool(request.session.get("admin"))
    if user_id:
        user = await db.get_user(user_id)
        return user_id, user["username"] if user else user_id, is_admin
    return None, "管理員（密碼登入）", is_admin


async def log(request: Request, action: str, loan_id: int | None, summary: str):
    actor_id, actor_name, is_admin = await _actor(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO audit_log(actor_id, actor_name, is_admin, action, loan_id, summary)
               VALUES($1,$2,$3,$4,$5,$6)""",
            actor_id, actor_name, is_admin, action, loan_id, summary,
        )


async def names(*discord_ids: str) -> list[str]:
    result = []
    for discord_id in discord_ids:
        user = await db.get_user(discord_id)
        result.append(user["username"] if user else discord_id)
    return result


def describe_loan(card_name: str, edition: str | None, quantity: int) -> str:
    edition_str = f" [{edition}]" if edition else ""
    return f"{card_name}{edition_str} x{quantity}"


async def search(loan_id: int | None = None, action: str = "", involving: str | None = None,
                 actor: str = "", limit: int = 300):
    """involving：只看這個人當過牌主、借用人或操作者的紀錄（成員頁用）。"""
    conditions, args = [], []
    if loan_id:
        args.append(loan_id); conditions.append(f"a.loan_id = ${len(args)}")
    if action:
        args.append(action); conditions.append(f"a.action = ${len(args)}")
    if actor:
        args.append(actor); conditions.append(f"a.actor_id = ${len(args)}")
    if involving:
        args.append(involving)
        n = len(args)
        conditions.append(
            f"(a.actor_id = ${n} OR a.loan_id IN (SELECT id FROM loans WHERE lender_id = ${n} OR borrower_id = ${n}))"
        )
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    args.append(limit)
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            f"SELECT a.* FROM audit_log a {where} ORDER BY a.created_at DESC, a.id DESC LIMIT ${len(args)}",
            *args,
        )


def change_text(changes: list[tuple[str, object, object]]) -> str:
    def fmt(v):
        return "（空白）" if v in (None, "") else f"「{v}」" if isinstance(v, str) else str(v)
    return "、".join(f"{label} {fmt(old)} → {fmt(new)}" for label, old, new in changes)


async def log_created(request: Request, ids: list[int], items: list[tuple[str, int]],
                      lender_id: str, borrower_id: str, edition: str | None):
    lender, borrower = await names(lender_id, borrower_id)
    for loan_id, (card_name, quantity) in zip(ids, items):
        await log(request, "create", loan_id,
                  f"{lender} 借給 {borrower}：{describe_loan(card_name, edition, quantity)}")


async def edit_loan(request: Request, loan, card_name: str, edition: str, quantity: int, note: str) -> bool:
    """驗證後改借牌紀錄並寫入稽核紀錄；有錯誤或沒變更時用 flash 告訴使用者。回傳是否成功。"""
    card_name = card_name.strip()
    if not card_name:
        flash(request, "卡名不可空白", "error")
        return False
    if quantity < 1:
        flash(request, "數量至少為 1", "error")
        return False
    changes = await admin_db.update_loan(
        loan["id"], card_name, edition.strip() or None, quantity, note.strip() or None,
    )
    if changes is None:
        flash(request, f"找不到 #{loan['id']}", "error")
        return False
    if not changes:
        flash(request, "沒有任何變更")
        return True
    await log(request, "edit", loan["id"], f"{loan['card_name']}：{change_text(changes)}")
    flash(request, f"#{loan['id']} 已更新：{change_text(changes)}")
    return True
