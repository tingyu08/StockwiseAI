import logging


def test_api_response_exposes_app_and_db_timing(client, caplog):
    caplog.set_level(logging.INFO, logger="app.performance")

    response = client.get(
        "/api/v1/watchlist",
        params={"market": "TW", "secret": "synthetic-query-secret"},
    )

    assert response.status_code == 200
    assert response.headers["Server-Timing"].startswith("app;dur=")
    assert ", db;dur=" in response.headers["Server-Timing"]
    records = [record.getMessage() for record in caplog.records if record.name == "app.performance"]
    message = next(item for item in records if "path=/api/v1/watchlist" in item)
    assert "method=GET" in message
    assert "status=200" in message
    assert "total_ms=" in message
    assert "db_ms=" in message
    assert "db_queries=" in message
    assert "synthetic-query-secret" not in message
    assert "secret=" not in message


def test_liveness_does_not_emit_info_timing_log(client, caplog):
    caplog.set_level(logging.INFO, logger="app.performance")
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert "Server-Timing" in response.headers
    assert not any(
        record.name == "app.performance"
        and "path=/api/v1/health/live" in record.getMessage()
        for record in caplog.records
    )
