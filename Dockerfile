
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-liberation \
        fonts-dejavu-core \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so a code change does not re-run the (slow) pip install.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV WORKDIR=/tmp/carousel-work
RUN mkdir -p /tmp/carousel-work

CMD ["sh", "-c", "uvicorn review_api.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
