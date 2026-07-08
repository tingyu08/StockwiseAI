from app.providers.ai.gemini import _to_gemini_schema
from app.providers.ai.schemas import AnalysisReport, BatchAnalysisResult


def test_analysis_report_schema_converts():
    schema = _to_gemini_schema(AnalysisReport.model_json_schema())
    assert schema["type"] == "OBJECT"
    props = schema["properties"]
    assert props["action"]["type"] == "STRING"
    assert set(props["action"]["enum"]) == {"buy", "sell", "hold"}
    assert props["confidence"]["type"] == "NUMBER"
    assert props["risks"]["type"] == "ARRAY"
    # 巢狀 $ref（scenarios）需被展開
    assert props["scenarios"]["type"] == "OBJECT"
    assert props["scenarios"]["properties"]["bull"]["type"] == "OBJECT"


def test_batch_schema_is_array_of_reports():
    schema = _to_gemini_schema(BatchAnalysisResult.model_json_schema())
    reports = schema["properties"]["reports"]
    assert reports["type"] == "ARRAY"
    assert reports["items"]["type"] == "OBJECT"


def test_report_validation_roundtrip():
    payload = {
        "symbol": "2330",
        "action": "hold",
        "confidence": 0.62,
        "target_price_low": 2300,
        "target_price_high": 2600,
        "stop_loss": 2250,
        "reasoning": "測試",
        "scenarios": {
            "bull": {"target_price": 2700, "trigger_condition": "放量突破", "probability": 0.3},
            "base": {"target_price": 2500, "trigger_condition": "區間震盪", "probability": 0.5},
            "bear": {"target_price": 2200, "trigger_condition": "跌破月線", "probability": 0.2},
        },
        "risks": ["風險一", "風險二"],
    }
    report = AnalysisReport.model_validate(payload)
    assert report.action == "hold"
