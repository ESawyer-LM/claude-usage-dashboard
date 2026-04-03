# Claude Usage Dashboard — Project Context

## What This Is

An automated Claude.ai usage reporting tool for Lou Malnati's Pizzeria. It collects member data, activity analytics, usage metrics, and Claude Code stats from claude.ai's internal API, generates an HTML dashboard and PDF report, and emails the PDF on a configurable schedule.

**GitHub repo:** https://github.com/ESawyer-LM/claude-usage-dashboard (private)

**Deployed on:** Ubuntu 25.04 VM as a systemd service (`claude-dashboard`), installed at `/opt/claude-dashboard/`

**Admin UI:** Flask web app on port 8934, accessible from any machine on the same network.

---

## Architecture

```
main.py                    Entry point (CLI args: --now, --friday, --no-admin)
  ├── scheduler.py         APScheduler BlockingScheduler (Mon-Thu + Friday cron jobs, toggleable)
  │     ├── scraper.py     Direct HTTP API calls to claude.ai (no browser needed)
  │     ├── html_generator.py   Self-contained HTML with Chart.js 4.4.1
  │     ├── pdf_generator.py    ReportLab + matplotlib PDF with custom Flowables
  │     └── emailer.py    SMTP delivery (PDF attachment only — Exchange blocks .html)
  ├── admin.py             Flask web admin UI on port 8934
  └── config.py            .env (bootstrap) + settings.json (runtime) + Fernet encryption
```

**No browser/Playwright needed.** We initially tried Playwright headless scraping but Cloudflare Turnstile blocked it. We then discovered claude.ai's internal REST API endpoints (by analyzing HAR captures) and switched to direct HTTP calls with `urllib`. This is faster, simpler, and the sessionKey cookie lasts ~30 days.

---

## Data Sources — claude.ai Internal API Endpoints

All authenticated via `Cookie: sessionKey=...` header. Org UUID is stored in `settings.json` (configurable in admin UI).

### Members & Seats
| Endpoint | Data |
|----------|------|
| `/api/organizations/{org}/members_v2?offset=0&limit=50` | Paginated member list. **Members** have `item.member.account.email_address`. **Invites** have `item.invite.email_address` (different structure!) |
| `/api/organizations/{org}/members/counts` | `{total: 22, by_seat_tier: {team_standard: 20, team_tier_1: 2}, pending_invites_total: 1}` |
| `/api/organizations/{org}/members_limit` | `{members_limit: 150, seat_tier_quantities: {team_standard: 22, team_tier_1: 2}}` |
| `/api/organizations/{org}/subscription_details` | Plan status, billing interval, payment method |

**Seat count logic:** `total_seats` = sum of `seat_tier_quantities` values (not `members_limit`, which is the max cap). Display as "assigned/total" e.g. "23/24".

### Activity Analytics (`/analytics/activity` page)
| Endpoint | Data |
|----------|------|
| `/api/organizations/{org}/analytics/activity/overview` | DAU, WAU, MAU, utilization %, stickiness, with change_percent |
| `/api/organizations/{org}/analytics/activity/timeseries?metric=dau&days=30` | Daily data points for line chart |

### Usage Analytics (`/analytics/usage` page)
| Endpoint | Data |
|----------|------|
| `/api/organizations/{org}/analytics/usage/overview` | chats_per_day, projects_created, artifacts_created, user percentages |
| `/api/organizations/{org}/analytics/usage/timeseries?metric=chats&days=7` | Daily chat count data points |
| `/api/organizations/{org}/analytics/users/rankings?metric=projects&start_date=YYYY-MM-DD&limit=10` | Top users by projects MTD (returns email + value, join with members for names) |
| `/api/organizations/{org}/analytics/users/rankings?metric=artifacts&start_date=...&limit=10` | Top users by artifacts MTD |

### Claude Code Analytics (`/analytics/claude-code` page)
| Endpoint | Data |
|----------|------|
| `/api/claude_code/metrics_aggs/overview?start_date=...&end_date=...&granularity=daily&organization_uuid={org}&customer_type=claude_ai&subscription_type=team` | Summary: active_users, total_sessions, total_lines_accepted, commits_created, pull_requests_created, total_cost_usd, tool_accept_rate. Time series: activity, spend, lines_of_code |
| `/api/claude_code/metrics_aggs/users?start_date=...&end_date=...&limit=20&offset=0&sort_by=total_lines_accepted&sort_order=desc&organization_uuid={org}&customer_type=claude_ai&subscription_type=team` | Per-user: email, total_cost, total_lines_accepted, total_sessions, last_active, prs_with_cc |

