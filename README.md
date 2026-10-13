# cardLoan_GUI

[cardLoan](https://github.com/gan86650/cardLoan) 借牌 Discord bot 的管理員網頁介面。
直接連 bot 用的同一個 PostgreSQL，借、還、轉借都呼叫跟 bot 同一份 `database.py`。

## 功能

| 頁面 | 對應的 bot 指令 |
| --- | --- |
| 借牌總覽（可依使用者、兩人之間、卡名篩選） | `/借牌總覽`、`/查借牌` |
| 新增紀錄（單筆或批次，`卡名 x數量`） | `/借牌`、`/借出`、`/紀錄他人`、`/批次紀錄` |
| 單筆紀錄頁：還牌（可部分歸還）、轉借 | `/還牌`、`/已取回`、`/轉借` |
| 使用者頁：借進來 / 借出去的牌、提醒設定 | `/借進來的牌`、`/借出去的牌`、`/提醒設定` |
| 歸還紀錄（最近 200 筆） | （bot 沒有） |

目前只有一組管理員密碼，登入後可以看、改所有人的紀錄。之後會加 Discord 登入給一般成員使用。

## 環境變數

| 名稱 | 說明 |
| --- | --- |
| `DATABASE_URL` | 跟 bot 同一個 PostgreSQL |
| `ADMIN_PASSWORD` | 管理員登入密碼 |
| `SESSION_SECRET` | 簽登入 cookie 用的隨機長字串，例如 `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `DEFAULT_GUILD_ID` | 選填，新增紀錄時預設的伺服器 ID（不填就用資料最多的那個） |
| `TIMEZONE` | 選填，顯示時間的時區，預設 `Asia/Taipei` |

## 本機執行

```bash
pip install -r requirements.txt
cp .env.example .env   # 填好上面的變數
uvicorn main:app --reload
```

## 部署到 Railway

1. 在 bot 所在的 Railway 專案裡 **New → GitHub Repo**，選這個 repo。
2. 在新服務的 **Variables** 加上 `ADMIN_PASSWORD`、`SESSION_SECRET`，`DATABASE_URL` 用
   `${{Postgres.DATABASE_URL}}` 引用 bot 的資料庫（名稱依你的 Postgres 服務而定）。
3. **Settings → Networking → Generate Domain** 產生網址。

啟動指令寫在 `Procfile`。

## 注意

- `database.py` 是從 bot repo 複製過來的，bot 那邊改了 SQL 記得同步。
- GUI 不會建表或跑 migration，這些交給 bot 啟動時處理。
- 下拉選單只列出 `users` 表裡的人（用過 bot 就會自動加入），沒用過的人可以在「使用者」頁手動新增。
