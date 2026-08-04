# Deploy the UM Job Alerts website on Railway

The GitHub Actions monitor remains responsible for checking the University of
Manitoba job portal. Railway hosts only the public signup website, subscriber
database, and protected email-dispatch API.

## 1. Deploy the repository

1. Merge the website pull request into `main`.
2. In Railway, create a new project.
3. Choose **Deploy from GitHub repo**.
4. Authorize Railway to access the private repository if prompted.
5. Select `jaskaran451/um-job-alert`.

Railway detects the Python application and reads `railway.json`. The configured
start command binds Gunicorn to Railway's injected `PORT`, and Railway checks
`/healthz` before activating a deployment.

## 2. Add PostgreSQL

1. Open the Railway project canvas.
2. Click **New** and add **PostgreSQL**.
3. Open the web service's **Variables** tab.
4. Add a reference variable named `DATABASE_URL` that points to the PostgreSQL
   service's `DATABASE_URL`.

When the database service is named `Postgres`, the reference normally appears
as:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

Do not rely on the local SQLite fallback in production. Railway service storage
is not the subscriber database for this deployment.

## 3. Configure the web service

Add these variables to the Railway web service:

```text
APP_SECRET_KEY=<long-random-secret>
DISPATCH_API_KEY=<another-long-random-secret>
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-16-character-Google-app-password
ALERT_EMAIL_FROM=your-email@gmail.com
BASE_URL=https://your-generated-domain.up.railway.app
```

Generate independent random values for `APP_SECRET_KEY` and
`DISPATCH_API_KEY`. For example:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

For Gmail, enable two-step verification and use a Google app password. Never
store your regular Gmail password in Railway or GitHub.

## 4. Generate the public domain

1. Open the web service in Railway.
2. Go to **Settings** or **Networking**.
3. Click **Generate Domain**.
4. Copy the HTTPS domain.
5. Set `BASE_URL` to that complete URL, without a trailing slash.
6. Redeploy the staged variable changes.

## 5. Connect GitHub Actions to Railway

In the GitHub repository, open **Settings → Secrets and variables → Actions**.

Add this Actions variable:

```text
SUBSCRIBER_API_URL=https://your-generated-domain.up.railway.app
```

Add this Actions secret:

```text
SUBSCRIBER_API_KEY=<same value as Railway DISPATCH_API_KEY>
```

The values must match exactly. The GitHub Action sends only newly detected jobs
to Railway's protected `/api/internal/dispatch` endpoint.

## 6. Verify the deployment

Open these addresses in a browser:

```text
https://your-generated-domain.up.railway.app/
https://your-generated-domain.up.railway.app/healthz
```

The health endpoint should return:

```json
{"status":"ok"}
```

Submit a test subscription through the website, then manually run the
**UM Job Alert** workflow in GitHub Actions. A normal run with no new jobs will
not send an email; it confirms that the monitor and dispatch steps complete
successfully.

## Production architecture

```text
University of Manitoba public job portal
                 |
                 v
      GitHub Actions monitor
          |             |
          v             v
   Telegram alert   Railway dispatch API
                          |
                          v
                 PostgreSQL subscribers
                          |
                          v
                   Personalized email
```

The website is an independent alert service and is not affiliated with the
University of Manitoba.
