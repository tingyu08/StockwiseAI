import json
from datetime import timedelta

from app.core.db import SessionLocal
from app.models import AiReport, DailyPrice, Stock
from app.services.time_service import market_today


def _seed_dashboard_stock(symbol: str = "DASH1", days: int = 40) -> None:
    today = market_today("TW")
    with SessionLocal() as db:
        stock = Stock(
            symbol=symbol,
            market="TW",
            name="Dashboard",
            currency="TWD",
            kind="stock",
        )
        db.add(stock)
        db.commit()
        db.refresh(stock)
        for offset in range(days):
            value = 100 + offset
            db.add(
                DailyPrice(
                    stock_id=stock.id,
                    date=today - timedelta(days=days - offset - 1),
                    open=value,
                    high=value + 1,
                    low=value - 1,
                    close=value + 0.5,
                    volume=1000 + offset,
                )
            )
        db.commit()


def test_dashboard_returns_one_complete_payload_without_external_calls(client, monkeypatch):
    _seed_dashboard_stock()

    async def external_call_forbidden(*_args, **_kwargs):
        raise AssertionError("dashboard must not call an external provider")

    monkeypatch.setattr(
        "app.services.market_gateway.market_data.get_daily_prices",
        external_call_forbidden,
    )
    monkeypatch.setattr(
        "app.providers.ai.gemini.GeminiProvider.generate",
        external_call_forbidden,
    )

    response = client.get(
        "/api/v1/stocks/DASH1/dashboard",
        params={"market": "TW", "range": "3m"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["stock"]["symbol"] == "DASH1"
    assert len(data["series"]) == 40
    assert data["prediction"]["method"] == "regression_channel"
    assert data["analysis"] is None
    assert data["news"] is None
    assert isinstance(data["usage"], list)


def test_dashboard_includes_stored_analysis_and_news(client):
    _seed_dashboard_stock("DASH2")
    today = market_today("TW")
    with SessionLocal() as db:
        stock = db.query(Stock).filter_by(market="TW", symbol="DASH2").one()
        db.add_all(
            [
                AiReport(
                    stock_id=stock.id,
                    trade_date=today,
                    provider="gemini",
                    model="gemini-3.5-flash-lite",
                    prompt_version="v2",
                    input_hash="dashboard-analysis",
                    kind="routine",
                    action="hold",
                    confidence=0.7,
                    payload_json=json.dumps(
                        {
                            "symbol": "DASH2",
                            "action": "hold",
                            "confidence": 0.7,
                            "target_price_low": 120,
                            "target_price_high": 130,
                            "stop_loss": 110,
                            "reasoning": "stored",
                            "scenarios": {},
                            "risks": [],
                        }
                    ),
                ),
                AiReport(
                    stock_id=stock.id,
                    trade_date=today,
                    provider="antigravity",
                    model="antigravity-preview-05-2026",
                    prompt_version="news-v2",
                    input_hash="dashboard-news",
                    kind="news",
                    action=None,
                    confidence=None,
                    payload_json=json.dumps({"summary": "stored news"}),
                ),
            ]
        )
        db.commit()

    data = client.get(
        "/api/v1/stocks/DASH2/dashboard",
        params={"market": "TW", "range": "1y"},
    ).json()["data"]

    assert data["analysis"]["report"]["reasoning"] == "stored"
    assert data["news"]["summary"] == "stored news"


def test_dashboard_unknown_stock_returns_404(client):
    response = client.get(
        "/api/v1/stocks/NO-DASH/dashboard",
        params={"market": "TW", "range": "1y"},
    )
    assert response.status_code == 404


def test_dashboard_returns_null_prediction_when_history_is_short(client):
    _seed_dashboard_stock("DASH3", days=10)
    response = client.get(
        "/api/v1/stocks/DASH3/dashboard",
        params={"market": "TW", "range": "3m"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["prediction"] is None
