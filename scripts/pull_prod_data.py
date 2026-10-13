"""把正式資料庫的資料複製到本機資料庫，用來在本機拿真實資料測試。

    python scripts/pull_prod_data.py

讀 .env 的兩個變數：
  SOURCE_DATABASE_URL  正式資料庫（Railway Postgres 的 DATABASE_PUBLIC_URL），只會讀取
  DATABASE_URL         本機資料庫，會被清空後寫入正式資料；只接受 localhost，避免寫錯地方
"""
import asyncio
import os
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv()

import asyncpg  # noqa: E402

import database as db  # noqa: E402
import member_db  # noqa: E402

TABLES = ["_migrations", "users", "loans", "return_requests"]


async def main():
    source = os.environ.get("SOURCE_DATABASE_URL")
    target = os.environ.get("DATABASE_URL")
    if not source or not target:
        sys.exit(".env 需要 SOURCE_DATABASE_URL 和 DATABASE_URL")
    if urlparse(target).hostname not in ("localhost", "127.0.0.1", "::1"):
        sys.exit(f"DATABASE_URL 不是本機（{urlparse(target).hostname}），為了安全不執行")

    # 建好本機的表（bot 的表 + GUI 的 return_requests）
    await db.init_db()
    await member_db.ensure_schema()
    await db._pool.close()

    src = await asyncpg.connect(source)
    dst = await asyncpg.connect(target)
    try:
        async with src.transaction(isolation="repeatable_read", readonly=True):
            data = {}
            for table in TABLES:
                if await src.fetchval("SELECT to_regclass($1)", table):
                    data[table] = await src.fetch(f"SELECT * FROM {table}")

        async with dst.transaction():
            await dst.execute(f"TRUNCATE {', '.join(TABLES)} CASCADE")
            for table, rows in data.items():
                if rows:
                    await dst.copy_records_to_table(table, records=rows, columns=list(rows[0].keys()))
                print(f"{table}: {len(rows)} 筆")
            for table in ("loans", "return_requests"):
                await dst.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'),"
                    f" COALESCE((SELECT MAX(id) FROM {table}), 1), (SELECT COUNT(*) > 0 FROM {table}))"
                )
    finally:
        await src.close()
        await dst.close()
    print("完成")


asyncio.run(main())
