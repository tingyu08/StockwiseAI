#!/bin/sh
# GitHub 部署金鑰的 SSH forced command：authorized_keys 裡那把金鑰被
# `restrict,command="sh ~/bin/stockwise-deploy-entry"` 綁死，不管對方要求
# 執行什麼，都只會跑這支。由 deploy.sh 每次部署時安裝到 ~/bin。
#
# 從 stdin 收 git archive 的 tar（backend/、deploy/vps/、REVISION），
# 解到乾淨的 release 目錄後交給其中的 deploy.sh。

set -eu

RELEASE="$HOME/stockwise-release"

# 兩次 push 接連觸發時排隊，不要兩個部署同時動同一份檔案
exec 9>"$HOME/.stockwise-deploy.lock"
flock -w 1800 9 || { echo "等不到上一次部署結束，放棄"; exit 1; }

rm -rf "$RELEASE.tmp"
mkdir -p "$RELEASE.tmp"
tar -x -C "$RELEASE.tmp"
rm -rf "$RELEASE"
mv "$RELEASE.tmp" "$RELEASE"

exec sh "$RELEASE/deploy/vps/deploy.sh"
