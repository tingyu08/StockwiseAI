# 25 項程式碼稽核修復紀錄

狀態日期：2026-07-12。下列項目均已完成程式碼修復與自動測試；最後一節列出只能在外部環境驗證的邊界。

1. Gemini RPD 依 `America/Los_Angeles`（含 DST）重置，不再用台北日期。
2. AI quota 在送出前建立 reservation，並以 process lock／PostgreSQL advisory lock 避免併發超額。
3. timeout/失敗的 provider attempt 也計入用量，reservation 會正確 finalize。
4. Antigravity polling 使用 monotonic deadline，HTTP 等待時間納入總 timeout。
5. FinMind 的統一 upstream error 會進入指數退避重試與備援流程。
6. overview、news、simulation decide 與排程改為 DB-backed job queue。
7. queue 具 claim、lease、heartbeat、stale recovery 與最大重試次數。
8. active idempotency partial index 防止重複工作；失敗工作保留 payload 可原地 retry。
9. GitHub cron 會輪詢 JobRun 完成才執行下一步，避免背景任務互相踩資料。
10. AI 單檔/批次輸出加入價格順序、停損、機率總和、風險與 symbol completeness 語意驗證。
11. 新聞內容視為 untrusted data，HTML escape 並以明確標籤隔離，降低 prompt injection。
12. Antigravity 新聞要求來源 URL 並限制摘要長度，避免無來源或過長輸出。
13. 分析快取加入 prompt version 與 input hash，資料變動不再誤用舊報告。
14. overview/news 支援 force refresh 且使用 upsert，不再卡在當日舊快取。
15. 台美市場日期、交易日與 UTC 轉換集中處理，不依賴 Render host timezone。
16. 行情同步重抓最近 14 天並 upsert，能補缺口、接受修訂並重算指標。
17. 模擬多買單依信心排序並預留現金，避免同批決策超配。
18. 可買數量改為 O(log n) 搜尋；撮合採 claim/原子狀態避免重複成交。
19. 警示改為 outbox，事件每日唯一；通知失敗保留 pending 可重送。
20. API client 能處理 HTML/空白 gateway error、timeout、AbortSignal 與 Retry-After。
21. 前端工作中心持久化 active run id，可恢復輪詢、顯示階段、移除或重試失敗工作。
22. Prediction 唯一約束、熱門查詢索引與持倉固定三次批次 query，消除 N+1。
23. liveness/readiness 分離，data-status 分列 AI kind，並定期清理過期 operational history。
24. production 排程憑證 fail-closed、安全標頭/request ID/secret redaction、hash lock、non-root standalone images 與 CI gate。
25. 每日簡報與模擬交易分析優先使用 Gemini 3.5 Flash，額度或上游失敗時才走降級鏈。

## 驗證證據

- Backend：127 tests，總 statement coverage 78%，coverage gate 75%，Ruff 通過。
- Frontend：12 tests；實測 statements 77%、branches 72%、functions 70%、lines 83%，coverage gate 分別為 75%/70%/70%/80%，ESLint 與 standalone production build 通過。
- SQLite Alembic：完整 upgrade head、downgrade base 通過。
- Supply chain：`pip-audit` 與 `npm audit --audit-level=high` 均為 0 known vulnerabilities。

## 外部驗證邊界

- 本機沒有 Docker CLI，因此兩個 image 的實際 build 由 GitHub Actions `containers` job 驗證；目前只確認 Dockerfile、locked install 與 Next standalone artifact。
- PostgreSQL/Neon migration 與 partial index 的真實 dialect 驗證由 CI 的 PostgreSQL 16 service 執行。
- Render cold start、LB timeout、Neon scale-to-zero 與真實 Gemini/FinMind 額度只能在部署後 smoke test；程式已用 durable job polling、readiness 與 timeout/retry 降低風險。
