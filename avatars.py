"""頭像：自訂上傳的頭像優先，其次用 Discord 頭像，都沒有就用 Discord 的預設頭像。

自訂頭像存在 GUI 自己的 user_profiles 表（Railway 的檔案系統重新部署就會清空，所以不存檔案）。
上傳的圖一律用 Pillow 重新編碼成 128x128 的 WebP，順便去掉圖片裡夾帶的其他資料。
"""
import io

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from PIL import Image, ImageOps, UnidentifiedImageError

import guild_members
from database import get_pool
from web import back, flash, redirect, require_member

SIZE = 128
MAX_UPLOAD_BYTES = 3 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 25_000_000  # 擋掉解壓縮炸彈

router = APIRouter()


async def ensure_schema():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                discord_id   TEXT PRIMARY KEY,
                avatar_hash  TEXT,
                custom_image BYTEA,
                updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)


async def set_discord_avatar(discord_id: str, avatar_hash: str | None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO user_profiles(discord_id, avatar_hash) VALUES($1,$2)
               ON CONFLICT(discord_id) DO UPDATE SET avatar_hash=EXCLUDED.avatar_hash""",
            discord_id, avatar_hash,
        )


async def _get_profile(discord_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM user_profiles WHERE discord_id=$1", discord_id)


def process_image(data: bytes) -> bytes:
    """裁成正方形、縮成 128x128、轉成 WebP。不是圖片會丟出 ValueError。"""
    try:
        img = Image.open(io.BytesIO(data))
        img.seek(0)  # GIF 等動圖只取第一格
        img = ImageOps.exif_transpose(img)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as e:
        raise ValueError("看不懂這個檔案，請上傳 PNG、JPG、WebP 或 GIF 圖片") from e
    img = ImageOps.fit(img.convert("RGBA"), (SIZE, SIZE), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "WEBP", quality=85)
    return out.getvalue()


def _default_avatar(discord_id: str) -> str:
    index = (int(discord_id) >> 22) % 6 if discord_id.isdigit() else 0
    return f"https://cdn.discordapp.com/embed/avatars/{index}.png"


@router.get("/avatar/{discord_id}")
async def avatar(request: Request, discord_id: str):
    if not (request.session.get("admin") or request.session.get("user_id")):
        return Response(status_code=401)
    profile = await _get_profile(discord_id)
    if profile and profile["custom_image"]:
        return Response(
            profile["custom_image"], media_type="image/webp",
            headers={"Cache-Control": "private, max-age=60", "X-Content-Type-Options": "nosniff"},
        )
    avatar_hash = profile["avatar_hash"] if profile else None
    if not avatar_hash:
        avatar_hash = next(
            (m.get("avatar") for m in await guild_members.guild_members() if m["discord_id"] == discord_id),
            None,
        )
    url = (f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar_hash}.png?size=128"
           if avatar_hash else _default_avatar(discord_id))
    return RedirectResponse(url, status_code=302, headers={"Cache-Control": "private, max-age=600"})


@router.post("/me/avatar")
async def upload_avatar(request: Request, image: UploadFile = File(...), me: str = Depends(require_member)):
    data = await image.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        flash(request, "圖片太大了，請小於 3 MB", "error")
        return redirect(back(request, "/me/settings"))
    try:
        webp = process_image(data)
    except ValueError as e:
        flash(request, str(e), "error")
        return redirect(back(request, "/me/settings"))
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO user_profiles(discord_id, custom_image, updated_at) VALUES($1,$2,NOW())
               ON CONFLICT(discord_id) DO UPDATE SET custom_image=EXCLUDED.custom_image, updated_at=NOW()""",
            me, webp,
        )
    flash(request, "頭像已更新")
    return redirect("/me/settings")


@router.post("/me/avatar/reset")
async def reset_avatar(request: Request, me: str = Depends(require_member)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE user_profiles SET custom_image=NULL, updated_at=NOW() WHERE discord_id=$1", me,
        )
    flash(request, "已改回 Discord 頭像")
    return redirect("/me/settings")
