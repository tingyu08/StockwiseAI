def test_health_returns_envelope(client):
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["data"] == {"status": "ok"}
    assert body["error"] is None


def test_usage_lists_all_quota_models(client):
    res = client.get("/api/v1/usage")
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    models = {row["model"] for row in body["data"]}
    assert "gemini-3.1-flash-lite" in models
    assert "gemini-3.5-flash" in models
    assert "gemma-4-31b-it" in models
    for row in body["data"]:
        assert row["used"] == 0
        assert row["remaining"] == row["rpd"]
