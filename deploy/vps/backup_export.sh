#!/bin/sh
# GitHub 異地備份金鑰的 SSH forced command：authorized_keys 裡那把金鑰被
# `restrict,command="sh ~/bin/stockwise-backup-export"` 綁死，只能取得這支的輸出。
# 由 deploy.sh 每次部署時安裝到 ~/bin。
#
# 當場做一份 pg_dump，用 backup-cert.pem（公鑰）加密成 CMS 後寫到 stdout。
# 加密在 VPS 上完成，GitHub 只會拿到密文；解密用的私鑰只在使用者手上，
# 還原步驟見私有備份 repo 的 README。
#
# 不沿用 19:00 backup-db 產生的檔案：那支失敗時異地備份不該跟著空掉。

set -eu

VPS_DIR="$HOME/stockwise/deploy/vps"
cd "$VPS_DIR"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > "$TMP/db.dump"
if [ "$(head -c 5 "$TMP/db.dump")" != "PGDMP" ]; then
    echo "pg_dump 輸出不是 custom 格式，放棄" >&2
    exit 1
fi

openssl cms -encrypt -binary -aes-256-gcm -outform DER \
    -in "$TMP/db.dump" -out "$TMP/db.cms" backup-cert.pem
echo "dump $(wc -c < "$TMP/db.dump") bytes → 加密後 $(wc -c < "$TMP/db.cms") bytes" >&2
cat "$TMP/db.cms"
