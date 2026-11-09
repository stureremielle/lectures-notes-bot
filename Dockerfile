# syntax=docker/dockerfile:1
# aarch64 (Amlogic S905X box) friendly: python:3.12-slim publishes arm64.
# No ffmpeg, no audio/video download — captions only.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Makassar \
    LECTURE_BOT_CONFIG=/app/config.yaml

WORKDIR /app

# Fonts for the calendar PNG (Pillow needs a real TTF for the Indonesian grid).
# ca-certificates + tzdata keep TLS and WITA date math working off the box.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        tzdata \
        fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY prompts ./prompts

RUN mkdir -p /app/data/config /app/data/calendar

VOLUME ["/app/data"]

# No ports: every call is outbound.
CMD ["python", "-m", "src.main", "watch", "--all"]
