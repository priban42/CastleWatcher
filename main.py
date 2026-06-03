#!/usr/bin/env python3
"""
Hrad.cz lecture monitor
Scrapes the lectures page and sends notifications when new lectures are found.
Notifications: email, Discord, Google Calendar.
"""

import base64
import hashlib
import json
import os
import re
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from bs4 import BeautifulSoup

PAGE_URL = "https://www.hrad.cz/cs/kultura-na-hrade/prednasky-6"
STATE_FILE = "lectures_state.json"

# Prague is UTC+1 (CET) / UTC+2 (CEST). We'll use Europe/Prague for calendar events.
TIMEZONE = "Europe/Prague"

# Assume lectures start at 18:00 local time if no time is found on the page
DEFAULT_HOUR = 18
DEFAULT_DURATION_HOURS = 2


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

def fetch_lectures() -> list[dict]:
    """Fetch and parse lectures from hrad.cz."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "cs,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    resp = requests.get(PAGE_URL, headers=headers, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    lectures = []

    candidates = (
        soup.select("article")
        or soup.select(".list-events li")
        or soup.select(".event-item")
        or soup.select(".article-item")
    )

    for item in candidates:
        title_tag = item.find(["h2", "h3", "h4", "a"])
        title = title_tag.get_text(strip=True) if title_tag else ""

        date_tag = item.find(class_=lambda c: c and ("date" in c or "datum" in c))
        date_str = date_tag.get_text(strip=True) if date_tag else ""

        link_tag = item.find("a", href=True)
        link = link_tag["href"] if link_tag else ""
        if link and not link.startswith("http"):
            link = "https://www.hrad.cz" + link

        if title:
            lectures.append({"title": title, "date": date_str, "url": link})

    if not lectures:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/prednasky" in href or (
                "/kultura" in href and href != "/cs/kultura-na-hrade/prednasky-6"
            ):
                title = a.get_text(strip=True)
                if title:
                    full = href if href.startswith("http") else "https://www.hrad.cz" + href
                    lectures.append({"title": title, "date": "", "url": full})

    return lectures


def lecture_id(lecture: dict) -> str:
    raw = f"{lecture['title']}|{lecture['url']}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Date parsing (Czech format)
# ---------------------------------------------------------------------------

CZECH_MONTHS = {
    "ledna": 1, "února": 2, "března": 3, "dubna": 4,
    "května": 5, "června": 6, "července": 7, "srpna": 8,
    "září": 9, "října": 10, "listopadu": 11, "prosince": 12,
    # short / alternative forms
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "maj": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12,
}


def parse_czech_date(date_str: str) -> tuple[datetime | None, bool]:
    """
    Try to extract a datetime from a Czech date string.
    Returns (datetime_obj, has_time).
    Returns (None, False) if parsing fails.

    Handles patterns like:
      "15. března 2025"
      "15. března 2025, 18:00"
      "15. 3. 2025 18:00"
      "2025-03-15"
    """
    if not date_str:
        return None, False

    text = date_str.lower().strip()

    # Try ISO format first
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?", text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if m.group(4):
            return datetime(y, mo, d, int(m.group(4)), int(m.group(5))), True
        return datetime(y, mo, d, DEFAULT_HOUR, 0), False

    # Czech word month: "15. března 2025" or "15. března 2025, 18:00"
    for word, num in CZECH_MONTHS.items():
        pattern = rf"(\d{{1,2}})\.\s*{re.escape(word)}\s*(\d{{4}})"
        m = re.search(pattern, text)
        if m:
            day, year = int(m.group(1)), int(m.group(2))
            # Look for time
            t = re.search(r"(\d{1,2}):(\d{2})", text)
            if t:
                return datetime(year, num, day, int(t.group(1)), int(t.group(2))), True
            return datetime(year, num, day, DEFAULT_HOUR, 0), False

    # Numeric: "15. 3. 2025" or "15.3.2025"
    m = re.search(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", text)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        t = re.search(r"(\d{1,2}):(\d{2})", text)
        if t:
            return datetime(year, month, day, int(t.group(1)), int(t.group(2))), True
        return datetime(year, month, day, DEFAULT_HOUR, 0), False

    return None, False


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"known_ids": []}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Google Calendar  (Service Account via raw REST – no extra SDK needed)
# ---------------------------------------------------------------------------

def _make_jwt(service_account: dict) -> str:
    """Build a signed JWT for the Google OAuth2 service-account flow."""
    import hmac, hashlib, time

    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "RS256", "typ": "JWT"}).encode()
    ).rstrip(b"=")

    now = int(time.time())
    claims = {
        "iss": service_account["client_email"],
        "scope": "https://www.googleapis.com/auth/calendar",
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now,
        "exp": now + 3600,
    }
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
    signing_input = header + b"." + payload

    # Import RSA signing (available in stdlib via cryptography or via jwt lib)
    # We use the 'cryptography' package which is widely available.
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        private_key = serialization.load_pem_private_key(
            service_account["private_key"].encode(), password=None
        )
        signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    except ImportError:
        raise RuntimeError(
            "The 'cryptography' package is required for Google Calendar integration. "
            "Add it to the pip install step in your workflow."
        )

    sig_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=")
    return (signing_input + b"." + sig_b64).decode()


def _get_access_token(service_account: dict) -> str:
    """Exchange a JWT for a short-lived OAuth2 access token."""
    jwt = _make_jwt(service_account)
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def add_to_calendar(new_lectures: list[dict]):
    """Create Google Calendar events for each new lecture."""
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "primary")

    if not sa_json:
        print("⚠️  GOOGLE_SERVICE_ACCOUNT_JSON not set – skipping Calendar.")
        return

    try:
        service_account = json.loads(sa_json)
    except json.JSONDecodeError as e:
        print(f"❌ Could not parse GOOGLE_SERVICE_ACCOUNT_JSON: {e}")
        return

    try:
        token = _get_access_token(service_account)
    except Exception as e:
        print(f"❌ Failed to obtain Google access token: {e}")
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    base_url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"

    for lec in new_lectures:
        dt, has_time = parse_czech_date(lec.get("date", ""))

        if dt is None:
            # No date at all → create an all-day event today as a placeholder
            today = datetime.now().date()
            event_body = {
                "summary": f"🏰 {lec['title']}",
                "description": (
                    f"Přednáška na Pražském hradě\n\n"
                    f"Datum: {lec.get('date') or 'neuvedeno'}\n"
                    f"Odkaz: {lec.get('url', '')}\n\n"
                    f"Zdroj: {PAGE_URL}"
                ),
                "start": {"date": today.isoformat()},
                "end": {"date": today.isoformat()},
                "source": {"title": "hrad.cz", "url": lec.get("url") or PAGE_URL},
            }
        elif has_time:
            # Exact start time known
            end_dt = dt + timedelta(hours=DEFAULT_DURATION_HOURS)
            fmt = "%Y-%m-%dT%H:%M:%S"
            event_body = {
                "summary": f"🏰 {lec['title']}",
                "description": (
                    f"Přednáška na Pražském hradě\n\n"
                    f"Odkaz: {lec.get('url', '')}\n\n"
                    f"Zdroj: {PAGE_URL}"
                ),
                "start": {"dateTime": dt.strftime(fmt), "timeZone": TIMEZONE},
                "end": {"dateTime": end_dt.strftime(fmt), "timeZone": TIMEZONE},
                "source": {"title": "hrad.cz", "url": lec.get("url") or PAGE_URL},
                "reminders": {
                    "useDefault": False,
                    "overrides": [
                        {"method": "popup", "minutes": 60 * 24},   # 1 day before
                        {"method": "popup", "minutes": 60},         # 1 hour before
                    ],
                },
            }
        else:
            # Date only, no time → use default hour
            end_dt = dt + timedelta(hours=DEFAULT_DURATION_HOURS)
            fmt = "%Y-%m-%dT%H:%M:%S"
            event_body = {
                "summary": f"🏰 {lec['title']}",
                "description": (
                    f"Přednáška na Pražském hradě\n\n"
                    f"Čas zahájení odhadnut na {DEFAULT_HOUR}:00 (neuveden na webu)\n"
                    f"Odkaz: {lec.get('url', '')}\n\n"
                    f"Zdroj: {PAGE_URL}"
                ),
                "start": {"dateTime": dt.strftime(fmt), "timeZone": TIMEZONE},
                "end": {"dateTime": end_dt.strftime(fmt), "timeZone": TIMEZONE},
                "source": {"title": "hrad.cz", "url": lec.get("url") or PAGE_URL},
                "reminders": {
                    "useDefault": False,
                    "overrides": [
                        {"method": "popup", "minutes": 60 * 24},
                        {"method": "popup", "minutes": 60},
                    ],
                },
            }

        resp = requests.post(base_url, headers=headers, json=event_body, timeout=15)
        if resp.status_code in (200, 201):
            event = resp.json()
            print(f"📅 Calendar event created: {event.get('htmlLink', '(no link)')}")
        else:
            print(f"❌ Calendar event failed for '{lec['title']}': {resp.status_code} {resp.text}")


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def send_email(new_lectures: list[dict]):
    smtp_user = os.environ.get("EMAIL_USER")
    smtp_pass = os.environ.get("EMAIL_PASS")
    recipient = os.environ.get("EMAIL_TO", smtp_user)

    if not smtp_user or not smtp_pass:
        print("⚠️  EMAIL_USER / EMAIL_PASS not set – skipping email.")
        return

    subject = f"🏰 {len(new_lectures)} new lecture(s) at Prague Castle!"
    body_lines = [
        f"<h2>New lecture(s) announced at <a href='{PAGE_URL}'>hrad.cz</a></h2>",
        "<ul>",
    ]
    for lec in new_lectures:
        date_part = f" – {lec['date']}" if lec["date"] else ""
        url_part = f"<br><a href='{lec['url']}'>{lec['url']}</a>" if lec["url"] else ""
        body_lines.append(f"<li><strong>{lec['title']}</strong>{date_part}{url_part}</li>")
    body_lines += ["</ul>", f"<p><small>Checked at {datetime.utcnow().isoformat()} UTC</small></p>"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = recipient
    msg.attach(MIMEText("\n".join(body_lines), "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, recipient, msg.as_string())

    print(f"✅ Email sent to {recipient}")


def send_discord(new_lectures: list[dict]):
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("⚠️  DISCORD_WEBHOOK_URL not set – skipping Discord.")
        return

    lines = [f"## 🏰 {len(new_lectures)} new Prague Castle lecture(s)!"]
    for lec in new_lectures:
        date_part = f" · {lec['date']}" if lec["date"] else ""
        url_part = f"\n🔗 {lec['url']}" if lec["url"] else ""
        ping = ""
        if "věda na hradě" in lec['title'].lower():
            ping = "@Lososový burger enjoyer"
        lines.append(f"**{lec['title']}**{date_part}{url_part}{ping}")



    lines.append(f"\n<{PAGE_URL}>")
    resp = requests.post(webhook_url, json={"content": "\n".join(lines)}, timeout=15)
    if resp.status_code in (200, 204):
        print("✅ Discord notification sent.")
    else:
        print(f"❌ Discord webhook failed: {resp.status_code} {resp.text}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"🔍 Checking {PAGE_URL} …")

    try:
        lectures = fetch_lectures()
    except Exception as exc:
        print(f"❌ Failed to fetch page: {exc}")
        sys.exit(1)

    print(f"   Found {len(lectures)} lecture(s) on page.")

    state = load_state()
    known_ids: set = set(state.get("known_ids", []))
    new_lectures = [lec for lec in lectures if lecture_id(lec) not in known_ids]

    if not new_lectures:
        print("✅ No new lectures.")
        state["known_ids"] = [lecture_id(l) for l in lectures]
        save_state(state)
        return

    print(f"🆕 {len(new_lectures)} new lecture(s) detected:")
    for lec in new_lectures:
        print(f"   • {lec['title']} {lec['date']}")

    send_email(new_lectures)
    send_discord(new_lectures)
    add_to_calendar(new_lectures)

    all_ids = list({lecture_id(l) for l in lectures} | known_ids)
    state["known_ids"] = all_ids
    save_state(state)
    print("💾 State saved.")


if __name__ == "__main__":
    main()