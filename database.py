import asyncpg
import os

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    return _pool


async def init_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS _migrations (
                name       TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS users (
                discord_id       TEXT PRIMARY KEY,
                username         TEXT NOT NULL,
                reminder_enabled BOOLEAN DEFAULT FALSE,
                reminder_days    INTEGER DEFAULT 14
            );

            CREATE TABLE IF NOT EXISTS loans (
                id          SERIAL PRIMARY KEY,
                guild_id    TEXT NOT NULL,
                lender_id   TEXT NOT NULL,
                borrower_id TEXT NOT NULL,
                card_name   TEXT NOT NULL,
                edition     TEXT,
                quantity    INTEGER DEFAULT 1,
                note        TEXT,
                borrowed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                returned_at TIMESTAMPTZ
            );

            CREATE INDEX IF NOT EXISTS idx_loans_lender   ON loans(lender_id);
            CREATE INDEX IF NOT EXISTS idx_loans_borrower ON loans(borrower_id);
            CREATE INDEX IF NOT EXISTS idx_loans_guild    ON loans(guild_id);
        """)
        # 既有的 table 不會被 CREATE TABLE IF NOT EXISTS 更新，schema 預設值要手動補
        await conn.execute("ALTER TABLE users ALTER COLUMN reminder_days SET DEFAULT 14")
        await conn.execute("ALTER TABLE users ALTER COLUMN reminder_enabled SET DEFAULT FALSE")
        # 一次性把還停留在舊預設值的既有使用者資料改成新預設值，用 _migrations 記錄
        # 避免每次開機都重跑，蓋掉使用者之後自己明確設定的值
        await _run_once(conn, "reminder_days_7_to_14",
                         "UPDATE users SET reminder_days = 14 WHERE reminder_days = 7")
        await _run_once(conn, "reminder_enabled_default_off",
                         "UPDATE users SET reminder_enabled = FALSE")


async def _run_once(conn, name: str, sql: str):
    applied = await conn.fetchval("SELECT 1 FROM _migrations WHERE name=$1", name)
    if applied:
        return
    await conn.execute(sql)
    await conn.execute("INSERT INTO _migrations(name) VALUES ($1)", name)


async def upsert_user(discord_id: str, username: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO users(discord_id, username) VALUES($1,$2)
               ON CONFLICT(discord_id) DO UPDATE SET username=EXCLUDED.username""",
            discord_id, username,
        )