**Note:** Claude Code endpoints use `organization_uuid` as a query param, not in the URL path. Also require `customer_type=claude_ai&subscription_type=team`.

---

## Configuration

### .env (bootstrap, not in git)
- `OUTPUT_DIR` (default `./output`)
- `ADMIN_PORT` (default `8934`)
- `ADMIN_PASSWORD` — plain text password for admin UI login

### settings.json (runtime, in OUTPUT_DIR, not in git)
- `org_id` — claude.ai organization UUID
- `session_cookie` — sessionKey cookie value (~30 day lifespan)
- `smtp_host`, `smtp_port`, `smtp_user`, `smtp_pass` (Fernet-encrypted), `smtp_from_name`
- `weekday_recipients`, `friday_recipients` — email lists
- `weekday_cron`, `friday_cron` — `{hour, minute}` objects
- `weekday_enabled`, `friday_enabled` — on/off toggles for scheduled sends
- `timezone` — default `America/Chicago`

### Fernet encryption
- Key stored in `OUTPUT_DIR/.fernet_key` (auto-generated on first run, chmod 600)
- Used to encrypt `smtp_pass` at rest in settings.json
- Never returned in any API response

---

## Key Design Decisions & Lessons Learned

1. **No Playwright/browser** — Cloudflare Turnstile blocks headless browsers (even non-headless with Xvfb). Direct API calls with sessionKey bypass Cloudflare entirely.

2. **Invite vs member parsing** — The `members_v2` endpoint returns two types: `{"type": "member", "member": {"account": {"email_address": ...}}}` and `{"type": "invite", "invite": {"email_address": ...}}`. These have completely different structures and must be parsed separately.

3. **Seat count** — `members_limit` (150) is the org's max allowed seats. The actual assigned count is `sum(seat_tier_quantities.values())`. Display format: "23/24" (assigned/total).

4. **PDF-only email attachment** — Lou Malnati's Exchange server blocks .html attachments. HTML dashboard is saved to disk only.

5. **Atomic settings writes** — `save_settings()` writes to a temp file then `os.replace()` to prevent corruption from concurrent Flask + scheduler access.

6. **APScheduler rescheduling** — `reschedule_job()` is thread-safe, called from Flask thread to update scheduler in main thread.

7. **The Anthropic Admin API** (`api.anthropic.com`) is for Console/API usage only, NOT for claude.ai chat product usage. Don't confuse them.

---

## Admin UI Cards

1. **System Status** — last scrape, last email, next runs, cookie status (auto-refreshes every 30s)
2. **Claude.ai Connection** — org ID + sessionKey cookie input
3. **SMTP / Email Settings** — host, port, user, password (encrypted), from name, test connection button
4. **Schedule & Recipients** — weekday/Friday time dropdowns with on/off toggles, recipient textareas, **Send Now** buttons for both weekday and Friday
5. **Send Test Report** — single recipient test email

---

## Deployment

### Ubuntu VM (production)
```bash
sudo bash install.sh    # Idempotent: 8 steps, creates service user, venv, systemd service
```
Install path: `/opt/claude-dashboard/`, runs as `claude-dashboard` system user.

### Updating
```bash
# Copy changed files and restart
sudo cp *.py /opt/claude-dashboard/
sudo chown claude-dashboard:claude-dashboard /opt/claude-dashboard/*.py
sudo systemctl restart claude-dashboard
```

### Useful commands
```bash
journalctl -u claude-dashboard -f          # Live logs
sudo systemctl status claude-dashboard     # Service status
sudo systemctl restart claude-dashboard    # Restart after changes
ls -lh /opt/claude-dashboard/output/       # Debug screenshots, HTML, PDF, logs
```

---

## Branding

- Primary color: `#C8102E` (Lou Malnati's red)
- Claude Code accent: `#7c3aed` (purple)
- Font: `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif`
- Logo badge: white circle with "LM" in red
