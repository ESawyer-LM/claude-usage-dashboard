# Changelog

All notable changes to the Claude Usage Dashboard will be documented in this file.

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
