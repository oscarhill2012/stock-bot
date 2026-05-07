# StockBot one-shot tick container — runs orchestrator.tick once and exits.
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    STOCKBOT_ENV=prod

ENTRYPOINT ["python", "-m", "orchestrator.tick"]
CMD ["--mode", "paper"]
