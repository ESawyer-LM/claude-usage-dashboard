# Claude Usage Dashboard

Automated usage reporting for Claude.ai Teams and Enterprise plans. Collects member data, activity analytics, usage metrics, and Claude Code stats via the claude.ai internal API, then generates an HTML dashboard and PDF report and emails them on a schedule.

## Features

- **Direct API data collection** — no browser or Playwright needed; uses claude.ai's internal REST API with a sessionKey cookie (~30 day lifespan)
- **Interactive HTML dashboard** with Chart.js charts (DAU/WAU/utilization, daily chats, top users by projects & artifacts, Claude Code sessions)
- **PDF report** generated with ReportLab + matplotlib
- **Scheduled email delivery** (Mon-Thu + Friday with different recipient lists, individually toggleable)
- **Claude Code analytics** — sessions, lines accepted, commits, PRs, cost, per-user breakdown
- **Web admin UI** for managing cookie, SMTP, schedule, recipients, and on-demand sends
- **Fernet encryption** for SMTP password at rest
- **Fallback to cached data** when API calls fail

## Data Sources

All data is collected from claude.ai's internal API endpoints (authenticated via sessionKey cookie):

| Endpoint | Data |
|----------|------|
| `/api/organizations/{org}/members_v2` | Member directory (name, email, role, seat tier) |
| `/api/organizations/{org}/members/counts` | Active/pending counts by tier |
| `/api/organizations/{org}/members_limit` | Seat allocation |
| `/api/organizations/{org}/analytics/activity/overview` | DAU, WAU, MAU, utilization % |
| `/api/organizations/{org}/analytics/usage/overview` | Chats/day, projects, artifacts |
| `/api/organizations/{org}/analytics/usage/timeseries` | Daily chat count chart |
| `/api/organizations/{org}/analytics/users/rankings` | Top users by projects/artifacts |
| `/api/claude_code/metrics_aggs/overview` | Claude Code sessions, lines, cost, commits, PRs |
| `/api/claude_code/metrics_aggs/users` | Per-user Claude Code metrics |

---

## Setup

### Option A — Ubuntu VM (recommended, runs automatically)

```bash
git clone <repo> claude_dashboard
cd claude_dashboard
sudo bash install.sh
```

The installer prompts for an admin UI password. Everything else is automatic. After install, the service starts immediately and on every reboot.

Open the admin UI from any machine on the same network:
```
http://<VM-IP-address>:8934
```

Then complete setup in the admin UI:
1. **Claude.ai Connection** — paste your Organization ID and sessionKey cookie
2. **SMTP / Email Settings** — configure your mail server, click "Test Connection"
3. **Schedule & Recipients** — confirm send times and toggle on/off
4. **Send Now** — fire off a test to verify end-to-end delivery

### Option B — Manual / development

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set ADMIN_PASSWORD
python main.py
```

---

## Getting the Session Cookie & Org ID

1. Log into [claude.ai](https://claude.ai) in Chrome
2. Open DevTools (F12) → **Application** → **Cookies** → **claude.ai** → copy `sessionKey`
3. Go to **claude.ai/admin-settings/organization** → open DevTools **Network** tab → filter by `members` → copy the UUID from the API URL path (e.g. `/api/organizations/xxxxxxxx-xxxx-.../members_v2`)
4. Paste both into the admin UI

The sessionKey lasts approximately 30 days.

---

## CLI Usage

```bash
python main.py                # Start scheduler + admin UI (default)
python main.py --now          # Run once, email weekday recipients, exit
python main.py --now --friday # Run once, email Friday recipients, exit
python main.py --no-admin     # Scheduler only, no admin UI
```

---

## Management Commands

```bash
journalctl -u claude-dashboard -f          # Live logs
sudo systemctl status claude-dashboard     # Service status
sudo systemctl restart claude-dashboard    # Restart
ls -lh /opt/claude-dashboard/output/       # Output files
```

---

## Architecture

```
main.py                    Entry point (CLI + threading)
  ├── scheduler.py         APScheduler cron jobs (Mon-Thu + Friday)
  │     ├── scraper.py     Direct HTTP API calls to claude.ai
  │     ├── html_generator.py   Self-contained HTML with Chart.js
  │     ├── pdf_generator.py    ReportLab + matplotlib PDF
  │     └── emailer.py    SMTP delivery (PDF attachment)
  ├── admin.py             Flask web admin UI (port 8934)
  └── config.py            .env + settings.json + Fernet encryption
```

| Component | Library |
|-----------|---------|
| Data collection | urllib (stdlib) — direct claude.ai API calls |
| HTML dashboard | Python string templating + Chart.js 4.4.1 (CDN) |
| PDF report | reportlab + matplotlib |
| Email | smtplib (stdlib) + STARTTLS/SSL |
| Scheduling | apscheduler |
| Config | python-dotenv + settings.json |
| Encryption | cryptography (Fernet) |
| Admin UI | Flask |
