# Changelog

All notable changes to the Claude Usage Dashboard will be documented in this file.

## [0.11.6] - 2026-04-11

### Added
- Column visibility checkboxes in report builder for Member Directory — toggle Role, Chats MTD, Projects MTD, and Artifacts MTD columns via gear icon on the component card
- Options persist per-report and are respected by both HTML and PDF custom report renderers

## [0.11.5] - 2026-04-11

### Added
- "Chats MTD" column in Member Directory table (between Status and Projects MTD) across all report outputs

## [0.11.4] - 2026-04-11

### Fixed
- Fix missing cowork stage in report builder progress modal — `report_builder.py` has its own duplicate `SCRAPER_STAGES` list separate from `admin.py`

## [0.11.3] - 2026-04-11

### Fixed
- Fix progress modal missing "Fetching Cowork stats" in checklist (lost during squash merge)
- Remove Cowork top users chart, user table, and per-user ranking API calls — the `/analytics/users/rankings` endpoint silently ignores `product_filter=cowork` and returns identical unfiltered data
- Cowork section now correctly shows only the DAU chart (the one endpoint that respects the filter)

### Removed
- `cowork_top_users` and `cowork_user_table` report builder components (data was not Cowork-filtered)

## [0.11.1] - 2026-04-11

### Added
- **Cowork User Breakdown table** — per-user table with Chats, Projects, and Artifacts columns
  - Scraper fetches three ranking metrics with `product_filter=cowork` and merges per-user
  - Available in standard HTML/PDF reports and as `cowork_user_table` report builder component
  - All built-in templates updated to include the table
- Cowork stage added to admin UI progress modal checklist

## [0.11.0] - 2026-04-11

### Added
- **Claude Cowork analytics section** — new data collection and reporting for the `/analytics/cowork` page
  - Scraper fetches Cowork DAU timeseries and top users via `product_filter=cowork` on existing activity/rankings endpoints
  - Standard HTML/PDF reports include Cowork section with DAU line chart and Top Users bar chart (teal accent `#0891b2`)
  - Custom report builder gains two new components: `cowork_dau_chart` and `cowork_top_users`
  - New "Cowork Analytics" built-in template (`tpl-cowork`)
  - All existing templates (Full Dashboard, Activity Deep Dive, Expanded Report) updated to include Cowork components
  - Executive summary auto-narrative includes Cowork peak DAU when data is available

## [0.10.14] - 2026-04-11

### Added
- DEBUG-level logging throughout the application for troubleshooting:
  - **scraper.py**: API request/response details, response keys, pagination, data point counts, timeseries sample data, member counts, seat tier calculations
  - **emailer.py**: SMTP config, connection type, PDF attachment size, test connection details
  - **scheduler.py**: Trigger construction, schedule config, biweekly skip logic, job registration/removal counts
  - **html_generator.py**: Report type/sections, data shape summary, output size
  - **pdf_generator.py**: Report type/sections, flowable count, output path
  - **main.py**: Startup args, version, output directory
  - **config.py**: Logger initialization level and log file path

## [0.10.13] - 2026-04-11

### Added
- Log level setting in admin UI (DEBUG, INFO, WARNING, ERROR, CRITICAL) with immediate effect on running logger

## [0.10.12] - 2026-04-11

### Fixed
- Fix Lines Accepted chart showing all zeros — API data points use field `lines_of_code`, not `total_lines_accepted`
- Remove diagnostic logging added in v0.10.11

## [0.10.11] - 2026-04-11

### Fixed
- Add INFO-level logging for CC overview/timeseries keys and sample data points to diagnose zeroed Lines Accepted chart

## [0.10.10] - 2026-04-11

### Changed
- Replace Commits column with Avg Lines/Day in Claude Code User Breakdown table (per-user API has no commits field)
- Use `total_prs` field for PRs column instead of normalized `prs_with_cc`
- Remove unnecessary field normalization from scraper now that generators read API fields directly

## [0.10.9] - 2026-04-11

