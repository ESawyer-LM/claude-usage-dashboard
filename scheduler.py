"""
APScheduler-based job scheduler for automated report generation.
Two cron jobs: Mon-Thu and Friday, with live rescheduling support.
"""

import traceback
from datetime import datetime
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


def run_report_job(is_friday: bool = False):
    """
    Execute the full pipeline: scrape -> generate HTML -> generate PDF -> email.
    Reads recipients from settings.json at execution time.
    """
    logger.info(f"Starting report job (friday={is_friday})")
    settings = config.load_settings()

    # Check if this job is enabled
    if is_friday and not settings.get("friday_enabled", True):
        logger.info("Friday job is disabled, skipping.")
        return
    if not is_friday and not settings.get("weekday_enabled", True):
        logger.info("Weekday job is disabled, skipping.")
        return

    try:
        # 1. Scrape data
        logger.info("Step 1/4: Scraping data...")
        data = scraper.scrape()

        # 2. Generate HTML
        logger.info("Step 2/4: Generating HTML dashboard...")
        html_path = html_generator.save_html(data)

        # 3. Generate PDF
        logger.info("Step 3/4: Generating PDF report...")
        pdf_path = pdf_generator.generate_pdf(data)

        # 4. Email
        logger.info("Step 4/4: Sending email...")
        if is_friday:
            recipients = settings.get("friday_recipients", [])
        else:
            recipients = settings.get("weekday_recipients", [])

        if recipients:
            emailer.send_report(pdf_path, data, recipients)
            logger.info(f"Report emailed to {recipients}")
        else:
            logger.warning("No recipients configured, skipping email")

        # Update last run status
        settings["last_run"] = datetime.now().isoformat()
        settings["last_status"] = "success"
        settings["last_email_sent"] = datetime.now().isoformat()
        config.save_settings(settings)

        logger.info("Report job completed successfully")

    except Exception as e:
        logger.error(f"Report job failed: {e}")
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


def create_scheduler() -> BlockingScheduler:
    """Create and configure the APScheduler with cron jobs."""
    global _scheduler

    settings = config.load_settings()
    tz_str = settings.get("timezone", "America/Chicago")
    weekday_cron = settings.get("weekday_cron", {"hour": 7, "minute": 0})
    friday_cron = settings.get("friday_cron", {"hour": 7, "minute": 0})

    sched = BlockingScheduler()

    # Mon-Thu job
    sched.add_job(
        run_report_job,
        CronTrigger(
            day_of_week="mon-thu",
            hour=weekday_cron.get("hour", 7),
            minute=weekday_cron.get("minute", 0),
            timezone=tz_str,
        ),
        id="weekday_report",
        kwargs={"is_friday": False},
        replace_existing=True,
        name="Weekday Report (Mon-Thu)",
    )

    # Friday job
    sched.add_job(
        run_report_job,
        CronTrigger(
            day_of_week="fri",
            hour=friday_cron.get("hour", 7),
            minute=friday_cron.get("minute", 0),
            timezone=tz_str,
        ),
        id="friday_report",
        kwargs={"is_friday": True},
        replace_existing=True,
        name="Friday Report",
    )

    _scheduler = sched
    return sched


def reschedule(settings: dict = None):
    """
    Reschedule both jobs based on current settings.
    Thread-safe — called from the Flask admin thread.
    """
    global _scheduler
    if _scheduler is None:
        logger.warning("Cannot reschedule: scheduler not initialized")
        return

    if settings is None:
        settings = config.load_settings()

    tz_str = settings.get("timezone", "America/Chicago")
    weekday_cron = settings.get("weekday_cron", {"hour": 7, "minute": 0})
    friday_cron = settings.get("friday_cron", {"hour": 7, "minute": 0})

    _scheduler.reschedule_job(
        "weekday_report",
        trigger=CronTrigger(
            day_of_week="mon-thu",
            hour=weekday_cron.get("hour", 7),
            minute=weekday_cron.get("minute", 0),
            timezone=tz_str,
        ),
    )

    _scheduler.reschedule_job(
        "friday_report",
        trigger=CronTrigger(
            day_of_week="fri",
            hour=friday_cron.get("hour", 7),
            minute=friday_cron.get("minute", 0),
            timezone=tz_str,
        ),
    )

    logger.info(
        f"Rescheduled: Mon-Thu {weekday_cron['hour']:02d}:{weekday_cron['minute']:02d}, "
        f"Fri {friday_cron['hour']:02d}:{friday_cron['minute']:02d} ({tz_str})"
    )


def get_next_run_times() -> dict:
    """Get the next run times for both jobs."""
    global _scheduler
    result = {"weekday": None, "friday": None}

    if _scheduler is None:
        return result

    for job in _scheduler.get_jobs():
        if job.id == "weekday_report" and job.next_run_time:
            result["weekday"] = job.next_run_time.isoformat()
        elif job.id == "friday_report" and job.next_run_time:
            result["friday"] = job.next_run_time.isoformat()

    return result
