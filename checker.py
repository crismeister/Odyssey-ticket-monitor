#!/usr/bin/env python3
"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  The Odyssey Ticket Monitor
  Checks AMC & Regal Irvine Spectrum every 10 min
  Sends instant push notifications via ntfy.sh
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import requests
import json
import os
import sys
import logging
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from typing import Optional

# ─────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S UTC",
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# Config  (set NTFY_TOPIC as a GitHub Actions secret or .env)
# ─────────────────────────────────────────────────────────────────
NTFY_TOPIC  = os.environ.get("NTFY_TOPIC", "odyssey-tickets-YOUR-UNIQUE-ID")
STATE_FILE  = "state.json"
REQUEST_TIMEOUT = 15  # seconds

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

# ─────────────────────────────────────────────────────────────────
# Targets
# Each target has:
#   on_sale_phrases  – ANY of these in the HTML → tickets available
#   sold_out_phrases – if ALL on_sale_phrases are absent OR these
#                      block phrases are present → not yet on sale
# ─────────────────────────────────────────────────────────────────
TARGETS = [
    # ── AMC (general Odyssey page) ────────────────────────────────
    {
        "id":   "amc_odyssey",
        "name": "🎬 AMC Theatres – The Odyssey",
        "url":  "https://www.amctheatres.com/movies/the-odyssey-80679",
        "buy_url": "https://www.amctheatres.com/movies/the-odyssey-80679",
        "on_sale_phrases":  ["get tickets", "buy tickets", "showtimes", "select showtime"],
        "block_phrases":    ["coming soon", "notify me when"],
    },
    # ── AMC API fallback (lightweight JSON endpoint) ──────────────
    {
        "id":   "amc_api",
        "name": "🎬 AMC API – The Odyssey",
        "url":  "https://api.amctheatres.com/v2/movies/80679",
        "buy_url": "https://www.amctheatres.com/movies/the-odyssey-80679",
        "on_sale_phrases":  ["hasshowtime", "showtimecount", "ticketsavailable"],
        "block_phrases":    [],
        "is_api": True,
    },
    # ── Regal – IMAX 70mm movie page ─────────────────────────────
    {
        "id":   "regal_imax_70mm",
        "name": "🎭 Regal Irvine Spectrum – IMAX 70mm",
        "url":  "https://www.regmovies.com/movies/imax-the-odyssey-70mm-ho00019076",
        "buy_url": "https://www.regmovies.com/movies/imax-the-odyssey-70mm-ho00019076",
        "on_sale_phrases":  ["add to cart", "buy tickets", "get tickets", "select seats"],
        "block_phrases":    [],
    },
    # ── Regal – standard Odyssey movie page ──────────────────────
    {
        "id":   "regal_odyssey",
        "name": "🎭 Regal – The Odyssey (Standard/IMAX)",
        "url":  "https://www.regmovies.com/movies/the-odyssey-ho00019076",
        "buy_url": "https://www.regmovies.com/movies/the-odyssey-ho00019076",
        "on_sale_phrases":  ["add to cart", "buy tickets", "get tickets", "select seats"],
        "block_phrases":    [],
    },
    # ── Regal – Irvine Spectrum theater page ─────────────────────
    {
        "id":   "regal_irvine_theater",
        "name": "📍 Regal Irvine Spectrum – Theater Showtimes",
        "url":  "https://www.regmovies.com/theatres/regal-edwards-irvine-spectrum-1010",
        "buy_url": "https://www.regmovies.com/theatres/regal-edwards-irvine-spectrum-1010",
        "on_sale_phrases":  ["the odyssey"],
        "block_phrases":    ["coming soon"],
        "must_find_phrase": "the odyssey",   # odyssey must appear in the current showtimes
    },
    # ── Fandango (Standard) ───────────────────────────────────────
    {
        "id":   "fandango_odyssey",
        "name": "🎟️ Fandango – The Odyssey",
        "url":  "https://www.fandango.com/the-odyssey-2026-241283/movie-overview",
        "buy_url": "https://www.fandango.com/the-odyssey-2026-241283/movie-overview",
        "on_sale_phrases":  ["buy tickets", "get tickets", "find tickets"],
        "block_phrases":    ["notify me when tickets go on sale", "we'll notify you"],
    },
    # ── Fandango IMAX 70mm ────────────────────────────────────────
    {
        "id":   "fandango_imax70mm",
        "name": "🎟️ Fandango – The Odyssey IMAX 70mm",
        "url":  "https://www.fandango.com/the-odyssey-the-imax-experience-in-70mm-2026-241386/movie-overview",
        "buy_url": "https://www.fandango.com/the-odyssey-the-imax-experience-in-70mm-2026-241386/movie-overview",
        "on_sale_phrases":  ["buy tickets", "get tickets", "find tickets"],
        "block_phrases":    ["notify me when tickets go on sale", "we'll notify you"],
    },
]


# ─────────────────────────────────────────────────────────────────
# State helpers
# ─────────────────────────────────────────────────────────────────

def load_state() -> dict:
    """Load previous run state from state.json, or return empty dict."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"Could not load state file: {e}")
    return {}


def save_state(state: dict) -> None:
    """Persist state to state.json."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    log.info(f"State saved to {STATE_FILE}")


# ─────────────────────────────────────────────────────────────────
# Scraping
# ─────────────────────────────────────────────────────────────────

