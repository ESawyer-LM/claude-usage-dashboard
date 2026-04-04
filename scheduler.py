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

    try:
        # 1. Scrape data
        logger.info("Step 1/4: Scraping data...")
        data = scraper.scrape()

        # 2. Generate HTML
        logger.info("Step 2/4: Generating HTML dashboard...")
        html_generator.save_html(data)

        # 3. Generate PDF
        logger.info("Step 3/4: Generating PDF report...")
        pdf_path = pdf_generator.generate_pdf(data)

        # 4. Email
        if recipients:
            logger.info(f"Step 4/4: Sending email to {len(recipients)} recipients...")
            emailer.send_report(pdf_path, data, recipients)
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


def run_test_report(recipient: str):
    """Run a test report to a single recipient."""
    logger.info(f"Running test report for {recipient}")
    settings = config.load_settings()

    try:
        data = scraper.scrape()
        html_generator.save_html(data)
        pdf_path = pdf_generator.generate_pdf(data)
        emailer.send_report(pdf_path, data, [recipient], is_test=True)

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
