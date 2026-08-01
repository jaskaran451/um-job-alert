# University of Manitoba Job Alert

This monitor checks the public University of Manitoba recruitment portal every
30 minutes and sends an alert when a matching new posting appears. It does not
sign in, apply for jobs, or store a UM password.

By default it watches:

- **Sessional and student academic:** `https://viprecprod.ad.umanitoba.ca/B`
- **Support staff:** `https://viprecprod.ad.umanitoba.ca/S`

The default keywords focus on roles relevant to a Computer Engineering student:
Teaching Assistant, TA/Demo, Grader/Marker, Lab Demonstrator, Tutor, COMP, ECE,
Computer Science, and Engineering.

## Recommended setup: GitHub Actions

GitHub runs the monitor even when your computer is turned off.

### 1. Create the repository

1. Extract this project.
2. Create a new **private** GitHub repository.
3. Upload every extracted file, including the `.github` folder.
4. In the repository, open **Settings → Actions → General**.
5. Under **Workflow permissions**, select **Read and write permissions** and
   save. The monitor uses this permission only to update
   `data/seen_jobs.json`.

### 2. Choose an alert method

You can configure Telegram, email, or both.

#### Option A — Telegram (recommended for fast phone alerts)

1. In Telegram, message `@BotFather`.
2. Send `/newbot`, follow the prompts, and copy the bot token.
3. Send any message to your new bot.
4. In a browser, open:
   `https://api.telegram.org/botYOUR_TOKEN/getUpdates`
5. Find `"chat":{"id":...}` and copy that number.
6. In the GitHub repository, open
   **Settings → Secrets and variables → Actions → Secrets**.
7. Add:

   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`

Do not put the bot token directly in a code file.

#### Option B — Gmail

Gmail requires two-step verification and an app password.

Add these GitHub Actions secrets:

| Secret | Value |
| --- | --- |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `465` |
| `SMTP_USERNAME` | The Gmail address sending the alert |
| `SMTP_PASSWORD` | A 16-character Google app password |
| `ALERT_EMAIL_FROM` | Usually the same Gmail address |
| `ALERT_EMAIL_TO` | The address that should receive alerts |

Do not use your normal Google password.

### 3. Adjust job filters (optional)

Open **Settings → Secrets and variables → Actions → Variables**.

To replace the default filters, add `INCLUDE_KEYWORDS` with comma-separated
terms. Example:

```text
COMP,ECE,teaching assistant,TA/Demo,grader/marker,lab demonstrator
```

Optional variables:

| Variable | Purpose |
| --- | --- |
| `INCLUDE_KEYWORDS` | Alert when any listed term appears |
| `EXCLUDE_KEYWORDS` | Suppress a posting when any listed term appears |
| `ALERT_ALL` | Set to `true` to alert for every new posting |

Short course codes such as `COMP` and `ECE` are matched as complete words.

### 4. Activate and test it

1. Open the repository's **Actions** tab.
2. Select **UM Job Alert**.
3. Click **Run workflow**.

The first normal run creates a baseline and deliberately sends no old-job
alerts. Future runs alert only for new requisition numbers.

To test the notification immediately, temporarily change this line in the
workflow:

```yaml
run: python um_job_alert.py
```

to:

```yaml
run: python um_job_alert.py --test-notification
```

Run the workflow once, confirm the alert, and then restore the original line.

## Run locally

Python 3.10 or newer and Google Chrome are required.

### macOS/Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
python um_job_alert.py --test-notification
python um_job_alert.py
```

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:TELEGRAM_BOT_TOKEN="..."
$env:TELEGRAM_CHAT_ID="..."
python um_job_alert.py --test-notification
python um_job_alert.py
```

If Chrome is unavailable, install Playwright's Chromium browser:

```bash
python -m playwright install chromium
```

## Useful commands

```bash
# View matching postings without alerting or changing history
python um_job_alert.py --dry-run

# Alert for matching jobs even on a brand-new first run
python um_job_alert.py --alert-existing

# Run the test suite
python -m unittest discover -s tests -v
```

## How detection works

The portal is JavaScript-driven, so the monitor opens its public listings in
headless Chrome. It reads each visible posting's requisition number, title,
category, job type, location, and posting date. Requisition numbers already
recorded in `data/seen_jobs.json` are ignored.

Only the newest results page is needed because the portal sorts by posting
date. Checking every 30 minutes makes it extremely unlikely for more than one
full page of jobs to appear between checks.

If the site stops returning recognizable rows, the run fails instead of
overwriting history. The next run will try again.

## Privacy and limits

- No UM account or password is needed.
- Secrets remain in GitHub's encrypted Actions secrets.
- The script checks two pages twice per hour, which is a modest request rate.
- GitHub scheduled workflows can occasionally start late.
