FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl netcat-openbsd && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml physis/ ./physis/
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Agent data lives here — mount a volume to persist across restarts
VOLUME /data
WORKDIR /data

EXPOSE 4242

ENV PHYSIS_PORT=4242

CMD ["physis"]
