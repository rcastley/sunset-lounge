FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Create a non-root user that owns /app and /data
RUN useradd --create-home --uid 1000 app

WORKDIR /app

# Install Python dependencies first so the layer caches well
COPY requirements.txt ./
RUN pip install -r requirements.txt

# App code
COPY app.py seed.py schema.sql ./
COPY templates ./templates
COPY static ./static

# Persistent data lives under /data (mounted as a volume)
RUN mkdir -p /data && chown -R app:app /app /data

USER app

ENV SUNSET_DB_PATH=/data/sunset_lounge.db \
    PORT=8000

EXPOSE 8000

# --preload imports the app once in the master so ensure_db() (schema +
# migrations + seed) runs exactly once before workers fork.
CMD ["gunicorn", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--threads", "4", \
     "--preload", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]
