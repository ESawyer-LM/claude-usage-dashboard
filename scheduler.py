"""
APScheduler-based job scheduler for automated report generation.
Supports multiple named schedule sets with flexible recurrence patterns
(daily, weekly, biweekly, monthly).  Live rescheduling from the admin UI.
"""

import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

import config
import emailer
import html_generator
import pdf_generator
import scraper

logger = config.get_logger()

# Module-level scheduler reference for cross-thread access from admin.py
_scheduler: BlockingScheduler | None = None

# Prefix used for all schedule-related APScheduler job IDs
_JOB_PREFIX = "schedule_"
_REPORT_JOB_PREFIX = "report_"


# ---------------------------------------------------------------------------
# Trigger builder
# ---------------------------------------------------------------------------

def _build_trigger(schedule: dict, tz_str: str):
    """Build an APScheduler trigger from a schedule config dict."""
    rtype = schedule.get("recurrence_type", "weekly")
    hour = schedule["time"]["hour"]
    minute = schedule["time"]["minute"]

    if rtype == "weekdays":
        return CronTrigger(
            day_of_week="mon-fri", hour=hour, minute=minute, timezone=tz_str,
        )

    if rtype == "every_day":
        return CronTrigger(hour=hour, minute=minute, timezone=tz_str)

    if rtype in ("weekly", "biweekly"):
        # biweekly fires weekly; skip logic is handled in run_report_job()
        days = ",".join(schedule.get("days_of_week", ["mon"]))
        return CronTrigger(
            day_of_week=days, hour=hour, minute=minute, timezone=tz_str,
        )

    if rtype == "monthly":
        day = schedule.get("month_day", 1)
        day_str = "last" if day == "last" else str(day)
        return CronTrigger(
            day=day_str, hour=hour, minute=minute, timezone=tz_str,
        )

    # Fallback: treat as weekly on Monday
    logger.warning(f"Unknown recurrence_type '{rtype}', defaulting to weekly Mon")
    return CronTrigger(
        day_of_week="mon", hour=hour, minute=minute, timezone=tz_str,
    )


# ---------------------------------------------------------------------------
# Report execution
# ---------------------------------------------------------------------------

def run_report_job(schedule_id: str, force: bool = False):
    """Execute the full pipeline for a specific schedule set.

    Reads the schedule config from settings.json at execution time to pick
    up any changes made via the admin UI since the job was registered.

    When force=True (Send Now), the enabled check and biweekly skip are
    bypassed so the report sends regardless of schedule state.
    """
    logger.info(f"Starting report job for schedule '{schedule_id}' (force={force})")
    settings = config.load_settings()

    # Find the schedule entry
    schedule = None
    for s in settings.get("schedules", []):
        if s["id"] == schedule_id:
            schedule = s
            break

    if schedule is None:
        logger.warning(f"Schedule '{schedule_id}' not found in settings, skipping")
        return

    if not force and not schedule.get("enabled", True):
        logger.info(f"Schedule '{schedule['name']}' is disabled, skipping")
        return

    # Biweekly skip: if last_sent is within 10 days, skip this run
    if not force and schedule.get("recurrence_type") == "biweekly" and schedule.get("last_sent"):
        try:
            last = datetime.fromisoformat(schedule["last_sent"])
            if datetime.now() - last < timedelta(days=10):
                logger.info(
                    f"Schedule '{schedule['name']}' is biweekly and last sent "
                    f"{schedule['last_sent']}, skipping this week"
                )
                return
        except (ValueError, TypeError):
            pass  # invalid date — proceed with send

    recipients = schedule.get("recipients", [])
    if not recipients:
        logger.warning(f"Schedule '{schedule['name']}' has no recipients, skipping email")

    report_type = schedule.get("report_type", config.DEFAULT_REPORT_TYPE)

    # Check if this is a custom report (report_type starts with "custom:")
    is_custom = report_type.startswith("custom:")

    try:
        # 1. Scrape data (use fresh cache if available from recent UI-triggered scrape)
        logger.info("Step 1/4: Scraping data...")
        data = None
        if force:
            from admin import get_cached_data_if_fresh
            data = get_cached_data_if_fresh()
            if data:
                logger.info("Using fresh cached data (scraped <60s ago)")
        if not data:
            data = scraper.scrape()

        if is_custom:
            # Custom report pipeline
            custom_report_id = report_type.split(":", 1)[1]
            import report_storage
            from report_pdf_generator import generate_report_pdf

            report_config = report_storage.get_report(custom_report_id)
            if not report_config:
                logger.error(f"Custom report '{custom_report_id}' not found")
                return

            logger.info(f"Step 2/4: Generating custom report '{report_config.get('title')}'...")
            # Skip HTML for custom reports (no standard HTML to save)

            logger.info("Step 3/4: Generating PDF report...")
            pdf_path = generate_report_pdf(data, report_config)

            if recipients:
                logger.info(f"Step 4/4: Sending email to {len(recipients)} recipients...")
                emailer.send_report(pdf_path, data, recipients, report_type="custom")
                logger.info(f"Custom report emailed to {recipients}")
            else:
                logger.info("Step 4/4: Skipped (no recipients)")
        else:
            # Standard report pipeline
            # 2. Generate HTML
            logger.info("Step 2/4: Generating HTML dashboard...")
            html_generator.save_html(data, report_type=report_type)

            # 3. Generate PDF
            logger.info("Step 3/4: Generating PDF report...")
            pdf_path = pdf_generator.generate_pdf(data, report_type=report_type)

            # 4. Email
            if recipients:
                logger.info(f"Step 4/4: Sending email to {len(recipients)} recipients...")
                emailer.send_report(pdf_path, data, recipients, report_type=report_type)
                logger.info(f"Report emailed to {recipients}")
            else:
                logger.info("Step 4/4: Skipped (no recipients)")

        # Update last run status (global)
        now_iso = datetime.now().isoformat()
        settings["last_run"] = now_iso
        settings["last_status"] = "success"
        if recipients:
            settings["last_email_sent"] = now_iso

        # Update per-schedule last_sent
        for s in settings.get("schedules", []):
            if s["id"] == schedule_id and recipients:
                s["last_sent"] = now_iso
                break

        config.save_settings(settings)
        logger.info(f"Report job completed for schedule '{schedule['name']}'")

    except Exception as e:
        logger.error(f"Report job failed for schedule '{schedule_id}': {e}")
        logger.error(traceback.format_exc())

        settings["last_run"] = datetime.now().isoformat()
        settings["last_status"] = f"error: {e}"
        config.save_settings(settings)


