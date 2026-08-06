# UM Job Alerts

UM Job Alerts is an independent notification service that monitors public
University of Manitoba job postings and sends personalized alerts through
Telegram.

Users select role categories and optional keywords, then connect a private
Telegram chat. No email address, University account, or University password is
required.

> This project is not affiliated with, endorsed by, or operated by the
> University of Manitoba.

## Features

- Checks the public `/default` University of Manitoba recruitment portal
- Runs every 30 minutes with a short-lived Railway cron service
- Stores permanent monitoring state in PostgreSQL
- Detects new jobs by requisition number
- Suppresses resurfaced old postings
- Matches roles and custom keywords
- Sends personalized Telegram alerts
- Uses short-lived private Telegram connection links
- Supports `/status` and `/stop`
- Prevents duplicate and partially repeated alert batches
- Shows the latest detected postings from PostgreSQL
- Includes health checks and automated tests

## Architecture

```text
University of Manitoba /default portal
                |
                v
      Railway cron every 30 minutes
                |
                v
        PostgreSQL job state
           /             \
          v               v
 Latest-jobs website   Matching engine
                            |
                            v
                 Personalized Telegram alerts
```

The Railway cron service runs `railway_cron.py`, saves newly observed jobs before
attempting delivery, and exits after each execution. Pending jobs remain in
PostgreSQL when Telegram delivery is incomplete, allowing the next run to retry
without repeating already completed per-user batches.

GitHub Actions is retained only as a manually triggered emergency fallback.

## Project structure

```text
um-job-alert/
├── .github/workflows/
├── data/seen_jobs.json
├── static/
├── templates/
├── tests/
├── app.py
├── configure_telegram_webhook.py
├── delivery_service.py
├── Dockerfile.cron
├── models.py
├── railway-cron.json
├── railway.json
├── railway_cron.py
├── telegram_service.py
├── um_job_alert.py
└── verify_database.py
```

`data/seen_jobs.json` is retained as a migration baseline and manual-fallback
state. PostgreSQL is the production source of truth after the Railway cron
service runs for the first time.

## Telegram user flow

1. Choose role types and optional keywords on the website.
2. Accept Telegram notifications.
3. Click **Create Telegram alert**.
4. Click **Connect Telegram**.
5. Press **Start** in the bot.
6. Receive only new jobs matching the saved preferences.

Bot commands:

```text
/status  Check whether alerts are active
/stop    Stop alerts and deactivate the subscription
```

## Privacy and security

- No email address is collected.
- No University credentials are requested.
- Only the Telegram chat ID needed for delivery is stored.
- Bot tokens and webhook secrets remain in Railway variables.
- Connection tokens are random, short-lived, stored only as hashes, and removed after use.
- `/stop` deactivates the subscription.
