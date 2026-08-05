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

## Local development

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Set local variables:

```bash
export PORT="5001"
export FLASK_DEBUG="true"
export APP_SECRET_KEY="replace-with-a-long-random-value"
export DISPATCH_API_KEY="replace-with-a-different-long-random-value"
export BASE_URL="http://127.0.0.1:5001"
export TELEGRAM_BOT_TOKEN="your-bot-token"
export TELEGRAM_BOT_USERNAME="YourBotUsername"
export TELEGRAM_WEBHOOK_SECRET="replace-with-another-long-random-value"
```

Without `DATABASE_URL`, local development uses:

```text
data/subscribers.db
```

Start the website:

```bash
python app.py
```

Open:

```text
http://127.0.0.1:5001
```

## Run the monitor

```bash
python um_job_alert.py
```

Useful commands:

```bash
python um_job_alert.py --dry-run
python um_job_alert.py --test-notification
python -m unittest discover -s tests -v
node --check static/app.js
```

## Railway deployment

Create a web service and PostgreSQL service in the same Railway project. Add a
reference variable to the web service:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

Add these web-service variables:

```text
APP_SECRET_KEY=<long random value>
DISPATCH_API_KEY=<different long random value>
BASE_URL=https://${{RAILWAY_PUBLIC_DOMAIN}}
TELEGRAM_BOT_TOKEN=<BotFather token>
TELEGRAM_BOT_USERNAME=<username without @>
TELEGRAM_WEBHOOK_SECRET=<another long random value>
TELEGRAM_CONNECT_TTL_MINUTES=30
```

No SMTP variables are required. Public subscribers receive Telegram alerts only.

Railway runs this before deployment:

```text
python verify_database.py && python configure_telegram_webhook.py
```

See `RAILWAY_DEPLOYMENT.md` and `TELEGRAM_SETUP.md` for production steps.

## GitHub Actions connection

Add this Actions variable:

```text
SUBSCRIBER_API_URL=https://your-service.up.railway.app
```

Add this Actions secret:

```text
SUBSCRIBER_API_KEY=<same value as Railway DISPATCH_API_KEY>
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

Creating a new alert from the same Telegram chat replaces the old active
preferences.

## Matching rules

Users may choose:

- All new postings
- Teaching Assistant
- Grader or Marker
- Instructor or Sessional
- Technical or IT
- Research

They may also add custom keywords such as `COMP`, `ECE`, `Engineering`, or
`Computer Science`.

A posting matches when a selected role matches or a custom keyword appears in
the title. Selecting **All new postings** matches every new posting.

## Duplicate prevention

`data/seen_jobs.json` determines whether a job is new to the monitor. The
PostgreSQL `deliveries` table records successful Telegram deliveries using a
unique combination of subscription, job ID, and channel. Retries therefore do
not send the same job twice to the same user.

## Database tables

- `subscriptions` stores anonymous internal identifiers and job preferences.
- `telegram_connections` stores private chat IDs and connection state.
- `deliveries` stores successful Telegram delivery history.

The internal identifier uses the existing database column named `email` for
backward compatibility, but it contains a generated `@alerts.invalid` value and
is never collected from or shown to the user.

## API endpoints

### Public

- `GET /` — website and recent postings
- `POST /api/subscriptions` — create anonymous preferences and return a private Telegram link
- `POST /api/telegram/webhook` — receive authenticated Telegram updates
- `GET /healthz` — verify database and Telegram configuration

Example subscription request:

```json
{
  "role_types": ["teaching_assistant"],
  "keywords": ["COMP"],
  "consent": true,
  "company": ""
}
```

### Internal

- `POST /api/internal/dispatch` — receive newly detected jobs from GitHub Actions

The internal route requires the `X-Dispatch-Key` header.

## Privacy and security

- No email address is collected.
- No University credentials are requested.
- Telegram chat IDs remain in PostgreSQL.
- Bot tokens and webhook secrets remain in Railway variables.
- Connection tokens are random, short-lived, stored only as hashes, and removed after use.
- `/stop` deactivates the subscription.
