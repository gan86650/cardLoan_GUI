"""還牌申請：借用人送出申請，牌主同意後才真的寫入歸還。

這張表只有 GUI 會用到，bot 不認得它，所以 GUI 啟動時自己建（只新增、不改 bot 的表）。
申請只在「紀錄還沒還、而且借用人沒變」時有效；轉借或被牌主直接取回後，
舊申請在查詢時就會被略過，同意時也會被擋下。
"""
import database as db
from database import get_pool

_VALID_PENDING = """
    r.status = 'pending'
    AND l.returned_at IS NULL
    AND l.borrower_id = r.requester_id
"""

_REQUEST_SELECT = """
    SELECT r.id AS request_id, r.quantity AS request_qty, r.created_at AS requested_at,
           l.*,
           ul.username AS lender_name,
           ub.username AS borrower_name
    FROM return_requests r
    JOIN loans l  ON l.id = r.loan_id
    JOIN users ul ON ul.discord_id = l.lender_id
    JOIN users ub ON ub.discord_id = l.borrower_id
"""


async def ensure_schema():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS return_requests (
                id           SERIAL PRIMARY KEY,
                loan_id      INTEGER NOT NULL REFERENCES loans(id),
                requester_id TEXT NOT NULL,
                quantity     INTEGER NOT NULL,
                status       TEXT NOT NULL DEFAULT 'pending',
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                resolved_at  TIMESTAMPTZ
            );
            CREATE INDEX IF NOT EXISTS idx_return_requests_loan ON return_requests(loan_id);
        """)


async def create_return_request(loan_id: int, borrower_id: str, quantity: int) -> int | None:
    """建立還牌申請；同一筆紀錄已經有有效申請時回傳 None。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 鎖住借牌紀錄，避免同時送出兩張申請
            loan = await conn.fetchrow(
                "SELECT id FROM loans WHERE id=$1 AND borrower_id=$2 AND returned_at IS NULL FOR UPDATE",
                loan_id, borrower_id,
            )
            if loan is None:
                return None
            exists = await conn.fetchval(
                f"""SELECT 1 FROM return_requests r JOIN loans l ON l.id = r.loan_id
                    WHERE r.loan_id=$1 AND {_VALID_PENDING}""",
                loan_id,
            )
            if exists:
                return None
            return await conn.fetchval(
                """INSERT INTO return_requests(loan_id, requester_id, quantity)
                   VALUES($1,$2,$3) RETURNING id""",
                loan_id, borrower_id, quantity,
            )


async def pending_for_lender(lender_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            f"{_REQUEST_SELECT} WHERE l.lender_id=$1 AND {_VALID_PENDING} ORDER BY r.created_at",
            lender_id,
        )


async def pending_by_borrower(borrower_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            f"{_REQUEST_SELECT} WHERE r.requester_id=$1 AND {_VALID_PENDING} ORDER BY r.created_at",
            borrower_id,
        )


async def pending_loan_ids() -> set[int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT r.loan_id FROM return_requests r JOIN loans l ON l.id = r.loan_id WHERE {_VALID_PENDING}"
        )
        return {r["loan_id"] for r in rows}


async def _claim(request_id: int, new_status: str, owner_column: str, owner_id: str):
    """把有效的待確認申請改成 new_status，同時確認操作的人有權限。回傳申請內容或 None。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            f"""UPDATE return_requests r SET status=$3, resolved_at=NOW()
                FROM loans l
                WHERE r.id=$1 AND l.id = r.loan_id AND l.{owner_column}=$2 AND {_VALID_PENDING}
                RETURNING r.loan_id, r.requester_id, r.quantity, l.card_name""",
            request_id, owner_id, new_status,
        )


async def approve(request_id: int, lender_id: str):
    """牌主同意：先把申請搶下來（避免重複按兩次就還兩次），再走 bot 的 return_loan。"""
    req = await _claim(request_id, "approved", "lender_id", lender_id)
    if req is None:
        return None
    result = await db.return_loan(req["loan_id"], req["requester_id"], req["quantity"])
    if result is None:
        await _set_status(request_id, "void")
        return None
    return {**result, "loan_id": req["loan_id"]}


async def reject(request_id: int, lender_id: str):
    return await _claim(request_id, "rejected", "lender_id", lender_id)


async def cancel(request_id: int, borrower_id: str):
    return await _claim(request_id, "cancelled", "borrower_id", borrower_id)


async def _set_status(request_id: int, status: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE return_requests SET status=$2 WHERE id=$1", request_id, status)


async def has_pending(loan_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return bool(await conn.fetchval(
            f"SELECT 1 FROM return_requests r JOIN loans l ON l.id = r.loan_id WHERE r.loan_id=$1 AND {_VALID_PENDING}",
            loan_id,
        ))
