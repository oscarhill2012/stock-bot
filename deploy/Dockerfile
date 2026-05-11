# StockBot one-shot tick container — runs orchestrator.tick once and exits.
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# TA-Lib (Phase 4 technical analyst) installs from the manylinux wheel published on
# PyPI — no `libta-lib` system library is required. `build-essential` above stays as
# a safety net for any source-builds of other deps; do not strip it without retesting.
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    STOCKBOT_ENV=prod

ENTRYPOINT ["python", "-m", "orchestrator.tick"]
CMD ["--mode", "paper"]
