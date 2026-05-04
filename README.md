# 🎟️ The Odyssey Ticket Monitor

Monitors **AMC Theatres** and **Regal Irvine Spectrum** every 10 minutes and
sends an instant push notification to your phone the moment tickets go on sale
for Christopher Nolan's *The Odyssey* (July 17, 2026).

No server needed. Runs 100% free on GitHub Actions.

---

## What It Monitors

| Source | What it watches |
|---|---|
| AMC Theatres | Main Odyssey movie page |
| AMC API | AMC's internal API (lightweight backup) |
| Regal Irvine Spectrum | IMAX 70mm movie page |
| Regal Irvine Spectrum | Standard / IMAX movie page |
| Regal Irvine Spectrum | Theater showtimes page |
| Fandango | Standard Odyssey listing |
| Fandango | IMAX 70mm listing |

---

## Setup (15 minutes, one-time)

### Step 1 — Install ntfy on your phone

ntfy.sh is a free push notification service — no account required.

1. **iPhone**: Install [ntfy from the App Store](https://apps.apple.com/us/app/ntfy/id1625396347)
2. **Android**: Install [ntfy from Google Play](https://play.google.com/store/apps/details?id=io.heckel.ntfy)

### Step 2 — Choose your unique topic name

Your topic name is like a private channel. Make it hard to guess so strangers
don't subscribe to your alerts.

**Good example:** `odyssey-tickets-jsmith-8472`

Write it down — you'll need it in Steps 3 and 4.

### Step 3 — Subscribe on your phone

1. Open the ntfy app
2. Tap **"+"** or **Subscribe to topic**
3. Enter your topic name (e.g. `odyssey-tickets-jsmith-8472`)
4. Tap **Subscribe**

### Step 4 — Create your GitHub repository

1. Go to [github.com](https://github.com) and sign in (or create a free account)
2. Click **"New repository"**
3. Name it `odyssey-ticket-monitor`
4. Set it to **Private** (recommended)
5. Click **Create repository**

### Step 5 — Upload the files

Upload all three files to your new repo:

```
odyssey-ticket-monitor/
├── checker.py
├── requirements.txt
└── .github/
    └── workflows/
        └── check_tickets.yml
```

**Easiest way:** In your new repo, click **"uploading an existing file"** and
drag in `checker.py` and `requirements.txt`. Then create the workflow file:
- Click **"Create new file"**
- In the filename box, type: `.github/workflows/check_tickets.yml`
- Paste the contents of `check_tickets.yml`
- Click **Commit**

### Step 6 — Add your ntfy topic as a secret

This keeps your topic name private.

1. In your GitHub repo, go to **Settings → Secrets and variables → Actions**
2. Click **"New repository secret"**
3. Name: `NTFY_TOPIC`
4. Value: your topic name (e.g. `odyssey-tickets-jsmith-8472`)
5. Click **Add secret**

### Step 7 — Send a test notification

1. In your repo, go to **Actions** tab
2. Click **"🎟️ Odyssey Ticket Monitor"** in the left sidebar
3. Click **"Run workflow"**
4. Set "Send a test notification?" to `true`
5. Click **"Run workflow"**

Within 30 seconds your phone should buzz with:
> ✅ **Odyssey Monitor is Active** — Your ticket monitor is set up correctly!

If it worked, you're done! The monitor will now run automatically every 10
minutes, 24/7.

---

## How It Works

```
Every 10 minutes:
  GitHub Actions wakes up
      │
      ▼
  checker.py fetches 7 URLs (AMC, Regal, Fandango)
      │
      ▼
  Looks for "Get Tickets" / "Buy Tickets" text appearing
  AND "Coming Soon" / "Notify Me" text disappearing
      │
      ├── No change → saves state, goes back to sleep
      │
      └── Tickets detected! → sends ntfy push notification
                               (once per theater — no spam)
```

State is cached between runs so you only get notified once, not every 10
minutes after tickets go on sale.

---

## Manual / Local Testing

```bash
# Install dependencies
pip install -r requirements.txt

# Set your topic
export NTFY_TOPIC="odyssey-tickets-jsmith-8472"

# Send test notification
python checker.py test

# Run a real check
python checker.py
```

---

## Customizing

### Add more movies or theaters

Edit the `TARGETS` list in `checker.py`. Each target needs:

```python
{
    "id":   "unique_id_no_spaces",
    "name": "Human-readable name",
    "url":  "URL to monitor",
    "buy_url": "URL to open when tickets found (can be same as url)",
    "on_sale_phrases":  ["text that appears when on sale"],
    "block_phrases":    ["text that appears when NOT on sale"],
}
```

### Change check frequency

Edit `cron` in `check_tickets.yml`. GitHub Actions minimum is 5 minutes:

```yaml
- cron: "*/5 * * * *"   # every 5 minutes
- cron: "*/10 * * * *"  # every 10 minutes (default)
- cron: "*/30 * * * *"  # every 30 minutes
```

> ⚠️ GitHub Actions free tier gives you 2,000 minutes/month. At 10-minute
> intervals, this monitor uses ~4,464 minutes/month (each run ~1 min).
> **Upgrade to the free tier workaround:** GitHub doesn't charge for public
> repos. Make your repo public, or use the 5-minute interval sparingly.
>
> Better option: set a tighter schedule only in June–July 2026 when tickets
> are more likely to drop, and use `*/30` before then.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| No test notification received | Double-check `NTFY_TOPIC` secret matches what you subscribed to in the app |
| Workflow not running | Go to Actions tab → enable workflows if prompted |
| False positive alert | AMC/Regal updated their page layout; open an issue or adjust `on_sale_phrases` |
| GitHub Actions minutes running out | Switch to a public repo, or reduce frequency |

---

## Links

- [AMC – The Odyssey](https://www.amctheatres.com/movies/the-odyssey-80679)
- [Regal – IMAX 70mm](https://www.regmovies.com/movies/imax-the-odyssey-70mm-ho00019076)
- [Regal Irvine Spectrum Theater](https://www.regmovies.com/theatres/regal-edwards-irvine-spectrum-1010)
- [Fandango – The Odyssey](https://www.fandango.com/the-odyssey-2026-241283/movie-overview)
- [ntfy.sh docs](https://docs.ntfy.sh)
