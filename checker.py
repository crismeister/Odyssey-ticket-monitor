#!/usr/bin/env python3
"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  The Odyssey Ticket Monitor  v2
  - Takes a baseline snapshot on first run
  - Only alerts if page CHANGES from baseline
    AND new ticket availability is detected
  - Ignores already-sold-out dates
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import requests
import hashlib
import json
import os
import sys
import re
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
# Config
# ─────────────────────────────────────────────────────────────────
NTFY_TOPIC  = os.environ.get("NTFY_TOPIC", "odyssey-tickets-YOUR-UNIQUE-ID")
STATE_FILE  = "state.json"
REQUEST_TIMEOUT = 15

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
# ─────────────────────────────────────────────────────────────────
TARGETS = [
    {
        "id":   "amc_odyssey",
        "name": "AMC Theatres - The Odyssey",
        "url":  "https://www.amctheatres.com/movies/the-odyssey-80679",
        "buy_url": "https://www.amctheatres.com/movies/the-odyssey-80679",
        "on_sale_phrases":  ["get tickets", "buy tickets", "showtimes", "select showtime"],
        "block_phrases":    ["coming soon", "notify me when"],
    },
    {
        "id":   "amc_api",
        "name": "AMC API - The Odyssey",
        "url":  "https://api.amctheatres.com/v2/movies/80679",
        "buy_url": "https://www.amctheatres.com/movies/the-odyssey-80679",
        "on_sale_phrases":  ["hasshowtime", "showtimecount", "ticketsavailable"],
        "block_phrases":    [],
        "is_api": True,
    },
    {
        "id":   "regal_imax_70mm",
        "name": "Regal Irvine Spectrum - IMAX 70mm",
        "url":  "https://www.regmovies.com/movies/imax-the-odyssey-70mm-ho00019076",
        "buy_url": "https://www.regmovies.com/movies/imax-the-odyssey-70mm-ho00019076",
        "on_sale_phrases":  ["add to cart", "buy tickets", "get tickets", "select seats"],
        "block_phrases":    [],
    },
    {
        "id":   "regal_odyssey",
        "name": "Regal - The Odyssey (Standard/IMAX)",
        "url":  "https://www.regmovies.com/movies/the-odyssey-ho00019076",
        "buy_url": "https://www.regmovies.com/movies/the-odyssey-ho00019076",
        "on_sale_phrases":  ["add to cart", "buy tickets", "get tickets", "select seats"],
        "block_phrases":    [],
    },
    {
        "id":   "regal_irvine_theater",
        "name": "Regal Irvine Spectrum - Theater Showtimes",
        "url":  "https://www.regmovies.com/theatres/regal-edwards-irvine-spectrum-1010",
        "buy_url": "https://www.regmovies.com/theatres/regal-edwards-irvine-spectrum-1010",
        "on_sale_phrases":  ["the odyssey"],
        "block_phrases":    ["coming soon"],
        "must_find_phrase": "the odyssey",
    },
    {
        "id":   "fandango_odyssey",
        "name": "Fandango - The Odyssey",
        "url":  "https://www.fandango.com/the-odyssey-2026-241283/movie-overview",
        "buy_url": "https://www.fandango.com/the-odyssey-2026-241283/movie-overview",
        "on_sale_phrases":  ["buy tickets", "get tickets", "find tickets"],
        "block_phrases":    ["notify me when tickets go on sale", "we'll notify you"],
    },
    {
        "id":   "fandango_imax70mm",
        "name": "Fandango - The Odyssey IMAX 70mm",
        "url":  "https://www.fandango.com/the-odyssey-the-imax-experience-in-70mm-2026-241386/movie-overview",
        "buy_url": "https://www.fandango.com/the-odyssey-the-imax-experience-in-70mm-2026-241386/movie-overview",
        "on_sale_phrases":  ["buy tickets", "get tickets", "find tickets"],
        "block_phrases":    ["notify me when tickets go on sale", "we'll notify you"],
    },
]


# ─────────────────────────────────────────────────────────────────
# Content fingerprinting
#
# We strip dynamic noise (timestamps, tokens, session IDs, ad
# tracking params) before hashing so minor page wobble doesn't
# count as a meaningful change.
# ─────────────────────────────────────────────────────────────────

# Regex patterns for dynamic content we want to IGNORE when comparing
_NOISE_PATTERNS = [
    r'"token"\s*:\s*"[^"]+"',           # auth/csrf tokens
    r'"expires?(?:At|In|_at|_in)"\s*:\s*"[^"]+"',  # expiry timestamps
    r'"timestamp"\s*:\s*[\d.]+',        # numeric timestamps
    r'__cf_bm=[^";\s]+',                # cloudflare cookies
    r'_ga=[^";\s&]+',                   # Google Analytics
    r'sid=[a-f0-9\-]+',                 # session IDs
    r'\b\d{13}\b',                      # 13-digit epoch ms timestamps
]
_NOISE_RE = re.compile("|".join(_NOISE_PATTERNS), re.IGNORECASE)


