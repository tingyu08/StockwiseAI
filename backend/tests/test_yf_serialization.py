"""yfinance 呼叫必須序列化。

背景：預建時區快取目錄後，TzCache 從「完全停用」變成真的啟用，於是併發
首次查詢開始撞它的 SQLite —— 正式環境出現
`yfinance 即時報價 VOO 失敗：database is locked`。

加長等待無效：實測該 DB 的 busy_timeout 已是 5000ms、journal_mode 已是 wal，
而 WAL 的寫寫升級衝突不會觸發 busy handler。唯一可靠的辦法是不要併發。
"""
import threading
import time

import pytest

from app.providers.market import yf_cache
from app.providers.market.yf_cache import YFinanceBusyError, yfinance_guard


def test_guard_serialises_concurrent_callers():
    """同時只允許一個呼叫進入——這是不撞 SQLite 鎖的充要條件。"""
    inside = 0
    max_inside = 0
    lock = threading.Lock()

    def worker(_):
        nonlocal inside, max_inside
        with yfinance_guard():
            with lock:
                inside += 1
                max_inside = max(max_inside, inside)
            time.sleep(0.02)
            with lock:
                inside -= 1

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert max_inside == 1


def test_guard_releases_on_exception():
    """呼叫端丟例外也必須放鎖，否則一次失敗會永久卡死所有後續報價。"""
    with pytest.raises(ValueError):
        with yfinance_guard():
            raise ValueError("boom")

    with yfinance_guard():
        pass  # 還拿得到＝已釋放


def test_guard_gives_up_instead_of_blocking_forever(monkeypatch):
    """等不到就放棄：yfinance 偶爾會卡住，無上限等待會讓一檔拖垮整批。"""
    monkeypatch.setattr(yf_cache, "YF_LOCK_TIMEOUT_SEC", 0.05)
    holder_done = threading.Event()

    def hold():
        with yfinance_guard():
            holder_done.wait(timeout=2)

    t = threading.Thread(target=hold)
    t.start()
    try:
        time.sleep(0.05)
        with pytest.raises(YFinanceBusyError):
            with yfinance_guard():
                pass
    finally:
        holder_done.set()
        t.join()


async def test_us_quotes_never_calls_yfinance_concurrently(monkeypatch):
    """契約鎖在真正的呼叫點：_us_quotes 用 gather 平行跑，
    若少了 guard，多條 thread 會同時進到 yfinance。"""
    from app.providers.market import intraday

    peak = 0
    inside = 0
    lock = threading.Lock()

    class FakeTicker:
        def __init__(self, symbol):
            nonlocal inside, peak
            with lock:
                inside += 1
                peak = max(peak, inside)
            time.sleep(0.02)
            with lock:
                inside -= 1
            self.fast_info = {"last_price": 100.0}

    monkeypatch.setattr("yfinance.Ticker", FakeTicker)

    quotes = await intraday._us_quotes(["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"])

    assert len(quotes) == 6
    assert peak == 1  # 沒有任何兩檔同時進到 yfinance
