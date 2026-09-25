#!/usr/bin/env python3
"""
Utilities for discovering the current "Věda na hradě" lecture's booking page
and reading its free-capacity count from hrad.cz.

hrad.cz doesn't expose a stable URL for the booking form: each lecture gets a
new program-page slug and a new numeric `sourceId`, so the chain is
re-discovered every time starting from the one static URL:

    listing page (static)
      -> program page for "Věda na hradě" (title/url changes every month)
        -> booking form page (has the free-spot count; sourceId changes every month)

Run this file directly to test each step on its own, e.g.:

    python capacity.py find-lecture
    python capacity.py find-booking-url <program_url>
    python capacity.py capacity <booking_url>
    python capacity.py check            # runs the full chain end-to-end

main.py calls update_capacity() on every run; it posts to the
DISCORD_CAPACITY_WEBHOOK_URL channel when free spots open up, and appends
every reading to capacity_log.csv (graphed by bot.py).
"""

import argparse
import csv
import html
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from main import fetch_lectures

# Role pinged for "Věda na hradě" (same one main.py pings for new lectures).
VEDA_ROLE_PING = "<@&1511813682516983959>"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "cs,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

LECTURE_TITLE_MATCH = "věda na hradě"

# Next to this file so it doesn't depend on the cwd cron / systemd starts us in.
CAPACITY_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capacity_log.csv")
CAPACITY_LOG_FIELDS = ["timestamp", "source_id", "title", "free"]

# Matches a quoted booking-form URL anywhere in the page source (plain <a href>,
# a data-* attribute, or an inline onclick) regardless of query-param order.
BOOKING_URL_RE = re.compile(
    r"""["'](?P<url>[^"']*ramjet/booking/bookingForm\?[^"']*sourceId=\d+[^"']*)["']""",
    re.IGNORECASE,
)

EXHAUSTED_RE = re.compile(r"kapacita\s+vy[čc]erpána", re.IGNORECASE)
FREE_SPOTS_RE = re.compile(
    r"počet\s+voln[ýy]ch\s+m[íi]st\D{0,40}?(\d+)", re.IGNORECASE | re.DOTALL
)


@dataclass
class Lecture:
    title: str
    date: str
    program_url: str


def find_veda_na_hrade_lecture(lectures: list[dict] | None = None) -> Lecture | None:
    """Find the (first / soonest) 'Věda na hradě' lecture on the static listing page."""
    if lectures is None:
        lectures = fetch_lectures()
    for lec in lectures:
        if LECTURE_TITLE_MATCH in lec["title"].lower():
            return Lecture(title=lec["title"], date=lec.get("date", ""), program_url=lec["url"])
    return None


