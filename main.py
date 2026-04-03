"""
Entry point for Claude Usage Dashboard.
Supports: scheduler + admin (default), --now, --now --friday, --no-admin.
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
        "--friday", action="store_true",
        help="Use Friday recipient list (only with --now)",
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
        logger.info(f"Running one-shot report (friday={args.friday})")
        sched_module.run_report_job(is_friday=args.friday)
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
    wd_cron = settings.get("weekday_cron", {"hour": 7, "minute": 0})
    fri_cron = settings.get("friday_cron", {"hour": 7, "minute": 0})
    tz = settings.get("timezone", "America/Chicago")

    print()
    print(f"  Scheduler started (Mon-Thu {wd_cron['hour']:02d}:{wd_cron['minute']:02d}, "
          f"Fri {fri_cron['hour']:02d}:{fri_cron['minute']:02d} {tz})")
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
