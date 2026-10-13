"""用 bot token 讀伺服器成員名單，讓還沒用過平台的人也能被選進借牌紀錄。

bot 不需要上線，只用 Discord REST API。需要：
- 環境變數 DISCORD_TOKEN（bot 的 token）
- Developer Portal → Bot → 打開 Server Members Intent
沒設定或讀取失敗時，就只列出 users 表裡已經有的人。
"""
import logging
import os
import time

import httpx

import admin_db

log = logging.getLogger(__name__)

API = "https://discord.com/api/v10"
CACHE_SECONDS = 600
_cache: dict[str, tuple[float, list[dict]]] = {}


def _bot_token() -> str:
    return os.getenv("DISCORD_TOKEN", "")


async def _guild_id() -> str | None:
    for var in ("GUILD_ID", "DEFAULT_GUILD_ID"):
        ids = [g.strip() for g in os.getenv(var, "").split(",") if g.strip()]
        if ids:
            return ids[0]
    guilds = await admin_db.list_guilds()
    return guilds[0] if guilds else None


async def fetch_guild_members(guild_id: str) -> list[dict]:
    """回傳 [{discord_id, username}]，username 用伺服器暱稱（跟 bot 的 display_name 一樣）。測試時會被替換掉。"""
    members: list[dict] = []
    after = "0"
    async with httpx.AsyncClient(timeout=10, headers={"Authorization": f"Bot {_bot_token()}"}) as client:
        while True:
            resp = await client.get(f"{API}/guilds/{guild_id}/members", params={"limit": 1000, "after": after})
            resp.raise_for_status()
            page = resp.json()
            for m in page:
                user = m["user"]
                if user.get("bot"):
                    continue
                members.append({
                    "discord_id": user["id"],
                    "username": m.get("nick") or user.get("global_name") or user["username"],
                })
            if len(page) < 1000:
                return members
            after = page[-1]["user"]["id"]


async def guild_members() -> list[dict]:
    if not _bot_token():
        return []
    guild_id = await _guild_id()
    if not guild_id:
        return []
    cached = _cache.get(guild_id)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    try:
        members = await fetch_guild_members(guild_id)
    except httpx.HTTPError as e:
        log.warning("讀取伺服器成員失敗：%s", e)
        return cached[1] if cached else []
    _cache[guild_id] = (time.monotonic() + CACHE_SECONDS, members)
    return members


async def selectable_users() -> list[dict]:
    """已註冊的使用者 + 伺服器裡還沒用過平台的成員（registered=False）。"""
    users = [
        {"discord_id": u["discord_id"], "username": u["username"], "registered": True}
        for u in await admin_db.list_users()
    ]
    known = {u["discord_id"] for u in users}
    newcomers = [
        {**m, "registered": False}
        for m in await guild_members() if m["discord_id"] not in known
    ]
    return sorted(users + newcomers, key=lambda u: u["username"].lower())


async def ensure_users(*discord_ids: str) -> str | None:
    """確保這些人都在 users 表；不在的話從伺服器成員名單建立。失敗回傳錯誤訊息。"""
    known = {u["discord_id"] for u in await admin_db.list_users()}
    missing = [i for i in discord_ids if i and i not in known]
    if not missing:
        return None
    members = {m["discord_id"]: m for m in await guild_members()}
    for discord_id in missing:
        member = members.get(discord_id)
        if member is None:
            return "找不到這個人，請重新整理頁面再選一次"
        await admin_db.add_user(discord_id, member["username"])
    return None