### Fixed
- Fix per-user API field logging to use INFO level (was DEBUG, which is silently dropped)
- Log full first user object to reveal actual API field names for commits diagnostics

## [0.10.8] - 2026-04-11

### Fixed
- Fix Claude Code User Breakdown table showing 0 for Commits and PRs columns by normalizing per-user API field names (`prs_with_cc` → `pull_requests_created`, `commits` → `commits_created`)
- Add debug logging of per-user API field names to aid future diagnostics

## [0.10.7] - 2026-04-07

### Fixed
- Fix get_next_run_times to compute next fire times from schedule config when scheduler jobs are unavailable (jobs pending before start())

## [0.10.6] - 2026-04-07

### Fixed
- Fix "Next Scheduled Run" always showing blank by handling None return from _format_time()
- Add next run time and per-schedule status updates to 30-second auto-refresh

### Removed
- Remove "LM" logo badge from all page headers (admin, login, dashboard, logs, reports) and PDF banner

## [0.10.5] - 2026-04-07

### Added
- Stat mini-boxes below daily_chats chart (total, peak, avg, team engagement)
- Stat mini-boxes below wau_trend chart (current WAU, WoW change, utilization rate, growth)
- WoW % change annotations between WAU chart x-axis data points
- Section headers with horizontal rule for Activity, WAU, Claude Code, and Member sections
- Tier column in member directory table (both report builder and standard dashboard)
- Premium badge inline with member name for premium-tier users
- Search, filter, and sort functionality for report builder member directory
- Graceful degradation for empty WAU data and missing total_seats

### Changed
- stats_row now matches PDF layout: Total Seats, Active Members, Pending Invites, Seat Tier with dynamic premium member sub-text
- Member directory uses compact two-line layout (name + email) instead of avatar circles
- Top users bar charts use 5-color red gradient instead of single color
- Table styling: alternating row shading, outer border, column width guidance
- CSS refactored to use custom properties (--red, --border, --muted, etc.)
- Projects/Artifacts cells use conditional formatting (red bold if > 0, gray if 0)

## [0.10.1] - 2026-04-07

### Fixed
- Fixed report not opening after scrape refresh completes (browser popup blocker triggered by setTimeout delay)

## [0.10.0] - 2026-04-07

### Added
- "Refresh data?" confirmation popup on all one-time report triggers (Send Now, Preview, Export PDF)
- Live progress bar with stage-by-stage tracking during data scrape
- Progress polling API endpoints (`/api/scrape/start`, `/api/scrape/progress/<job_id>`)
- Thread-safe `ProgressStore` for background scrape job tracking (`progress.py`)
- Fresh cache detection (60s window) to avoid double-scraping after UI-triggered refresh
- Scraper progress callback support via `progress_callback` parameter on `scrape()`

## [0.9.8] - 2026-04-06

### Changed
- Replaced native HTML5 drag-and-drop with pointer-event-based system for report builder section reordering
- Sections now smoothly slide out of the way during drag with CSS transform animations
- Dragged section shows as a floating clone with lifted appearance (shadow, red border, slight scale)
- Added touch device support with pointer capture for reliable tracking

## [0.9.7] - 2026-04-06

### Changed
- Stats row in report builder now shows Total Seats, Active Members, Pending Invites, Seat Tier (was DAU/WAU/Utilization)

### Added
- Claude Code Daily Sessions line chart component (cc_sessions_chart)
- Claude Code Daily Lines Accepted line chart component (cc_lines_chart)
- Claude Code Top Users bar charts component (cc_top_users)
- Claude Code User Breakdown table component (cc_user_table)
- All new components available in both HTML and PDF report builders
- Updated Full Dashboard, Activity Deep Dive, and Expanded Report templates

## [0.9.6] - 2026-04-06

### Fixed
- Fixed internal server error on PDF generation caused by KeepTogether wrapping in pie chart table cells

## [0.9.5] - 2026-04-06