async def get_user(discord_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE discord_id=$1", discord_id)


async def add_loan(guild_id, lender_id, borrower_id, card_name, edition, quantity, note):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO loans(guild_id, lender_id, borrower_id, card_name, edition, quantity, note)
               VALUES($1,$2,$3,$4,$5,$6,$7)""",
            guild_id, lender_id, borrower_id, card_name, edition, quantity, note,
        )


async def add_loans_bulk(guild_id, lender_id, borrower_id, items, edition, note):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            ids = []
            for card_name, quantity in items:
                loan_id = await conn.fetchval(
                    """INSERT INTO loans(guild_id, lender_id, borrower_id, card_name, edition, quantity, note)
                       VALUES($1,$2,$3,$4,$5,$6,$7) RETURNING id""",
                    guild_id, lender_id, borrower_id, card_name, edition, quantity, note,
                )
                ids.append(loan_id)
            return ids


async def get_active_loans_by_borrower(borrower_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            """SELECT l.*, u.username AS lender_name
               FROM loans l JOIN users u ON u.discord_id = l.lender_id
               WHERE l.borrower_id=$1 AND l.returned_at IS NULL
               ORDER BY l.borrowed_at""",
            borrower_id,
        )


async def get_active_loans_by_lender(lender_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            """SELECT l.*, u.username AS borrower_name
               FROM loans l JOIN users u ON u.discord_id = l.borrower_id
               WHERE l.lender_id=$1 AND l.returned_at IS NULL
               ORDER BY l.borrowed_at""",
            lender_id,
        )


async def get_loans_between(user_a: str, user_b: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            """SELECT l.*,
                      ul.username AS lender_name,
                      ub.username AS borrower_name
               FROM loans l
               JOIN users ul ON ul.discord_id = l.lender_id
               JOIN users ub ON ub.discord_id = l.borrower_id
               WHERE l.returned_at IS NULL
                 AND ((l.lender_id=$1 AND l.borrower_id=$2)
                   OR (l.lender_id=$2 AND l.borrower_id=$1))
               ORDER BY l.borrowed_at""",
            user_a, user_b,
        )


async def get_all_active_loans(guild_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            """SELECT l.*,
                      ul.username AS lender_name,
                      ub.username AS borrower_name
               FROM loans l
               JOIN users ul ON ul.discord_id = l.lender_id
               JOIN users ub ON ub.discord_id = l.borrower_id
               WHERE l.guild_id=$1 AND l.returned_at IS NULL
               ORDER BY l.borrowed_at""",
            guild_id,
        )


async def get_loan(loan_id: int, borrower_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """SELECT l.*, u.username AS lender_name
               FROM loans l JOIN users u ON u.discord_id = l.lender_id
               WHERE l.id=$1 AND l.borrower_id=$2 AND l.returned_at IS NULL""",
            loan_id, borrower_id,
        )


async def get_loan_by_lender(loan_id: int, lender_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """SELECT l.*, u.username AS borrower_name
               FROM loans l JOIN users u ON u.discord_id = l.borrower_id
               WHERE l.id=$1 AND l.lender_id=$2 AND l.returned_at IS NULL""",
            loan_id, lender_id,
        )


async def transfer_loan(loan_id: int, current_borrower_id: str, new_borrower_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            loan = await conn.fetchrow(
                """SELECT l.*, u.username AS lender_name
                   FROM loans l JOIN users u ON u.discord_id = l.lender_id
                   WHERE l.id=$1 AND l.borrower_id=$2 AND l.returned_at IS NULL
                   FOR UPDATE""",
                loan_id, current_borrower_id,
            )
            if loan is None:
                return None

            await conn.execute(
                "UPDATE loans SET borrower_id=$1 WHERE id=$2",
                new_borrower_id, loan_id,
            )
            return loan


async def _close_loan(loan_id: int, owner_column: str, owner_id: str, quantity: int | None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            loan = await conn.fetchrow(
                f"""SELECT * FROM loans
                    WHERE id=$1 AND {owner_column}=$2 AND returned_at IS NULL
                    FOR UPDATE""",
                loan_id, owner_id,
            )
            if loan is None:
                return None

            current_qty = loan["quantity"]
            return_qty = current_qty if quantity is None else min(quantity, current_qty)
            fully_returned = return_qty >= current_qty

            if fully_returned:
                await conn.execute(
                    "UPDATE loans SET returned_at=NOW() WHERE id=$1", loan_id,
                )
            else:
                await conn.execute(
                    "UPDATE loans SET quantity=quantity-$1 WHERE id=$2",
                    return_qty, loan_id,
                )

            return {
                "returned_qty": return_qty,
                "remaining_qty": current_qty - return_qty,
                "fully_returned": fully_returned,
                "card_name": loan["card_name"],
                "edition": loan["edition"],
            }


async def return_loan(loan_id: int, borrower_id: str, quantity: int | None = None):
    return await _close_loan(loan_id, "borrower_id", borrower_id, quantity)


async def reclaim_loan(loan_id: int, lender_id: str, quantity: int | None = None):
    return await _close_loan(loan_id, "lender_id", lender_id, quantity)


async def get_overdue_loans():
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            """SELECT l.*,
                      ul.username AS lender_name,
                      ub.username AS borrower_name,
                      u.reminder_enabled,
                      u.reminder_days
               FROM loans l
               JOIN users ul ON ul.discord_id = l.lender_id
               JOIN users ub ON ub.discord_id = l.borrower_id
               JOIN users u  ON u.discord_id  = l.borrower_id
               WHERE l.returned_at IS NULL
                 AND u.reminder_enabled = TRUE
                 AND NOW() - l.borrowed_at >= (u.reminder_days || ' days')::INTERVAL
               ORDER BY l.borrower_id, l.borrowed_at""",
        )


async def set_reminder(discord_id: str, enabled: bool, days: int | None = None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if days is not None:
            await conn.execute(
                "UPDATE users SET reminder_enabled=$1, reminder_days=$2 WHERE discord_id=$3",
                enabled, days, discord_id,
            )
        else:
            await conn.execute(
                "UPDATE users SET reminder_enabled=$1 WHERE discord_id=$2",
                enabled, discord_id,
            )


async def get_card_autocomplete(partial: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT card_name FROM loans WHERE card_name ILIKE $1 LIMIT 25",
            f"%{partial}%",
        )
        return [r["card_name"] for r in rows]
