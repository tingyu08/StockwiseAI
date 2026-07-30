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


def test_http_backend_is_reported_at_startup(caplog):
    """啟動就要看得出有沒有 TLS 偽裝。

    缺這個資訊時，「美股報價整批 429」無法分辨是來源 IP 被限流，
    還是容器裡 curl_cffi 沒裝起來退回了無偽裝的 requests——
    兩者症狀相同、修法完全不同。
    """
    from app.providers.market.yf_cache import log_http_backend

    with caplog.at_level("INFO", logger="app.providers.market.yf_cache"):
        enabled = log_http_backend()

    assert enabled is True  # 本機/CI 都應該有 curl_cffi（在 requirements.lock 內）
    assert "curl_cffi" in caplog.text


def test_missing_tls_impersonation_is_a_warning_not_silence(monkeypatch, caplog):
    """退回無偽裝的 requests 必須是 WARNING——這是會導致被封鎖的狀態。"""
    from yfinance import _http as yf_http

    from app.providers.market.yf_cache import log_http_backend

    monkeypatch.setattr(yf_http, "HAS_CURL_CFFI", False)
    with caplog.at_level("INFO", logger="app.providers.market.yf_cache"):
        assert log_http_backend() is False

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings and "TLS" in warnings[0].getMessage()


def test_failure_to_configure_never_breaks_startup(monkeypatch):
    """快取只是加速；拿不到就退回每次查詢，不能讓服務起不來。"""

    class ReadOnlyPath:
        def mkdir(self, *_args, **_kwargs):
            raise OSError("read-only fs")

    monkeypatch.setattr(
        "app.providers.market.yf_cache.TZ_CACHE_DIR", ReadOnlyPath()
    )
    assert configure_yfinance_cache() is None
