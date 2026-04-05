# Changelog

All notable changes to the Claude Usage Dashboard will be documented in this file.

## [0.7.5] - 2026-04-05

### Added
- Mobile responsive layout with viewport meta tag and CSS breakpoints for all three templates
- Toast notification system with slide-in animations for form submission feedback
- Button loading spinners during async operations (save, send, test, delete)
- Keyboard shortcuts: Escape closes password modal, triggers "Stay Signed In" on inactivity modal
- Auto-focus on modal open (password modal focuses first input, inactivity modal focuses button)
- Login page branding with centered LM badge, subtitle, and version number
- Styled inline delete confirmations replacing browser `confirm()` dialogs
- Visual hierarchy: red left-border accent on Status card, "Configuration" section divider
- Reusable CSS classes: `.modal-overlay`, `.modal-box`, `.btn-sm`, `.btn-loading`, `.status-badge`, `.sched-meta`, `.separator`, `.result-span`, `.toast`

### Changed
- Schedule card headers now use a two-line layout (name + badge on row 1, metadata on row 2) for better readability
- Timezone selector moved from Status card to its own "Preferences" section at the bottom of the page
- Extracted ~50 inline style attributes across all templates into named CSS classes

### Fixed
- "Add Schedule" button now reappears after clicking Cancel on the new schedule form

## [0.7.2] - 2026-04-04

### Added
- Auto-logout after 15 minutes of inactivity with a 1-minute countdown warning popup and "Stay Signed In" button

## [0.7.1] - 2026-04-04

### Fixed
- Section headers (e.g., "Weekly Active Users") no longer get separated from their charts by page breaks in the PDF report

## [0.7.0] - 2026-04-04

### Added
- "Expanded Report" report type with enhanced metrics beyond the standard Full Report
- Trend arrows (up/down with percentage change) on DAU, WAU, MAU, Utilization stat cards (gated by `trends` section)
- Stickiness metric (DAU/MAU ratio) stat card (gated by `stickiness` section)
- Usage summary stat cards: Avg Chats/Day, Projects Created, Artifacts Created with trends (gated by `usage_stats` section)
- Top Users by Chats MTD horizontal bar chart (gated by `chat_rankings` section)
- Daily Active Users (DAU) 30-day line chart (gated by `dau_chart` section)
- DAU timeseries and chat rankings data collection in scraper
- New scraper return keys: `dau_chart`, `top_users_chats`

## [0.6.0] - 2026-04-04

### Added
- Multi-version report type infrastructure: `REPORT_TYPES` registry in config.py with section-based gating
- `report_type` field on each schedule, with automatic migration for existing schedules (defaults to "full")
- Report type dropdown in schedule create/edit forms and collapsed schedule headers in admin UI
- Report type selector on the Send Test Report card
- Section gating in PDF and HTML generators: sections are conditionally included based on report type config
- Report type name included in email subject line
- Output filenames now include report type (e.g., `claude_usage_dashboard_full.pdf`)

## [0.5.0] - 2026-04-04

### Added
- Claude Code daily lines accepted time series chart (HTML and PDF)
- Claude Code top users by lines accepted horizontal bar chart (HTML and PDF)
- Claude Code user breakdown table with sessions, lines, commits, PRs, and last active (HTML and PDF)
- PDF Claude Code section now includes daily sessions chart, daily lines chart, top users chart, and full user table
- PDF chart helpers now support custom colors for Claude Code purple theming

### Changed
- Claude Code stat cards reduced from 6 to 5 (removed cost), now showing: Active Users, Sessions, Lines Accepted, Commits, Pull Requests
- Claude Code user API fetch limit increased from 20 to 50
- PDF Claude Code section expanded from 3 stat cards to 5 stat cards plus charts and user table
- PDF section headers now support custom accent bar colors

### Removed
- Total Cost stat card from Claude Code Analytics section

## [0.4.5] - 2026-04-04

### Changed
- Email Schedules card moved to position #2 (right after System Status)
- Schedule cards collapse to compact summary row; click to expand edit form
- Auto-update reload polls server with live progress counter instead of fixed timeout
- Fixed schedule migration not running due to DEFAULT_SETTINGS merge order

## [0.4.2] - 2026-04-04

## [0.4.1] - 2026-04-04

### Changed
- Schedule cards now collapse to a compact summary (name, status, recurrence, time, recipient count, toggle) — click to expand edit form
- Auto-update reload now polls server every 2s (up to 30 attempts) instead of a fixed 5s timeout, with live progress counter

## [0.4.0] - 2026-04-04

### Added
- Multi-schedule email system: create unlimited named schedule sets, each with its own recurrence, time, and recipient list
- Recurrence options: Weekdays (Mon-Fri), Every Day, Weekly (pick specific days), Biweekly, Monthly (day 1-28 or last)
- Per-schedule enable/disable toggle, Send Now, and Delete with confirmation
- Per-schedule last_sent and next_run timestamps displayed in the admin UI
- "Add Schedule" form in Card 4 for creating new schedule sets on the fly
- Automatic migration from legacy weekday/friday format to new multi-schedule format
- CLI `--schedule <id>` flag for one-shot mode targeting a specific schedule's recipients

### Changed
- Card 4 "Schedule & Recipients" redesigned as "Email Schedules" with dynamic sub-cards
- `/api/save-schedule` and `/api/send-now` replaced with per-schedule CRUD routes (`/api/schedules`, `/api/schedules/<id>`, etc.)
- `scheduler.py` rewritten: `run_report_job(schedule_id)` replaces `run_report_job(is_friday)`, `sync_jobs()` manages N dynamic APScheduler jobs
- `main.py` startup banner now lists all active schedules with recurrence details
- CLI `--friday` flag replaced with `--schedule <id>`; `--now` without `--schedule` runs all enabled schedules

