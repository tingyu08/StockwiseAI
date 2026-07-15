from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.rate_limiter import usage_snapshot
from app.services import analysis_service, news_service, prediction_service
from app.services.stock_read_service import get_price_series, get_stock


def build_dashboard(db: Session, market: str, symbol: str, range_key: str) -> dict:
    stock = get_stock(db, market, symbol)
    data = get_price_series(db, stock, range_key)
    try:
        prediction = prediction_service.get_predictions(db, stock)
    except NotFoundError:
        prediction = None
    analysis = analysis_service.latest_report(db, stock)
    news = news_service.latest_news_report(db, stock)
    return {
        **data,
        "prediction": prediction,
        "analysis": analysis_service.report_dto(analysis) if analysis else None,
        "news": news_service.news_dto(news) if news else None,
        "usage": usage_snapshot(db),
    }
