================================================================================
StockBot — agentic paper-trading bot (Phase 1.5 + 2a complete)
================================================================================

WHAT THIS IS
------------
A multi-agent stock-trading system built on Google ADK. Four specialised
analysts (Technical, Fundamental, Sentiment, Smart Money) feed signals to a
single Strategist agent, which is constrained by a deterministic RiskGate
and executed against Trading 212's paper account. Runs once per hour during
US market hours via Cloud Run Jobs + Cloud Scheduler. Compares performance
against SPY buy-and-hold.

CURRENT STATE
-------------
What works:
  - End-to-end multi-agent pipeline (4 analysts -> strategist -> risk gate
    -> executor -> memory -> snapshot), all signals persisted for attribution.
  - Trading 212 paper-account broker with a fake-broker test double.
  - Cloud SQL Postgres / SQLite persistence behind one SessionService factory.
  - Lifecycle scripts: hard_reset (pause + archive + truncate) and initialise
    (pre-flight + anchor snapshot + scheduler resume).
  - Static bot-vs-SPY equity-curve PNG.
  - Local validation: smoke_run (3 ticks, FakeBroker, real LLMs) and
    replay_backtest (30-day walk-forward).
  - Cloud deployment artefacts: Dockerfile, cloudbuild.yaml, scheduler.yaml.

What is intentionally NOT here yet:
  - Local web dashboard (Phase 2b).
  - Pluralised deliberation / multiple strategists (Phase 2c).
  - MLP baseline (Phase 3).
  - Live trading: gated by >=30 days beating SPY on Sharpe AND cumulative
    return. Manual flip only.

================================================================================
FROM CLONE TO RUNNING
================================================================================

PREREQUISITES
-------------
  - Python 3.12
  - git
  - A Trading 212 practice account + API key
  - A Finnhub API key
  - A GCP project (only required for cloud deployment, not for local runs)
  - gcloud CLI logged in (only for cloud deployment)
  - Docker (only required if you build the image locally; Cloud Build does
    this for you in the deploy path)

1. CLONE AND ENTER THE REPO
---------------------------
  git clone <repo-url> StockBot
  cd StockBot

2. CREATE A VIRTUALENV AND INSTALL DEPENDENCIES
-----------------------------------------------
  python -m venv .venv
  # Windows PowerShell:
  .venv\Scripts\Activate.ps1
  # macOS / Linux:
  source .venv/bin/activate

  pip install -r requirements.txt

3. CONFIGURE ENVIRONMENT
------------------------
  cp .env.example .env
  # Edit .env and fill in:
  #   FINNHUB_API_KEY
  #   TRADING212_API_KEY (paper)
  #   EDGAR_IDENTITY
  #   GOOGLE_ADK_PROJECT (your GCP project id)
  # Leave QUIVER_QUANT_API_KEY blank if you don't have one -- Smart Money
  # analyst degrades gracefully.

4. RUN THE TEST SUITE
---------------------
  pytest

  Expected: all unit + integration tests pass (replay tests are skipped by
  default; they're long-running and need real LLMs).

5. VERIFY YOUR SETUP WITH A LOCAL SMOKE RUN
-------------------------------------------
  PYTHONPATH=src python -m scripts.smoke_run --ticks 1

  Runs one full tick against FakeBroker with real LLMs and real data
  providers. Costs ~$0.07. Confirms your API keys and ADK auth work.

6. (OPTIONAL) RUN A 30-DAY HISTORICAL BACKTEST
----------------------------------------------
  PYTHONPATH=src python -m scripts.replay_backtest --window 30d

  Walk-forward through 30 days of cached yfinance data. Useful for tuning
  the strategist prompt before deploying.

================================================================================
DEPLOY TO GCP (PAPER TRADING)
================================================================================

The bot only trades autonomously when running in GCP. Local commands above
are for validation. The full deployment runbook (one-time GCP setup, secrets,
service account, Cloud Build trigger, Cloud SQL, Cloud Scheduler) is in:

  deploy/README.md

Short version, assuming GCP setup from deploy/README.md is complete:

  1. Push to main; Cloud Build builds + deploys the Cloud Run Job.
  2. From your laptop:
       PYTHONPATH=src python -m scripts.initialise --capital 10000 \
         --broker-mode paper --scheduler-job stockbot-tick
     This runs pre-flight checks, writes the equity-curve anchor, and
     resumes Cloud Scheduler. The bot starts trading on the next cron firing.
  3. Watch logs:
       gcloud run jobs executions list --job=stockbot-tick --region=us-central1

================================================================================
RESET AND START OVER
================================================================================

  1. PYTHONPATH=src python -m scripts.hard_reset \
       --scheduler-job stockbot-tick --starting-capital 10000
     Pauses the scheduler, archives every StockBot table to
     data/archives/<timestamp>.db (or a Postgres archive schema in prod),
     and truncates the live tables.

  2. Reset the Trading 212 practice account in their UI
     (Settings -> Practice account -> Reset).

  3. PYTHONPATH=src python -m scripts.initialise --capital 10000 \
       --broker-mode paper --scheduler-job stockbot-tick
     Verifies the reset, writes a fresh anchor, resumes the scheduler.

================================================================================
VIEWING PERFORMANCE
================================================================================

  PYTHONPATH=src python -m scripts.plot_equity \
    --out docs/performance/equity.png

  Renders bot-vs-SPY equity curve since the last reset, plus an excess-
  return overlay. Reads from portfolio_snapshots.

================================================================================
WHERE TO LOOK NEXT
================================================================================

  deploy/README.md               GCP setup runbook + kickoff checklist + live-trading gate
  docs/Phase1-build/             Phase 1 design docs and per-area design notes
  docs/superpowers/specs/        Approved feature specs (phase 2a, future phases)
  docs/superpowers/plans/        Implementation plans (this phase, future phases)
  src/agents/                    The four analysts + strategist + executor + memory
  src/orchestrator/pipeline.py   ADK SequentialAgent composition (the "brain wiring")
  src/baselines/                 SPY metrics + equity-curve library (shared with future dashboard)
  src/lifecycle/                 hard_reset and initialise libraries
  src/scripts/                   CLI entrypoints (PYTHONPATH=src python -m scripts.<name>)
