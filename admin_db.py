"""管理員 GUI 專用的查詢。

借、還、轉借等寫入動作一律呼叫 database.py（跟 bot 同一份邏輯），
這裡只放 bot 沒有、但管理介面需要的「看全部」類查詢。
"""
from database import get_pool

_LOAN_SELECT = """
    SELECT l.*,
           ul.username AS lender_name,
           ub.username AS borrower_name
    FROM loans l
    JOIN users ul ON ul.discord_id = l.lender_id
    JOIN users ub ON ub.discord_id = l.borrower_id
"""


async def list_users():
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            """SELECT u.*,
                      (SELECT COALESCE(SUM(quantity), 0) FROM loans
                        WHERE borrower_id = u.discord_id AND returned_at IS NULL) AS borrowed_qty,
                      (SELECT COALESCE(SUM(quantity), 0) FROM loans
                        WHERE lender_id = u.discord_id AND returned_at IS NULL) AS lent_qty
               FROM users u
               ORDER BY LOWER(u.username)""",
        )


async def list_guilds() -> list[str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT guild_id, COUNT(*) AS n FROM loans GROUP BY guild_id ORDER BY n DESC"
        )
        return [r["guild_id"] for r in rows]


async def search_active_loans(guild_id: str | None, user_id: str | None, card: str | None):
    conditions = ["l.returned_at IS NULL"]
    args: list = []
    if guild_id:
        args.append(guild_id)
        conditions.append(f"l.guild_id = ${len(args)}")
    if user_id:
        args.append(user_id)
        conditions.append(f"(l.lender_id = ${len(args)} OR l.borrower_id = ${len(args)})")
    if card:
        args.append(f"%{card}%")
        conditions.append(f"l.card_name ILIKE ${len(args)}")

    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            f"{_LOAN_SELECT} WHERE {' AND '.join(conditions)} ORDER BY l.borrowed_at",
            *args,
        )


async def get_loan_any(loan_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(f"{_LOAN_SELECT} WHERE l.id = $1", loan_id)


async def get_returned_loans(limit: int = 200):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            f"{_LOAN_SELECT} WHERE l.returned_at IS NOT NULL ORDER BY l.returned_at DESC LIMIT $1",
            limit,
        )


async def add_user(discord_id: str, username: str) -> bool:
    """新增使用者；已存在就不動（避免蓋掉 bot 同步過來的名稱）。回傳是否真的新增。"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "INSERT INTO users(discord_id, username) VALUES($1,$2) ON CONFLICT DO NOTHING",
            discord_id, username,
        )
        return result.endswith(" 1")
