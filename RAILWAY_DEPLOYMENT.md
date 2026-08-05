# Deploy the UM Job Alerts website on Railway

GitHub Actions checks the University of Manitoba job portal. Railway hosts the
public website, PostgreSQL preferences database, protected dispatch API, and
Telegram webhook. Public subscribers receive alerts only through Telegram.

## 1. Connect the web service to PostgreSQL

The PostgreSQL service and web service must be in the same Railway project.

1. Open the **web application service**, not the Postgres service.
2. Open **Variables**.
3. Choose **Add Reference Variable**.
4. Select the PostgreSQL service and its `DATABASE_URL`.
5. Deploy the staged changes.

When the database service is named `Postgres`, the web service should show:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

The repository runs this before each deployment:

```text
python verify_database.py && python configure_telegram_webhook.py
```

Deployment stops if PostgreSQL is unavailable or Telegram cannot be configured.

## 2. Generate the public website domain

Open **Settings → Networking → Generate Domain**, then add:

```text
BASE_URL=https://${{RAILWAY_PUBLIC_DOMAIN}}
```

The Telegram webhook must use the public HTTPS domain.

## 3. Add web-service variables

```text
APP_SECRET_KEY=<long-random-secret>
DISPATCH_API_KEY=<different-long-random-secret>
TELEGRAM_BOT_TOKEN=<BotFather token>
TELEGRAM_BOT_USERNAME=<username without @>
TELEGRAM_WEBHOOK_SECRET=<another-long-random-secret>
TELEGRAM_CONNECT_TTL_MINUTES=30
```

Generate independent secrets with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

No SMTP or email variables are required. The public website does not collect an
email address and does not send subscriber emails.

## 4. Verify the deployment

Open:

```text
https://your-domain.up.railway.app/
https://your-domain.up.railway.app/healthz
```

The health endpoint should report PostgreSQL and Telegram availability. In the
deployment logs, confirm that database verification succeeds and the webhook is
configured for:

```text
https://your-domain.up.railway.app/api/telegram/webhook
```

## 5. Connect GitHub Actions to Railway

In GitHub, open **Settings → Secrets and variables → Actions**.

Add this Actions variable:

```text
SUBSCRIBER_API_URL=https://your-domain.up.railway.app
```

Add this Actions secret:

```text
SUBSCRIBER_API_KEY=<same value as Railway DISPATCH_API_KEY>
```

The scheduled workflow sends only newly detected jobs to Railway's protected
`/api/internal/dispatch` endpoint.

## 6. Test Telegram delivery

1. Open the live website.
2. Choose **All new postings**.
3. Accept Telegram notifications and click **Create Telegram alert**.
4. Click **Connect Telegram** and press **Start** in the bot.
5. Send `/status` and confirm alerts are active.
6. Run this protected test dispatch:

```bash
curl -X POST "https://your-domain.up.railway.app/api/internal/dispatch" \
  -H "Content-Type: application/json" \
  -H "X-Dispatch-Key: YOUR_DISPATCH_API_KEY" \
  -d '{"jobs":[{"id":"TEST-TELEGRAM-001","title":"Test posting - UM Job Alerts is connected","posting_date":"Aug/05/2026","url":"https://viprecprod.ad.umanitoba.ca/default"}]}'
```

Use a new test job ID for each repeat because successful Telegram deliveries are
recorded and intentionally not sent twice.

## Production architecture

```text
University of Manitoba public job portal
                 |
                 v
      GitHub Actions monitor
                 |
                 v
        Railway dispatch API
                 |
                 v
      PostgreSQL preferences
                 |
                 v
       Personalized Telegram
```

The website is an independent project and is not affiliated with the University
of Manitoba.
