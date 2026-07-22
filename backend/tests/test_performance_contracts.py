import inspect
from datetime import date, datetime

import pytest
from sqlalchemy import delete, event
from sqlalchemy.exc import IntegrityError

from app.api.v1 import alerts, backtest, compare, premium, simulation, usage
from app.core.db import SessionLocal, engine
from app.models import DailyPrice, EtfNav, JobRun, Prediction, SimOrder, Stock
from app.models.alert import Alert, AlertEvent
from app.services.premium_service import premium_list
from app.services.sim.engine import get_or_create_account
from app.services.sim.portfolio import positions_dto


def test_database_only_endpoints_run_in_fastapi_threadpool():
    endpoints = (
        usage.get_usage,
        premium.list_premium,
        premium.premium_history,
        alerts.list_alerts,
        backtest.backtest,
        compare.compare_stocks,
        simulation.account_view,
        simulation.orders_view,
        simulation.trigger_fill,
    )
    assert all(not inspect.iscoroutinefunction(endpoint) for endpoint in endpoints)


def test_premium_list_uses_one_query_for_all_etfs(client):
    db = SessionLocal()
    try:
        for i in range(3):
            stock = Stock(
                symbol=f"QETF{i}", market="TW", name=f"Query ETF {i}",
                currency="TWD", kind="etf",
            )
            db.add(stock)
            db.flush()
            db.add(EtfNav(
                stock_id=stock.id, date=date(2026, 7, 1),
                nav=100, close=101 + i, premium_pct=1 + i,
            ))
        db.commit()

        statements = []
        def count_query(*args):
            statements.append(args[2])

        event.listen(engine, "before_cursor_execute", count_query)
        try:
            rows = premium_list(db, "TW")
        finally:
            event.remove(engine, "before_cursor_execute", count_query)

        assert len([row for row in rows if row["symbol"].startswith("QETF")]) == 3
        assert len(statements) == 1
    finally:
        db.close()


def test_alert_list_uses_one_query_for_latest_events(client):
    db = SessionLocal()
    try:
        for i in range(3):
            stock = Stock(
                symbol=f"QALT{i}", market="US", name=f"Query Alert {i}",
                currency="USD", kind="stock",
            )
            db.add(stock)
            db.flush()
            alert = Alert(stock_id=stock.id, kind="price_above", threshold=100)
            db.add(alert)
            db.flush()
            db.add(AlertEvent(alert_id=alert.id, trade_date=date(2026, 7, 1), value=101))
        db.commit()

        statements = []
        def count_query(*args):
            statements.append(args[2])

        event.listen(engine, "before_cursor_execute", count_query)
        try:
            response = client.get("/api/v1/alerts?market=US")
        finally:
            event.remove(engine, "before_cursor_execute", count_query)

        assert response.status_code == 200
        # One authentication lookup plus one endpoint query.
        assert len(statements) == 2
    finally:
        db.close()


def test_hot_path_composite_indexes_are_declared():
    sim_indexes = {index.name for index in __import__("app.models.simulation", fromlist=["SimOrder"]).SimOrder.__table__.indexes}
    event_indexes = {index.name for index in AlertEvent.__table__.indexes}
    job_indexes = {index.name for index in JobRun.__table__.indexes}
    prediction_constraints = {
        constraint.name for constraint in Prediction.__table__.constraints
    }

    assert "ix_sim_orders_account_status_created" in sim_indexes
    assert "ix_alert_events_alert_trade_date" in event_indexes
    assert "ix_alert_events_notification_created" in event_indexes
    assert "ix_job_runs_status_created" in job_indexes
    assert "uq_predictions_identity" in prediction_constraints


def test_prediction_identity_is_unique(client):
    db = SessionLocal()
    try:
        stock = Stock(symbol="QPRED", market="US", name="Prediction", currency="USD", kind="stock")
        db.add(stock)
        db.flush()
        values = dict(
            stock_id=stock.id,
            trade_date=date(2026, 7, 1),
            horizon_days=5,
            method="regression",
            predicted_json="{}",
        )
        db.add_all([Prediction(**values), Prediction(**values)])
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_positions_dto_query_count_does_not_grow_with_positions(client):
    db = SessionLocal()
    stock_ids = []
    try:
        account = get_or_create_account(db, "US")
        for index in range(4):
            stock = Stock(
                symbol=f"QPOS{index}", market="US", name=f"Position {index}",
                currency="USD", kind="stock",
            )
            db.add(stock)
            db.flush()
            stock_ids.append(stock.id)
            db.add(DailyPrice(
                stock_id=stock.id, date=date(2026, 7, 1), open=100, high=101,
                low=99, close=100 + index, volume=1000,
            ))
            db.add(SimOrder(
                account_id=account.id, stock_id=stock.id, side="buy", qty=1,
                fill_price=100, fee=0, status="filled", decided_by="test",
                filled_at=datetime(2026, 7, 1, 14, 30),
            ))
        db.commit()

        statements = []

        def count_query(*args):
            statements.append(args[2])

        event.listen(engine, "before_cursor_execute", count_query)
        try:
            rows = positions_dto(db, account)
        finally:
            event.remove(engine, "before_cursor_execute", count_query)

        assert len([row for row in rows if row["symbol"].startswith("QPOS")]) == 4
        assert len(statements) <= 3
    finally:
        if stock_ids:
            db.execute(delete(SimOrder).where(SimOrder.stock_id.in_(stock_ids)))
            db.execute(delete(DailyPrice).where(DailyPrice.stock_id.in_(stock_ids)))
            db.execute(delete(Stock).where(Stock.id.in_(stock_ids)))
            db.commit()
        db.close()