### Removed
- Hardcoded weekday/friday dual-schedule system
- Legacy settings keys: `weekday_cron`, `friday_cron`, `weekday_recipients`, `friday_recipients`, `weekday_enabled`, `friday_enabled`

## [0.3.8] - 2026-04-03

### Added
- Password change modal in admin UI navbar (lock icon) — no more manual .env edits

## [0.3.7] - 2026-04-03

### Changed
- Member table condensed: name + email on single line, tighter padding, smaller fonts
- Premium badge shortened to "P", column widths rebalanced

## [0.3.6] - 2026-04-03

### Removed
- Claude Code cost ($) from email body text

## [0.3.5] - 2026-04-03

### Changed
- Header bar now has straight edges (no rounded corners)
- Daily Chat Activity always shows full 7-day timeframe, pads with zeros if fewer
- Featured card has straight corners and straight red left border
- Line charts use gradient fill under the line (matching HTML template)
- WAU graph shows all data point labels, limited to 7 days matching daily chat dates
- WAU chart and stats wrapped in thin black border to group them visually
- Horizontal bar charts wrapped in thin black borders
- Removed sparse_labels — all data points labeled in both charts

## [0.3.4] - 2026-04-03

### Fixed
- PDF no longer renders duplicate pages (NumberedCanvas two-pass fix)
- Stat card values now colored: red for seats/tier, green for active, amber for pending
- Summary callout values default to red matching HTML template
- Featured Daily Chat Activity card: white background, full border, rounded corners, section label inside card

## [0.3.3] - 2026-04-03

### Changed
- PDF pages now have a light-grey content background with white side margins
- "Daily Chat Activity" is a featured section with red highlight extending full height
- Weekly Active Users graph only labels key dates for readability
- All Members table has a 30% darker background throughout
- "As of" date in header now includes time
- Every page now has a footer with "Page X of Y", data source, date/time, and version

## [0.3.2] - 2026-04-03

### Added
- Last test recipient saved and pre-filled for re-use
- Timezone selection is now a dropdown with common US and international timezones

## [0.3.1] - 2026-04-03

### Fixed
- Auto-updater now logs each file copy and reports permission errors clearly
- Verifies config.py version after update to confirm files were written

## [0.3.0] - 2026-04-03

### Changed
- Complete PDF report redesign to match new layout
- Stat cards now show uppercase labels, large values, and descriptive subtitles (Total Seats, Active Members, Pending Invites, Seat Tier)
- Section headers are gray uppercase dividers with red accent bars
- Daily Chat Activity section includes summary stats (total, peak, avg, engagement)
- New Weekly Active Users (WAU) section with timeseries chart and growth metrics
- Claude Code section simplified to stats row (Lines Accepted, Acceptance Rate, Top User)
- Member table redesigned with role badges, tier column, premium badges, and colored status indicators
- Line charts now display data point value labels

### Added
- WAU timeseries data collection in scraper
- SectionHeader and StatsSummaryRow custom Flowables
- Collapsible card sections in admin UI with chevron indicators and smooth animations
- Timezone setting in System Status card with validation
- Human-readable datetime formatting in configured timezone

### Removed
- Donut charts (Org Overview section)
- Claude Code sessions chart and top CC users chart (replaced by compact stats row)

## [0.2.10] - 2026-04-03

### Added
- Auto-update checker queries GitHub for newer version tags
- Update install button in admin UI with confirmation dialog
- Version number displayed in admin UI System Status card and HTML/PDF report footers
- Auto-restart service after installing updates from admin UI
- Sudoers entry for service user to manage systemd (created by installer)

### Changed
- Installer (`install.sh`) uses `git clone` for new installs (enables auto-update), with file-copy fallback
- Installer adds `git` as a system dependency

### Fixed
- Auto-updater supports both git-based and file-copy production installs
- Removed `NoNewPrivileges=true` from systemd service — it blocked sudo's setuid bit, preventing auto-restart
- Service file grants write access to full app directory for updates
- Service restart tries multiple methods with logging for diagnostics

## [0.1.4] - 2026-04-03

### Added
- Auto-update checker queries GitHub for newer version tags
- Update install button in admin UI with confirmation dialog
- Version number displayed in admin UI System Status card
- Support for both git-based and file-copy production installs in auto-updater

### Changed
- Installer (`install.sh`) now uses `git clone` for auto-update support, with file-copy fallback
- Installer now includes `git` as a system dependency

## [0.1.3] - 2026-04-03

### Added
- Software version number displayed in HTML dashboard and PDF report footers
- CLAUDE.md with full project context for session continuity

## [0.1.2] - 2026-04-03

### Fixed
- Pending invite parsing — invites use `item.invite` not `item.member` structure

## [0.1.1] - 2026-04-03

### Added
- Initial release
- Claude.ai usage dashboard with automated email reports
- Direct HTTP API scraping (no browser/Playwright needed)
- Interactive HTML dashboard with Chart.js visualizations
- PDF report generation with ReportLab and matplotlib
- Scheduled email delivery (Mon-Thu + Friday with separate recipient lists)
- Flask web admin UI for configuration and manual sends
- Claude Code analytics (sessions, lines accepted, commits, PRs, cost)
- Fernet encryption for SMTP credentials at rest
- APScheduler cron-based scheduling with live rescheduling
- Automatic fallback to cached data when API calls fail
- Ubuntu VM installer with systemd service
