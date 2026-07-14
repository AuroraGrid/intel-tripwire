FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AURORA_QUIET=1 \
    DATABASE_PATH=/data/aurora-live.db \
    GUNICORN_WORKERS=2 \
    GUNICORN_TIMEOUT=30

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY . /app
RUN mkdir -p /data && useradd --create-home --uid 10001 aurora && chown -R aurora:aurora /app /data
USER aurora
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:8090/api/platform/ready',timeout=3)); assert d['status']=='ready'"
CMD ["sh","-c","exec gunicorn release_wsgi:application --bind 0.0.0.0:8090 --workers ${GUNICORN_WORKERS} --timeout ${GUNICORN_TIMEOUT} --access-logfile - --error-logfile -"]
