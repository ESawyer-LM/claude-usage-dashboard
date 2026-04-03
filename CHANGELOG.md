# Changelog

All notable changes to the Claude Usage Dashboard will be documented in this file.

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
