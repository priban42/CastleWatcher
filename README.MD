# 🏰 Hrad.cz Lecture Monitor (Vibecoded)

Watches the [hrad.cz lectures page](https://www.hrad.cz/cs/kultura-na-hrade/prednasky-6) and:

- notifies you via **Discord** (and optionally **email** / **Google Calendar**) when a new lecture is announced,
- watches free spots for the current **Věda na hradě** lecture and pings a role in `#kapacita` when spots open up,
- logs every free-spot reading, so a Discord bot can answer `/kapacita` with a graph of free spots over time.

It mainly runs on a Raspberry Pi: `main.py` runs from cron, and `bot.py` runs all the time as a systemd service.
The GitHub Actions workflow still works as an alternative runner for the checks, but not for the bot or the graph.

---

## How it works

1. `main.py` runs on a schedule (cron on the RPi, or GitHub Actions).
2. It fetches the lectures page and parses all listed events.
3. It compares them against `lectures_state.json`. Anything new → Discord / email / calendar.
4. It checks the Věda na hradě booking page (`capacity.py`):
   - it appends the free-spot count to `capacity_log.csv`,
   - and posts to `#kapacita` when spots go from 0 (or unknown) to above 0.
5. It saves the updated state to `lectures_state.json`.
6. `bot.py` reads `capacity_log.csv` and answers `/kapacita` with a PNG graph.

---

## Discord setup

### Webhooks

Discord webhooks belong to a single channel, so each channel needs its own webhook.
For each channel: right-click it → **Edit Channel → Integrations → Webhooks → New Webhook → Copy Webhook URL**.
The URL looks like `https://discord.com/api/webhooks/1234567890/xxxxxxxxxxxx`.

| Env variable                    | Channel / purpose                                  |
|---------------------------------|----------------------------------------------------|
| `DISCORD_WEBHOOK_URL`           | new-lecture announcements                          |
| `DISCORD_CAPACITY_WEBHOOK_URL`  | `#kapacita`, capacity alerts                       |
| `DISCORD_DEBUG_WEBHOOK_URL`     | debug log sent after every run                     |
| `RUNNER_NAME`                   | name shown in the debug log (e.g. `RPi`)           |

If a webhook isn't set, that notification is skipped. Everything else still runs.

### Roles and channel (hardcoded)

| What                                   | Where                                      | ID                    |
|----------------------------------------|--------------------------------------------|-----------------------|
| Věda na hradě role (new lectures)      | `main.py` → `send_discord`                 | `1511813682516983959` |
| Capacity role (spots opened)           | `capacity.py` → `CAPACITY_ROLE_ID`         | `1552623294593896469` |
| `#kapacita` channel (`/kapacita` only works here) | `bot.py` → `KAPACITA_CHANNEL_ID` | `1552626317432721408` |

To find an ID, enable **Developer Mode** (User Settings → Advanced). Then:

- **Role:** Server Settings → Roles → right-click the role → **Copy Role ID**.
- **Channel:** right-click the channel → **Copy Channel ID**.
- **Server:** right-click the server icon → **Copy Server ID**.

### Capacity alerts

A capacity alert is posted only when spots **open up**, i.e. the count goes from 0 (or unknown) to above 0.
That includes a newly announced lecture that already has free spots. Decreases, or further increases while
spots are open, don't post. A failed scrape doesn't change the state, so it can't cause a false alert later.

---

## Running on the Raspberry Pi

The paths below assume the repo is in `~/jarvis/CastleWatcher` and the venv is in `~/jarvis/venv`
(user `jarvis`). Change them if yours differ.

### 1. Install dependencies

```bash
cd ~/jarvis/CastleWatcher
~/jarvis/venv/bin/pip install -r requirements.txt
```

### 2. Periodic check (`main.py`)

Create `start.sh` in the project root. It holds your webhook URLs, so don't commit it:

```bash
#!/bin/bash
cd "$(dirname "$0")"   # lectures_state.json is read relative to the cwd
export DISCORD_WEBHOOK_URL="your_production_webhook_url_here"
export DISCORD_DEBUG_WEBHOOK_URL="your_debug_webhook_url_here"
export DISCORD_CAPACITY_WEBHOOK_URL="your_kapacita_webhook_url_here"
export RUNNER_NAME="your_runner_name_here"

~/jarvis/venv/bin/python3 main.py
```

```bash
chmod +x start.sh
crontab -e
```

Add a line to run it, e.g. every 10 minutes:

```
*/10 * * * * /home/jarvis/jarvis/CastleWatcher/start.sh >> /home/jarvis/jarvis/CastleWatcher/monitor.log 2>&1
```

Every run adds a row to `capacity_log.csv`. The more often it runs, the denser the graph.

### 3. Capacity graph bot (`bot.py`, `/kapacita`)

`bot.py` stays running and answers `/kapacita` in `#kapacita` with a PNG graph of free spots over time
for the **latest** lecture. When a new lecture appears (new `sourceId`), its history starts fresh and the
graph shows only that one. The old rows stay in the CSV.

**Create the bot:**

1. Go to https://discord.com/developers/applications → **New Application**, e.g. "Hrad Kapacita".
2. **Bot** → **Reset Token** → copy the token.
3. **OAuth2 → URL Generator**: scopes `bot` + `applications.commands`, permissions
   **Send Messages** + **Attach Files**. Open the generated URL and add the bot to your server.
4. Copy your **Server ID** (see above) for `DISCORD_GUILD_ID`. With it set, `/kapacita` appears
   right away instead of taking up to an hour.

**Create `bot.env`** in the project root (git-ignored):

```bash
DISCORD_BOT_TOKEN=your_bot_token
DISCORD_GUILD_ID=your_server_id
```

```bash
chmod 600 bot.env
```

**Run it with systemd**: `sudo nano /etc/systemd/system/castlewatcher-bot.service`

```ini
[Unit]
Description=CastleWatcher /kapacita bot
After=network-online.target
Wants=network-online.target

[Service]
User=jarvis
WorkingDirectory=/home/jarvis/jarvis/CastleWatcher
EnvironmentFile=/home/jarvis/jarvis/CastleWatcher/bot.env
ExecStart=/home/jarvis/jarvis/venv/bin/python3 bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now castlewatcher-bot
journalctl -u castlewatcher-bot -f
```

A working start logs `🔄 Synced 1 command(s).` and `✅ Logged in as …`.
After pulling code changes, run `sudo systemctl restart castlewatcher-bot`.

### 4. Test things manually

```bash
python capacity.py find-lecture                   # listing page → program page URL
python capacity.py find-booking-url <program_url> # program page → booking form URL
python capacity.py capacity <booking_url>         # booking form → free spots
python capacity.py check                          # all of the above in one go
python bot.py --render out.png                    # draw the graph from capacity_log.csv, no Discord
```

The `capacity.py` commands only print results. They don't send to Discord, touch the state file, or
write to the log.

---

## Optional: email and Google Calendar

**Email (Gmail SMTP with an App Password):**

1. Enable **2-Step Verification** at https://myaccount.google.com/security.
2. Create an app password at https://myaccount.google.com/apppasswords.
3. Set `EMAIL_USER` (Gmail address), `EMAIL_PASS` (the 16-char app password), and `EMAIL_TO`
   (recipient; defaults to the sender).

**Google Calendar:** set `GOOGLE_SERVICE_ACCOUNT_JSON` (service-account key JSON) and
`GOOGLE_CALENDAR_ID` (share the calendar with the service account's email).

---

## Optional: GitHub Actions

`.github/workflows/monitor.yml` runs `main.py` every 13 minutes and commits `lectures_state.json` back.
Set the env variables above as **Settings → Secrets and variables → Actions** secrets. To start a run
by hand, go to **Actions → Hrad.cz Lecture Monitor → Run workflow**.

This isn't needed if the RPi runs the checks. `capacity_log.csv` isn't committed, so the
graph only uses readings from the RPi.

> GitHub's scheduler has ~5–15 min jitter and pauses schedules on inactive repos.

---

## Files

| File                            | Purpose                                             |
|---------------------------------|-----------------------------------------------------|
| `main.py`                       | Lecture scraper + notifications, entry point        |
| `capacity.py`                   | Věda na hradě capacity lookup, alert, and log       |
| `bot.py`                        | Always-on Discord bot, `/kapacita` graph            |
| `requirements.txt`              | Python dependencies                                 |
| `lectures_state.json`           | Known lectures and last capacity                    |
| `capacity_log.csv`              | Every capacity reading (local, git-ignored)         |
| `bot.env` / `start.sh`          | Your secrets (local, don't commit)                  |
| `.github/workflows/monitor.yml` | Optional GitHub Actions runner                      |

---

## Troubleshooting

| Symptom                                   | Fix                                                                  |
|-------------------------------------------|----------------------------------------------------------------------|
| Bot: `Failed to load environment files`   | The `EnvironmentFile=` path in the unit is wrong or `bot.env` doesn't exist |
| Bot: `Failed to spawn 'start' task`       | Wrong `WorkingDirectory=`, `ExecStart=` path, or `User=`             |
| `/kapacita` doesn't show up               | Set `DISCORD_GUILD_ID`, restart the bot; check it was invited with `applications.commands` |
| `/kapacita` says "No capacity data logged yet" | `main.py` hasn't logged a reading yet; check cron and `python capacity.py check` |
| Discord message not sent                  | Check the webhook URL has no trailing spaces                         |
| No capacity alert                         | Run `python capacity.py check`; `free_spots: None` means hrad.cz changed the booking page |
| Script finds 0 lectures                   | hrad.cz may have changed their HTML; inspect manually                |
| Email not arriving                        | Check spam; verify the App Password                                  |
