# UM Job Alerts

UM Job Alerts is an independent notification service that monitors public University of Manitoba job postings and sends personalized alerts to subscribers by email and Telegram.

Users can select role categories, add custom keywords, and connect a private Telegram chat without creating an account or providing University credentials.

> This project is not affiliated with, endorsed by, or operated by the University of Manitoba.

---

## Features

- Checks five public University of Manitoba career boards
- Runs automatically every 30 minutes with GitHub Actions
- Stores the latest 40 detected postings
- Detects new jobs using requisition numbers
- Lets users create personalized alerts
- Supports role categories and custom keywords
- Sends alerts by email
- Supports secure per-user Telegram connections
- Prevents duplicate alerts
- Provides signed unsubscribe links
- Uses PostgreSQL in production
- Includes automated tests and deployment health checks

---

## System Architecture

The project contains two connected applications.

### 1. Job monitor

GitHub Actions runs `um_job_alert.py` every 30 minutes.

The monitor:

1. Opens the public University of Manitoba job boards.
2. Extracts visible posting information.
3. Sorts and deduplicates the postings.
4. Compares them with `data/seen_jobs.json`.
5. Saves the latest 40 postings.
6. Sends newly detected jobs to the subscriber web application.

### 2. Subscriber web application

The Flask application runs on Railway.

It:

1. Displays the signup website.
2. Stores subscriber preferences in PostgreSQL.
3. Connects individual Telegram chats.
4. Receives newly detected jobs from GitHub Actions.
5. Matches jobs against subscriber preferences.
6. Sends email and Telegram alerts.
7. Records successful deliveries to prevent duplicates.

```text
University of Manitoba job boards
                │
                ▼
       GitHub Actions schedule
                │
                ▼
         um_job_alert.py
                │
     Compare with seen_jobs.json
                │
                ▼
      dispatch_subscribers.py
                │
       Protected HTTP request
                │
                ▼
      Flask application on Railway
                │
       Match active subscribers
          │              │
          ▼              ▼
        Email         Telegram
```

---

## Job Boards Monitored

The monitor checks these public recruitment categories:

| Code | Category |
|---|---|
| `A` | Academic and research |
| `B` | Sessional and student academic |
| `P` | Professional and management |
| `S` | Support staff |
| `T` | Trades and services |

No University account or password is required.

---

## Project Structure

```text
um-job-alert/
├── .github/
│   └── workflows/
│       ├── test-web-app.yml
│       └── um-job-alert.yml
├── data/
│   └── seen_jobs.json
├── static/
│   ├── app.js
│   ├── styles.css
│   └── telegram.css
├── templates/
│   ├── index.html
│   └── unsubscribe.html
├── tests/
│   ├── test_monitor.py
│   └── test_web_app.py
├── app.py
├── configure_telegram_webhook.py
├── delivery_service.py
├── dispatch_subscribers.py
├── models.py
├── railway.json
├── requirements.txt
├── telegram_service.py
├── um_job_alert.py
└── verify_database.py
```

### Main files

| File | Purpose |
|---|---|
| `um_job_alert.py` | Scrapes job boards and updates job history |
| `dispatch_subscribers.py` | Sends newly detected jobs to the Flask application |
| `app.py` | Flask routes, APIs and application configuration |
| `models.py` | SQLAlchemy database models |
| `delivery_service.py` | Subscriber matching, email delivery and deduplication |
| `telegram_service.py` | Telegram linking, commands and message delivery |
| `configure_telegram_webhook.py` | Configures the Telegram webhook during deployment |
| `verify_database.py` | Verifies the production database before deployment |
| `data/seen_jobs.json` | Stores the latest detected job postings |

---

## Requirements

- Python 3.10 or newer
- Google Chrome or Playwright Chromium
- A GitHub repository
- A Railway project
- A Railway PostgreSQL database
- An SMTP account for email delivery
- A Telegram bot for Telegram alerts

Install the Python dependencies with:

```bash
pip install -r requirements.txt
```

If Chrome is unavailable, install Playwright Chromium:

```bash
python -m playwright install chromium
```

---

# Local Development

## 1. Clone the repository

```bash
git clone https://github.com/jaskaran451/um-job-alert.git
cd um-job-alert
```

## 2. Create a virtual environment

### macOS or Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

## 3. Install dependencies