def extract_ticket_section(html: str, target: dict) -> str:
    """
    Parse the HTML and extract ONLY the portion of the page that
    is relevant to ticket availability — showtime grids, seat
    selectors, etc.  This makes our hash immune to unrelated page
    changes (hero banner swaps, ad rotations, nav updates).
    """
    soup = BeautifulSoup(html, "lxml")

    # Remove script/style/meta noise
    for tag in soup(["script", "style", "meta", "noscript", "svg", "iframe"]):
        tag.decompose()

    # Candidate CSS selectors that typically wrap showtime/ticket content
    TICKET_SELECTORS = [
        "[class*='showtime']",
        "[class*='ticketing']",
        "[class*='schedule']",
        "[class*='session']",
        "[class*='screening']",
        "[class*='cart']",
        "[id*='showtime']",
        "[id*='ticket']",
        "[class*='availability']",
    ]

    fragments = []
    for selector in TICKET_SELECTORS:
        for el in soup.select(selector):
            text = el.get_text(separator=" ", strip=True)
            if text:
                fragments.append(text)

    if fragments:
        content = " | ".join(fragments)
    else:
        # Fallback: use the full visible text
        content = soup.get_text(separator=" ", strip=True)

    # Strip dynamic noise before returning
    content = _NOISE_RE.sub("", content).lower()
    # Collapse whitespace
    content = re.sub(r"\s+", " ", content).strip()
    return content


