"""yfinance 時區快取的目錄初始化。

為什麼需要這個：yfinance 初始化 TzCache 的寫法是
`if not os.path.isdir(dir): os.makedirs(dir)`——先檢查再建立，中間沒有鎖。
哨兵用 asyncio.to_thread 平行抓多檔報價，N 條 thread 同時第一次呼叫 yfinance
就會同時通過 isdir 檢查、再一起 makedirs，只有一個成功，其餘 N-1 條收到
`[Errno 17] File exists` 並**整個停用時區快取**。

實測正式環境每次容器啟動後的第一輪哨兵都會噴出一批 ERROR
（`Failed to create TzCache ... File exists`），把真正的錯誤淹沒在噪音裡。

解法是在服務啟動時、任何併發發生之前，單執行緒地把目錄建好並指定位置：
之後每條 thread 的 isdir 都直接為真，不會再有人去 makedirs。

位置選 temp 目錄而非平台預設快取目錄：跨平台一致、必定可寫，
而這只是時區查詢的快取，遺失的代價僅是多打一次網路查詢。
"""
import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

TZ_CACHE_DIR = Path(tempfile.gettempdir()) / "stockwise-yfinance-tz"


def configure_yfinance_cache() -> Path | None:
    """預先建立時區快取目錄並告知 yfinance。失敗不影響啟動。"""
    try:
        import yfinance as yf

        TZ_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        yf.set_tz_cache_location(str(TZ_CACHE_DIR))
    except Exception:
        # 快取只是加速，拿不到也只是退回每次查詢；不能因此讓服務起不來
        logger.warning("yfinance 時區快取初始化失敗，改為不使用快取", exc_info=True)
        return None
    logger.info("yfinance 時區快取位置：%s", TZ_CACHE_DIR)
    return TZ_CACHE_DIR