```bash
pip install -r requirements.txt
```

## 4. Set local environment variables

At minimum, set an application secret and dispatch key.

### macOS or Linux

```bash
export APP_SECRET_KEY="replace-with-a-long-random-value"
export DISPATCH_API_KEY="replace-with-a-different-long-random-value"
export BASE_URL="http://127.0.0.1:5001"
export PORT="5001"
export FLASK_DEBUG="true"
```

### Windows PowerShell

```powershell
$env:APP_SECRET_KEY="replace-with-a-long-random-value"
$env:DISPATCH_API_KEY="replace-with-a-different-long-random-value"
$env:BASE_URL="http://127.0.0.1:5001"
$env:PORT="5001"
$env:FLASK_DEBUG="true"
```

When `DATABASE_URL` is not provided, the application uses a local SQLite database:

```text
data/subscribers.db
```

## 5. Start the Flask application

```bash
python app.py
```

Open:

```text
http://127.0.0.1:5001
```

---

# Running the Job Monitor Locally

Run the monitor normally:

```bash
python um_job_alert.py
```

The first run creates or updates the baseline in:

```text
data/seen_jobs.json
```

The monitor does not send subscriber alerts directly. Subscriber delivery is handled separately through the Flask application.

## Dry run

To inspect newly detected jobs without changing job history:

```bash
python um_job_alert.py --dry-run
```

## Custom state file

```bash
python um_job_alert.py --state-file /path/to/custom-state.json
```

## Custom job-board URLs

Set `UM_JOB_URLS` as a comma-separated environment variable:

```bash
export UM_JOB_URLS="https://viprecprod.ad.umanitoba.ca/B,https://viprecprod.ad.umanitoba.ca/S"
```

---

# Running the Test Suite

Run all tests:

```bash
python -m unittest discover -s tests -v
```

Check Python syntax:

```bash
python -m compileall -q \
  app.py \
  models.py \
  delivery_service.py \
  telegram_service.py \
  dispatch_subscribers.py \
  configure_telegram_webhook.py \
  verify_database.py \
  um_job_alert.py
```

Check JavaScript syntax:

```bash
node --check static/app.js
```

The GitHub Actions test workflow runs these checks automatically on pushes to `main` and on pull requests.

---

# Railway Deployment

The Flask website and PostgreSQL database are deployed on Railway.

## 1. Create a Railway project

Create:

1. A web service connected to this GitHub repository.
2. A PostgreSQL service in the same Railway project.

## 2. Connect PostgreSQL

In the web service, create a `DATABASE_URL` variable that references the PostgreSQL service.

The application converts Railway PostgreSQL URLs into the format required by SQLAlchemy and Psycopg.

## 3. Add Railway environment variables

### Required application variables

```text
APP_SECRET_KEY
DISPATCH_API_KEY
BASE_URL
DATABASE_URL
```

Example:

```text
APP_SECRET_KEY=a-long-random-secret
DISPATCH_API_KEY=a-different-long-random-secret
BASE_URL=https://your-service.up.railway.app
```

`APP_SECRET_KEY` is used to sign unsubscribe links.

`DISPATCH_API_KEY` protects the internal job-dispatch endpoint.

`BASE_URL` must be the public HTTPS URL of the Railway web service.

---

## Email variables

Add these variables to enable subscriber email delivery:

```text
SMTP_HOST
SMTP_PORT
SMTP_USERNAME
SMTP_PASSWORD
ALERT_EMAIL_FROM
```

Example for Gmail:

```text
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-google-app-password
ALERT_EMAIL_FROM=your-email@gmail.com
```

For Gmail, use a Google app password instead of your normal account password.

There is no `ALERT_EMAIL_TO` variable because each subscriber’s email address is stored in the database.

---

## Telegram variables

