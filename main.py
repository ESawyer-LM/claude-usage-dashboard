"""
Entry point for Claude Usage Dashboard.
Supports: scheduler + admin (default), --now, --now --schedule <id>, --no-admin.
"""

import argparse
import signal
import sys
import threading

import config
import scheduler as sched_module
from admin import create_app

logger = config.get_logger()


def main():
    parser = argparse.ArgumentParser(description="Claude Usage Dashboard")
    parser.add_argument(
        "--now", action="store_true",
        help="Run the report pipeline once immediately, then exit",
    )
    parser.add_argument(
        "--schedule",
        help="Schedule ID to use for --now (sends to that schedule's recipients)",
    )
    parser.add_argument(
        "--no-admin", action="store_true",
        help="Start scheduler without the web admin UI",
    )
    args = parser.parse_args()

    # Ensure output dir and settings exist
    config.load_settings()

    logger.info("Claude Usage Dashboard starting...")

    # --now mode: run once and exit
    if args.now:
        if args.schedule:
            logger.info(f"Running one-shot report for schedule '{args.schedule}'")
            sched_module.run_report_job(schedule_id=args.schedule)
        else:
            # Run for all enabled schedules
            settings = config.load_settings()
            schedules = [s for s in settings.get("schedules", []) if s.get("enabled", True)]
            if not schedules:
                logger.warning("No enabled schedules found")
            for s in schedules:
                logger.info(f"Running one-shot report for schedule '{s['name']}'")
                sched_module.run_report_job(schedule_id=s["id"])
        logger.info("One-shot report complete, exiting.")
        sys.exit(0)

    # Create the scheduler
    sched = sched_module.create_scheduler()

    # Start Flask admin in a daemon thread (unless --no-admin)
    if not args.no_admin:
        app = create_app(scheduler_ref=sched)

        def start_admin():
            app.run(
                host="0.0.0.0",
                port=config.ADMIN_PORT,
                use_reloader=False,
                threaded=True,
            )

        admin_thread = threading.Thread(target=start_admin, daemon=True)
        admin_thread.start()
        logger.info(f"Admin UI running at http://0.0.0.0:{config.ADMIN_PORT}")

    # Graceful shutdown on SIGTERM
    def handle_sigterm(*_):
        logger.info("Received SIGTERM, shutting down...")
        sched.shutdown(wait=False)

    signal.signal(signal.SIGTERM, handle_sigterm)

    # Print startup banner
    settings = config.load_settings()
    tz = settings.get("timezone", "America/Chicago")
    schedules = settings.get("schedules", [])
    enabled = [s for s in schedules if s.get("enabled", True)]

    print()
    print(f"  Scheduler started — {len(enabled)} active schedule(s) ({tz})")
    for s in enabled:
        rtype = s.get("recurrence_type", "weekly")
        t = s.get("time", {})
        time_str = f"{t.get('hour', 7):02d}:{t.get('minute', 0):02d}"
        if rtype in ("weekly", "biweekly"):
            days = ",".join(s.get("days_of_week", []))
            print(f"    - {s['name']}: {rtype} ({days}) at {time_str}")
        elif rtype == "monthly":
            print(f"    - {s['name']}: monthly (day {s.get('month_day', 1)}) at {time_str}")
        else:
            print(f"    - {s['name']}: {rtype} at {time_str}")
    if not args.no_admin:
        print(f"  Admin UI running at http://localhost:{config.ADMIN_PORT}")
    print()

    # Start the blocking scheduler (blocks main thread)
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
