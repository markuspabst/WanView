# WanView: multi-arch (amd64, arm64) via `docker buildx build --platform linux/amd64,linux/arm64`.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    DATA_DIR=/data \
    PORT=8080

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src

# Unprivileged user; /data holds logs, settings and router passwords.
RUN useradd --system --uid 1000 --home /app wanview \
    && mkdir -p /data && chown wanview /data
USER wanview
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import socket, os; socket.create_connection(('127.0.0.1', int(os.environ['PORT'])), 3).close()"

CMD ["python", "-m", "wanview"]
