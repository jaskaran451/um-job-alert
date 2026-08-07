# Deploy UM Job Alerts on Railway

Railway runs two services from the same repository:

1. **Web service** — public signup page, Telegram webhook, and PostgreSQL access.
2. **Cron service** — checks the University of Manitoba portal every 30 minutes,
   saves monitoring state in PostgreSQL, sends matching Telegram alerts, and
   exits.

GitHub Actions remains available only as a manual emergency fallback. It is no
longer the production scheduler.

## 1. Existing web service

Keep the existing web service connected to PostgreSQL:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

Required web-service variables:

```text
APP_SECRET_KEY=<long-random-secret>
DISPATCH_API_KEY=<different-long-random-secret>
BASE_URL=https://${{RAILWAY_PUBLIC_DOMAIN}}
TELEGRAM_BOT_TOKEN=<BotFather token>
TELEGRAM_BOT_USERNAME=<username without @>
TELEGRAM_WEBHOOK_SECRET=<another-long-random-secret>
TELEGRAM_CONNECT_TTL_MINUTES=30
```

The web service continues to use the root `railway.json` file and starts
Gunicorn. Its pre-deploy command creates and verifies all PostgreSQL tables and
configures the Telegram webhook.

## 2. Create the Railway cron service

In the same Railway project:

1. Click **New → GitHub Repo**.
2. Select this repository again.
3. Name the new service something clear, such as `UM Job Monitor`.
4. Keep its source branch set to `main`.
5. Open **Settings → Build**.
6. Set **Railway Config File** to:

```text
/railway-cron.json
```

This is critical. Without the custom config path, Railway would use the web
service's `railway.json` and start Gunicorn instead of the scraper.

The cron config uses:

```text
Dockerfile: Dockerfile.cron
Start command: python railway_cron.py
Cron schedule: */30 * * * *
Restart policy: Never
```

Railway cron schedules are evaluated in UTC. Because this schedule runs every
30 minutes, no timezone conversion is needed.

## 3. Add cron-service variables

The cron service needs only the shared PostgreSQL database and Telegram bot
token:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
TELEGRAM_BOT_TOKEN=<same BotFather token used by the web service>
```

Optional tuning variables:

```text
MAX_ALERT_AGE_DAYS=14
PAGE_TIMEOUT_MS=120000
TELEGRAM_JOBS_PER_MESSAGE=8
TELEGRAM_MESSAGE_LIMIT=3800
LOG_LEVEL=INFO
```

The cron Docker image installs Playwright Chromium and its Linux dependencies.
No public domain, port, webhook secret, or email variables are required for this
service.

## 4. First-run migration

On its first run, the cron service imports the committed
`data/seen_jobs.json` history into PostgreSQL. This preserves:

- previously observed requisition IDs;
- the latest portal snapshot;
- any retryable pending jobs;
- the last successful scrape and dispatch timestamps.

The migration prevents current jobs from being announced again simply because
the scheduler changed. After the import, PostgreSQL becomes the monitoring
source of truth.

The website also reads its latest-job cards from PostgreSQL. It temporarily
falls back to `data/seen_jobs.json` until the first cron run completes.

## 5. Test before relying on the schedule

After deploying the cron service, open its deployment and run it once manually.
A successful log should include lines similar to:

```text
Imported 500 legacy seen requisitions into PostgreSQL.
Read 50 jobs from /default; 0 unseen, 0 fresh for alert, 0 suppressed as old.
No pending Telegram jobs to dispatch.
```

The exact imported count will vary.

Then verify the next scheduled execution appears about 30 minutes later. The
cron process must finish and show a completed deployment. If a run remains
`Active`, Railway will skip the next scheduled execution.

## 6. Failure and retry behavior

The cron service writes newly discovered jobs to PostgreSQL before sending
Telegram messages.

```text
Scrape /default
      ↓
Persist new and seen requisitions in PostgreSQL
      ↓
Dispatch matching Telegram alerts
      ↓
Clear pending jobs only after successful delivery
```

If Telegram fails or the process exits unexpectedly, pending jobs remain in
PostgreSQL for the next cron run. Successful per-user deliveries are already
recorded, so retries do not resend completed batches.

Jobs older than `MAX_ALERT_AGE_DAYS` are remembered but suppressed from alerts.
This prevents an old posting that reappears on the portal from being announced
as new.

## 7. GitHub Actions fallback

The workflow is now named:

```text
UM Job Alert - Manual Fallback
```

It has only a `workflow_dispatch` trigger. It will not run on a schedule, but it
can still be launched manually from GitHub Actions during Railway maintenance.

## Production architecture

```text
University of Manitoba /default portal
                 |
                 v
       Railway cron every 30 minutes
                 |
                 v
         Railway PostgreSQL state
            /               \
           v                 v
  Website latest jobs   Matching engine
                              |
                              v
                    Personalized Telegram
```

The website is an independent project and is not affiliated with the University
of Manitoba.