def run_test_report(recipient: str, report_type: str = None):
    """Run a test report to a single recipient."""
    if report_type is None:
        report_type = config.DEFAULT_REPORT_TYPE
    logger.info(f"Running test report for {recipient} (report_type={report_type})")
    settings = config.load_settings()

    try:
        data = scraper.scrape()
        html_generator.save_html(data, report_type=report_type)
        pdf_path = pdf_generator.generate_pdf(data, report_type=report_type)
        emailer.send_report(pdf_path, data, [recipient], is_test=True, report_type=report_type)

        settings["last_run"] = datetime.now().isoformat()
        settings["last_status"] = f"test sent to {recipient}"
        config.save_settings(settings)

        logger.info(f"Test report sent to {recipient}")
        return True, "Test report sent successfully"
    except Exception as e:
        logger.error(f"Test report failed: {e}")
        settings["last_run"] = datetime.now().isoformat()
        settings["last_status"] = f"test failed: {e}"
        config.save_settings(settings)
        return False, str(e)


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------

def create_scheduler() -> BlockingScheduler:
    """Create and configure the APScheduler with jobs for all schedules."""
    global _scheduler

    settings = config.load_settings()
    tz_str = settings.get("timezone", "America/Chicago")
    sched = BlockingScheduler()

    for schedule in settings.get("schedules", []):
        try:
            trigger = _build_trigger(schedule, tz_str)
            sched.add_job(
                run_report_job,
                trigger,
                id=f"{_JOB_PREFIX}{schedule['id']}",
                kwargs={"schedule_id": schedule["id"]},
                replace_existing=True,
                name=schedule.get("name", "Unnamed Schedule"),
            )
        except Exception as e:
            logger.error(f"Failed to create job for schedule '{schedule['id']}': {e}")

    _scheduler = sched

    # Also register custom report schedules
    try:
        sync_report_jobs()
    except Exception as e:
        logger.error(f"Failed to sync report jobs at startup: {e}")

    return sched


