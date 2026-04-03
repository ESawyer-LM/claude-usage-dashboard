# Changelog

All notable changes to the Claude Usage Dashboard will be documented in this file.

## [0.2.11] - 2026-04-03

### Added
- Collapsible card sections in admin UI (Cards 2-5) with chevron indicators and smooth animations
- localStorage persistence for card collapse state across page reloads
- Timezone setting prominently displayed in System Status card with dedicated save button
- Timezone validation rejects invalid IANA timezone strings with user-friendly error
- `_format_time()` helper for human-readable datetime formatting in configured timezone

### Changed
- Status times (last scrape, last email, next runs) now display in configured timezone (e.g. "Apr 3, 2026 7:00 AM CDT") instead of raw ISO strings
- Timezone input moved from Schedule & Recipients card to System Status card for better visibility
- `/api/status` endpoint now returns formatted times and includes timezone field

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
