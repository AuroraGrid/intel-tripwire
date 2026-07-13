FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AURORA_QUIET=1 \
    DATABASE_PATH=/data/aurora-live.db

WORKDIR /app
COPY . /app
RUN mkdir -p /data && useradd --create-home --uid 10001 aurora && chown -R aurora:aurora /app /data
USER aurora
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:8090/api/platform/health',timeout=3)); assert d['status']=='ok'"
CMD ["python","platform_api.py","--host","0.0.0.0","--port","8090"]
