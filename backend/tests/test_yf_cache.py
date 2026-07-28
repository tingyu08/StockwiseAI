"""yfinance 時區快取目錄必須在併發前備妥。

yfinance 的初始化是 `if not isdir(dir): makedirs(dir)`——先檢查再建立、
中間沒有鎖。哨兵用 asyncio.to_thread 平行抓多檔報價，N 條 thread 同時
第一次呼叫就會一起通過檢查再一起 makedirs，只有一個成功，其餘收到
`[Errno 17] File exists` 並整個停用快取。正式環境每次容器啟動後的第一輪
哨兵都會噴出一批這種 ERROR，把真正的錯誤淹沒。
"""
import inspect
from concurrent.futures import ThreadPoolExecutor

from app.providers.market.yf_cache import TZ_CACHE_DIR, configure_yfinance_cache


def test_cache_directory_exists_after_configuration():
    assert configure_yfinance_cache() == TZ_CACHE_DIR
    assert TZ_CACHE_DIR.is_dir()


def test_configuration_is_idempotent_under_concurrency():
    """重複／併發呼叫都不得拋例外（exist_ok 的行為契約）。"""
    configure_yfinance_cache()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: configure_yfinance_cache(), range(8)))
    assert all(r == TZ_CACHE_DIR for r in results)


def test_lifespan_configures_cache_before_starting_workers():
    """順序契約：必須早於 worker/排程啟動，否則第一輪哨兵仍會撞上競態。"""
    from app.main import lifespan

    source = inspect.getsource(lifespan)
    assert source.index("configure_yfinance_cache()") < source.index("run_worker_loop()")


def test_failure_to_configure_never_breaks_startup(monkeypatch):
    """快取只是加速；拿不到就退回每次查詢，不能讓服務起不來。"""

    class ReadOnlyPath:
        def mkdir(self, *_args, **_kwargs):
            raise OSError("read-only fs")

    monkeypatch.setattr(
        "app.providers.market.yf_cache.TZ_CACHE_DIR", ReadOnlyPath()
    )
    assert configure_yfinance_cache() is None
