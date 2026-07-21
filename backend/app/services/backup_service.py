"""每日資料庫備份：pg_dump 到本機 volume，輪替保留 N 份。

單機自架 Postgres 的必要配套——磁碟或容器出事時，至少有每日快照可還原。
dump 檔落在 BACKUP_DIR（Zeabur 上掛 persistent volume），開發用 SQLite 時跳過。
"""
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.core.exceptions import UpstreamError

logger = logging.getLogger(__name__)

KEEP_COUNT = 14
DUMP_TIMEOUT_SECONDS = 300


def run_db_backup() -> dict:
    settings = get_settings()
    url = settings.database_url
    if not url.startswith(("postgres://", "postgresql://", "postgresql+")):
        return {"skipped": "非 PostgreSQL（開發用 SQLite 不備份）"}

    backup_dir = Path(settings.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    target = backup_dir / f"stockwise-{stamp}.dump"

    # pg_dump 不認識 SQLAlchemy 的 postgresql+psycopg:// scheme
    plain_url = url.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgres://", "postgresql://"
    )
    try:
        subprocess.run(
            ["pg_dump", "--format=custom", f"--file={target}", plain_url],
            check=True,
            capture_output=True,
            timeout=DUMP_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise UpstreamError("pg_dump 不存在：映像檔需安裝 postgresql-client") from exc
    except subprocess.TimeoutExpired as exc:
        raise UpstreamError(f"pg_dump 逾時（>{DUMP_TIMEOUT_SECONDS}s）") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode(errors="replace")[-500:]
        raise UpstreamError(f"pg_dump 失敗：{stderr}") from exc

    removed = _rotate(backup_dir)
    size = target.stat().st_size
    logger.info("db backup ok: %s (%d bytes), removed=%s", target.name, size, removed)
    return {"file": target.name, "bytes": size, "kept": KEEP_COUNT, "removed": removed}


def _rotate(backup_dir: Path) -> list[str]:
    dumps = sorted(backup_dir.glob("stockwise-*.dump"))
    removed = []
    for old in dumps[:-KEEP_COUNT]:
        old.unlink()
        removed.append(old.name)
    return removed
