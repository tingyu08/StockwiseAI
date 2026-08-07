"""美股資料源：FinMind（日線/搜尋/指數）＋ Finnhub（盤中報價），不含 yfinance。

移除 yfinance 的理由：Yahoo 以 IP 信譽封鎖機房來源，正式環境上它不是
「偶爾失敗的備援」而是「必定失敗的備援」——2026-08-06 18:40 的美股哨兵
5 檔持倉全數退到 yfinance，全部收到 429，當輪停損停利完全失效，
還白花 5 次請求與數秒延遲。

移除前逐一實測 FinMind 對所有實際用到的代號皆有資料（指數 ^GSPC/^IXIC/
^DJI/^SOX/^TWII、大型股、小型股、ETF 含 kind 分類），故無功能損失。

一個必定失敗的備援比沒有備援更糟：它讓失敗看起來像是有在努力。
"""
import re
from pathlib import Path

import pytest

from app.providers.market import intraday

APP_DIR = Path(__file__).resolve().parent.parent / "app"


IMPORT_PATTERN = re.compile(r"^\s*(import\s+yfinance|from\s+yfinance)", re.MULTILINE)


def test_no_module_imports_yfinance():
    """守門：yfinance 不得以任何形式回到 app/。

    移除它同時拆掉了 yf_cache 的 TzCache 預建、SQLite 序列化鎖與
    curl_cffi TLS 偽裝偵測——那些複雜度全都只為了讓一個必定被限流的
    來源勉強可用。悄悄加回來的話，那些坑會一併回來。

    只擋真正的 import：說明「為什麼移除」的註解有保存價值，
    否則後人只會看到一個沒有備援的來源，不明白那是刻意的。
    """
    offenders = [
        path.relative_to(APP_DIR).as_posix()
        for path in APP_DIR.rglob("*.py")
        if IMPORT_PATTERN.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"這些模組又 import 了 yfinance：{offenders}"


def test_yf_cache_module_is_gone():
    with pytest.raises(ModuleNotFoundError):
        __import__("app.providers.market.yf_cache")


async def test_us_intraday_quotes_come_only_from_finnhub(monkeypatch):
    """Finnhub 沒回的標的就是沒有——不再有第二條路可退。"""
    called = {"finnhub": 0}

    async def fake_finnhub(symbols):
        called["finnhub"] += 1
        return {"AAPL": 261.74}  # MSFT 取不到

    monkeypatch.setattr(
        "app.providers.market.finnhub.fetch_quotes", fake_finnhub
    )

    quotes = await intraday._us_quotes(["AAPL", "MSFT"])

    assert quotes == {"AAPL": 261.74}
    assert called["finnhub"] == 1


async def test_us_daily_prices_have_no_second_source(monkeypatch):
    """FinMind 失敗即如實失敗，不假裝還有備援。"""
    from app.core.exceptions import UpstreamError
    from app.providers.market.us_market import USMarketProvider

    def boom(*args, **kwargs):
        raise RuntimeError("finmind down")

    monkeypatch.setattr("app.providers.market.finmind_us.fetch_daily", boom)

    from datetime import date

    with pytest.raises(UpstreamError):
        await USMarketProvider().get_daily_prices(
            "AAPL", date(2026, 1, 1), date(2026, 1, 31)
        )
