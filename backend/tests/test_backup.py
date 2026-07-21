"""每日 pg_dump 備份：SQLite 跳過、成功輪替、失敗不吞錯。"""
import subprocess
from pathlib import Path

import pytest

from app.core.exceptions import UpstreamError
from app.services import backup_service


def _patch_settings(monkeypatch, tmp_path: Path, url: str):
    class _S:
        database_url = url
        backup_dir = str(tmp_path / "backups")

    monkeypatch.setattr(backup_service, "get_settings", lambda: _S())


def test_backup_skips_sqlite(monkeypatch, tmp_path):
    _patch_settings(monkeypatch, tmp_path, "sqlite:///x.db")
    result = backup_service.run_db_backup()
    assert "skipped" in result


def test_backup_runs_pg_dump_and_rotates(monkeypatch, tmp_path):
    _patch_settings(monkeypatch, tmp_path, "postgresql+psycopg://u:p@h/db")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    # 既有 15 份舊備份 → 新增後應輪替掉最舊的 2 份（保留 14）
    for i in range(15):
        (backup_dir / f"stockwise-202601{i:02d}-0000.dump").write_bytes(b"old")

    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        Path(cmd[2].removeprefix("--file=")).write_bytes(b"dump-data")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(backup_service.subprocess, "run", fake_run)

    result = backup_service.run_db_backup()

    assert captured["cmd"][0] == "pg_dump"
    assert captured["cmd"][-1].startswith("postgresql://")  # scheme 已正規化
    assert result["bytes"] == len(b"dump-data")
    assert len(result["removed"]) == 2
    assert len(list(backup_dir.glob("stockwise-*.dump"))) == backup_service.KEEP_COUNT


def test_backup_surfaces_pg_dump_failure(monkeypatch, tmp_path):
    _patch_settings(monkeypatch, tmp_path, "postgresql://u:p@h/db")

    def fail_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr=b"connection refused")

    monkeypatch.setattr(backup_service.subprocess, "run", fail_run)

    with pytest.raises(UpstreamError, match="connection refused"):
        backup_service.run_db_backup()
