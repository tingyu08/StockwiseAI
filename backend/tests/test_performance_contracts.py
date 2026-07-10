import inspect
from datetime import date

from sqlalchemy import event

from app.api.v1 import alerts, backtest, compare, premium, simulation, usage
from app.core.db import SessionLocal, engine
from app.models import EtfNav, Stock
from app.models.alert import Alert, AlertEvent
from app.services.premium_service import premium_list


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
                symbol=f"QETF{i}", market="US", name=f"Query ETF {i}",
                currency="USD", kind="etf",
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
            rows = premium_list(db, "US")
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
        assert len(statements) == 1
    finally:
        db.close()


def test_hot_path_composite_indexes_are_declared():
    sim_indexes = {index.name for index in __import__("app.models.simulation", fromlist=["SimOrder"]).SimOrder.__table__.indexes}
    event_indexes = {index.name for index in AlertEvent.__table__.indexes}

    assert "ix_sim_orders_account_status_created" in sim_indexes
    assert "ix_alert_events_alert_trade_date" in event_indexes
