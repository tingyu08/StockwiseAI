#!/bin/sh
# 在「候選 VPS 上」跑，確認這台機器的 IP 連得到各外部資料來源。
# 證交所 MIS／期交所 MIS 沒有備援，連不到等於哨兵停損與夜盤段落失效——
# 下單買整年之前先跑這支（yfinance 就是栽在機房 IP 被封鎖）。
#
# 用法：sh check_sources.sh        全部通過 exit 0，任一失敗 exit 1
# 只需要 curl；不帶任何 API key，因此 Gemini／Finnhub 只驗「連得到」。
# 端點與參數對齊 app/providers/market/intraday.py、services/market_context.py、
# services/premium_service.py，改那邊時請一併更新。

set -u

TIMEOUT=20
UA="Mozilla/5.0"
FAILED=0
BODY_FILE=$(mktemp)
trap 'rm -f "$BODY_FILE"' EXIT

# check <名稱> <預期 HTTP 狀態碼> <回應中必須出現的字串，空字串＝不檢查> <curl 參數...>
check() {
    name=$1 want_code=$2 want_text=$3
    shift 3
    code=$(curl -sS -o "$BODY_FILE" -w '%{http_code}' --max-time "$TIMEOUT" \
        -A "$UA" "$@" 2>/dev/null) || code="連線失敗"
    if [ "$code" != "$want_code" ]; then
        echo "FAIL  $name：HTTP $code（預期 $want_code）"
        FAILED=1
    elif [ -n "$want_text" ] && ! grep -q "$want_text" "$BODY_FILE"; then
        echo "FAIL  $name：HTTP $code，但回應缺少「$want_text」（可能被擋或改版）"
        head -c 200 "$BODY_FILE"; echo
        FAILED=1
    else
        echo "OK    $name"
    fi
}

check "證交所 MIS 盤中報價" 200 '"msgArray"' \
    "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_2330.tw&json=1&delay=0"

check "證交所 MIS ETF 淨值" 200 "" \
    "https://mis.twse.com.tw/stock/data/all_etf.txt"

# payload 先寫進檔案再 --data-binary @檔案：中文直接放在命令列參數時，
# Windows 的 curl 會把「全部」轉成 Big5，期交所回 400（Invalid UTF-8）
TAIFEX_PAYLOAD=$(mktemp)
trap 'rm -f "$BODY_FILE" "$TAIFEX_PAYLOAD"' EXIT
printf '%s' '{"MarketType":"1","SymbolType":"F","KindID":"1","CID":"TXF","ExpireMonth":"","RowSize":"全部","PageNo":"","SortColumn":"","AscDesc":"A"}' \
    > "$TAIFEX_PAYLOAD"
check "期交所 MIS 台指期夜盤" 200 '"RtData"' \
    -X POST -H "Content-Type: application/json" \
    --data-binary "@$TAIFEX_PAYLOAD" \
    "https://mis.taifex.com.tw/futures/api/getQuoteList"

check "FinMind" 200 "" \
    "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo&data_id=2330"

# 沒帶 key：回 401/403 代表「連得到、只是沒授權」，對這支腳本就是通過
check "Finnhub（連線）" 401 "" \
    "https://finnhub.io/api/v1/quote?symbol=AAPL"

check "Gemini API（連線）" 403 "" \
    "https://generativelanguage.googleapis.com/v1beta/models"

check "Google News RSS" 200 "<rss" \
    "https://news.google.com/rss/search?q=2330&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"

echo
if [ "$FAILED" -ne 0 ]; then
    echo "有來源連不到——先別下單，或換一個機房再測。"
    exit 1
fi
echo "全部來源可達。"