def fetch_page(url: str) -> Optional[str]:
    """Fetch a URL and return lowercase text content, or None on failure."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.text.lower()
    except requests.RequestException as e:
        log.warning(f"  Fetch error for {url}: {e}")
        return None


def is_tickets_available(target: dict, html: str) -> bool:
    """
    Return True if the page signals tickets are on sale.

    Logic:
      1. At least one on_sale_phrase must appear in the HTML
      2. None of the block_phrases may appear (unless block_phrases is empty)
    """
    found_on_sale = any(phrase in html for phrase in target["on_sale_phrases"])
    blocked       = any(phrase in html for phrase in target.get("block_phrases", []))

    # If the target has a must_find_phrase, that phrase alone must appear
    # outside a "coming soon" context — treat same as on_sale check.
    if "must_find_phrase" in target:
        found_on_sale = target["must_find_phrase"] in html

    return found_on_sale and not blocked


def check_target(target: dict) -> dict:
    """
    Check a single target. Returns a result dict:
      { available: bool, error: bool, url: str, name: str }
    """
    log.info(f"Checking: {target['name']}")
    html = fetch_page(target["url"])

    if html is None:
        log.warning(f"  ⚠️  Could not reach {target['url']}")
        return {"available": False, "error": True,
                "name": target["name"], "url": target["buy_url"]}

    available = is_tickets_available(target, html)
    status_icon = "✅ ON SALE" if available else "❌ Not yet"
    log.info(f"  {status_icon}")

    return {"available": available, "error": False,
            "name": target["name"], "url": target["buy_url"]}


# ─────────────────────────────────────────────────────────────────
# Notifications  (ntfy.sh)
# ─────────────────────────────────────────────────────────────────

def send_ntfy(title: str, message: str, buy_url: str, priority: str = "urgent") -> bool:
    """Send a push notification via ntfy.sh."""
    try:
        resp = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title":    title.encode("utf-8").decode("latin-1", errors="ignore"),
                "Priority": priority,
                "Tags":     "movie_camera,rotating_light,ticket",
                "Click":    buy_url,
            },
            timeout=10,
        )
        resp.raise_for_status()
        log.info(f"  📱 ntfy notification sent! (topic: {NTFY_TOPIC})")
        return True
    except requests.RequestException as e:
        log.error(f"  Failed to send ntfy notification: {e}")
        return False


def send_test_notification() -> None:
    """Send a test notification to confirm setup is working."""
    log.info("Sending test notification...")
    send_ntfy(
        title="Odyssey Monitor is Active",
        message=(
            "Your ticket monitor is set up correctly! "
            "You'll get alerted the moment tickets go on sale at "
            "AMC or Regal Irvine Spectrum."
        ),
        buy_url="https://www.amctheatres.com/movies/the-odyssey-80679",
        priority="default",
    )


# ─────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────

def main():
    # Allow `python checker.py test` to send a test notification
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        send_test_notification()
        return

    log.info("=" * 56)
    log.info("  🔍 The Odyssey Ticket Monitor  —  Starting check")
    log.info(f"  ntfy topic : {NTFY_TOPIC}")
    log.info(f"  UTC time   : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 56)

    state = load_state()
    newly_on_sale = []

    for target in TARGETS:
        result   = check_target(target)
        prev     = state.get(target["id"], {})
        was_live = prev.get("available", False)
        alerted  = prev.get("alerted",   False)

        # Update state
        state[target["id"]] = {
            "available":    result["available"],
            "last_checked": datetime.now(timezone.utc).isoformat(),
            "error":        result["error"],
            # Only mark alerted once tickets go live; reset if they somehow go away
            "alerted": alerted if (result["available"] or was_live) else False,
        }

        # NEW: just became available and we haven't alerted yet
        if result["available"] and not alerted:
            newly_on_sale.append(result)
            state[target["id"]]["alerted"] = True

    # ── Send notifications ────────────────────────────────────────
    if newly_on_sale:
        log.info("")
        log.info("🚨 TICKETS DETECTED ON SALE! Sending notifications...")

        for result in newly_on_sale:
            send_ntfy(
                title=f"🎟️ TICKETS ON SALE — {result['name']}",
                message=(
                    f"Tickets for The Odyssey (Christopher Nolan) are NOW ON SALE at "
                    f"{result['name']}!\n\n"
                    f"👉 Buy now before they sell out: {result['url']}"
                ),
                buy_url=result["url"],
                priority="urgent",
            )

        # Also send a summary notification if multiple sources triggered
        if len(newly_on_sale) > 1:
            sources = "\n".join(f"• {r['name']}" for r in newly_on_sale)
            send_ntfy(
                title="🎟️ The Odyssey – Tickets Available at Multiple Theaters!",
                message=(
                    f"Tickets detected at {len(newly_on_sale)} sources:\n{sources}\n\n"
                    f"AMC: https://www.amctheatres.com/movies/the-odyssey-80679\n"
                    f"Regal: https://www.regmovies.com/movies/imax-the-odyssey-70mm-ho00019076"
                ),
                buy_url="https://www.amctheatres.com/movies/the-odyssey-80679",
                priority="urgent",
            )
    else:
        log.info("")
        log.info("No tickets on sale yet. State saved. Next check in ~10 min.")

    save_state(state)
    log.info("=" * 56)


if __name__ == "__main__":
    main()
