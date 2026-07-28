"""盤中出場哨兵：對「持倉」做停損/停利的即時檢查（不做買入，維持進場日線紀律）。

- 純硬規則、零 AI 呼叫：停損價與目標價來自建倉當時的 AI 報告
- 觸發即以「當下觀察到的報價」成交（等同停損市價單的近似），
  訂單標記 fill_kind = stop_loss / take_profit，與日線「隔日開盤成交」區分
- 併發安全：沿用 pending 單的 partial unique index——先建 pending 再立即成交
- 同股已有 pending 日線委託單時「接管」而非跳過：把該單標記 rejected 後再出場。
  日線單要到下一個開盤才撮合，而決策單建立於開盤前、覆蓋整個交易時段，
  等於停損在最需要它的時候被自己的委託單擋住（見 _supersede_pending）
"""
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import UpstreamError
from app.models import AiReport, SimOrder, Stock
from app.providers.market.intraday import fetch_intraday_quotes
from app.services.sim.engine import calc_fee, get_or_create_account
from app.services.sim.portfolio import current_positions
from app.services.time_service import MARKET_TIMEZONES, market_today, utc_now_naive
from app.services.trading_calendar import is_trading_day

logger = logging.getLogger(__name__)

# 盤中時段（當地時間，含少量收盤緩衝）
MARKET_HOURS = {"TW": ((9, 0), (13, 35)), "US": ((9, 30), (16, 5))}

_KIND_LABELS = {"stop_loss": "停損", "take_profit": "停利"}


async def run_exit_sentinel(db: Session, market: str) -> dict:
    today = market_today(market)
    if not is_trading_day(market, today):
        return {"market": market, "skipped": "非交易日", "checked": 0, "exits": []}
    if not _in_market_hours(market):
        return {"market": market, "skipped": "非交易時段", "checked": 0, "exits": []}

    account = get_or_create_account(db, market)
    positions = current_positions(db, account)
    if not positions:
        return {"market": market, "checked": 0, "exits": []}

    stocks = {
        s.id: s
        for s in db.execute(select(Stock).where(Stock.id.in_(positions))).scalars()
    }
    quotes = await fetch_intraday_quotes(
        market, [stocks[sid].symbol for sid in positions if sid in stocks]
    )

    exits: list[dict] = []
    unpriced: list[str] = []
    blocked: list[str] = []
    for stock_id, qty in positions.items():
        stock = stocks.get(stock_id)
        if stock is None:
            continue
        quote = quotes.get(stock.symbol)
        if quote is None:
            logger.info("sentinel %s：無報價，本輪跳過", stock.symbol)
            unpriced.append(stock.symbol)
            continue
        stop, target, report_id = _entry_exit_levels(db, account.id, stock_id)

        fill_kind: str | None = None
        if stop is not None and quote <= stop:
            fill_kind = "stop_loss"
        elif target is not None and quote >= target:
            fill_kind = "take_profit"
        if fill_kind is None:
            continue

        outcome = _fill_exit(
            db, account, stock_id, qty, quote, report_id, fill_kind,
            is_etf=stock.kind == "etf",
        )
        if outcome == "blocked":
            # 接管失敗（並發哨兵剛好插進來）。無聲跳過等於停損靜默失效，
            # 必須留下紀錄——這曾是只能從 Postgres 唯一索引錯誤反推的黑洞。
            logger.warning(
                "sentinel %s %s 觸發但無法建立出場單（同股 pending 接管失敗），本輪略過",
                stock.symbol, _KIND_LABELS.get(fill_kind, fill_kind),
            )
            blocked.append(stock.symbol)
            continue
        exits.append(
            {
                "symbol": stock.symbol,
                "kind": fill_kind,
                "qty": qty,
                "price": quote,
                "trigger": stop if fill_kind == "stop_loss" else target,
                "superseded_pending": outcome == "superseded",
            }
        )
        logger.info(
            "sentinel exit %s %s x%.2f @ %.2f (%s%s)",
            market, stock.symbol, qty, quote, fill_kind,
            "，已接管同股 pending 委託" if outcome == "superseded" else "",
        )

    result = {
        "market": market,
        "checked": len(positions),
        "exits": exits,
        # 有持倉但當輪拿不到可成交價的標的（跌停鎖死/暫停交易等），供工作中心檢視
        "unpriced": unpriced,
        "blocked": blocked,
    }
    if unpriced and len(unpriced) == len(positions):
        # 一檔都拿不到報價＝這一輪哨兵完全沒有保護作用（Yahoo 封鎖機房 IP 時
        # 整批失敗）。回成功會讓「停損整段時間沒在運作」從工作中心看起來一切正常，
        # 故讓這一輪算失敗：工作中心留紅、log 留 ERROR。
        raise UpstreamError(
            f"{market} 盤中哨兵取不到任何報價（{len(positions)} 檔持倉全滅），"
            f"本輪停損/停利未生效：{'、'.join(unpriced)}"
        )
    return result