def fingerprint(content: str) -> str:
    """Return a short SHA-256 hex digest of the content string."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


# ─────────────────────────────────────────────────────────────────
# State helpers
# ─────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"Could not load state file: {e}")
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    log.info(f"State saved to {STATE_FILE}")


# ─────────────────────────────────────────────────────────────────
# Scraping
# ─────────────────────────────────────────────────────────────────

def fetch_page(url: str) -> Optional[str]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        log.warning(f"  Fetch error for {url}: {e}")
        return None


def has_ticket_signals(target: dict, html_lower: str) -> bool:
    """Check if the raw HTML contains ticket-availability signals."""
    found = any(p in html_lower for p in target["on_sale_phrases"])
    blocked = any(p in html_lower for p in target.get("block_phrases", []))
    if "must_find_phrase" in target:
        found = target["must_find_phrase"] in html_lower
    return found and not blocked


def check_target(target: dict, prev_state: dict) -> dict:
    """
    Check a single target. Returns a result dict with keys:
      status:  'new_availability' | 'no_change' | 'not_available' | 'error'
      name, url, fingerprint
    """
    log.info(f"Checking: {target['name']}")

    html = fetch_page(target["url"])
    if html is None:
        log.warning(f"  Could not reach page")
        return {"status": "error", "name": target["name"], "url": target["buy_url"],
                "fingerprint": None}

    html_lower = html.lower()
    tickets_signalled = has_ticket_signals(target, html_lower)

    # Extract the stable, ticket-relevant portion of the page
    content    = extract_ticket_section(html, target)
    fp_now     = fingerprint(content)
    fp_baseline = prev_state.get("baseline_fingerprint")
    fp_last     = prev_state.get("last_fingerprint")
    is_first_run = fp_baseline is None

    log.info(f"  Ticket signals present : {tickets_signalled}")
    log.info(f"  Content fingerprint    : {fp_now}")
    log.info(f"  Baseline fingerprint   : {fp_baseline or '(none – first run)'}")

    # ── First run: record baseline and do NOT alert ───────────────
    if is_first_run:
        log.info("  First run — saving baseline. Will alert only on future changes.")
        return {
            "status": "baseline_set",
            "name": target["name"],
            "url": target["buy_url"],
            "fingerprint": fp_now,
        }

    # ── Subsequent runs ───────────────────────────────────────────
    page_changed = (fp_now != fp_baseline) and (fp_now != fp_last)

    if tickets_signalled and page_changed:
        log.info("  NEW availability detected — page changed from baseline!")
        return {
            "status": "new_availability",
            "name": target["name"],
            "url": target["buy_url"],
            "fingerprint": fp_now,
        }
    elif tickets_signalled and not page_changed:
        log.info("  Ticket signals present but page unchanged from baseline — ignoring (already sold out dates)")
        return {
            "status": "no_change",
            "name": target["name"],
            "url": target["buy_url"],
            "fingerprint": fp_now,
        }
    else:
        log.info("  No ticket availability detected")
        return {
            "status": "not_available",
            "name": target["name"],
            "url": target["buy_url"],
            "fingerprint": fp_now,
        }


# ─────────────────────────────────────────────────────────────────
# Notifications
# ─────────────────────────────────────────────────────────────────

def send_ntfy(title: str, message: str, buy_url: str, priority: str = "urgent") -> bool:
    # Safely encode title — HTTP headers are latin-1 only
    safe_title = title.encode("ascii", errors="ignore").decode("ascii")
    try:
        resp = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title":    safe_title,
                "Priority": priority,
                "Tags":     "movie_camera,rotating_light,ticket",
                "Click":    buy_url,
            },
            timeout=10,
        )
        resp.raise_for_status()
        log.info(f"  Push notification sent! (topic: {NTFY_TOPIC})")
        return True
    except requests.RequestException as e:
        log.error(f"  Failed to send notification: {e}")
        return False


def send_test_notification() -> None:
    log.info("Sending test notification...")
    send_ntfy(
        title="Odyssey Monitor is Active",
        message=(
            "Your ticket monitor is set up correctly! "
            "Baseline snapshots will be saved on the next full run. "
            "You will be alerted only when NEW ticket dates appear at "
            "AMC or Regal Irvine Spectrum."
        ),
        buy_url="https://www.amctheatres.com/movies/the-odyssey-80679",
        priority="default",
    )


# ─────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        send_test_notification()
        return

    log.info("=" * 60)
    log.info("  The Odyssey Ticket Monitor  v2  —  Starting check")
    log.info(f"  ntfy topic : {NTFY_TOPIC}")
    log.info(f"  UTC time   : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 60)

    state = load_state()
    newly_on_sale = []
    baseline_targets = []  # targets getting their first snapshot this run

    for target in TARGETS:
        prev = state.get(target["id"], {})
        result = check_target(target, prev)

        # ── Update state ──────────────────────────────────────────
        if result["status"] == "baseline_set":
            # First run — save baseline, don't alert
            state[target["id"]] = {
                "baseline_fingerprint": result["fingerprint"],
                "last_fingerprint":     result["fingerprint"],
                "last_checked":         datetime.now(timezone.utc).isoformat(),
                "alerted":              False,
            }
            baseline_targets.append(target["name"])

        elif result["status"] == "new_availability":
            already_alerted = prev.get("alerted", False)
            state[target["id"]] = {
                "baseline_fingerprint": prev.get("baseline_fingerprint"),
                "last_fingerprint":     result["fingerprint"],
                "last_checked":         datetime.now(timezone.utc).isoformat(),
                "alerted":              True,
            }
            if not already_alerted:
                newly_on_sale.append(result)

        else:
            # no_change / not_available / error — keep baseline, update last seen
            state[target["id"]] = {
                "baseline_fingerprint": prev.get("baseline_fingerprint"),
                "last_fingerprint":     result.get("fingerprint") or prev.get("last_fingerprint"),
                "last_checked":         datetime.now(timezone.utc).isoformat(),
                "alerted":              prev.get("alerted", False),
            }

    # ── Notifications ─────────────────────────────────────────────
    if baseline_targets:
        log.info("")
        log.info(f"Baseline saved for {len(baseline_targets)} target(s).")
        log.info("Future runs will only alert on changes from this baseline.")

    if newly_on_sale:
        log.info("")
        log.info(f"NEW TICKETS DETECTED at {len(newly_on_sale)} source(s)! Sending alerts...")
        for result in newly_on_sale:
            send_ntfy(
                title=f"TICKETS ON SALE: {result['name']}",
                message=(
                    f"NEW tickets for The Odyssey (Nolan) are on sale at "
                    f"{result['name']}!\n\n"
                    f"Buy now: {result['url']}"
                ),
                buy_url=result["url"],
                priority="urgent",
            )
        if len(newly_on_sale) > 1:
            sources = "\n".join(f"- {r['name']}" for r in newly_on_sale)
            send_ntfy(
                title="The Odyssey - New Tickets at Multiple Theaters!",
                message=(
                    f"NEW tickets detected at {len(newly_on_sale)} sources:\n{sources}\n\n"
                    f"AMC: https://www.amctheatres.com/movies/the-odyssey-80679\n"
                    f"Regal: https://www.regmovies.com/movies/the-odyssey-ho00019076"
                ),
                buy_url="https://www.amctheatres.com/movies/the-odyssey-80679",
                priority="urgent",
            )
    elif not baseline_targets:
        log.info("")
        log.info("No new ticket availability detected. All sources checked.")

    save_state(state)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
