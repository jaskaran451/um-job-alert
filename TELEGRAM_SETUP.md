# Telegram-only job alerts

The website uses one Telegram bot for every subscriber. Each person chooses
roles and keywords, then links a private Telegram chat through a short-lived
deep link. The bot token is never shown in the browser, and the website does not
collect an email address.

## 1. Use or create a bot

In Telegram, open `@BotFather` and create a bot with `/newbot`, or use the bot
already sending the repository owner's personal job alerts. Copy:

- the bot token;
- the bot username, without the leading `@`.

Using the existing bot does not break GitHub Actions. GitHub can continue using
that bot token for the owner's personal notification, while Railway receives
webhook updates and sends personalized alerts to connected users.

## 2. Add Railway variables

Add these variables to the Railway web service:

```text
TELEGRAM_BOT_TOKEN=<BotFather token>
TELEGRAM_BOT_USERNAME=<username without @>
TELEGRAM_WEBHOOK_SECRET=<long random value>
TELEGRAM_CONNECT_TTL_MINUTES=30
```

Generate the webhook secret locally:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`BASE_URL` must also be the public HTTPS Railway domain:

```text
BASE_URL=https://${{RAILWAY_PUBLIC_DOMAIN}}
```

Railway runs this pre-deploy command:

```text
python verify_database.py && python configure_telegram_webhook.py
```

A successful log includes a safe result similar to:

```json
{"bot_username":"YourBot","telegram_webhook":"configured","url":"https://your-domain.up.railway.app/api/telegram/webhook"}
```

The bot token and webhook secret are never printed.

## 3. User linking flow

1. A user chooses role types and optional keywords.
2. The website creates an anonymous subscription in PostgreSQL.
3. The website displays **Connect Telegram**.
4. The link opens `t.me/<bot>?start=<short-lived-token>`.
5. The user presses **Start**.
6. Telegram calls Railway's protected webhook.
7. Railway stores the private chat ID against the subscription.
8. Future matching jobs are delivered only through Telegram.

Connection tokens are random, stored only as SHA-256 hashes, expire after 30
minutes, and are removed after successful use.

If the same Telegram chat creates a new alert, the old active preferences are
disabled and replaced by the new selection.

## 4. Bot commands

```text
/status  Check whether alerts are active in this chat
/stop    Stop alerts and deactivate this subscription
```

The user can create a new alert by returning to the website, choosing new
preferences, and reconnecting Telegram.

## 5. PostgreSQL tables

Deployment automatically creates:

- `subscriptions` — anonymous job-matching preferences;
- `telegram_connections` — private chat ID, connection state, and temporary token;
- `deliveries` — Telegram delivery history by job and subscription.

The `deliveries` table prevents duplicate alerts when GitHub Actions or the
dispatch endpoint retries the same job.

## 6. Test production

1. Select **All new postings** on the website.
2. Click **Connect Telegram** and press **Start** in the bot.
3. Send `/status`; the bot should confirm alerts are active.
4. Run a protected test dispatch, replacing the URL and key:

```bash
curl -X POST "https://your-domain.up.railway.app/api/internal/dispatch" \
  -H "Content-Type: application/json" \
  -H "X-Dispatch-Key: YOUR_DISPATCH_API_KEY" \
  -d '{"jobs":[{"id":"TELEGRAM-TEST-1","title":"Test Teaching Assistant posting","posting_date":"Aug/05/2026","url":"https://viprecprod.ad.umanitoba.ca/default"}]}'
```

Use a new test job ID for each repeat because successful deliveries are recorded
and intentionally not sent twice.
