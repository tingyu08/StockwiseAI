"""憑證不得寫進 log。

2026-08-06 的正式環境 log 留下整串 Finnhub 金鑰明文：_secrets() 列了
五個秘密卻漏了 finnhub_token。同一份 log 裡 FinMind 的 token 是
[REDACTED]，差別只在於「有沒有記得加進那份清單」。

靠人記得是靠不住的，所以這裡改用列舉：任何名字看起來像憑證的設定，
都必須被遮蔽層蓋到。新增設定時漏了加，這個測試會先失敗。
"""
from app.core.config import get_settings
from app.core.logging_config import redact_sensitive

# 名稱含這些字樣的設定一律視為憑證
CREDENTIAL_HINTS = ("token", "key", "secret", "password")

# database_url 也可能含密碼，但整串被遮會讓連線錯誤訊息無法閱讀
# （SQLite 的預設值更是完全不敏感）。是否納入是獨立的取捨，不在此強制。
EXEMPT = {"database_url"}


def _credential_fields() -> list[str]:
    settings = get_settings()
    return [
        name
        for name in type(settings).model_fields
        if name not in EXEMPT
        and any(hint in name.lower() for hint in CREDENTIAL_HINTS)
    ]


def test_credential_settings_exist_to_be_checked():
    """守住這份測試本身：欄位改名而掃不到任何東西時，上面那條會空轉通過。"""
    assert len(_credential_fields()) >= 4


def test_every_credential_setting_is_redacted():
    settings = get_settings()
    for name in _credential_fields():
        original = getattr(settings, name)
        marker = f"SENTINEL-{name}-abcdef123456"
        object.__setattr__(settings, name, marker)
        try:
            leaked = f"GET https://example.com/api?x=1&{name}={marker}"
            assert marker not in redact_sensitive(leaked, settings), (
                f"{name} 沒有被遮蔽——請加進 app/core/logging_config._secrets()"
            )
        finally:
            object.__setattr__(settings, name, original)