def find_booking_url(program_url: str) -> str | None:
    """Fetch a lecture's program page and find its booking-form URL."""
    resp = requests.get(program_url, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    m = BOOKING_URL_RE.search(html.unescape(resp.text))
    if not m:
        return None

    url = m.group("url")
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = "https://www.hrad.cz" + url
    return url


def get_free_capacity(booking_url: str) -> int | None:
    """
    Fetch a booking-form page and return the number of free spots.
    Returns 0 if capacity is exhausted, None if the free-spot text couldn't be
    found or parsed (page structure changed, request blocked, etc.).
    """
    resp = requests.get(booking_url, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text(" ", strip=True)

    if EXHAUSTED_RE.search(text):
        return 0

    m = FREE_SPOTS_RE.search(text)
    if m:
        return int(m.group(1))

    return None


def source_id_from_booking_url(booking_url: str) -> str | None:
    m = re.search(r"sourceId=(\d+)", booking_url)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Capacity log
# ---------------------------------------------------------------------------

def log_capacity(source_id: str | None, title: str, free: int):
    """Append one reading (UTC timestamp) to CAPACITY_LOG."""
    is_new = not os.path.exists(CAPACITY_LOG) or os.path.getsize(CAPACITY_LOG) == 0
    with open(CAPACITY_LOG, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CAPACITY_LOG_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_id": source_id or "",
            "title": title,
            "free": free,
        })


def load_latest_lecture_log() -> tuple[str, list[tuple[datetime, int]]] | None:
    """
    Return (title, [(utc_datetime, free), ...]) for the most recently logged
    lecture, i.e. the rows sharing the last row's source_id. A new lecture has
    a new sourceId, so its history starts fresh. None if nothing is logged.
    """
    if not os.path.exists(CAPACITY_LOG):
        return None
    with open(CAPACITY_LOG, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return None

    latest = rows[-1]["source_id"]
    points = [
        (datetime.fromisoformat(r["timestamp"]), int(r["free"]))
        for r in rows
        if r["source_id"] == latest
    ]
    return rows[-1]["title"], points


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

def send_capacity_discord(lec: Lecture, booking_url: str, free: int):
    webhook_url = os.environ.get("DISCORD_CAPACITY_WEBHOOK_URL")
    if not webhook_url:
        print("⚠️  DISCORD_CAPACITY_WEBHOOK_URL not set – skipping capacity alert.")
        return

    date_part = f" · {lec.date}" if lec.date else ""
    message = (
        f"## 🎟️ Free spots: {free}\n"
        f"**{lec.title}**{date_part}\n"
        f"🔗 {booking_url} {VEDA_ROLE_PING}"
    )
    resp = requests.post(webhook_url, json={"content": message}, timeout=15)
    if resp.status_code in (200, 204):
        print("✅ Capacity alert sent.")
    else:
        print(f"❌ Capacity webhook failed: {resp.status_code} {resp.text}")


def update_capacity(state: dict, lectures: list[dict]) -> str:
    """
    Check the current Věda na hradě lecture's capacity and alert when spots
    open up (0 or unknown -> above 0). Stores the last seen count in
    state["capacity"]. Failures are logged and leave the state untouched, so
    a broken scrape can't trigger a false "spots opened" alert later.
    Returns a one-line summary for the debug log.
    """
    try:
        lec = find_veda_na_hrade_lecture(lectures)
        if not lec or not lec.program_url:
            print("ℹ️  No Věda na hradě lecture with a program page – skipping capacity.")
            return "no Věda na hradě lecture found"
        booking_url = find_booking_url(lec.program_url)
        if not booking_url:
            print(f"⚠️  No booking form found on {lec.program_url}")
            return f"{lec.title}: no booking form found"
        free = get_free_capacity(booking_url)
    except Exception as exc:
        print(f"❌ Capacity check failed: {exc}")
        return f"check failed: {exc}"

    if free is None:
        print(f"⚠️  Couldn't read free spots from {booking_url}")
        return f"{lec.title}: couldn't read free spots"

    source_id = source_id_from_booking_url(booking_url)
    prev = state.get("capacity") or {}
    # A different sourceId means a new lecture, so the old count doesn't apply.
    prev_free = prev.get("free") if prev.get("source_id") == source_id else None
    print(f"🎟️  {lec.title}: {free} free spot(s) (previously {prev_free})")

    alerted = free > 0 and not prev_free
    if alerted:
        send_capacity_discord(lec, booking_url, free)

    try:
        log_capacity(source_id, lec.title, free)
    except OSError as exc:
        print(f"❌ Couldn't write {CAPACITY_LOG}: {exc}")

    state["capacity"] = {"source_id": source_id, "title": lec.title, "free": free}
    return f"{lec.title}: {free} free" + (" (alert sent)" if alerted else "")


# ---------------------------------------------------------------------------
# CLI for manual testing of each step
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("find-lecture", help="Find the current Věda na hradě lecture on the listing page.")

    p_booking = sub.add_parser("find-booking-url", help="Find the booking-form URL for a program page.")
    p_booking.add_argument("program_url")

    p_capacity = sub.add_parser("capacity", help="Read free capacity from a booking-form URL.")
    p_capacity.add_argument("booking_url")

    sub.add_parser("check", help="Run the full chain: listing -> program -> booking -> capacity.")

    args = parser.parse_args()

    if args.command == "find-lecture":
        lec = find_veda_na_hrade_lecture()
        if not lec:
            print("No 'Věda na hradě' lecture found on the listing page.")
            sys.exit(1)
        print(f"title:       {lec.title}")
        print(f"date:        {lec.date}")
        print(f"program_url: {lec.program_url}")

    elif args.command == "find-booking-url":
        url = find_booking_url(args.program_url)
        print(f"booking_url: {url}")
        if not url:
            sys.exit(1)

    elif args.command == "capacity":
        free = get_free_capacity(args.booking_url)
        print(f"free_spots: {free}")

    elif args.command == "check":
        lec = find_veda_na_hrade_lecture()
        if not lec:
            print("No 'Věda na hradě' lecture found on the listing page.")
            sys.exit(1)
        print(f"title:       {lec.title}")
        print(f"date:        {lec.date}")
        print(f"program_url: {lec.program_url}")

        booking_url = find_booking_url(lec.program_url)
        print(f"booking_url: {booking_url}")
        if not booking_url:
            sys.exit(1)

        print(f"source_id:   {source_id_from_booking_url(booking_url)}")
        print(f"free_spots:  {get_free_capacity(booking_url)}")


if __name__ == "__main__":
    main()