Add these variables to enable per-user Telegram alerts:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_BOT_USERNAME
TELEGRAM_WEBHOOK_SECRET
TELEGRAM_CONNECT_TTL_MINUTES
```

Example:

```text
TELEGRAM_BOT_TOKEN=123456789:your-bot-token
TELEGRAM_BOT_USERNAME=YourBotUsername
TELEGRAM_WEBHOOK_SECRET=another-long-random-secret
TELEGRAM_CONNECT_TTL_MINUTES=30
```

Do not add `@` before the bot username.

There is no `TELEGRAM_CHAT_ID` variable. Each subscriber connects their own Telegram chat through the website.

---

## Railway deployment configuration

`railway.json` runs these checks before starting the application:

```bash
python verify_database.py
python configure_telegram_webhook.py
```

The production server starts with Gunicorn:

```bash
gunicorn --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120 app:app
```

Railway checks application health through:

```text
/healthz
```

---

# Telegram Bot Setup

## 1. Create a bot

In Telegram:

1. Open `@BotFather`.
2. Send `/newbot`.
3. Follow the instructions.
4. Save the bot token.
5. Save the exact bot username.

## 2. Add Railway variables

Add:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_BOT_USERNAME
TELEGRAM_WEBHOOK_SECRET
BASE_URL
```

`BASE_URL` must use HTTPS.

## 3. Deploy the application

During deployment, `configure_telegram_webhook.py`:

1. Calls Telegram’s `getMe` API.
2. Confirms the configured username matches the bot token.
3. Sets the webhook to:

```text
https://your-domain/api/telegram/webhook
```

4. Configures these commands:

```text
/status
/stop
```

## 4. User connection flow

After saving alert preferences, the website creates a private Telegram link.

The user:

1. Selects **Connect Telegram**.
2. Opens the bot.
3. Presses **Start**.
4. Receives a confirmation message.

The connection link:

- Uses a cryptographically secure random token
- Stores only the token’s SHA-256 hash
- Expires after the configured time
- Can be used only to connect the saved subscription

## Bot commands

```text
/status
```

Shows whether the Telegram chat is connected.

```text
/stop
```

Disconnects Telegram alerts for that chat. The email subscription remains active.

---

# GitHub Actions Setup

The active scheduled workflow is:

```text
.github/workflows/um-job-alert.yml
```

It runs at minute 17 and minute 47 of every hour:

```yaml
schedule:
  - cron: "17,47 * * * *"
```

GitHub Actions cron schedules use UTC.

The workflow can also be started manually from the repository’s **Actions** tab.

## Repository workflow permission

Open:

```text
Settings
→ Actions
→ General
→ Workflow permissions
```

Select:

```text
Read and write permissions
```

The workflow needs write permission to commit updates to:

```text
data/seen_jobs.json
```

---

## GitHub Actions variable

Add this repository variable:

```text
SUBSCRIBER_API_URL
```

Its value should be the public Railway URL:

```text
https://your-service.up.railway.app
```

Do not include a trailing slash.

---

## GitHub Actions secret

Add this repository secret:

```text
SUBSCRIBER_API_KEY
```

Its value must exactly match Railway’s:

```text
DISPATCH_API_KEY
```

The relationship is:

```text
GitHub SUBSCRIBER_API_KEY
            =
Railway DISPATCH_API_KEY
```

The two names are different because one is used by the client and the other by the server.

---

## Workflow sequence

Each scheduled run performs these steps:

1. Checks out the repository.
2. Installs Python.
3. Installs dependencies.
4. Runs automated tests.
5. Copies the previous job-history file.
6. Runs the monitor.
7. Compares the old and new job-history files.
8. Sends newly detected jobs to Railway.
9. Commits the updated job-history file.

The workflow uses a concurrency group so two monitor runs cannot modify the job history at the same time.

---

# Subscription Matching

Users can choose one or more role categories:

- All new postings
- Teaching Assistant
- Grader or Marker
- Instructor or Sessional
- Technical or IT
- Research

Users can also add custom keywords such as:

```text
COMP
ECE
Engineering
Computer Science
```

A posting matches when:

```text
selected role matches
OR
custom keyword appears in the job title
```

Selecting **All new postings** matches every new posting.

The application currently matches subscriber preferences against job titles.

---

# Duplicate Alert Prevention

The system uses two different forms of history.

## Monitor history

```text
data/seen_jobs.json
```

This answers:

> Is this job new to the monitor?

## Delivery history

The `deliveries` database table answers:

> Has this job already been sent to this subscriber through this channel?

A unique database constraint covers:

```text
subscription_id
job_id
channel
```

Therefore, the same subscriber can receive a job once by email and once by Telegram, but not twice through the same channel.

---

# Database Tables

## `subscriptions`

Stores:

- Email address
- Selected role types
- Custom keywords
- Active status
- Creation and update timestamps

