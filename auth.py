"""Discord 登入（OAuth2），只讓借牌伺服器的成員登入。"""
import os
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Form, Request

import admin_db
import database as db
from web import flash, redirect, start_session

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "")
DISCORD_ENABLED = bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET)

# 本機測試用：不經過 Discord，直接選一個使用者登入。正式環境絕對不能開。
DEV_LOGIN = os.getenv("DEV_LOGIN") == "1"
if DEV_LOGIN and os.getenv("RAILWAY_ENVIRONMENT"):
    raise RuntimeError("DEV_LOGIN 只能在本機使用，Railway 上請拿掉這個變數")

# 這些 Discord ID 用 Discord 登入時會同時拿到管理員權限（逗號分隔）
ADMIN_DISCORD_IDS = {i.strip() for i in os.getenv("ADMIN_DISCORD_IDS", "").split(",") if i.strip()}

API = "https://discord.com/api/v10"

router = APIRouter()


def _redirect_uri(request: Request) -> str:
    return DISCORD_REDIRECT_URI or str(request.url_for("discord_callback"))


async def allowed_guilds() -> set[str]:
    configured = os.getenv("GUILD_ID") or os.getenv("DEFAULT_GUILD_ID") or ""
    ids = {g.strip() for g in configured.split(",") if g.strip()}
    return ids or set(await admin_db.list_guilds())


async def fetch_discord_identity(code: str, redirect_uri: str) -> tuple[dict, list[dict]]:
    """用授權碼換 token，回傳 (使用者資料, 所在伺服器列表)。測試時會被替換掉。"""
    async with httpx.AsyncClient(timeout=10) as client:
        token = await client.post(
            f"{API}/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            auth=(DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET),
        )
        token.raise_for_status()
        headers = {"Authorization": f"Bearer {token.json()['access_token']}"}
        me = await client.get(f"{API}/users/@me", headers=headers)
        me.raise_for_status()
        guilds = await client.get(f"{API}/users/@me/guilds", headers=headers)
        guilds.raise_for_status()
        return me.json(), guilds.json()


async def _sign_in(request: Request, discord_id: str, fallback_name: str):
    # 新使用者才寫入；已存在的保留 bot 同步的伺服器暱稱
    await admin_db.add_user(discord_id, fallback_name)
    start_session(request, admin=discord_id in ADMIN_DISCORD_IDS, user_id=discord_id)


@router.get("/auth/discord")
async def discord_login(request: Request):
    if not DISCORD_ENABLED:
        flash(request, "還沒設定 Discord 登入", "error")
        return redirect("/login")
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": _redirect_uri(request),
        "response_type": "code",
        "scope": "identify guilds",
        "state": state,
        "prompt": "none",
    }
    return redirect(f"https://discord.com/oauth2/authorize?{urlencode(params)}")


@router.get("/auth/discord/callback", name="discord_callback")
async def discord_callback(request: Request, code: str = "", state: str = ""):
    expected = request.session.pop("oauth_state", None)
    if not code or not expected or not secrets.compare_digest(state, expected):
        flash(request, "Discord 登入失敗，請再試一次", "error")
        return redirect("/login")

    try:
        me, guilds = await fetch_discord_identity(code, _redirect_uri(request))
    except httpx.HTTPError:
        flash(request, "無法連到 Discord，請再試一次", "error")
        return redirect("/login")

    if not {g["id"] for g in guilds} & await allowed_guilds():
        flash(request, "只有借牌伺服器的成員可以登入", "error")
        return redirect("/login")

    await _sign_in(request, me["id"], me.get("global_name") or me["username"])
    return redirect("/")


@router.post("/dev-login")
async def dev_login(request: Request, discord_id: str = Form(...)):
    if not DEV_LOGIN:
        return redirect("/login")
    user = await db.get_user(discord_id)
    if user is None:
        flash(request, "找不到這個使用者", "error")
        return redirect("/login")
    await _sign_in(request, discord_id, user["username"])
    return redirect("/")
