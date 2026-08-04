# Multi-user Telegram alerts

The website uses one Telegram bot for every subscriber. Each person links their
private chat through a short-lived deep link generated after saving alert
preferences. The bot token is never shown in the browser.

## 1. Use or create a bot

In Telegram, open `@BotFather` and create a bot with `/newbot`, or use the bot
already sending your personal job alerts. Copy:

- the bot token;
- the bot username, without the leading `@`.

Using the existing bot does not break GitHub Actions. GitHub can continue using
that bot token to send your personal alert while Railway receives webhook
updates and sends personalized alerts to other chats.

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

`BASE_URL` must also be set to the public HTTPS Railway domain, for example:

```text
BASE_URL=https://${{RAILWAY_PUBLIC_DOMAIN}}
```

Deploy the staged variables. Railway runs:

```text
python verify_database.py && python configure_telegram_webhook.py
```

The deployment log should include a safe result similar to:

```json
{"bot_username":"YourBot","telegram_webhook":"configured","url":"https://your-domain.up.railway.app/api/telegram/webhook"}
```

The log never prints the bot token or webhook secret.

## 3. User linking flow

1. A user saves their email, role types, and keywords on the website.
2. The website displays **Connect Telegram**.
3. The link opens `t.me/<bot>?start=<short-lived-token>`.
4. The user presses **Start**.
5. Telegram sends the command to Railway's protected webhook.
6. Railway stores that private chat ID against the subscription in PostgreSQL.
7. Future matching jobs are delivered to both email and Telegram.

Connection tokens are random, stored only as SHA-256 hashes, expire after 30
minutes, and are removed after successful use.

## 4. Bot commands

```text
/status  Check whether the current chat is connected
/stop    Disconnect Telegram while leaving email alerts active
```

The user can reconnect by saving their website preferences again and opening the
new connection link.

## 5. PostgreSQL tables

Deployment automatically creates:

- `subscriptions` — email and job-matching preferences;
- `telegram_connections` — chat ID, connection status, and temporary token;
- `deliveries` — per-channel job delivery history.

The `deliveries` table prevents duplicate email or Telegram alerts when GitHub
Actions or the dispatch endpoint retries a job.

## 6. Test production

1. Save your own email and select **All new postings**.
2. Click **Connect Telegram** and press **Start** in the bot.
3. Send `/status`; the bot should confirm the connection.
4. Run a protected test dispatch from a terminal, replacing the values:

```bash
curl -X POST "https://your-domain.up.railway.app/api/internal/dispatch" \
  -H "Content-Type: application/json" \
  -H "X-Dispatch-Key: YOUR_DISPATCH_API_KEY" \
  -d '{"jobs":[{"id":"TELEGRAM-TEST-1","title":"Test Teaching Assistant posting","posting_date":"Aug/04/2026","url":"https://viprecprod.ad.umanitoba.ca/default"}]}'
```

Use a new test job ID for each repeat because successful deliveries are recorded
and intentionally not sent twice.
