# EHentai Downloader – web version (build from the project folder):
#   docker build -f docker/Dockerfile -t ehentaidownloader .
FROM python:3.13-slim

LABEL org.opencontainers.image.title="EHentai Downloader" \
      org.opencontainers.image.description="E-Hentai / ExHentai / nhentai gallery downloader with a web interface" \
      org.opencontainers.image.source="https://github.com/LainX84/EHentaiDownloader" \
      org.opencontainers.image.documentation="https://github.com/LainX84/EHentaiDownloader/blob/main/docker/README.md" \
      org.opencontainers.image.licenses="GPL-3.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EHD_DATA_DIR=/data \
    EHD_CONFIG_DIR=/config \
    EHD_PORT=8000

# tzdata: log times in the zone of the TZ variable (e.g. TZ=Europe/Rome)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY docker/requirements-web.txt /tmp/requirements-web.txt
RUN pip install --no-cache-dir -r /tmp/requirements-web.txt && rm /tmp/requirements-web.txt

COPY LICENSE ./
COPY assets/icon_small.png assets/icon_32.png assets/logo_small.png ./assets/
COPY src ./src

# Default user 1000:1000 (change it with "user:" in compose); /data and /config are volumes
RUN useradd --uid 1000 --user-group --no-create-home --shell /usr/sbin/nologin ehd \
    && mkdir -p /data /config && chown ehd:ehd /data /config
USER ehd
VOLUME ["/data", "/config"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"EHD_PORT\",\"8000\")}/api/info', timeout=4)"

CMD ["python", "-m", "src.web"]
