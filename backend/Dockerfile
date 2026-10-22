FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv
COPY requirements.lock ./
# postgresql-client：每日備份工作要用 pg_dump（trixie 原生即 pg17，需 >= 伺服器版本）
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --require-hashes -r requirements.lock \
    && addgroup --system app \
    && adduser --system --ingroup app app \
    # 備份目錄預先建好並交給 app：named volume 首次建立時會沿用映像檔內
    # 的擁有者，否則掛上去是 root 所有，非 root 的 pg_dump 寫不進去
    && mkdir -p /data/backups \
    && chown -R app:app /data

COPY --chown=app:app app ./app
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app alembic.ini ./

USER app

EXPOSE 8000
# 啟動前套用 migration，再起服務；PORT 由平台注入（Zeabur/Render），預設 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