Submitting the same email again updates the existing subscription.

## `telegram_connections`

Stores:

- Subscription ID
- Telegram chat ID
- Telegram username
- First name
- Connection status
- Temporary connection-token hash
- Token expiration
- Connection timestamp

## `deliveries`

Stores:

- Subscription ID
- Job requisition ID
- Delivery channel
- Delivery timestamp

---

# API Endpoints

## Public endpoints

### `GET /`

Displays the signup page and recently detected jobs.

### `POST /api/subscriptions`

Creates or updates a subscription.

Example request:

```json
{
  "email": "student@example.com",
  "role_types": ["teaching_assistant"],
  "keywords": ["COMP"],
  "consent": true,
  "company": ""
}
```

### `POST /api/telegram/webhook`

Receives Telegram bot updates.

Telegram requests must include the configured webhook-secret header.

### `GET /unsubscribe/<token>`

Disables the email subscription and connected Telegram alert.

### `GET /healthz`

Checks the web application and database connection.

---

## Internal endpoint

### `POST /api/internal/dispatch`

Receives newly detected jobs from GitHub Actions.

Required header:

```text
X-Dispatch-Key
```

Example request:

```json
{
  "jobs": [
    {
      "id": "50001",
      "title": "Teaching Assistant - COMP 1010",
      "posting_date": "Aug/04/2026",
      "url": "https://viprecprod.ad.umanitoba.ca/DEFAULT.ASPX?REQ_ID=50001"
    }
  ]
}
```

This endpoint should not be called directly by users.

---

# Security

The project includes several security controls:

- Secrets are stored in Railway and GitHub environment variables.
- The internal dispatch endpoint requires an API key.
- API keys are checked with constant-time comparison.
- Subscriber input is validated on the server.
- Role values use an allowlist.
- Job URLs are restricted to the official U of M recruitment domain.
- Telegram webhooks require a secret header.
- Telegram connection tokens are random, hashed and temporary.
- Unsubscribe links are cryptographically signed.
- A hidden honeypot field helps reject automated form spam.
- Database constraints prevent duplicate deliveries.
- Email addresses are masked in application error summaries.

Never commit real credentials, tokens, passwords or production environment files.

---

# Troubleshooting

## The website loads but subscriptions fail

Check:

- `DATABASE_URL`
- Railway PostgreSQL service status
- Railway deployment logs
- `/healthz`

## GitHub detects jobs but subscribers receive nothing

Check:

- `SUBSCRIBER_API_URL` in GitHub variables
- `SUBSCRIBER_API_KEY` in GitHub secrets
- `DISPATCH_API_KEY` in Railway
- GitHub Actions logs
- Railway application logs

The two API-key values must match exactly.

## Email alerts do not arrive

Check:

- SMTP host and port
- SMTP username and password
- `ALERT_EMAIL_FROM`
- Spam or junk folders
- Gmail app-password configuration

## Telegram connection button does not appear

Check Railway variables:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_BOT_USERNAME
TELEGRAM_WEBHOOK_SECRET
```

Then redeploy the application.

## Telegram link is expired

Save the subscription again to generate a new private connection link.

## Telegram bot does not respond

Check:

- Railway `BASE_URL` uses HTTPS
- The bot username matches the bot token
- `configure_telegram_webhook.py` succeeded
- Telegram webhook requests are reaching Railway
- Railway logs for webhook errors

## The monitor cannot parse jobs

The University job-board HTML structure may have changed.

Check the selectors and parsing functions in:

```text
um_job_alert.py
```

The monitor intentionally fails instead of overwriting valid history when it cannot recognize job rows.

---

# Privacy and Limitations

- The service monitors only public job-posting pages.
- It does not sign into the University website.
- It does not collect University passwords.
- Subscriber email addresses are used only for job alerts.
- Telegram chat IDs are used only for connected job alerts.
- GitHub scheduled workflows may occasionally start late.
- Delivery depends on third-party services including GitHub, Railway, SMTP providers and Telegram.
- Job information should always be confirmed on the official University of Manitoba posting page.

---

# License and Disclaimer

This is an independent educational and job-alert project.

It is not affiliated with, sponsored by, endorsed by or operated by the University of Manitoba.

All job-posting information belongs to its respective owner. Users should consult the official University of Manitoba recruitment website for complete and authoritative posting details.