def sync_jobs(settings: dict = None):
    """Remove all schedule jobs and re-add from settings.

    Thread-safe — called from the Flask admin thread after schedule CRUD.
    Simpler and safer than trying to diff jobs.
    """
    global _scheduler
    if _scheduler is None:
        logger.warning("Cannot sync jobs: scheduler not initialized")
        return

    if settings is None:
        settings = config.load_settings()

    tz_str = settings.get("timezone", "America/Chicago")

    # Remove all existing schedule jobs
    for job in _scheduler.get_jobs():
        if job.id.startswith(_JOB_PREFIX):
            _scheduler.remove_job(job.id)

    # Re-add from current settings
    for schedule in settings.get("schedules", []):
        try:
            trigger = _build_trigger(schedule, tz_str)
            _scheduler.add_job(
                run_report_job,
                trigger,
                id=f"{_JOB_PREFIX}{schedule['id']}",
                kwargs={"schedule_id": schedule["id"]},
                replace_existing=True,
                name=schedule.get("name", "Unnamed Schedule"),
            )
        except Exception as e:
            logger.error(f"Failed to sync job for schedule '{schedule['id']}': {e}")

    names = [s.get("name", s["id"]) for s in settings.get("schedules", [])]
    logger.info(f"Synced {len(names)} schedule(s): {', '.join(names)} ({tz_str})")


# Keep reschedule() as an alias for backward compat with admin.py
reschedule = sync_jobs


# ---------------------------------------------------------------------------
# Custom report scheduling
# ---------------------------------------------------------------------------
def sync_report_jobs():
    """Sync APScheduler jobs for custom reports with enabled schedules."""
    global _scheduler
    if _scheduler is None:
        logger.warning("Cannot sync report jobs: scheduler not initialized")
        return

    import report_storage

    # Remove existing report jobs
    for job in _scheduler.get_jobs():
        if job.id.startswith(_REPORT_JOB_PREFIX):
            _scheduler.remove_job(job.id)

    # Load reports and add jobs for enabled schedules
    data = report_storage.load_reports()
    settings = config.load_settings()
    tz_str = settings.get("timezone", "America/Chicago")
    count = 0

    for report in data.get("reports", []):
        schedule = report.get("schedule", {})
        if not schedule.get("enabled"):
            continue
        cron = schedule.get("cron", {})
        try:
            trigger = CronTrigger(
                day_of_week=cron.get("day_of_week", "fri"),
                hour=cron.get("hour", 8),
                minute=cron.get("minute", 0),
                timezone=tz_str,
            )
            _scheduler.add_job(
                run_custom_report_job,
                trigger,
                id=f"{_REPORT_JOB_PREFIX}{report['id']}",
                kwargs={"report_id": report["id"]},
                replace_existing=True,
                name=f"Report: {report.get('title', 'Untitled')}",
            )
            count += 1
        except Exception as e:
            logger.error(f"Failed to schedule report '{report.get('title')}': {e}")

    logger.info(f"Synced {count} custom report schedule(s)")


def run_custom_report_job(report_id: str):
    """Run a custom report: scrape -> generate PDF -> email."""
    import report_storage
    from report_pdf_generator import generate_report_pdf

    report = report_storage.get_report(report_id)
    if not report:
        logger.error(f"Custom report job: report {report_id} not found")
        return

    schedule = report.get("schedule", {})
    recipients = schedule.get("recipients", [])
    if not recipients:
        logger.warning(f"Custom report '{report.get('title')}': no recipients, skipping")
        return

    logger.info(f"Running custom report '{report.get('title')}'...")

    # Scrape fresh data, fall back to cache
    try:
        data = scraper.scrape()
    except Exception as e:
        logger.warning(f"Scrape failed for custom report, using cache: {e}")
        import json
        import os
        if os.path.exists(config.CACHE_FILE):
            with open(config.CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["from_cache"] = True
            data["cache_reason"] = "Scrape failed during scheduled report"
        else:
            logger.error("No cached data available for custom report")
            return

    # Generate PDF
    try:
        pdf_path = generate_report_pdf(data, report)
    except Exception as e:
        logger.error(f"PDF generation failed for custom report: {e}")
        return

    # Email
    try:
        emailer.send_report(pdf_path, data, recipients, report_type="custom")
        logger.info(f"Custom report '{report.get('title')}' sent to {len(recipients)} recipient(s)")
    except Exception as e:
        logger.error(f"Email failed for custom report: {e}\n{traceback.format_exc()}")


def get_next_run_times() -> dict:
    """Get the next run times for all schedule jobs.

    Returns a dict mapping schedule_id -> ISO timestamp (or None).
    """
    global _scheduler
    result = {}

    if _scheduler is None:
        return result

    for job in _scheduler.get_jobs():
        if job.id.startswith(_JOB_PREFIX):
            sched_id = job.id[len(_JOB_PREFIX):]
            result[sched_id] = job.next_run_time.isoformat() if job.next_run_time else None

    return result
