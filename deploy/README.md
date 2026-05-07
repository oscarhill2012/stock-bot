# StockBot Deployment Runbook

Phase 1 paper-trading deployment to GCP.

## Prerequisites

- GCP project with billing enabled
- `gcloud auth login` and `gcloud auth application-default login`
- Trading 212 practice account with API key

## One-time GCP setup

```bash
# 1. Set the project
gcloud config set project YOUR_PROJECT_ID

# 2. Enable required APIs
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  cloudscheduler.googleapis.com \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  aiplatform.googleapis.com

# 3. Create Artifact Registry repo
gcloud artifacts repositories create stockbot \
  --repository-format=docker --location=us-central1

# 4. Create Cloud SQL Postgres instance (db-f1-micro for Phase 1, ~$10/mo)
gcloud sql instances create stockbot-db \
  --database-version=POSTGRES_15 --tier=db-f1-micro --region=us-central1
gcloud sql databases create stockbot --instance=stockbot-db
gcloud sql users create stockbot --instance=stockbot-db --password=GENERATE_AND_STORE

# 5. Service account for the runner job
gcloud iam service-accounts create stockbot-runner --display-name="StockBot Runner"
SA="stockbot-runner@$(gcloud config get-value project).iam.gserviceaccount.com"
for role in \
  roles/aiplatform.user \
  roles/cloudsql.client \
  roles/secretmanager.secretAccessor \
  roles/storage.objectUser ; do
  gcloud projects add-iam-policy-binding $(gcloud config get-value project) \
    --member="serviceAccount:$SA" --role="$role"
done

# 6. Store secrets
echo -n "$TRADING212_API_KEY" | gcloud secrets create trading212-api-key --data-file=-
echo -n "$FINNHUB_API_KEY"    | gcloud secrets create finnhub-api-key    --data-file=-
echo -n "$DATABASE_URL"       | gcloud secrets create database-url       --data-file=-

# 7. Create the Cloud Run Job (initial deploy — Cloud Build updates after push)
gcloud run jobs create stockbot-tick \
  --image=us-central1-docker.pkg.dev/$(gcloud config get-value project)/stockbot/stockbot-tick:bootstrap \
  --region=us-central1 --service-account=$SA \
  --set-secrets=TRADING212_API_KEY=trading212-api-key:latest,FINNHUB_API_KEY=finnhub-api-key:latest,DATABASE_URL=database-url:latest \
  --set-env-vars=GOOGLE_GENAI_USE_VERTEXAI=1,STOCKBOT_ENV=prod,BROKER_MODE=paper

# 8. Create the Cloud Scheduler job (paused; lifecycle scripts resume it)
gcloud scheduler jobs create http stockbot-tick \
  --location=us-central1 \
  --schedule="30 9-15 * * 1-5" --time-zone=America/New_York \
  --uri="https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$(gcloud config get-value project)/jobs/stockbot-tick:run" \
  --oidc-service-account-email=$SA
gcloud scheduler jobs pause stockbot-tick --location=us-central1

# 9. Connect Cloud Build trigger to the GitHub repo (one-time, via console or):
gcloud builds triggers create github \
  --repo-name=StockBot --repo-owner=YOUR_GH_USER \
  --branch-pattern="^main$" --build-config=deploy/cloudbuild.yaml
```

## Initialise the bot for paper trading

After GCP setup is complete and Cloud Build has built/pushed an image:

```bash
# Run from your laptop:
PYTHONPATH=src python -m scripts.initialise --capital 10000 \
  --broker-mode paper --scheduler-job stockbot-tick
```

This pre-flights (DB reachable, env vars set, T212 cash matches), writes the
equity-curve anchor snapshot, and resumes Cloud Scheduler.

## Reset and start over

```bash
# 1. Pause + archive + truncate:
PYTHONPATH=src python -m scripts.hard_reset \
  --scheduler-job stockbot-tick --starting-capital 10000

# 2. Reset Trading 212 practice account in their UI

# 3. Re-initialise:
PYTHONPATH=src python -m scripts.initialise --capital 10000 \
  --broker-mode paper --scheduler-job stockbot-tick
```

## Paper-trading kickoff checklist

Before flipping the scheduler on for the first run:

1. `PYTHONPATH=src python -m scripts.smoke_run` — confirm clean output, no errors.
2. `PYTHONPATH=src python -m scripts.replay_backtest --window 30d` — verify sane decisions over 30 days of historical data.
3. `PYTHONPATH=src python -m scripts.plot_equity --out docs/performance/$(date +%Y-%m-%d).png` — sanity-check the plotter.
4. Confirm Cloud Logging is receiving structured events from a manual `gcloud run jobs execute stockbot-tick --region=us-central1`.
5. `PYTHONPATH=src python -m scripts.initialise --capital 10000 --scheduler-job stockbot-tick`.

## Live-trading gate

The bot is paper-only until it has beaten **both**:

- SPY buy-and-hold on **cumulative return**, AND
- SPY buy-and-hold on **Sharpe ratio**

over **>=30 consecutive days** of paper trading. The MLP baseline (originally part
of this gate per `docs/baselines.md`) is deferred to Phase 3 — for now SPY is the
only baseline.

Gate is manual / observational. Flip `--broker-mode live` only after the gate
passes; redeploy with `BROKER_MODE=live` env var.
