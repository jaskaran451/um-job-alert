# Deploy the UM Job Alerts website on Railway

The GitHub Actions monitor checks the University of Manitoba job portal.
Railway hosts the signup website, PostgreSQL subscriber database, and protected
email-dispatch API.

## 1. Connect the web service to PostgreSQL

The PostgreSQL service and the web service must be in the same Railway project.

1. Open the **web application service**, not the Postgres service.
2. Open **Variables**.
3. Click **New Variable** or **Add Reference Variable**.
4. Select the PostgreSQL service.
5. Select its `DATABASE_URL` variable.
6. Save and deploy the staged changes.

When the database service is named `Postgres`, the web service should show:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

Use the reference variable instead of copying the database password or public
connection URL. Railway will then use the private connection available inside
the project.

The repository's `railway.json` runs this command before every deployment:

```text
python verify_database.py
```

The deployment will stop if `DATABASE_URL` is missing, PostgreSQL cannot be
reached, or the `subscriptions` table cannot be initialized.

A successful deployment log contains output similar to:

```json
{"database":"postgresql","status":"ok","subscriber_count":0,"subscriptions_table":true}
```

## 2. Generate the public website domain

1. Open the web service.
2. Go to **Settings → Networking**.
3. Click **Generate Domain**.
4. Copy the generated hostname.

Add this variable to the web service:

```text
BASE_URL=https://${{RAILWAY_PUBLIC_DOMAIN}}
```

This keeps unsubscribe links synchronized with the current Railway domain.

## 3. Add the remaining web-service variables

Add these variables to the Railway web service:

```text
APP_SECRET_KEY=<long-random-secret>
DISPATCH_API_KEY=<different-long-random-secret>
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-16-character-Google-app-password
ALERT_EMAIL_FROM=your-email@gmail.com
```

Generate `APP_SECRET_KEY` and `DISPATCH_API_KEY` independently:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

For Gmail, enable two-step verification and create a Google app password. Do
not use or store the normal Google account password.

After changing Railway variables, review and deploy the staged changes.

## 4. Verify PostgreSQL persistence

Open the following pages:

```text
https://your-domain.up.railway.app/
https://your-domain.up.railway.app/healthz
```

The health endpoint should return:

```json
{"status":"ok"}
```

Then perform this persistence test:

1. Submit your own email address through the website signup form.
2. Redeploy the web service without deleting PostgreSQL.
3. Check the pre-deploy log from `verify_database.py`.
4. Confirm `subscriber_count` is at least `1`.
5. Submit the same email with different preferences and redeploy again.
6. Confirm the count does not increase; the existing subscription is updated.

This proves that subscribers are stored in PostgreSQL rather than Railway's
ephemeral application filesystem.

## 5. Connect GitHub Actions to Railway

In GitHub, open:

**Repository → Settings → Secrets and variables → Actions**

Add this Actions variable:

```text
SUBSCRIBER_API_URL=https://your-domain.up.railway.app
```

Add this Actions secret:

```text
SUBSCRIBER_API_KEY=<same value as Railway DISPATCH_API_KEY>
```

The values of `SUBSCRIBER_API_KEY` and `DISPATCH_API_KEY` must match exactly.
The scheduled workflow sends only newly detected jobs to Railway's protected
`/api/internal/dispatch` endpoint.

## 6. Test email delivery directly

First, subscribe your own address and choose **All postings**. Then run this
from a terminal, replacing the URL and key:

```bash
curl -X POST "https://your-domain.up.railway.app/api/internal/dispatch" \
  -H "Content-Type: application/json" \
  -H "X-Dispatch-Key: YOUR_DISPATCH_API_KEY" \
  -d '{
    "jobs": [
      {
        "id": "TEST-001",
        "title": "Test posting - UM Job Alerts is connected",
        "posting_date": "Aug/04/2026",
        "url": "https://viprecprod.ad.umanitoba.ca/default"
      }
    ]
  }'
```

A successful response should report one matching subscriber and one delivered
email. Remove the command from shell history afterward because it contains the
dispatch key.

## 7. Confirm the scheduled workflow

Open **GitHub → Actions → UM Job Alert → Run workflow**.

A normal run with no new posting will not email subscribers, but the following
steps should succeed:

```text
Run monitor or Telegram test
Dispatch new jobs to website subscribers
Save updated job history
```

When a genuinely new posting appears, GitHub Actions sends it to Railway,
Railway reads preferences from PostgreSQL, and matching subscribers receive an
email.

## Production architecture

```text
University of Manitoba public job portal
                 |
                 v
      GitHub Actions monitor
          |             |
          v             v
 personal Telegram   Railway dispatch API
                           |
                           v
                  PostgreSQL subscribers
                           |
                           v
                    Personalized email
```

The public website is an independent alert service and is not affiliated with
the University of Manitoba.
