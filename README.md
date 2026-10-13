# cardLoan_GUI

[cardLoan](https://github.com/gan86650/cardLoan) 借牌 Discord bot 的管理員網頁介面。
直接連 bot 用的同一個 PostgreSQL，借、還、轉借都呼叫跟 bot 同一份 `database.py`。

## 功能

每個 bot 指令都有對應的頁面，左側選單照 `/指令說明` 分組，首頁列出所有功能。

| 頁面 | 對應的 bot 指令 |
| --- | --- |
| 借牌／借出／紀錄他人 | `/借牌`、`/借出`、`/紀錄他人` |
| 批次紀錄（一行一張，`數量 卡名`） | `/批次紀錄` |
| 還牌、已取回（可部分） | `/還牌`、`/已取回` |
| 轉借 | `/轉借` |
| 借牌總覽（每列都有還牌／已取回／轉借按鈕） | `/借牌總覽` |
| 借進來的牌、借出去的牌 | `/借進來的牌`、`/借出去的牌` |
| 查借牌 | `/查借牌` |
| 提醒設定（所有人一頁改完） | `/提醒設定` |
| 歸還紀錄、使用者 | （bot 沒有） |

管理員可以代任何人操作。`ADMIN_DISCORD_IDS` 裡的人用 Discord 登入就是管理員；本機也可以設 `ADMIN_PASSWORD` 用密碼登入。

## 一般成員（Discord 登入）

借牌伺服器的成員用 Discord 登入，在 `/me` 底下使用。權限跟 bot 一致：

| 功能 | 誰可以用 | 寫入時機 |
| --- | --- | --- |
| 借牌、借出、紀錄他人、批次紀錄 | 所有成員 | 送出就寫入，不用牌主同意 |
| 還牌 | 借用人 | 送出申請，**牌主在「待我確認」按同意後**才寫入歸還 |
| 已取回 | 牌主 | 直接寫入 |
| 轉借 | 目前的借用人 | 直接寫入（有還牌申請在等時不能轉借） |
| 我的借牌、借牌總覽、查借牌 | 所有成員 | 只讀 |

成員不能新增使用者，也看不到管理員頁面。成員可以在「個人設定」改顯示名稱和頭像（沒上傳就用 Discord 頭像）。

還牌申請存在 GUI 自己建的 `return_requests` 表，自訂頭像存在 `user_profiles` 表，
都是啟動時自動建立，不會動到 bot 的 `users`、`loans` 表。

### 設定 Discord 登入

1. [Discord Developer Portal](https://discord.com/developers/applications) 打開 bot 的應用程式 → **OAuth2**
2. **Redirects** 加上 `https://你的網域/auth/discord/callback`（本機測試再加 `http://localhost:8000/auth/discord/callback`）
3. 把 **Client ID**、**Client Secret** 填到 `DISCORD_CLIENT_ID`、`DISCORD_CLIENT_SECRET`

## 環境變數

| 名稱 | 說明 |
| --- | --- |
| `DATABASE_URL` | 跟 bot 同一個 PostgreSQL |
| `ADMIN_DISCORD_IDS` | 管理員的 Discord ID（逗號分隔）。這些人用 Discord 登入就有管理員權限，可以在管理員介面和我的借牌之間切換。ID 可以在「個人設定」頁看到 |
| `ADMIN_PASSWORD` | 選填，管理員密碼登入。不設就關閉密碼登入，只能用 Discord 當管理員 |
| `SESSION_SECRET` | 簽登入 cookie 用的隨機長字串，例如 `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `DISCORD_CLIENT_ID`、`DISCORD_CLIENT_SECRET` | Discord 登入用，見上方 |
| `DISCORD_TOKEN` | 選填，bot 的 token。設了之後借牌、轉借的選單會列出伺服器所有成員（包含還沒用過平台的人），需要在 Developer Portal → Bot 打開 **Server Members Intent** |
| `GUILD_ID` | 選填，只讓這些伺服器（逗號分隔）的成員登入；不填就用資料庫裡出現過的伺服器 |
| `DEFAULT_GUILD_ID` | 選填，新增紀錄時預設的伺服器 ID（不填就用資料最多的那個） |
| `SESSION_MAX_HOURS` | 選填，登入後最多維持幾小時，預設 12 |
| `SESSION_IDLE_MINUTES` | 選填，閒置幾分鐘自動登出，預設 120 |
| `TIMEZONE` | 選填，顯示時間的時區，預設 `Asia/Taipei` |
| `DEV_LOGIN` | 本機測試用，設 `1` 時登入頁可以直接選使用者登入；Railway 上設了會拒絕啟動 |

## 本機開發（新電腦也照這個做）

需要 Python 3.13 和 Docker Desktop。

```powershell
git clone https://github.com/gan86650/cardLoan_GUI.git
cd cardLoan_GUI
git checkout LocalTest
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env          # 照下面說明填
docker compose up -d            # 本機 PostgreSQL 18，資料存在 Docker volume
.\.venv\Scripts\python scripts\pull_prod_data.py   # 複製正式資料到本機（要真實資料才需要）
.\.venv\Scripts\python -m uvicorn main:app --reload
```

`.env` 本機用的設定：

```
DATABASE_URL=postgresql://postgres@localhost:55432/cardloan_local
ADMIN_PASSWORD=admin
SESSION_SECRET=隨便一串字
DEV_LOGIN=1
SOURCE_DATABASE_URL=Railway Postgres → Variables → DATABASE_PUBLIC_URL
```

`scripts/pull_prod_data.py` 只會**讀取**正式資料庫，寫入前會檢查 `DATABASE_URL` 一定是 localhost，
再清空本機資料、換成正式資料的副本。之後想更新成最新資料，再跑一次就好。

## 部署到 Railway

1. 在 bot 所在的 Railway 專案裡 **New → GitHub Repo**，選這個 repo。
2. 在新服務的 **Variables** 加上 `ADMIN_DISCORD_IDS`、`SESSION_SECRET`、`DISCORD_CLIENT_ID`、`DISCORD_CLIENT_SECRET`，`DATABASE_URL` 用
   `${{Postgres.DATABASE_URL}}` 引用 bot 的資料庫（名稱依你的 Postgres 服務而定）。
3. **Settings → Networking → Generate Domain** 產生網址。

啟動指令寫在 `railway.json`。`main` branch 就是正式環境，推上去 Railway 會自動重新部署。

## 注意

- `database.py` 是從 bot repo 複製過來的，bot 那邊改了 SQL 記得同步。
- GUI 不會建 bot 的表或跑 bot 的 migration，這些交給 bot 啟動時處理；只會建自己的 `return_requests` 和 `user_profiles`。
- 有設 `DISCORD_TOKEN` 時，記錄和轉借的下拉選單會列出整個伺服器的成員，被選到的新成員會自動加進 `users`；沒設時只列出 `users` 表裡的人。
