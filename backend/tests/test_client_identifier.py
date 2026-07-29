"""登入限流的來源識別。

正式環境的每一行 log 都是 Zeabur ingress 的 10.42.0.1（uvicorn 沒有處理
代理標頭），等於所有訪客共用同一個限流桶：任何人連打 5 次錯誤密碼就能
把唯一的擁有者鎖在門外，每 5 分鐘重複即可無限期封鎖，不需任何憑證。

修法的方向性是關鍵：X-Forwarded-For 必須由右往左取第一個非信任項。
取最左（uvicorn --forwarded-allow-ips="*" 的行為）會讓攻擊者自填假 IP，
限流直接失效——比原本的問題更糟。本檔的測試就是釘住這個方向。
"""
import pytest

from app.core import auth
from app.core.auth import client_identifier


class _Req:
    def __init__(self, peer: str | None, xff: str | None = None):
        self.client = type("C", (), {"host": peer})() if peer else None
        self.headers = {"X-Forwarded-For": xff} if xff is not None else {}


@pytest.fixture(autouse=True)
def _trust_private(monkeypatch):
    """只覆寫設定值，讓每個測試都走真實的解析邏輯（那正是要驗的東西）。"""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "trusted_proxy_ips", "private")


def test_direct_connection_uses_peer_address():
    assert client_identifier(_Req("203.0.113.9")) == "203.0.113.9"


def test_direct_connection_ignores_forwarded_header():
    """未經信任代理的來源自報 XFF 一律不採信，否則限流可被直接繞過。"""
    assert client_identifier(_Req("203.0.113.9", "1.2.3.4")) == "203.0.113.9"


def test_behind_trusted_proxy_uses_forwarded_client():
    assert client_identifier(_Req("10.42.0.1", "203.0.113.9")) == "203.0.113.9"


def test_spoofed_left_hand_entries_are_ignored():
    """攻擊者能自填 XFF 前段。取最右邊的非信任項才是代理實際看到的來源。"""
    req = _Req("10.42.0.1", "1.1.1.1, 2.2.2.2, 203.0.113.9")
    assert client_identifier(req) == "203.0.113.9"


def test_multiple_trusted_hops_are_skipped():
    req = _Req("10.42.0.1", "203.0.113.9, 10.0.0.5, 172.16.0.9")
    assert client_identifier(req) == "203.0.113.9"


def test_all_hops_trusted_falls_back_to_peer_not_open():
    """整條鏈都是內網時不可 fail-open 成攻擊者可控的值。"""
    assert client_identifier(_Req("10.42.0.1", "10.0.0.5, 172.16.0.9")) == "10.42.0.1"


def test_empty_forwarded_header_falls_back_to_peer():
    assert client_identifier(_Req("10.42.0.1", "")) == "10.42.0.1"


def test_forwarded_entry_with_port_is_normalised():
    assert client_identifier(_Req("10.42.0.1", "203.0.113.9:51823")) == "203.0.113.9"


def test_ipv6_forwarded_entry_is_not_truncated():
    req = _Req("10.42.0.1", "[2001:db8::1]:443")
    assert client_identifier(req) == "2001:db8::1"


def test_missing_client_is_not_none():
    assert client_identifier(_Req(None)) == "unknown"


def test_trust_disabled_ignores_forwarded_header(monkeypatch):
    """TRUSTED_PROXY_IPS 留空＝完全不信任 XFF（可用於直接對外的部署）。"""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "trusted_proxy_ips", "")
    assert client_identifier(_Req("10.42.0.1", "203.0.113.9")) == "10.42.0.1"


def test_explicit_cidr_configuration(monkeypatch):
    """可收斂成只信任實際的 ingress 網段，其餘私有位址不再享有代理待遇。"""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "trusted_proxy_ips", "10.42.0.0/16")
    assert client_identifier(_Req("10.42.0.1", "203.0.113.9")) == "203.0.113.9"
    assert client_identifier(_Req("192.168.1.1", "203.0.113.9")) == "192.168.1.1"


def test_unparseable_configuration_does_not_open_the_gate(monkeypatch):
    """設定寫錯時退回「不信任」，不可變成信任全部。"""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "trusted_proxy_ips", "not-an-ip")
    assert auth._trusted_proxy_networks() == ()
    # 且行為上退回 peer，不採信任何自報的 XFF
    assert client_identifier(_Req("10.42.0.1", "203.0.113.9")) == "10.42.0.1"
