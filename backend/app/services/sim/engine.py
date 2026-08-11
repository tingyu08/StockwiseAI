"""模擬交易撮合引擎。

規則（docs/SD.md §3）：
- 委託單於 AI 決策時建立為 pending，以「決策後第一個開盤」的開盤價成交：
  開盤前（晨間決策流程）建立的單吃當地「當天」開盤；開盤後建立的單吃下一個交易日
- 台股費用：手續費 0.1425%（最低 20 元），賣出另課證交稅——個股 0.3%、ETF 0.1%
- 美股費用：0（主流券商零手續費）
- 事件溯源：orders 一經 filled/rejected 不再變更；持倉由重放推導
"""
import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from app.models import DailyPrice, SimAccount, SimOrder, Stock
from app.services.sim.portfolio import current_positions
from app.services.time_service import MARKET_TIMEZONES, utc_now_naive

logger = logging.getLogger(__name__)

INITIAL_CASH = {"TW": 1_000_000.0, "US": 30_000.0}
CURRENCY = {"TW": "TWD", "US": "USD"}
MARKET_OPEN = {"TW": (9, 0), "US": (9, 30)}  # 當地開盤時間

TW_FEE_RATE = 0.001425
TW_FEE_MIN = 20.0
# 買進的最低委託金額。台股的手續費有 20 元下限，金額越小費率越誇張：
# 正式環境曾對 00981A 買進 1 股（28.92 元）卻照收 20 元，手續費佔成交金額
# 69%，成本均價被推到 48.92，一買進就浮虧 41%，與行情完全無關。
# 10,000 元讓最低手續費壓在 0.2% 以內（見 test_min_order_value）。
# 美股手續費為 0，小額單沒有成本劣勢，故不設限。
MIN_ORDER_VALUE = {"TW": 10_000.0, "US": 0.0}
# 'filling' 的租約。單筆撮合是秒級的，15 分鐘足以斷定是撮合中斷留下的孤兒，
# 又遠大於任何正常處理時間——手動撮合（POST :fill）是同步直呼、不經 job
# queue，可能與排程並行，租約太短會把別人正在處理的訂單搶走。
STALE_FILLING_SECONDS = 900
STUCK_FILLING_REASON = "撮合流程中斷，決策已過期（系統回收）"
TW_TAX_RATE = 0.003  # 賣出證交稅（個股）
TW_ETF_TAX_RATE = 0.001  # 賣出證交稅（受益憑證/ETF）——為個股的 1/3


def get_or_create_account(db: Session, market: str) -> SimAccount:
    account = db.execute(
        select(SimAccount).where(SimAccount.market == market)
    ).scalar_one_or_none()
    if account is None:
        account = SimAccount(
            market=market,
            currency=CURRENCY[market],
            initial_cash=INITIAL_CASH[market],
            cash=INITIAL_CASH[market],
        )
        db.add(account)
        db.commit()
        db.refresh(account)
    return account


def market_open_utc(session_day: date, market: str) -> datetime:
    """該交易日「當地開盤時刻」換算成 naive UTC。

    存進 DB 的時間欄位一律是 naive UTC（見 time_service.utc_now_naive），
    filled_at 也不例外。原本寫的是 `datetime.combine(交易日, 00:00)`——
    既不是 UTC 也不是明確的當地時刻，而 sentinel 在同一欄位寫的是真正的
    UTC 時刻，一個欄位兩種語意，畫面只好退到「只顯示日期」。

    用 zoneinfo 換算而非寫死偏移：美東有夏令時間，寫死會在換季後錯一小時。
    兩個市場的開盤換算後都落在 UTC 同一日（台股 01:00、美股 13:30/14:30），
    所以權益曲線用 filled_at.date() 歸屬每日損益仍然正確。
    """
    hour, minute = MARKET_OPEN[market]
    local = datetime.combine(session_day, time(hour, minute)).replace(
        tzinfo=MARKET_TIMEZONES[market]
    )
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def meets_min_order_value(market: str, gross: float) -> bool:
    """買進金額是否達到下限。

    只約束買進：擋住賣出等於把零股永遠鎖在帳上，停損更不能被金額卡住。
    """
    return gross + 1e-9 >= MIN_ORDER_VALUE.get(market, 0.0)


def tw_tax_rate(is_etf: bool) -> float:
    """台股賣出證交稅：ETF（受益憑證）0.1%，個股 0.3%。"""
    return TW_ETF_TAX_RATE if is_etf else TW_TAX_RATE


