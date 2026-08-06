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

- Checks five public University of Manitoba career boards
- Runs every 30 minutes with GitHub Actions
- Stores the latest 40 detected postings
- Detects new jobs by requisition number
- Matches roles and custom keywords
- Sends personalized Telegram alerts
- Uses short-lived private Telegram connection links
- Supports `/status` and `/stop`
- Prevents duplicate alerts
- Uses PostgreSQL in production
- Includes health checks and automated tests

## Architecture

```text
University of Manitoba job boards
                |
                v
       GitHub Actions monitor
                |
                v
      Railway dispatch endpoint
                |
                v
       PostgreSQL preferences
                |
                v
      Personalized Telegram alerts
```

The monitor runs from `.github/workflows/um-job-alert.yml`. It updates
`data/seen_jobs.json` and sends only newly detected jobs to Railway. Railway
matches each job against active subscriber preferences and sends matching links
to connected Telegram chats.

## Job boards monitored

| Code | Category |
|---|---|
| `A` | Academic and research |
| `B` | Sessional and student academic |
| `P` | Professional and management |
| `S` | Support staff |
| `T` | Trades and services |

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
├── dispatch_subscribers.py
├── models.py
├── railway.json
├── telegram_service.py
├── um_job_alert.py
└── verify_database.py
```

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
- Telegram chat IDs remain in PostgreSQL.
- Bot tokens and webhook secrets remain in Railway variables.
- Connection tokens are random, short-lived, stored only as hashes, and removed after use.
- `/stop` deactivates the subscription.