### Fixed
- Pie charts in HTML now use dedicated pie-row class with reduced height to prevent oversized charts
- Restored 2-column grid for activity chart sections that was broken by pie chart changes
- PDF pie charts now render side-by-side in a horizontal row instead of vertically stacked

## [0.9.4] - 2026-04-06

### Fixed
- Account Type Distribution pie chart now correctly reads seat_tier field to show Premium vs Standard members

## [0.9.3] - 2026-04-06

### Changed
- Consolidated all pie chart changes: donut-to-pie conversion, Account Type Distribution chart, horizontal row layout, square PDF aspect ratio, and legacy key migration

## [0.9.2a] - 2026-04-06

### Fixed
- Pie charts in report builder now render in a horizontal row instead of a vertical column
- PDF pie charts now render as circles instead of squashed ellipses

## [0.9.2] - 2026-04-06

### Changed
- Updated report builder to use pie charts instead of donut charts
- Added Account Type Distribution pie chart to report builder component palette
- Added migration to auto-rename legacy donut component keys in existing custom reports
- Added backward-compatible aliases in HTML and PDF dispatchers for old donut keys

## [0.9.1] - 2026-04-06

### Changed
- Converted Member Status and Role Distribution donut charts to pie charts
- Added new Account Type Distribution pie chart showing tier breakdown
- Updated chart layout to 3-column horizontal row

## [0.9.0] - 2026-04-06

### Fixed
- Fixed version tag alignment — previous 0.8.x tags pointed to wrong commits causing update failures
- VERSION in code now matches the git tag for reliable auto-updates

## [0.8.8] - 2026-04-06

### Changed
- Version bump release

## [0.8.7] - 2026-04-06

### Changed
- Removed all hardcoded personal/company info from defaults and report templates
- Report headers, footers, and email subjects now use configurable "Organization Display Name" setting
- SMTP defaults cleared (no pre-filled email or host on clean install)
- Added "Organization Display Name" field to Claude.ai Connection settings card

## [0.8.6] - 2026-04-06

### Changed
- Version bump release

## [0.8.5] - 2026-04-06

### Added
- Warning when deleting a report that has linked email schedules
- Linked schedules are automatically deleted with the report

## [0.8.4] - 2026-04-06

### Fixed
- Disabled custom reports no longer appear in dashboard schedule Report Type dropdowns

## [0.8.3] - 2026-04-06

### Added
- Relative date ranges for scheduled reports (rolling window: last N days)
- Three date range modes: All available data, Rolling window, Fixed dates
- Per-component date range overrides support all three modes

### Changed
- Report builder schedule simplified to enable/disable toggle only; schedule details configured on Dashboard
- Date range UI replaced with radio button mode selector and preset day options

### Fixed
- Custom reports now appear in email schedule Report Type dropdowns
- Nav bar converted to dropdown menu on all pages including Reports and Builder

## [0.8.0] - 2026-04-06

### Added
- Report Builder feature: design, save, edit, clone, and delete custom reports built from individual dashboard components
- New Report Manager page (`/reports`) with card grid of saved reports and template dropdown
- Interactive Report Builder page with two-column layout: component palette with checkboxes and drag-and-drop reorderable canvas
- 12 available report components: stats row, status donut, role donut, daily chats, WAU trend, WAU stats tile, top users by projects/artifacts, Claude Code stats, member directory, executive summary, email highlights
- 4 built-in templates: Executive Summary, Full Dashboard, Activity Deep Dive, Team Overview
- HTML preview generation opens in new browser tab with full branding and Chart.js charts
- PDF export using existing ReportLab Flowables (HeaderBanner, StatCardRow, etc.)
- Auto-generated deterministic executive summary (3-5 sentences from data)
- Global and per-component date range filtering
- Report scheduling via APScheduler with configurable cron, timezone, and recipients
- Clone reports and create from templates
- "Reports" link added to nav bar on all admin pages
- New files: `report_storage.py`, `report_html_generator.py`, `report_pdf_generator.py`, `report_builder.py`

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