def calc_fee(market: str, side: str, gross: float, is_etf: bool = False) -> float:
    """交易成本（手續費＋稅）。

    ETF 的證交稅只有個股的 1/3，不分辨會讓 ETF 的賣出成本高估近三倍
    （0.3% vs 0.1%），對 ETF 佔比高的組合影響顯著。
    """
    if market == "US":
        return 0.0
    fee = max(TW_FEE_MIN, gross * TW_FEE_RATE)
    if side == "sell":
        fee += gross * tw_tax_rate(is_etf)
    return round(fee, 2)


def fill_pending_orders(db: Session, market: str) -> dict:
    """撮合所有 pending 單：以委託建立後第一個交易日的開盤價成交。

    現金不足（開盤價高於決策時估價）→ 縮量成交；縮到 0 → rejected。
    """
    account = get_or_create_account(db, market)
    recovered = _reject_stale_filling_orders(db, market)
    positions = current_positions(db, account)
    pending = db.execute(
        select(SimOrder, Stock)
        .join(Stock, SimOrder.stock_id == Stock.id)
        .join(SimAccount, SimOrder.account_id == SimAccount.id)
        .where(SimAccount.market == market, SimOrder.status == "pending")
        .order_by(SimOrder.created_at)
    ).all()

    filled = rejected = waiting = 0
    for order, stock in pending:
        leased_at = _claim_pending_order(db, order.id)
        if leased_at is None:
            continue
        # 告訴 ORM「DB 裡現在就是這兩個值」，而非把它們當成待寫入的變更。
        # 直接指派無效：ORM 載入時 filling_since 是 None，稍後再指派 None
        # 會被判定為無變更而不寫回，租約就永遠留在 DB 裡（成交後也是）。
        set_committed_value(order, "status", "filling")
        set_committed_value(order, "filling_since", leased_at)
        price_row = db.execute(
            select(DailyPrice)
            .where(
                DailyPrice.stock_id == stock.id,
                DailyPrice.date >= _earliest_fill_date(order.created_at, market),
                DailyPrice.open.is_not(None),
            )
            .order_by(DailyPrice.date)
            .limit(1)
        ).scalar_one_or_none()
        if price_row is None:
            if restore_or_reject(db, order) == "pending":
                waiting += 1  # 下一個交易日資料尚未同步
            else:
                rejected += 1
            continue

        open_price = float(price_row.open)
        qty = float(order.qty)

        if order.side == "buy":
            qty = _affordable_qty(
                float(account.cash), open_price, market, max_qty=qty
            )
            if qty <= 0:
                _reject(order, "開盤價高於預期，現金不足")
                rejected += 1
                continue
            gross = qty * open_price
            # 決策用昨收估價，開盤跳空時上面會縮量——縮到剩幾股的話成交金額
            # 可能只剩幾百元，手續費照收 20 元。只在決策端擋等於留了後門。
            if not meets_min_order_value(market, gross):
                _reject(
                    order,
                    f"縮量後委託金額 {gross:,.0f} 低於下限 "
                    f"{MIN_ORDER_VALUE[market]:,.0f}（手續費佔比過高）",
                )
                rejected += 1
                continue
            fee = calc_fee(market, "buy", gross, is_etf=stock.kind == "etf")
            account.cash = float(account.cash) - gross - fee
        else:
            held_qty = positions.get(stock.id, 0.0)
            if qty > held_qty + 1e-9:
                _reject(order, "賣出數量超過目前持倉")
                rejected += 1
                continue
            gross = qty * open_price
            fee = calc_fee(market, "sell", gross, is_etf=stock.kind == "etf")
            account.cash = float(account.cash) + gross - fee

        order.qty = qty
        order.fill_price = open_price
        order.fee = fee
        order.status = "filled"
        order.filling_since = None
        order.filled_at = market_open_utc(price_row.date, market)
        delta = qty if order.side == "buy" else -qty
        positions[stock.id] = round(positions.get(stock.id, 0.0) + delta, 4)
        filled += 1
        logger.info(
            "filled %s %s %s x%.2f @ %.2f fee=%.2f",
            market, order.side, stock.symbol, qty, open_price, fee,
        )

    db.commit()
    result = {
        "market": market, "filled": filled, "rejected": rejected, "waiting": waiting,
    }
    if recovered:
        result["recovered_stuck"] = recovered
    return result


def _earliest_fill_date(created_at_utc: datetime, market: str) -> date:
    """委託可成交的最早交易日：開盤前建立 → 當地當天；開盤後建立 → 次一日。

    無前視偏誤：晨間（開盤前）的決策用的是昨收＋隔夜國際盤資料，
    成交於幾小時後的當日開盤價，等同真實世界的開盤市價單。
    """
    aware = (
        created_at_utc.replace(tzinfo=timezone.utc)
        if created_at_utc.tzinfo is None
        else created_at_utc
    )
    local = aware.astimezone(MARKET_TIMEZONES[market])
    if (local.hour, local.minute) < MARKET_OPEN[market]:
        return local.date()
    return local.date() + timedelta(days=1)


