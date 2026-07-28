"""purge premium data for markets without NAV support

美股折溢價已於 ce0e4ba 下架（免費資料源皆無美股 ETF 淨值），但只移除了
程式與排程，資料留在庫裡。留下來的不是無害的歷史：

- data-status 無條件查 etf_nav 最大日期，把停止更新的舊日期當成資料新鮮度
  回報（正式環境顯示 NAV 2026-07-14，看起來像排程壞了）
- alert_service._current_value 取「最新一筆 etf_nav」時沒有任何日期下限，
  美股折溢價警示會永遠拿凍結的舊值比對門檻——不觸發或天天觸發，都是靜默的錯

故一併清除美股的淨值列與折溢價警示。API 端也已擋掉新建
（見 api/v1/alerts.py 的 PREMIUM_MARKETS 檢查），兩邊都收斂到
premium_service.SUPPORTED_MARKETS 這個單一真相。

不可逆：這些資料無法從任何免費資料源重建，downgrade 亦不還原。

Revision ID: b4d17e0a92c5
Revises: 3597922e5e09
Create Date: 2026-07-28 16:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b4d17e0a92c5'
down_revision = '3597922e5e09'
branch_labels = None
depends_on = None

# 與 app.services.premium_service.SUPPORTED_MARKETS 一致。
# migration 不 import 應用程式碼：它必須能對「當時」的 schema 重放，
# 而應用程式的常數會隨版本改變。兩邊的一致性由測試把關。
SUPPORTED_MARKETS = ("TW",)


def _unsupported_markets(conn) -> list[str]:
    markets = [
        row[0] for row in conn.execute(sa.text("SELECT DISTINCT market FROM stocks"))
    ]
    return [m for m in markets if m not in SUPPORTED_MARKETS]


def upgrade() -> None:
    conn = op.get_bind()
    markets = _unsupported_markets(conn)
    if not markets:
        return
    params = {f"m{i}": m for i, m in enumerate(markets)}
    placeholders = ", ".join(f":{k}" for k in params)

    # 先刪事件（alert_events.alert_id 參照 alerts），再刪警示，最後刪淨值
    conn.execute(
        sa.text(
            f"""
            DELETE FROM alert_events WHERE alert_id IN (
                SELECT a.id FROM alerts a JOIN stocks s ON s.id = a.stock_id
                WHERE a.kind LIKE 'premium%' AND s.market IN ({placeholders})
            )
            """
        ),
        params,
    )
    conn.execute(
        sa.text(
            f"""
            DELETE FROM alerts WHERE id IN (
                SELECT a.id FROM alerts a JOIN stocks s ON s.id = a.stock_id
                WHERE a.kind LIKE 'premium%' AND s.market IN ({placeholders})
            )
            """
        ),
        params,
    )
    conn.execute(
        sa.text(
            f"""
            DELETE FROM etf_nav WHERE stock_id IN (
                SELECT id FROM stocks WHERE market IN ({placeholders})
            )
            """
        ),
        params,
    )


def downgrade() -> None:
    """無法還原：免費資料源不提供這些市場的 ETF 淨值，資料無從重建。"""
    pass
