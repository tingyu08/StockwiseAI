"""安全審查修正：帳號列舉時間差、過期 session 清理、警示端點輸入驗證。"""
from datetime import datetime, timedelta, timezone
from time import perf_counter

from sqlalchemy import select

from app.api.v1 import auth as auth_api
from app.core.db import SessionLocal
from app.models import Stock, User, UserSession
from app.services.maintenance_service import cleanup_expired_records


# ---- L1：帳號存在與否不得由回應時間推測 ----

def test_unknown_user_still_runs_a_password_hash(client, monkeypatch):
    """契約：帳號不存在時仍要跑一次 Argon2 驗證。

    不比時間（CI 上會 flaky），改為直接觀察 verify 是否被呼叫——
    那才是時間相當的成因。
    """
    calls = []

    class _CountingHasher:
        """PasswordHasher.verify 是唯讀屬性，故整個換掉模組層級的 hasher。"""

        def __init__(self, inner):
            self._inner = inner

        def verify(self, hash_, password):
            calls.append(hash_)
            return self._inner.verify(hash_, password)

        def hash(self, password):
            return self._inner.hash(password)

    monkeypatch.setattr(auth_api, "hasher", _CountingHasher(auth_api.hasher))

    res = client.post("/api/v1/auth/login",
                      json={"username": "no-such-user-at-all", "password": "whatever"})

    assert res.status_code == 401
    assert len(calls) == 1
    assert calls[0] == auth_api._TIMING_EQUALISER


def test_unknown_and_wrong_password_are_indistinguishable(client):
    """回應內容也不能洩漏差異，且耗時應在同一個數量級。"""
    def timed(username):
        start = perf_counter()
        res = client.post("/api/v1/auth/login",
                          json={"username": username, "password": "definitely-wrong"})
        return res, perf_counter() - start

    unknown_res, unknown_s = timed("no-such-user-at-all")
    existing_res, existing_s = timed("test-owner")  # conftest 建立的帳號

    assert unknown_res.status_code == existing_res.status_code == 401
    assert unknown_res.json()["error"] == existing_res.json()["error"]
    # 未修正時差距是兩個數量級；放寬到 10 倍以避免 CI 抖動造成 flaky
    assert unknown_s < existing_s * 10


# ---- L2：過期 session 必須被清理 ----

def test_expired_sessions_are_purged_and_valid_ones_kept(client):
    db = SessionLocal()
    try:
        user = db.scalar(select(User).limit(1))
        assert user is not None
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        expired = UserSession(user_id=user.id, token_hash="purge-expired",
                              csrf_hash="c1", expires_at=now - timedelta(days=1))
        alive = UserSession(user_id=user.id, token_hash="purge-alive",
                            csrf_hash="c2", expires_at=now + timedelta(days=1))
        db.add_all([expired, alive])
        db.commit()

        result = cleanup_expired_records(db)

        assert result["expired_sessions_deleted"] >= 1
        remaining = {
            s.token_hash for s in db.execute(select(UserSession)).scalars()
        }
        assert "purge-expired" not in remaining   # 失效憑證不該無限期留存
        assert "purge-alive" in remaining         # 有效 session 不可誤刪
    finally:
        db.close()


# ---- L4：警示端點的 symbol 需與其他入口一致 ----

def test_alert_symbol_rejects_malformed_input(client):
    res = client.post("/api/v1/alerts",
                      json={"market": "TW", "symbol": "../../etc/passwd",
                            "kind": "price_above", "threshold": 1.0})
    assert res.status_code == 422


def test_alert_symbol_accepts_normal_symbols(client):
    db = SessionLocal()
    try:
        db.add(Stock(symbol="HARDEN", market="TW", name="驗證用",
                     currency="TWD", kind="stock"))
        db.commit()
    finally:
        db.close()

    res = client.post("/api/v1/alerts",
                      json={"market": "TW", "symbol": "HARDEN",
                            "kind": "price_above", "threshold": 1.0})
    assert res.status_code == 200