def _reject(order: SimOrder, reason: str) -> None:
    order.status = "rejected"
    order.reject_reason = reason
    order.filling_since = None


def restore_or_reject(db: Session, order: SimOrder) -> str:
    """把 filling 放回 pending；撞唯一索引就改判 rejected。回傳最終狀態。

    claim 是原生 UPDATE（session 內屬性仍是舊值），還原也必須走 UPDATE——
    只改屬性會被 SQLAlchemy 視為無變更而不寫回。

    撞索引的情境：'filling' 期間該單不受 partial unique index 保護，哨兵
    可能已為同一 (account, stock) 建了新的 pending 單。以前這裡只記一行
    「維持 filling 待下輪」就放著——但撿單的查詢全都只看 pending，
    根本沒有下輪，訂單就此永久卡死（正式環境卡了整整一個月）。
    改判 rejected 才有終局；那筆委託的意圖也已由哨兵的新單接手。

    用 savepoint 隔離：只放棄這一筆的還原，不讓整批已成交的訂單
    隨著外層 rollback 一起消失。
    """
    try:
        with db.begin_nested():
            db.execute(
                update(SimOrder)
                .where(SimOrder.id == order.id)
                .values(status="pending", filling_since=None)
                .execution_options(synchronize_session=False)
            )
        order.status = "pending"
        order.filling_since = None
        return "pending"
    except IntegrityError:
        logger.warning(
            "訂單 %s 還原 pending 撞唯一索引（同股已有新的 pending 單），改判 rejected",
            order.id,
        )
        db.execute(
            update(SimOrder)
            .where(SimOrder.id == order.id)
            .values(
                status="rejected",
                reject_reason="同股已有新的待成交委託，本單由系統回收",
                filling_since=None,
            )
            .execution_options(synchronize_session=False)
        )
        order.status = "rejected"
        order.filling_since = None
        return "rejected"


def _reject_stale_filling_orders(db: Session, market: str) -> int:
    """回收租約逾時的 filling 訂單（撮合中斷留下的孤兒）。

    標成 rejected 而非還原成 pending：那些決策可能是數週前做的，
    拿舊判斷去吃今天的開盤價比不成交更糟。

    filling_since 為 NULL 的是本次改動之前留下的，一併回收。
    """
    cutoff = utc_now_naive() - timedelta(seconds=STALE_FILLING_SECONDS)
    stale_ids = db.execute(
        select(SimOrder.id)
        .join(SimAccount, SimOrder.account_id == SimAccount.id)
        .where(
            SimAccount.market == market,
            SimOrder.status == "filling",
            or_(
                SimOrder.filling_since.is_(None),
                SimOrder.filling_since < cutoff,
            ),
        )
    ).scalars().all()
    if not stale_ids:
        return 0
    db.execute(
        update(SimOrder)
        .where(SimOrder.id.in_(stale_ids))
        .values(
            status="rejected",
            reject_reason=STUCK_FILLING_REASON,
            filling_since=None,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    logger.warning(
        "%s 回收卡在 filling 的訂單 %d 筆：%s", market, len(stale_ids), stale_ids
    )
    return len(stale_ids)


def _claim_pending_order(db: Session, order_id: int) -> datetime | None:
    """Atomically move one pending order into the in-flight state.

    回傳租約起算時刻（未搶到則 None）。呼叫端必須把它寫回 session 內的
    物件：claim 走的是原生 UPDATE，ORM 仍以為 filling_since 是 None，
    之後再指派 None 會被視為無變更而不寫回，租約就永遠留在 DB 裡。
    """
    leased_at = utc_now_naive()
    result = db.execute(
        update(SimOrder)
        .where(SimOrder.id == order_id, SimOrder.status == "pending")
        .values(status="filling", filling_since=leased_at)
        .execution_options(synchronize_session=False)
    )
    return leased_at if result.rowcount == 1 else None


def _affordable_qty(
    cash: float, price: float, market: str, max_qty: float | None = None
) -> float:
    """Largest affordable whole/fractional quantity via O(log n) search."""
    if cash <= 0 or price <= 0:
        return 0.0
    scale = 1 if market == "TW" else 100
    high = int(cash / price * scale)
    if max_qty is not None:
        high = min(high, int(max_qty * scale + 1e-9))
    low = 0
    while low < high:
        mid = (low + high + 1) // 2
        qty = mid / scale
        gross = qty * price
        if gross + calc_fee(market, "buy", gross) <= cash + 1e-9:
            low = mid
        else:
            high = mid - 1
    return float(low) if market == "TW" else round(low / scale, 2)