def _fill_exit(
    db: Session,
    account,
    stock_id: int,
    qty: float,
    price: float,
    report_id: int | None,
    fill_kind: str,
    is_etf: bool = False,
) -> str:
    """建 pending（吃 partial unique index 防重複）後立即以觀察價成交。

    回傳 'filled'（順利出場）／'superseded'（接管同股 pending 後出場）／
    'blocked'（連接管都失敗，本輪無法出場）。
    """
    # 先讓同股排隊中的委託單讓開，再 INSERT。
    # 只靠唯一索引擋下來也能運作，但那等於每次觸發都在 Postgres 留下一筆
    # ERROR（正式環境每小時一筆 uq_sim_orders_pending_account_stock），
    # 把真正的資料庫錯誤淹沒在噪音裡。INSERT 的 IntegrityError 兜底保留，
    # 用來處理「讓開之後又有人插進來」的真並發。
    superseded = _supersede_pending(db, account.id, stock_id, fill_kind)
    order = _insert_pending(db, account.id, stock_id, qty, report_id)
    if order is None:
        superseded += _supersede_pending(db, account.id, stock_id, fill_kind)
        order = _insert_pending(db, account.id, stock_id, qty, report_id)
        if order is None:
            # 仍建不起來（極罕見）。必須回滾，否則會留下「舊單被作廢、
            # 新出場單卻沒建立」的空窗——比原本被擋住更糟。
            db.rollback()
            return "blocked"

    gross = qty * price
    fee = calc_fee(account.market, "sell", gross, is_etf=is_etf)
    account.cash = float(account.cash) + gross - fee
    order.fill_price = price
    order.fee = fee
    order.status = "filled"
    order.fill_kind = fill_kind
    order.filled_at = utc_now_naive()
    db.commit()
    return "superseded" if superseded else "filled"


def _insert_pending(
    db: Session, account_id: int, stock_id: int, qty: float, report_id: int | None
) -> SimOrder | None:
    """建立 pending 出場單；撞 partial unique index（同股已有 pending）回 None。

    用 SAVEPOINT 包住：撞索引時只回滾這一筆 INSERT，外層交易與同一輪
    已處理的其他標的不受影響（直接 db.rollback() 會把整筆交易丟掉）。
    """
    order = SimOrder(
        account_id=account_id,
        stock_id=stock_id,
        side="sell",
        qty=qty,
        status="pending",
        decided_by="ai",
        ai_report_id=report_id,
        created_at=utc_now_naive(),
    )
    try:
        with db.begin_nested():
            db.add(order)
            db.flush()
    except IntegrityError:
        return None
    return order


def _supersede_pending(db: Session, account_id: int, stock_id: int, fill_kind: str) -> int:
    """作廢同股排隊中的 pending 委託單，讓位給盤中出場。回傳作廢筆數。

    為何是「接管」而非「跳過」：pending 日線單於開盤前（決策流程）建立，
    要到下一個開盤才撮合，中間橫跨整個交易時段。哨兵若禮讓它，等於持倉
    在盤中觸價卻只能等明天開盤才出場——停損的全部意義就是不等到那時候。
    無論該單是買是賣都作廢：已經要停損的標的不該再加碼，賣單則由哨兵
    以更好的（觸發當下）價格代為執行。

    用原生 UPDATE 且限定 status='pending'：同一條件也是唯一索引的 predicate，
    rowcount 即代表「確實由我們拿下」，並發哨兵不會兩邊都以為自己成功。
    """
    label = _KIND_LABELS.get(fill_kind, fill_kind)
    result = db.execute(
        update(SimOrder)
        .where(
            SimOrder.account_id == account_id,
            SimOrder.stock_id == stock_id,
            SimOrder.status == "pending",
        )
        .values(
            status="rejected",
            reject_reason=f"盤中{label}觸發，由出場哨兵接管",
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount


def _entry_exit_levels(
    db: Session, account_id: int, stock_id: int
) -> tuple[float | None, float | None, int | None]:
    """最近一次建倉買單所附報告的 (stop_loss, target_price_high, report_id)。"""
    buy = db.execute(
        select(SimOrder)
        .where(
            SimOrder.account_id == account_id,
            SimOrder.stock_id == stock_id,
            SimOrder.side == "buy",
            SimOrder.status == "filled",
            SimOrder.ai_report_id.is_not(None),
        )
        .order_by(SimOrder.filled_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if buy is None:
        return None, None, None
    report = db.get(AiReport, buy.ai_report_id)
    if report is None:
        return None, None, None
    try:
        payload = json.loads(report.payload_json)
    except ValueError:
        return None, None, buy.ai_report_id
    return (
        _to_float(payload.get("stop_loss")),
        _to_float(payload.get("target_price_high")),
        buy.ai_report_id,
    )


def _to_float(value) -> float | None:
    try:
        result = float(value)
        return result if result > 0 else None
    except (TypeError, ValueError):
        return None


def _in_market_hours(market: str, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(MARKET_TIMEZONES[market])
    (open_h, open_m), (close_h, close_m) = MARKET_HOURS[market]
    minutes = local.hour * 60 + local.minute
    return open_h * 60 + open_m <= minutes <= close_h * 60 + close_m
