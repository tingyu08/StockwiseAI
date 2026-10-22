# 從 Zeabur 搬到自己的 VPS

Zeabur 2026 年底停運（[公告](https://zeabur.com/zh-TW/blogs/zeabur-is-shutting-down)）：
12/1 起專屬伺服器不再續約、**到期日當天就關機，磁碟一起消失**；12/31 全面停止。
本文依 Zeabur 官方遷移指南（`https://zeabur.com/blogs/migrate-from-zeabur.md`）
套到本專案的架構：Zeabur 東京機上的後端＋PostgreSQL 17 → 一台 VPS 上的
`docker-compose.yml`（postgres:17＋後端＋Caddy）。前端留在 Vercel 不動。

> **計畫要以你那台伺服器的到期日為準，不是 12/31。**

## 這個目錄有什麼

| 檔案 | 用途 |
|---|---|
| `docker-compose.yml` | 正式環境：db（PG 17）＋ backend ＋ caddy，只有 caddy 對外開 80/443 |
| `Caddyfile` | 反向代理＋自動 HTTPS（Let's Encrypt），取代 Zeabur 幫你做的網域與憑證 |
| `.env.example` | 環境變數範本；複製成 `.env` 填值 |
| `check_sources.sh` | 在候選機器上測各外部資料來源連不連得到 |
| `count_rows.sql` | 搬遷前後比對每張表筆數 |

## 原則（摘自 Zeabur 指南，對本專案同樣適用）

- **密碼、token、連線字串不進聊天、log、commit**。匯出的檔案一律 `chmod 600`，
  放在 repo 以外的地方
- 不要執行 `zeabur server ssh-info`——它會把密碼印在畫面上
- 在**新機器上**演練還原，不是在自己電腦上
- 切換前不刪 Zeabur 上任何東西；穩定運作一段時間後才清理，清了就不能回滾

---

## 0. 今天就做：查到期日、把備份帶出來

```bash
npx zeabur@latest auth login          # 瀏覽器登入
npx zeabur@latest server list         # 找到東京那台的 server ID
npx zeabur@latest server get <serverID>   # 看到期日；IsManaged: true＝Zeabur 的機器，到期磁碟就沒了
npx zeabur@latest project list
npx zeabur@latest service list --project-id <projectID>   # 記下 backend 與 postgresql 的 service ID
```

接著照下面「2. 匯出資料庫」做一次 dump，**存到你自己電腦**。現有的每日備份
`/data/backups` 跟資料庫在同一台機器，關機時會一起消失，不算異地備份。

## 1. 開新機器，先測再買

1. 開機器（Ubuntu 24.04；能按月付或有試用期的優先），先**不要買整年**
2. 把 `check_sources.sh` 傳上去跑：

   ```bash
   sh check_sources.sh
   ```

   全部 `OK` 才繼續。證交所／期交所 MIS 沒有備援，連不到就換機房
3. 裝 Docker（官方腳本）並開防火牆：

   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw allow 443/udp && sudo ufw enable
   ```

4. 把專案拉下來：`git clone <repo> && cd stock-ai-advisor/deploy/vps`

> **ARM 機器（如 Oracle Cloud A1）也可以**：postgres:17、caddy:2、python:3.12-slim
> 都有 arm64 映像，`requirements.lock` 是 `pip-compile --generate-hashes` 產生的，
> 含各平台 wheel 的雜湊。直接在那台機器上 `docker compose build` 即可。

## 2. 匯出 Zeabur 上的東西

### 環境變數

```bash
npx zeabur@latest variable list --id <backendServiceID> --json > ~/zeabur-backend-env.json
chmod 600 ~/zeabur-backend-env.json
```

逐項對到 `deploy/vps/.env`（`cp .env.example .env && chmod 600 .env`），注意：

- **Zeabur 2026-10-14 有外部入侵事件**：資料庫密碼、`JOB_TOKEN` 一律重新產生；
  Gemini、FinMind、Finnhub、OpenRouter 的 key 也建議到各家後台換新的
- `DATABASE_URL`（Zeabur 上是 `${POSTGRES_CONNECTION_STRING}`）**不用填**，
  compose 會用 `POSTGRES_*` 組出來
- `PORT`、`ZEABUR_*`、`*.zeabur.internal` 這類 Zeabur 專屬變數都不需要
- `SCHEDULER_MODE` 先維持 `external`：這個模式仍會跑出場哨兵（只動演練用的那份資料，
  切換時會被最後一次 dump 蓋掉），但不跑 AI 批次與委託
- `ALERT_WEBHOOK_URL` 演練期間**先留空**，否則演練機的哨兵觸發時也會發通知給你，
  跟 Zeabur 那邊的通知重複；正式切換時再填

### 資料庫

Zeabur 上是 PG 17，用 PG 17 的 `pg_dump`（下面都用 postgres:17 映像執行），
單一資料庫用 `pg_dump -Fc`，**不要用 `pg_dumpall`**（會帶 `ALTER ROLE … PASSWORD`
覆蓋新密碼、`CREATE DATABASE` 跟新庫衝突）。

**方法 A：PostgreSQL 有開公開連接埠時（最簡單）**

Zeabur 控制台 → PostgreSQL 服務 → Networking 看得到對外的 host 與 port。
在**新 VPS** 上直接拉：

```bash
read -rs PGPASSWORD && export PGPASSWORD      # 貼上 Zeabur PG 密碼（不會留在 history）
docker run --rm -e PGPASSWORD postgres:17 \
  pg_dump -h <對外host> -p <對外port> -U <使用者> -Fc <資料庫名> > ~/zeabur.dump
unset PGPASSWORD
```

**方法 B：沒有公開連接埠時，經 `service exec` 串流出來**

```bash
timeout 900 npx zeabur@latest service exec --id <postgresServiceID> -- \
  sh -c 'pg_dump -h 127.0.0.1 -U "$POSTGRES_USER" -Fc "$POSTGRES_DB" | base64 -w0' \
  > ~/zeabur.dump.b64
# CLI 可能混入 log 行，只留 base64 內容再解碼
grep -E '^[A-Za-z0-9+/=]+$' ~/zeabur.dump.b64 | base64 -d > ~/zeabur.dump
```

- 容器內要連 `127.0.0.1`，不要用 `localhost`（指南回報過 `-h localhost` 會卡住）
- `POSTGRES_USER`／`POSTGRES_DB` 換成 `variable list --id <postgresServiceID>` 裡實際的變數名
- `service exec` 偶爾回 504，失敗就重跑；確切參數以 `npx zeabur@latest service exec --help` 為準

**兩種方法都要驗證檔案是好的：**

```bash
docker run --rm -i postgres:17 pg_restore --list < ~/zeabur.dump | head
chmod 600 ~/zeabur.dump
```

## 3. 在新機器上演練還原

```bash
cd deploy/vps
docker compose up -d --wait db              # 只起資料庫，並等到健康檢查通過
docker compose exec -T db sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges --clean --if-exists' \
  < ~/zeabur.dump
docker compose up -d --build                # 起後端與 Caddy（SCHEDULER_MODE=external）
```

- **一定要先還原、再起後端**：後端啟動時會跑 `alembic upgrade head`，先起的話會建出空表
- `--wait` 不能省：第一次啟動時 PG 會先初始化再重啟，太早還原會出現
  `database "stockwise" does not exist`
- 還原時出現 `role "xxx" does not exist` 之類的警告是 `--no-owner` 的正常現象

比對筆數（兩邊輸出應一致）：

```bash
# 新機器
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At' < count_rows.sql > ~/new.txt
# Zeabur（方法 A：同上用 docker run psql -h <對外host>；方法 B：service exec 執行 psql 並餵入 count_rows.sql）
diff ~/zeabur.txt ~/new.txt && echo 一致
```

DNS：在你的網域加一筆 A 記錄 `BACKEND_DOMAIN → VPS 的 IP`（改 DNS 前把 TTL 降到 300）。
生效後 Caddy 會自動拿憑證：

```bash
curl -s https://<BACKEND_DOMAIN>/api/v1/health/ready
docker compose logs caddy | tail      # 憑證申請失敗時看這裡
```

演練完成後新機器可以繼續開著（external 模式不跑排程），等正式切換。

## 4. 正式切換

**挑空檔**：排程最空的是**週六下午**，或平日台北 **15:00–17:30**（14:45 淨值之後、
18:00 同步之前）。不要碰 06:10–07:10 與 19:40–20:40 的 AI 批次。

1. 停掉 Zeabur 後端（**資料庫保持運作**），之後就不會再有新資料寫入：

   ```bash
   npx zeabur@latest service suspend --id <backendServiceID>
   ```

2. 重做一次第 2 步的 dump 與第 3 步的還原＋比對（這次是最後版本）
3. 新機器改跑排程：`.env` 設 `SCHEDULER_MODE=internal`、填回 `ALERT_WEBHOOK_URL`，然後
   `docker compose up -d backend`
4. Vercel → Project Settings → Environment Variables：把 `BACKEND_API_BASE`
   （或你用的 `NEXT_PUBLIC_API_BASE`）改成 `https://<BACKEND_DOMAIN>`，
   **要重新部署才生效**（rewrites 是 build 時決定的）
5. GitHub repo → Settings → Secrets：更新 `BACKEND_URL` 與 `JOB_TOKEN`
6. 驗證：前端登入、首頁有資料、工作中心看得到下一個排程；隔天確認早上
   06:10 起的台股序列與 19:00 備份都跑成功：

   ```bash
   docker compose exec backend ls -l /data/backups
   ```

## 5. 回滾（只在 Zeabur 到期前有效）

```bash
npx zeabur@latest service restart --id <backendServiceID>
```

再把 Vercel 的後端網址改回 `stockwise-backend.zeabur.app` 並重新部署、新機器
`SCHEDULER_MODE` 改回 `external`。注意：切換後在新機器上產生的資料不會回到 Zeabur。

## 6. 收尾

- 新機器穩定跑一到兩週後，才刪 Zeabur 上的服務（`service delete`）
- 到 Gemini／FinMind／Finnhub／OpenRouter 後台撤銷舊 key
- **異地備份**：新機器一樣是單機，每日備份仍跟資料庫同生共死。至少定期把
  `/data/backups` 最新的 dump 抓回自己電腦：

  ```bash
  docker compose cp backend:/data/backups ./backups-$(date +%F)
  ```
