# 🏰 Hrad.cz Lecture Monitor (Vibecoded)

Automatically checks [hrad.cz lectures page](https://www.hrad.cz/cs/kultura-na-hrade/prednasky-6) every 6 hours and notifies you via **email** and/or **Discord** when a new lecture is announced.

Runs entirely on **GitHub Actions** – no server needed, free tier is sufficient.

---

## How it works

1. GitHub Actions runs the Python script on a schedule (every 10 min).
2. The script fetches the lectures page and parses all listed events.
3. It compares them against `lectures_state.json` stored in the repo.
4. If anything is new → sends an email and/or Discord message.
5. The updated state is committed back to the repo automatically.

---

## Setup

### 1 · Fork / create the repository

Create a new **private** GitHub repository (private keeps your secrets safe) and push these files to it:

```
.github/
  workflows/
    monitor.yml
check_lectures.py
lectures_state.json   ← create this manually (see below)
README.md
```

Create an empty initial state file:
```bash
echo '{"known_ids": []}' > lectures_state.json
git add . && git commit -m "init" && git push
```

---

### 2 · Set up Email notifications (Gmail)

The script uses **Gmail SMTP with an App Password** (not your regular password).

#### Step-by-step:

1. Log in to the Google account you want to send FROM.
2. Enable **2-Step Verification** at https://myaccount.google.com/security
3. Go to https://myaccount.google.com/apppasswords
4. Click **Create app password**, name it "Hrad Monitor", copy the 16-char password.

#### Add GitHub Secrets:

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret**

| Secret name  | Value                                      |
|--------------|--------------------------------------------|
| `EMAIL_USER` | your Gmail address, e.g. `you@gmail.com`   |
| `EMAIL_PASS` | the 16-char App Password from step 4       |
| `EMAIL_TO`   | recipient address (can be same as sender)  |

---

### 3 · Set up Discord notifications

You need a **Discord Webhook URL** from your own server.

#### Create a Discord server & webhook:

1. Open Discord → click **+** (Add a Server) → **Create My Own** → **For me and my friends**.
2. Give it a name, e.g. "Hrad Alerts".
3. Right-click the `#general` channel (or create a new `#lectures` channel) → **Edit Channel**.
4. Go to **Integrations → Webhooks → New Webhook**.
5. Name it "Hrad Monitor", optionally set an avatar.
6. Click **Copy Webhook URL** – it looks like:
   `https://discord.com/api/webhooks/1234567890/xxxxxxxxxxxx`

#### Add GitHub Secret:

| Secret name            | Value                        |
|------------------------|------------------------------|
| `DISCORD_WEBHOOK_URL`  | the webhook URL you copied   |

#### Capacity alerts (`#kapacita`)

The monitor also watches free spots for the current **Věda na hradě** lecture and
posts to a separate channel when spots **open up**, i.e. the count goes from 0 (or
unknown) to above 0. That includes a newly announced lecture that already has free
spots. Decreases, or further increases while spots are open, don't post. The alert
pings the capacity role (`CAPACITY_ROLE_ID` in `capacity.py`).

Discord webhooks belong to a single channel, so create a second webhook:

1. Right-click the `#kapacita` channel → **Edit Channel → Integrations → Webhooks → New Webhook**.
2. Name it e.g. "Hrad Kapacita" and click **Copy Webhook URL**.
3. Add it as a GitHub secret:

| Secret name                     | Value                               |
|---------------------------------|-------------------------------------|
| `DISCORD_CAPACITY_WEBHOOK_URL`  | the `#kapacita` webhook URL         |

If the secret isn't set, the capacity check still runs and saves its state but skips the alert.

---

### 4 · Adjust the schedule (optional)

Edit `.github/workflows/monitor.yml`, find this line:

```yaml
- cron: "0 6,12,18,0 * * *"   # runs at 06:00, 12:00, 18:00, 00:00 UTC
```

Change it to whatever interval you prefer. Examples:

| Cron expression      | Meaning                  |
|----------------------|--------------------------|
| `0 */4 * * *`        | Every 4 hours            |
| `0 8,20 * * *`       | Twice a day (08:00/20:00)|
| `0 9 * * 1-5`        | Weekdays at 09:00        |

> **Note:** GitHub Actions scheduler has ~5–15 min jitter on free plans.

---

### 5 · Test it manually

After pushing all files and secrets, go to:

**GitHub repo → Actions → Hrad.cz Lecture Monitor → Run workflow → Run workflow**

Check the logs to confirm it runs without errors. A line starting with 🎟️ shows
the capacity check result, e.g. `🎟️  Věda na Hradě: … : 0 free spot(s) (previously 0)`.

#### Test the capacity finder locally

`capacity.py` has a CLI for running each step of the lookup on its own. It only
prints results; it doesn't send to Discord or touch the state file:

```bash
python capacity.py find-lecture                   # listing page → program page URL
python capacity.py find-booking-url <program_url> # program page → booking form URL
python capacity.py capacity <booking_url>         # booking form → free spots
python capacity.py check                          # all of the above in one go
```

---

## Files

| File                            | Purpose                                      |
|---------------------------------|----------------------------------------------|
| `check_lectures.py`             | Main scraper + notification script           |
| `.github/workflows/monitor.yml` | GitHub Actions workflow                      |
| `capacity.py`                   | Věda na hradě capacity lookup + alert + log  |
| `bot.py`                        | Always-on Discord bot, `/kapacita` graph     |
| `capacity_log.csv`              | Every capacity reading (local, git-ignored)  |
| `lectures_state.json`           | Tracks known lectures and last capacity (auto-updated by CI) |

---



## setup locally:

### 1. Create the Startup Script

Create a file named `start.sh` in the root directory of the project:

```bash
touch start.sh
chmod +x start.sh
```
```bash
#!/bin/bash
export DISCORD_WEBHOOK_URL="your_production_webhook_url_here"
export DISCORD_DEBUG_WEBHOOK_URL="your_debug_webhook_url_here"
export DISCORD_CAPACITY_WEBHOOK_URL="your_kapacita_webhook_url_here"
export RUNNER_NAME="your_runner_name_here"

~/jarvis/venv/bin/python3 main.py
```

## Capacity graph bot (`/kapacita`)

Each `main.py` run appends the current free-spot count to `capacity_log.csv`
next to the script. `bot.py` is a bot that stays running and answers `/kapacita`
with a PNG graph of free spots over time for the **latest** lecture. When a new
lecture appears (new `sourceId`), its history starts fresh and the graph shows
only that one. The old rows stay in the CSV.

### 1. Create the bot

1. Go to https://discord.com/developers/applications → **New Application**, e.g. "Hrad Kapacita".
2. **Bot** → **Reset Token** → copy the token.
3. **OAuth2 → URL Generator**: scopes `bot` + `applications.commands`, permissions
   **Send Messages** + **Attach Files**. Open the generated URL and add the bot to your server.
4. In Discord, enable **Developer Mode** (Settings → Advanced), right-click the server icon and
   pick **Copy Server ID** for `DISCORD_GUILD_ID`. The `#kapacita` channel ID is hardcoded
   in `bot.py` (`KAPACITA_CHANNEL_ID`).

### 2. Install and run

```bash
~/jarvis/venv/bin/pip install -r requirements.txt
```

Create `bot.env` (keep it out of git):

```bash
DISCORD_BOT_TOKEN=your_bot_token
DISCORD_GUILD_ID=your_server_id
```

Keep it running with systemd, e.g. `/etc/systemd/system/castlewatcher-bot.service`:

```ini
[Unit]
Description=CastleWatcher /kapacita bot
After=network-online.target
Wants=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/CastleWatcher
EnvironmentFile=/home/pi/CastleWatcher/bot.env
ExecStart=/home/pi/jarvis/venv/bin/python3 bot.py
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

Test the graph without Discord: `python bot.py --render out.png`.

## Troubleshooting

| Symptom                        | Fix                                                                  |
|--------------------------------|----------------------------------------------------------------------|
| Email not arriving             | Check spam; verify App Password was generated correctly              |
| Discord message not sent       | Make sure the webhook URL secret is set with no trailing spaces      |
| No capacity alert              | Run `python capacity.py check` locally; `free_spots: None` means hrad.cz changed the booking page |
| Script finds 0 lectures        | hrad.cz may have changed their HTML; open an issue or inspect manually |
| Actions not running on schedule | GitHub pauses schedules on inactive repos – trigger manually once   |
