#!/bin/sh
# 在 VPS 上把一份 release（git archive 解開的 backend/ 與 deploy/vps/）換上線。
#
# 平常由 GitHub Actions 經 deploy_entry.sh 呼叫；要手動重跑時：
#   sh ~/stockwise-release/deploy/vps/deploy.sh
#
# 流程：先建新映像（舊容器照跑）→ 等沒有執行中的工作 → 換上新版 →
# 健康檢查；失敗就換回上一版映像與程式碼，並以非 0 結束讓 CI 變紅。
#
# 注意：回滾只換回程式與映像，**不會**倒回新版啟動時已套用的 migration。

set -eu

RELEASE=$(cd "$(dirname "$0")/../.." && pwd)
APP="$HOME/stockwise"
VPS_DIR="$APP/deploy/vps"
IMAGE=stockwise-backend
# 早上的 AI 批次單次可能跑十幾分鐘；等不到就放棄這次部署，不硬切
WAIT_SECONDS=${DEPLOY_WAIT_SECONDS:-1200}
HEALTH_SECONDS=${DEPLOY_HEALTH_SECONDS:-90}
# deploy/vps 底下要隨 release 更新的檔案；.env 只存在 VPS 上，絕不覆蓋
DEPLOY_FILES="docker-compose.yml Caddyfile deploy.sh deploy_entry.sh check_sources.sh count_rows.sql MIGRATION.md .env.example"

log() { echo "[deploy $(date -u +%H:%M:%S)] $*"; }

running_jobs() {
    docker compose exec -T db sh -c \
        'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM job_runs WHERE status = '\''running'\''"'
}

# 重啟會中斷執行中的工作：lease 到期後雖會重新排隊，但已送出的 Gemini
# 請求照樣計入每日 20 次的 premium 額度，所以寧可等它跑完
wait_until_idle() {
    waited=0
    while :; do
        count=$(running_jobs)
        [ "$count" = "0" ] && return 0
        if [ "$waited" -ge "$WAIT_SECONDS" ]; then
            log "等了 ${WAIT_SECONDS}s 仍有 $count 個工作在跑，放棄這次部署（線上版本未變動）"
            return 1
        fi
        log "有 $count 個工作在跑，30 秒後再看"
        sleep 30
        waited=$((waited + 30))
    done
}

healthy() {
    elapsed=0
    while [ "$elapsed" -lt "$HEALTH_SECONDS" ]; do
        if docker compose exec -T backend python -c \
            "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/ready', timeout=5)" \
            >/dev/null 2>&1; then
            return 0
        fi
        sleep 5
        elapsed=$((elapsed + 5))
    done
    return 1
}

restore_code() {
    rm -rf "$APP/backend" && mv "$APP/backend.prev" "$APP/backend"
}

rollback() {
    log "新版健康檢查失敗，回滾到上一版"
    docker compose logs --tail 40 backend || true
    restore_code
    if docker image inspect "$IMAGE:previous" >/dev/null 2>&1; then
        docker tag "$IMAGE:previous" "$IMAGE:latest"
        docker compose up -d --no-build backend
        if healthy; then
            log "已回滾，上一版運作正常"
        else
            log "回滾後仍不健康，需要人工處理"
        fi
    else
        log "沒有上一版映像可回滾，需要人工處理"
    fi
    exit 1
}

main() {
    revision=$(cat "$RELEASE/REVISION" 2>/dev/null || echo "unknown")
    log "開始部署 $revision"

    for f in $DEPLOY_FILES; do
        if [ -f "$RELEASE/deploy/vps/$f" ]; then
            cp "$RELEASE/deploy/vps/$f" "$VPS_DIR/$f"
        fi
    done
    install -D -m 755 "$RELEASE/deploy/vps/deploy_entry.sh" "$HOME/bin/stockwise-deploy-entry"
    cd "$VPS_DIR"

    rm -rf "$APP/backend.prev"
    mv "$APP/backend" "$APP/backend.prev"
    cp -a "$RELEASE/backend" "$APP/backend"

    if docker image inspect "$IMAGE:latest" >/dev/null 2>&1; then
        docker tag "$IMAGE:latest" "$IMAGE:previous"
    fi
    log "建置新映像（舊容器照常服務）"
    if ! docker compose build backend; then
        log "建置失敗，線上版本未變動"
        restore_code
        exit 1
    fi

    if ! wait_until_idle; then
        restore_code
        if docker image inspect "$IMAGE:previous" >/dev/null 2>&1; then
            docker tag "$IMAGE:previous" "$IMAGE:latest"
        fi
        exit 1
    fi

    log "換上新版"
    docker compose up -d --no-build
    docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile >/dev/null 2>&1 || true

    healthy || rollback

    echo "$revision" > "$APP/REVISION"
    docker image prune -f >/dev/null
    log "部署完成：$revision"
}

main
