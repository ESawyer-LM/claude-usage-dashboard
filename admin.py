"""
Flask web admin UI for Claude Usage Dashboard.
Session-based password auth, settings management, and test report triggering.
"""

import os
import re
import threading
import time
from datetime import datetime, timedelta
from functools import wraps
from zoneinfo import ZoneInfo

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)

import config
import emailer
import scheduler as sched_module

logger = config.get_logger()


def _format_time(iso_str, tz_str="America/Chicago"):
    """Format an ISO datetime string for display in the given timezone."""
    if not iso_str or iso_str in ("Never", "N/A"):
        return iso_str
    try:
        tz = ZoneInfo(tz_str)
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        else:
            dt = dt.astimezone(tz)
        return dt.strftime("%b %-d, %Y %-I:%M %p %Z")
    except (ValueError, KeyError):
        return iso_str


def create_app(scheduler_ref=None):
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.secret_key = config.get_flask_secret()
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=15)

    # Store scheduler reference for rescheduling
    app.config["SCHEDULER_REF"] = scheduler_ref

    # Register Report Builder blueprint
    from report_builder import reports_bp
    app.register_blueprint(reports_bp)

    # Endpoints whose auto-refresh should NOT reset the inactivity timer
    _AUTO_REFRESH_PATHS = {"/api/status", "/logs"}

    @app.before_request
    def _refresh_session_activity():
        if session.get("authenticated") and request.path not in _AUTO_REFRESH_PATHS:
            session["last_active"] = time.time()

    # ---------------------------------------------------------------------------
    # Auth decorator
    # ---------------------------------------------------------------------------
    def login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("authenticated"):
                return redirect(url_for("login"))
            last = session.get("last_active")
            if last and time.time() - last > 15 * 60:
                session.clear()
                return redirect(url_for("login"))
            return f(*args, **kwargs)
        return decorated

    # ---------------------------------------------------------------------------
    # Routes
    # ---------------------------------------------------------------------------
    @app.route("/")
    def index():
        if session.get("authenticated"):
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            password = request.form.get("password", "")
            if password and password == config.ADMIN_PASSWORD:
                session.permanent = True
                session["authenticated"] = True
                session["last_active"] = time.time()
                return redirect(url_for("dashboard"))
            return render_template_string(LOGIN_TEMPLATE, error="Invalid password")
        return render_template_string(LOGIN_TEMPLATE, error=None)

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/api/keep-alive", methods=["POST"])
    @login_required
    def api_keep_alive():
        session["last_active"] = time.time()
        return jsonify({"ok": True})

    @app.route("/dashboard")
    @login_required
    def dashboard():
        settings = config.load_settings()
        # Mask the session cookie
        cookie = settings.get("session_cookie", "")
        if cookie and len(cookie) > 16:
            cookie_masked = cookie[:12] + "..." + cookie[-4:]
        elif cookie:
            cookie_masked = cookie[:4] + "..."
        else:
            cookie_masked = ""

        # Get status info, formatted in the configured timezone
        tz_str = settings.get("timezone", "America/Chicago")
        last_run = _format_time(settings.get("last_run", "Never"), tz_str)
        last_status = settings.get("last_status", "N/A")
        last_email_sent = _format_time(settings.get("last_email_sent", "Never"), tz_str)
        next_runs = sched_module.get_next_run_times()

        # Check for test result in query params
        test_result = request.args.get("test")
        test_msg = request.args.get("msg", "")

        # Build per-schedule next run times
        next_runs = sched_module.get_next_run_times()
        schedules_display = []
        for s in settings.get("schedules", []):
            s_copy = dict(s)
            s_copy["next_run"] = _format_time(next_runs.get(s["id"]), tz_str)
            s_copy["last_sent_fmt"] = _format_time(s.get("last_sent"), tz_str) if s.get("last_sent") else "Never"
            schedules_display.append(s_copy)

        # Load custom reports for the report type dropdown
        try:
            import report_storage
            custom_reports = report_storage.load_reports().get("reports", [])
        except Exception:
            custom_reports = []

        return render_template_string(
            DASHBOARD_TEMPLATE,
            settings=settings,
            cookie_masked=cookie_masked,
            cookie_set=bool(cookie),
            last_run=last_run,
            last_status=last_status,
            last_email_sent=last_email_sent,
            schedules=schedules_display,
            test_result=test_result,
            test_msg=test_msg,
            smtp_pass_set=bool(settings.get("smtp_pass", "")),
            report_types=config.REPORT_TYPES,
            default_report_type=config.DEFAULT_REPORT_TYPE,
            custom_reports=custom_reports,
        )

    @app.route("/logs")
    @login_required
    def logs_page():
        lines = []
        if os.path.exists(config.LOG_FILE):
            with open(config.LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
                lines = all_lines[-200:]
        return render_template_string(LOGS_TEMPLATE, log_lines="".join(lines))

    # ---------------------------------------------------------------------------
    # API Endpoints
    # ---------------------------------------------------------------------------
    @app.route("/api/save-cookie", methods=["POST"])
    @login_required
    def api_save_cookie():
        cookie = request.form.get("session_cookie", "").strip()
        org_id = request.form.get("org_id", "").strip()
        settings = config.load_settings()
        if cookie:
            settings["session_cookie"] = cookie
        if org_id:
            settings["org_id"] = org_id
        if not cookie and not org_id:
            return jsonify({"ok": False, "error": "Provide at least one value"}), 400
        config.save_settings(settings)
        logger.info("Connection settings updated via admin UI")
        parts = []
        if cookie:
            parts.append("sessionKey")
        if org_id:
            parts.append("org ID")
        return jsonify({"ok": True, "message": f"Saved: {', '.join(parts)}"})

    @app.route("/api/save-smtp", methods=["POST"])
    @login_required
    def api_save_smtp():
        settings = config.load_settings()

        smtp_host = request.form.get("smtp_host", "").strip()
        smtp_port = request.form.get("smtp_port", "587").strip()
        smtp_user = request.form.get("smtp_user", "").strip()
        smtp_pass = request.form.get("smtp_pass", "").strip()
        smtp_from_name = request.form.get("smtp_from_name", "Claude Dashboard").strip()

        # Validate
        if not smtp_host:
            return jsonify({"ok": False, "error": "SMTP host is required"}), 400
        try:
            smtp_port = int(smtp_port)
        except ValueError:
            return jsonify({"ok": False, "error": "Invalid port number"}), 400

        settings["smtp_host"] = smtp_host
        settings["smtp_port"] = smtp_port
        settings["smtp_user"] = smtp_user
        settings["smtp_from_name"] = smtp_from_name

        # Only update password if a new one was provided
        if smtp_pass:
            settings["smtp_pass"] = config.encrypt_value(smtp_pass)

        config.save_settings(settings)
        logger.info("SMTP settings updated via admin UI")
        return jsonify({"ok": True, "message": "SMTP settings saved"})

    @app.route("/api/test-smtp", methods=["POST"])
    @login_required
    def api_test_smtp():
        ok, msg = emailer.test_smtp_connection()
        return jsonify({"ok": ok, "message": msg})

    # ----- Schedule CRUD API -----

    _email_re = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    _valid_recurrences = {"weekdays", "every_day", "weekly", "biweekly", "monthly"}
    _valid_days = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}

    def _parse_schedule_form(form) -> tuple[dict | None, str | None]:
        """Parse and validate schedule form data.  Returns (data, error)."""
        import uuid as _uuid

        name = form.get("name", "").strip()
        if not name:
            return None, "Schedule name is required"

        recurrence_type = form.get("recurrence_type", "weekly")
        if recurrence_type not in _valid_recurrences:
            return None, f"Invalid recurrence type: {recurrence_type}"

        try:
            hour = int(form.get("hour", 7))
            minute = int(form.get("minute", 0))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except (ValueError, TypeError):
            return None, "Invalid time values"

        # Days of week (for weekly / biweekly)
        days_of_week = []
        if recurrence_type in ("weekly", "biweekly"):
            days_raw = form.getlist("days_of_week")
            days_of_week = [d for d in days_raw if d in _valid_days]
            if not days_of_week:
                return None, "Select at least one day of the week"

        # Month day (for monthly)
        month_day = 1
        if recurrence_type == "monthly":
            md = form.get("month_day", "1")
            if md == "last":
                month_day = "last"
            else:
                try:
                    month_day = int(md)
                    if not (1 <= month_day <= 28):
                        return None, "Day of month must be 1-28 or 'last'"
                except (ValueError, TypeError):
                    return None, "Invalid day of month"

        # Recipients
        raw = form.get("recipients", "").strip()
        recipients = [e.strip() for e in raw.split("\n") if e.strip()]
        for email in recipients:
            if not _email_re.match(email):
                return None, f"Invalid email: {email}"

        # Report type — standard types or custom:report_id
        report_type = form.get("report_type", config.DEFAULT_REPORT_TYPE)
        if report_type not in config.REPORT_TYPES and not report_type.startswith("custom:"):
            return None, f"Invalid report type: {report_type}"

        data = {
            "name": name,
            "recurrence_type": recurrence_type,
            "days_of_week": days_of_week,
            "month_day": month_day,
            "time": {"hour": hour, "minute": minute},
            "recipients": recipients,
            "report_type": report_type,
        }
        return data, None

    @app.route("/api/schedules", methods=["POST"])
    @login_required
    def api_create_schedule():
        import uuid as _uuid
        from datetime import datetime as _dt

        data, err = _parse_schedule_form(request.form)
        if err:
            return jsonify({"ok": False, "error": err}), 400

        settings = config.load_settings()
        schedule = {
            "id": _uuid.uuid4().hex[:8],
            "enabled": True,
            "created_at": _dt.now().isoformat(),
            "last_sent": None,
            **data,
        }
        settings.setdefault("schedules", []).append(schedule)
        config.save_settings(settings)

        try:
            sched_module.sync_jobs(settings)
        except Exception as e:
            logger.warning(f"Failed to sync jobs after create: {e}")

        logger.info(f"Schedule '{schedule['name']}' created (id={schedule['id']})")
        return jsonify({"ok": True, "message": f"Schedule '{schedule['name']}' created", "id": schedule["id"]})

    @app.route("/api/schedules/<schedule_id>", methods=["POST"])
    @login_required
    def api_update_schedule(schedule_id):
        data, err = _parse_schedule_form(request.form)
        if err:
            return jsonify({"ok": False, "error": err}), 400

        settings = config.load_settings()
        found = False
        for s in settings.get("schedules", []):
            if s["id"] == schedule_id:
                s.update(data)
                found = True
                break

        if not found:
            return jsonify({"ok": False, "error": "Schedule not found"}), 404

        config.save_settings(settings)

        try:
            sched_module.sync_jobs(settings)
        except Exception as e:
            logger.warning(f"Failed to sync jobs after update: {e}")

        logger.info(f"Schedule '{data['name']}' updated (id={schedule_id})")
        return jsonify({"ok": True, "message": f"Schedule '{data['name']}' saved"})

    @app.route("/api/schedules/<schedule_id>/toggle", methods=["POST"])
    @login_required
    def api_toggle_schedule(schedule_id):
        settings = config.load_settings()
        for s in settings.get("schedules", []):
            if s["id"] == schedule_id:
                s["enabled"] = not s.get("enabled", True)
                config.save_settings(settings)
                try:
                    sched_module.sync_jobs(settings)
                except Exception as e:
                    logger.warning(f"Failed to sync jobs after toggle: {e}")
                state = "enabled" if s["enabled"] else "disabled"
                logger.info(f"Schedule '{s['name']}' {state}")
                return jsonify({"ok": True, "enabled": s["enabled"], "message": f"Schedule {state}"})
        return jsonify({"ok": False, "error": "Schedule not found"}), 404

    @app.route("/api/schedules/<schedule_id>/delete", methods=["POST"])
    @login_required
    def api_delete_schedule(schedule_id):
        settings = config.load_settings()
        schedules = settings.get("schedules", [])
        name = None
        for i, s in enumerate(schedules):
            if s["id"] == schedule_id:
                name = s.get("name", schedule_id)
                schedules.pop(i)
                break
        if name is None:
            return jsonify({"ok": False, "error": "Schedule not found"}), 404

        settings["schedules"] = schedules
        config.save_settings(settings)

        try:
            sched_module.sync_jobs(settings)
        except Exception as e:
            logger.warning(f"Failed to sync jobs after delete: {e}")

        logger.info(f"Schedule '{name}' deleted (id={schedule_id})")
        return jsonify({"ok": True, "message": f"Schedule '{name}' deleted"})

    @app.route("/api/schedules/<schedule_id>/send-now", methods=["POST"])
    @login_required
    def api_send_now_schedule(schedule_id):
        settings = config.load_settings()
        schedule = None
        for s in settings.get("schedules", []):
            if s["id"] == schedule_id:
                schedule = s
                break
        if schedule is None:
            return jsonify({"ok": False, "error": "Schedule not found"}), 404

        name = schedule.get("name", schedule_id)

        def _run():
            try:
                sched_module.run_report_job(schedule_id=schedule_id, force=True)
                logger.info(f"Report sent via Send Now for schedule '{name}'")
            except Exception as e:
                logger.error(f"Send Now for schedule '{name}' failed: {e}")

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return jsonify({"ok": True, "message": f"Report queued for '{name}'"})

    @app.route("/api/reschedule", methods=["POST"])
    @login_required
    def api_reschedule():
        try:
            data = request.get_json()
            settings = config.load_settings()
            if "timezone" in data:
                tz_val = data["timezone"].strip()
                try:
                    ZoneInfo(tz_val)
                except (KeyError, Exception):
                    return jsonify({"ok": False, "error": f"Invalid timezone: {tz_val}"}), 400
                settings["timezone"] = tz_val
            config.save_settings(settings)
            sched_module.sync_jobs(settings)
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/send-test", methods=["POST"])
    @login_required
    def api_send_test():
        recipient = request.form.get("test_email", "").strip()
        if not recipient or "@" not in recipient:
            return jsonify({"ok": False, "error": "Valid email address required"}), 400

        report_type = request.form.get("report_type", config.DEFAULT_REPORT_TYPE)
        if report_type not in config.REPORT_TYPES and not report_type.startswith("custom:"):
            report_type = config.DEFAULT_REPORT_TYPE

        # Save last test recipient for re-use
        settings = config.load_settings()
        settings["last_test_recipient"] = recipient
        config.save_settings(settings)

        def _run():
            try:
                if report_type.startswith("custom:"):
                    # Custom report test send
                    import scraper
                    import report_storage
                    from report_pdf_generator import generate_report_pdf
                    custom_id = report_type.split(":", 1)[1]
                    report_config = report_storage.get_report(custom_id)
                    if not report_config:
                        logger.error(f"Custom report '{custom_id}' not found for test send")
                        return
                    data = scraper.scrape()
                    pdf_path = generate_report_pdf(data, report_config)
                    emailer.send_report(pdf_path, data, [recipient], is_test=True, report_type="custom")
                    logger.info(f"Custom test report sent to {recipient}")
                else:
                    ok, msg = sched_module.run_test_report(recipient, report_type=report_type)
                    logger.info(f"Test report result: ok={ok}, msg={msg}")
            except Exception as e:
                logger.error(f"Test report thread error: {e}")

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return jsonify({"ok": True, "message": f"Test report queued for {recipient}"})

    @app.route("/api/status")
    @login_required
    def api_status():
        settings = config.load_settings()
        tz_str = settings.get("timezone", "America/Chicago")
        next_runs = sched_module.get_next_run_times()

        # Build per-schedule status
        schedule_status = []
        for s in settings.get("schedules", []):
            schedule_status.append({
                "id": s["id"],
                "name": s.get("name", ""),
                "enabled": s.get("enabled", True),
                "next_run": _format_time(next_runs.get(s["id"]), tz_str),
                "last_sent": _format_time(s.get("last_sent"), tz_str) if s.get("last_sent") else "Never",
            })

        return jsonify({
            "last_run": _format_time(settings.get("last_run", "Never"), tz_str),
            "last_status": settings.get("last_status", "N/A"),
            "last_email_sent": _format_time(settings.get("last_email_sent", "Never"), tz_str),
            "schedules": schedule_status,
            "cookie_set": bool(settings.get("session_cookie")),
            "smtp_configured": bool(settings.get("smtp_pass")),
            "timezone": tz_str,
        })

    @app.route("/api/check-update")
    @login_required
    def api_check_update():
        result = config.check_for_updates()
        return jsonify(result)

    @app.route("/api/install-update", methods=["POST"])
    @login_required
    def api_install_update():
        version = request.form.get("version", "").strip()
        if not version:
            return jsonify({"ok": False, "message": "No version specified"}), 400
        logger.info(f"Admin initiated update to v{version}")
        result = config.install_update(version)
        if result["ok"]:
            logger.info(f"Update to v{version} successful — attempting restart")
            result["message"] = f"Updated to v{version}. Restarting service..."
            result["restarting"] = True
            timer = threading.Timer(1.5, config.restart_service)
            timer.daemon = True
            timer.start()
        else:
            logger.error(f"Update to v{version} failed: {result['message']}")
        return jsonify(result)

    @app.route("/api/change-password", methods=["POST"])
    @login_required
    def api_change_password():
        current = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        if not current or current != config.ADMIN_PASSWORD:
            return jsonify({"ok": False, "error": "Current password is incorrect"}), 400
        if not new_pw:
            return jsonify({"ok": False, "error": "New password cannot be empty"}), 400
        if new_pw != confirm:
            return jsonify({"ok": False, "error": "New passwords do not match"}), 400

        config.update_env_password(new_pw)
        session.clear()
        logger.info("Admin password changed via admin UI")
        return jsonify({"ok": True, "message": "Password changed. Please log in again."})

    return app


# ---------------------------------------------------------------------------
# Templates (inline Jinja2)
# ---------------------------------------------------------------------------

_BASE_CSS = """
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        background: #f5f5f5; color: #1a1a1a; line-height: 1.5;
    }
    .navbar {
        background: #C8102E; padding: 14px 24px; display: flex;
        align-items: center; justify-content: space-between; color: white;
    }
    .navbar-brand { font-size: 18px; font-weight: 700; display: flex; align-items: center; gap: 10px; text-decoration: none; color: white; }
    .navbar-brand .badge {
        width: 32px; height: 32px; border-radius: 50%; background: white;
        display: flex; align-items: center; justify-content: center;
        font-weight: 700; font-size: 12px; color: #C8102E;
    }
    .navbar a { color: white; text-decoration: none; font-size: 13px; opacity: 0.9; }
    .navbar a:hover { opacity: 1; }
    .container { max-width: 960px; margin: 0 auto; padding: 24px; }
    .card {
        background: white; border-radius: 12px; padding: 24px; margin-bottom: 20px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    }
    .card h2 { font-size: 16px; font-weight: 600; color: #111827; margin-bottom: 16px; border-bottom: 1px solid #f3f4f6; padding-bottom: 10px; }
    .form-group { margin-bottom: 14px; }
    .form-group label { display: block; font-size: 13px; font-weight: 500; color: #374151; margin-bottom: 4px; }
    .form-group input, .form-group textarea, .form-group select {
        width: 100%; padding: 8px 12px; border: 1px solid #d1d5db; border-radius: 8px;
        font-size: 14px; outline: none; font-family: inherit;
    }
    .form-group input:focus, .form-group textarea:focus, .form-group select:focus { border-color: #C8102E; }
    .form-group .hint { font-size: 11px; color: #9ca3af; margin-top: 2px; }
    .btn {
        display: inline-block; padding: 8px 20px; border-radius: 8px; font-size: 14px;
        font-weight: 500; cursor: pointer; border: none; text-decoration: none;
    }
    .btn-red { background: #C8102E; color: white; }
    .btn-red:hover { background: #a00d24; }
    .btn-gray { background: #f3f4f6; color: #374151; border: 1px solid #d1d5db; }
    .btn-gray:hover { background: #e5e7eb; }
    .status-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
    .status-item { }
    .status-label { font-size: 12px; color: #6b7280; }
    .status-value { font-size: 14px; font-weight: 500; color: #111827; }
    .alert {
        padding: 10px 16px; border-radius: 8px; margin-bottom: 16px; font-size: 13px;
    }
    .alert-success { background: #dcfce7; color: #15803d; border: 1px solid #86efac; }
    .alert-error { background: #fee2e2; color: #991b1b; border: 1px solid #fca5a5; }
    .inline-row { display: flex; gap: 12px; align-items: end; }
    .inline-row .form-group { flex: 1; }
    .toggle { position: relative; display: inline-block; width: 40px; height: 22px; cursor: pointer; }
    .toggle input { opacity: 0; width: 0; height: 0; }
    .toggle-slider {
        position: absolute; top: 0; left: 0; right: 0; bottom: 0;
        background: #d1d5db; border-radius: 22px; transition: 0.2s;
    }
    .toggle-slider:before {
        content: ""; position: absolute; width: 16px; height: 16px;
        left: 3px; bottom: 3px; background: white; border-radius: 50%; transition: 0.2s;
    }
    .toggle input:checked + .toggle-slider { background: #C8102E; }
    .toggle input:checked + .toggle-slider:before { transform: translateX(18px); }
    .card-header {
        cursor: pointer; display: flex; align-items: center; justify-content: space-between;
        user-select: none;
    }
    .card-header::after {
        content: '\\25BC'; font-size: 10px; color: #9ca3af; transition: transform 0.2s;
        flex-shrink: 0; margin-left: 8px;
    }
    .card.collapsed .card-header::after { transform: rotate(-90deg); }
    .card-body { overflow: hidden; max-height: 2000px; transition: max-height 0.3s ease; }
    .card.collapsed .card-body { max-height: 0; padding: 0; margin: 0; }
    .card-status { border-left: 3px solid #C8102E; }
    .section-divider { font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: #9ca3af; font-weight: 600; margin: 8px 0 16px; }
    .modal-overlay {
        display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(0,0,0,0.5); z-index: 1000; align-items: center; justify-content: center;
    }
    .modal-box {
        background: white; border-radius: 12px; padding: 24px; width: 400px;
        max-width: 90vw; box-shadow: 0 8px 30px rgba(0,0,0,0.2);
    }
    .modal-box h2 { font-size: 16px; font-weight: 600; margin-bottom: 16px; border-bottom: 1px solid #f3f4f6; padding-bottom: 10px; }
    .sched-header {
        display: flex; align-items: center; justify-content: space-between;
        padding: 12px 16px; cursor: pointer;
    }
    .sched-header-info { display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0; }
    .sched-chevron { font-size: 10px; color: #9ca3af; transition: transform 0.2s; }
    .sched-name { font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .status-badge { font-size: 11px; padding: 2px 8px; border-radius: 4px; flex-shrink: 0; }
    .status-badge-active { background: #dcfce7; color: #15803d; }
    .status-badge-paused { background: #f3f4f6; color: #6b7280; }
    .sched-meta { font-size: 12px; color: #6b7280; padding-left: 26px; margin-top: 2px; display: flex; gap: 12px; flex-wrap: wrap; }
    .sched-meta span { flex-shrink: 0; }
    .btn-sm { padding: 6px 16px; font-size: 13px; }
    .btn-danger-text { color: #991b1b; }
    .separator { border: none; border-top: 1px solid #f3f4f6; margin: 16px 0; }
    .result-span { font-size: 13px; }
    .btn-loading { pointer-events: none; opacity: 0.6; }
    .btn-loading::after {
        content: ''; display: inline-block; width: 14px; height: 14px;
        border: 2px solid currentColor; border-top-color: transparent;
        border-radius: 50%; animation: spin 0.6s linear infinite;
        margin-left: 8px; vertical-align: middle;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .toast-container {
        position: fixed; top: 16px; right: 16px; z-index: 2000;
        display: flex; flex-direction: column; gap: 8px; pointer-events: none;
    }
    .toast {
        padding: 10px 20px; border-radius: 8px; font-size: 13px; pointer-events: auto;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15); animation: toastIn 0.3s ease;
        max-width: 380px;
    }
    .toast-success { background: #dcfce7; color: #15803d; border: 1px solid #86efac; }
    .toast-error { background: #fee2e2; color: #991b1b; border: 1px solid #fca5a5; }
    @keyframes toastIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
    .tz-card { background: #fafbfc; }
    .nav-dropdown { position: relative; display: inline-block; }
    .nav-dropdown-btn {
        background: rgba(255,255,255,0.15); border: 1px solid rgba(255,255,255,0.3);
        color: white; padding: 6px 14px; border-radius: 6px; font-size: 13px;
        cursor: pointer; font-family: inherit; display: flex; align-items: center; gap: 6px;
    }
    .nav-dropdown-btn:hover { background: rgba(255,255,255,0.25); }
    .nav-dropdown-menu {
        display: none; position: absolute; right: 0; top: calc(100% + 6px);
        background: white; min-width: 180px; border-radius: 8px;
        box-shadow: 0 8px 24px rgba(0,0,0,0.18); z-index: 100; overflow: hidden;
        border: 1px solid #e5e7eb;
    }
    .nav-dropdown.open .nav-dropdown-menu { display: block; }
    .nav-dropdown-menu a, .nav-dropdown-menu button {
        display: block; width: 100%; padding: 10px 16px; font-size: 13px;
        color: #374151; text-decoration: none; text-align: left;
        border: none; background: none; cursor: pointer; font-family: inherit;
    }
    .nav-dropdown-menu a:hover, .nav-dropdown-menu button:hover { background: #f3f4f6; }
    .nav-dropdown-menu .nav-active-item { font-weight: 600; color: #C8102E; background: #fef2f2; }
    .nav-dropdown-divider { border-top: 1px solid #f3f4f6; margin: 4px 0; }
    @media (max-width: 640px) {
        .container { padding: 12px; }
        .inline-row { flex-direction: column; }
        .status-grid { grid-template-columns: 1fr; }
        .navbar { padding: 10px 14px; }
        .navbar-brand { font-size: 15px; }
        .card { padding: 16px; }
        .sched-meta { display: none; }
        .modal-box { width: auto; margin: 16px; }
    }
"""

LOGIN_TEMPLATE = """<!DOCTYPE html>
<html><head><meta name="viewport" content="width=device-width, initial-scale=1"><title>Login — Claude Dashboard Admin</title>
<style>""" + _BASE_CSS + """
    .login-box { max-width: 400px; margin: 80px auto; }
    .login-badge { width: 56px; height: 56px; border-radius: 50%; background: #C8102E; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 20px; color: white; margin: 0 auto 20px; }
</style></head><body>
<div class="navbar"><a class="navbar-brand" href="/login"><div class="badge">LM</div> Claude Dashboard Admin</a></div>
<div class="container"><div class="login-box"><div class="card" style="text-align:center;">
    <div class="login-badge">LM</div>
    <h2 style="border:none;padding:0;margin-bottom:4px;">Sign In</h2>
    <p style="color:#6b7280;font-size:13px;margin-bottom:16px;">Claude Usage Dashboard Administration</p>
    {% if error %}<div class="alert alert-error" style="text-align:left;">{{ error }}</div>{% endif %}
    <form method="POST" style="text-align:left;">
        <div class="form-group">
            <label>Password</label>
            <input type="password" name="password" autofocus required>
        </div>
        <button type="submit" class="btn btn-red" style="width:100%;">Sign In</button>
    </form>
    <p style="color:#9ca3af;font-size:11px;margin-top:16px;">v""" + config.VERSION + """</p>
</div></div></div></body></html>"""

DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html><head><meta name="viewport" content="width=device-width, initial-scale=1"><title>Claude Dashboard Admin</title>
<style>""" + _BASE_CSS + """</style></head><body>
<div class="navbar">
    <a class="navbar-brand" href="/dashboard"><div class="badge">LM</div> Claude Dashboard Admin</a>
    <div class="nav-dropdown" id="navDropdown">
        <button class="nav-dropdown-btn" onclick="this.parentElement.classList.toggle('open')">Menu &#9662;</button>
        <div class="nav-dropdown-menu">
            <a href="/dashboard" class="nav-active-item">Dashboard</a>
            <a href="/reports">Reports</a>
            <a href="/logs">Logs</a>
            <div class="nav-dropdown-divider"></div>
            <a href="#" onclick="document.getElementById('navDropdown').classList.remove('open');document.getElementById('pwModal').style.display='flex';setTimeout(function(){document.querySelector('#pwModal input').focus()},100);return false;">Change Password</a>
            <form method="POST" action="/logout"><button type="submit">Logout</button></form>
        </div>
    </div>
</div>
<!-- Password Change Modal -->
<div id="pwModal" class="modal-overlay">
    <div class="modal-box">
        <h2>Change Password</h2>
        <form id="pwForm">
            <div class="form-group">
                <label>Current Password</label>
                <input type="password" name="current_password" required>
            </div>
            <div class="form-group">
                <label>New Password</label>
                <input type="password" name="new_password" required>
            </div>
            <div class="form-group">
                <label>Confirm New Password</label>
                <input type="password" name="confirm_password" required>
            </div>
            <div style="display:flex;gap:12px;align-items:center;">
                <button type="submit" class="btn btn-red">Save</button>
                <button type="button" class="btn btn-gray" onclick="closePwModal()">Cancel</button>
                <span id="pwResult" class="result-span"></span>
            </div>
        </form>
    </div>
</div>

<div class="toast-container" id="toastContainer"></div>
<div class="container">

{% if test_result == 'sent' %}
<div class="alert alert-success">Test report queued successfully. Check your inbox.</div>
{% elif test_result == 'failed' %}
<div class="alert alert-error">Test report failed: {{ test_msg }}</div>
{% endif %}

<!-- Update Banner (hidden by default, shown by JS) -->
<div id="updateBanner" style="display:none; padding:12px 20px; border-radius:10px; margin-bottom:20px; background:#eff6ff; border:1px solid #93c5fd; color:#1e40af; font-size:14px; display:none; align-items:center; justify-content:space-between;">
    <div>
        <strong>Update available:</strong> v<span id="updateVersion"></span>
        <span style="color:#6b7280; margin-left:4px;">(current: v""" + config.VERSION + """)</span>
    </div>
    <div style="display:flex;gap:8px;align-items:center;">
        <span id="updateResult" class="result-span"></span>
        <button type="button" class="btn btn-red btn-sm" id="installUpdateBtn">Install Update</button>
    </div>
</div>

<!-- Card 1: Status -->
<div class="card card-status" id="statusCard">
    <h2>System Status</h2>
    <div class="status-grid">
        <div class="status-item">
            <div class="status-label">Last Scrape</div>
            <div class="status-value" id="lastRun">{{ last_run }}</div>
        </div>
        <div class="status-item">
            <div class="status-label">Last Email Sent</div>
            <div class="status-value" id="lastEmail">{{ last_email_sent }}</div>
        </div>
        <div class="status-item">
            <div class="status-label">Active Schedules</div>
            <div class="status-value">{{ schedules|selectattr('enabled')|list|length }} of {{ schedules|length }}</div>
        </div>
        <div class="status-item">
            <div class="status-label">Next Scheduled Run</div>
            <div class="status-value" id="nextRun">{% set enabled = schedules|selectattr('enabled')|selectattr('next_run', 'ne', 'N/A')|selectattr('next_run', 'ne', None)|list %}{% if enabled %}{{ enabled|sort(attribute='next_run')|first|attr('next_run') }}{% else %}N/A{% endif %}</div>
        </div>
        <div class="status-item">
            <div class="status-label">Last Status</div>
            <div class="status-value" id="lastStatus">{{ last_status }}</div>
        </div>
        <div class="status-item">
            <div class="status-label">Session Cookie</div>
            <div class="status-value" id="cookieStatus">{% if cookie_set %}&#10003; Set{% else %}<span style="color:#d97706;">&#9888; Not set</span>{% endif %}</div>
        </div>
        <div class="status-item">
            <div class="status-label">Version</div>
            <div class="status-value">v""" + config.VERSION + """</div>
        </div>
    </div>
</div>

<div class="section-divider">Configuration</div>

<!-- Card 2: Email Schedules -->
<div class="card" id="card-schedule">
    <h2 class="card-header" onclick="toggleCard('card-schedule')">Email Schedules</h2>
    <div class="card-body">

    {% for s in schedules %}
    <div class="schedule-card" id="sched-{{ s.id }}" style="border:1px solid #e5e7eb;border-radius:10px;margin-bottom:14px;{% if not s.enabled %}opacity:0.6;{% endif %}">
        <!-- Collapsed header — always visible -->
        <div class="sched-header" onclick="toggleSchedCard(event, '{{ s.id }}')">
            <div style="flex:1;min-width:0;">
                <div class="sched-header-info">
                    <span class="sched-chevron" id="chevron-{{ s.id }}">&#9660;</span>
                    <strong class="sched-name">{{ s.name }}</strong>
                    <span class="status-badge {{ 'status-badge-active' if s.enabled else 'status-badge-paused' }}">{{ 'Active' if s.enabled else 'Paused' }}</span>
                </div>
                <div class="sched-meta">
                    <span>{% if s.recurrence_type == 'weekly' %}{{ s.get('days_of_week', []) | join(', ') | title }}{% elif s.recurrence_type == 'biweekly' %}Every other {{ s.get('days_of_week', []) | join(', ') | title }}{% elif s.recurrence_type == 'monthly' %}Monthly (day {{ s.get('month_day', 1) }}){% else %}{{ s.recurrence_type | replace('_', ' ') | title }}{% endif %} at {{ '%02d' | format(s.time.hour) }}:{{ '%02d' | format(s.time.minute) }}</span>
                    <span>{% set rt = s.get('report_type', default_report_type) %}{% if rt.startswith('custom:') %}{% set cid = rt[7:] %}{% for cr in custom_reports %}{% if cr.id == cid %}{{ cr.title }}{% endif %}{% endfor %}{% else %}{{ report_types.get(rt, {}).get('name', 'Full Report') }}{% endif %}</span>
                    <span>{{ s.recipients | length }} recipient{{ 's' if s.recipients | length != 1 }}</span>
                </div>
            </div>
            <label class="toggle" style="flex-shrink:0;margin-left:12px;" onclick="event.stopPropagation();">
                <input type="checkbox" {{ 'checked' if s.enabled }} onchange="toggleSchedule('{{ s.id }}')">
                <span class="toggle-slider"></span>
            </label>
        </div>
        <!-- Expandable detail form — hidden by default -->
        <div class="sched-detail" id="detail-{{ s.id }}" style="display:none;padding:0 16px 16px 16px;border-top:1px solid #f3f4f6;">

        <form class="schedule-form" data-id="{{ s.id }}" data-mode="edit" style="margin-top:12px;">
            <div class="inline-row">
                <div class="form-group" style="flex:2;">
                    <label>Name</label>
                    <input type="text" name="name" value="{{ s.name }}" required>
                </div>
                <div class="form-group" style="flex:1;">
                    <label>Recurrence</label>
                    <select name="recurrence_type" onchange="toggleRecurrenceFields(this)">
                        <option value="weekdays" {{ 'selected' if s.recurrence_type == 'weekdays' }}>Weekdays (Mon-Fri)</option>
                        <option value="every_day" {{ 'selected' if s.recurrence_type == 'every_day' }}>Every Day</option>
                        <option value="weekly" {{ 'selected' if s.recurrence_type == 'weekly' }}>Weekly</option>
                        <option value="biweekly" {{ 'selected' if s.recurrence_type == 'biweekly' }}>Biweekly</option>
                        <option value="monthly" {{ 'selected' if s.recurrence_type == 'monthly' }}>Monthly</option>
                    </select>
                </div>
                <div class="form-group" style="flex:1;">
                    <label>Report Type</label>
                    <select name="report_type">
                        {% for rt_key, rt_val in report_types.items() %}
                        <option value="{{ rt_key }}" {{ 'selected' if s.get('report_type', default_report_type) == rt_key }}>{{ rt_val.name }}</option>
                        {% endfor %}
                        {% if custom_reports %}
                        <optgroup label="Custom Reports">
                        {% for cr in custom_reports %}
                        <option value="custom:{{ cr.id }}" {{ 'selected' if s.get('report_type') == 'custom:' + cr.id }}>{{ cr.title }}</option>
                        {% endfor %}
                        </optgroup>
                        {% endif %}
                    </select>
                </div>
            </div>
            <div class="days-row" style="margin-bottom:10px;{% if s.recurrence_type not in ('weekly', 'biweekly') %}display:none;{% endif %}">
                <label style="font-size:13px;font-weight:500;color:#374151;margin-bottom:4px;display:block;">Days</label>
                <div style="display:flex;gap:6px;flex-wrap:wrap;">
                    {% for d, dl in [('mon','Mon'),('tue','Tue'),('wed','Wed'),('thu','Thu'),('fri','Fri'),('sat','Sat'),('sun','Sun')] %}
                    <label style="font-size:13px;display:flex;align-items:center;gap:3px;cursor:pointer;padding:4px 8px;border:1px solid #d1d5db;border-radius:6px;{% if d in s.get('days_of_week', []) %}background:#C8102E;color:white;border-color:#C8102E;{% endif %}" class="day-chip">
                        <input type="checkbox" name="days_of_week" value="{{ d }}" {{ 'checked' if d in s.get('days_of_week', []) }} style="display:none;" onchange="this.parentElement.style.background=this.checked?'#C8102E':'';this.parentElement.style.color=this.checked?'white':'';this.parentElement.style.borderColor=this.checked?'#C8102E':'#d1d5db';">
                        {{ dl }}
                    </label>
                    {% endfor %}
                </div>
            </div>
            <div class="month-row" style="margin-bottom:10px;{% if s.recurrence_type != 'monthly' %}display:none;{% endif %}">
                <label style="font-size:13px;font-weight:500;color:#374151;margin-bottom:4px;display:block;">Day of Month</label>
                <select name="month_day" style="width:120px;">
                    {% for d in range(1, 29) %}
                    <option value="{{ d }}" {{ 'selected' if s.get('month_day') == d }}>{{ d }}</option>
                    {% endfor %}
                    <option value="last" {{ 'selected' if s.get('month_day') == 'last' }}>Last day</option>
                </select>
            </div>
            <div class="inline-row">
                <div class="form-group" style="flex:0 0 auto;">
                    <label>Time</label>
                    <div style="display:flex;gap:6px;align-items:center;">
                        <select name="hour" style="width:70px;">
                            {% for h in range(24) %}
                            <option value="{{ h }}" {{ 'selected' if h == s.time.hour }}>{{ '%02d'|format(h) }}</option>
                            {% endfor %}
                        </select>
                        <span>:</span>
                        <select name="minute" style="width:70px;">
                            {% for m in [0, 15, 30, 45] %}
                            <option value="{{ m }}" {{ 'selected' if m == s.time.minute }}>{{ '%02d'|format(m) }}</option>
                            {% endfor %}
                        </select>
                    </div>
                </div>
                <div class="form-group" style="flex:1;">
                    <label>Recipients (one per line)</label>
                    <textarea name="recipients" rows="3">{{ s.recipients | join('\n') }}</textarea>
                </div>
            </div>
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                <button type="submit" class="btn btn-red btn-sm">Save</button>
                <button type="button" class="btn btn-gray btn-sm" onclick="sendNowSchedule('{{ s.id }}', this)">Send Now</button>
                <button type="button" class="btn btn-gray btn-sm btn-danger-text btn-delete" onclick="deleteSchedule('{{ s.id }}', '{{ s.name | e }}')">Delete</button>
                <span class="sched-result result-span"></span>
                <span style="font-size:11px;color:#9ca3af;margin-left:auto;">Next: {{ s.next_run or 'N/A' }} &middot; Last: {{ s.last_sent_fmt }}</span>
            </div>
        </form>
        </div>
    </div>
    {% endfor %}

    {% if not schedules %}
    <div style="text-align:center;padding:20px;color:#6b7280;font-size:14px;" id="noSchedulesMsg">
        No schedules configured. Click "Add Schedule" to create one.
    </div>
    {% endif %}

    <hr class="separator">

    <!-- Add new schedule (collapsible) -->
    <div id="newScheduleWrapper" style="display:none;border:2px dashed #d1d5db;border-radius:10px;padding:16px;margin-bottom:12px;">
        <strong style="font-size:14px;display:block;margin-bottom:10px;">New Schedule</strong>
        <form id="newScheduleForm" data-mode="create">
            <div class="inline-row">
                <div class="form-group" style="flex:2;">
                    <label>Name</label>
                    <input type="text" name="name" placeholder="e.g. Weekly Leadership Report" required>
                </div>
                <div class="form-group" style="flex:1;">
                    <label>Recurrence</label>
                    <select name="recurrence_type" onchange="toggleRecurrenceFields(this)">
                        <option value="weekdays">Weekdays (Mon-Fri)</option>
                        <option value="every_day">Every Day</option>
                        <option value="weekly" selected>Weekly</option>
                        <option value="biweekly">Biweekly</option>
                        <option value="monthly">Monthly</option>
                    </select>
                </div>
                <div class="form-group" style="flex:1;">
                    <label>Report Type</label>
                    <select name="report_type">
                        {% for rt_key, rt_val in report_types.items() %}
                        <option value="{{ rt_key }}" {{ 'selected' if rt_key == default_report_type }}>{{ rt_val.name }}</option>
                        {% endfor %}
                        {% if custom_reports %}
                        <optgroup label="Custom Reports">
                        {% for cr in custom_reports %}
                        <option value="custom:{{ cr.id }}">{{ cr.title }}</option>
                        {% endfor %}
                        </optgroup>
                        {% endif %}
                    </select>
                </div>
            </div>
            <div class="days-row" style="margin-bottom:10px;">
                <label style="font-size:13px;font-weight:500;color:#374151;margin-bottom:4px;display:block;">Days</label>
                <div style="display:flex;gap:6px;flex-wrap:wrap;">
                    {% for d, dl in [('mon','Mon'),('tue','Tue'),('wed','Wed'),('thu','Thu'),('fri','Fri'),('sat','Sat'),('sun','Sun')] %}
                    <label style="font-size:13px;display:flex;align-items:center;gap:3px;cursor:pointer;padding:4px 8px;border:1px solid #d1d5db;border-radius:6px;" class="day-chip">
                        <input type="checkbox" name="days_of_week" value="{{ d }}" style="display:none;" onchange="this.parentElement.style.background=this.checked?'#C8102E':'';this.parentElement.style.color=this.checked?'white':'';this.parentElement.style.borderColor=this.checked?'#C8102E':'#d1d5db';">
                        {{ dl }}
                    </label>
                    {% endfor %}
                </div>
            </div>
            <div class="month-row" style="margin-bottom:10px;display:none;">
                <label style="font-size:13px;font-weight:500;color:#374151;margin-bottom:4px;display:block;">Day of Month</label>
                <select name="month_day" style="width:120px;">
                    {% for d in range(1, 29) %}
                    <option value="{{ d }}">{{ d }}</option>
                    {% endfor %}
                    <option value="last">Last day</option>
                </select>
            </div>
            <div class="inline-row">
                <div class="form-group" style="flex:0 0 auto;">
                    <label>Time</label>
                    <div style="display:flex;gap:6px;align-items:center;">
                        <select name="hour" style="width:70px;">
                            {% for h in range(24) %}
                            <option value="{{ h }}" {{ 'selected' if h == 7 }}>{{ '%02d'|format(h) }}</option>
                            {% endfor %}
                        </select>
                        <span>:</span>
                        <select name="minute" style="width:70px;">
                            {% for m in [0, 15, 30, 45] %}
                            <option value="{{ m }}">{{ '%02d'|format(m) }}</option>
                            {% endfor %}
                        </select>
                    </div>
                </div>
                <div class="form-group" style="flex:1;">
                    <label>Recipients (one per line)</label>
                    <textarea name="recipients" rows="3" placeholder="user@example.com"></textarea>
                </div>
            </div>
            <div style="display:flex;gap:8px;align-items:center;">
                <button type="submit" class="btn btn-red btn-sm">Create Schedule</button>
                <button type="button" class="btn btn-gray btn-sm" onclick="document.getElementById('newScheduleWrapper').style.display='none';document.getElementById('addScheduleBtn').style.display='';">Cancel</button>
                <span id="newSchedResult" class="result-span"></span>
            </div>
        </form>
    </div>
    <button type="button" class="btn btn-red" onclick="document.getElementById('newScheduleWrapper').style.display='block';this.style.display='none';" id="addScheduleBtn">+ Add Schedule</button>

    </div>
</div>

<!-- Card 3: Claude.ai Connection -->
<div class="card" id="card-connection">
    <h2 class="card-header" onclick="toggleCard('card-connection')">Claude.ai Connection</h2>
    <div class="card-body">
    {% if cookie_set %}
    <div style="font-size:13px;color:#16a34a;margin-bottom:12px;">
        &#10003; Cookie set: <code>{{ cookie_masked }}</code>
    </div>
    {% else %}
    <div style="font-size:13px;color:#d97706;margin-bottom:12px;">
        &#9888; No session cookie — data collection will not work.
    </div>
    {% endif %}
    <form id="cookieForm">
        <div class="form-group">
            <label>Organization ID <span style="color:#C8102E;">*</span></label>
            <input type="text" name="org_id" value="{{ settings.get('org_id', '') }}" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" style="font-family:monospace;">
            <div class="hint">DevTools (F12) → Network → filter "members" → copy the UUID from the URL path.</div>
        </div>
        <div class="form-group">
            <label>sessionKey <span style="color:#C8102E;">*</span></label>
            <input type="password" name="session_cookie" placeholder="Paste your sessionKey from claude.ai..." style="font-family:monospace;">
            <div class="hint">DevTools (F12) → Application → Cookies → claude.ai → sessionKey. Lasts ~30 days.</div>
        </div>
        <button type="submit" class="btn btn-red">Save Connection</button>
        <span id="cookieResult" class="result-span" style="margin-left:12px;"></span>
    </form>
    </div>
</div>

<!-- Card 4: SMTP Settings -->
<div class="card" id="card-smtp">
    <h2 class="card-header" onclick="toggleCard('card-smtp')">SMTP / Email Settings</h2>
    <div class="card-body">
    <form id="smtpForm">
        <div class="inline-row">
            <div class="form-group" style="flex:3;">
                <label>SMTP Host</label>
                <input type="text" name="smtp_host" value="{{ settings.smtp_host }}">
            </div>
            <div class="form-group" style="flex:1;">
                <label>Port</label>
                <input type="number" name="smtp_port" value="{{ settings.smtp_port }}">
                <div class="hint">587 = STARTTLS, 465 = SSL, 25 = none</div>
            </div>
        </div>
        <div class="form-group">
            <label>SMTP Username</label>
            <input type="text" name="smtp_user" value="{{ settings.smtp_user }}">
        </div>
        <div class="form-group">
            <label>SMTP Password</label>
            <input type="password" name="smtp_pass" placeholder="{% if smtp_pass_set %}&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;  (saved){% else %}Enter SMTP password{% endif %}">
            <div class="hint">Leave blank to keep current password</div>
        </div>
        <div class="form-group">
            <label>From Name</label>
            <input type="text" name="smtp_from_name" value="{{ settings.smtp_from_name }}">
        </div>
        <div style="display:flex;gap:12px;align-items:center;">
            <button type="submit" class="btn btn-red">Save SMTP Settings</button>
            <button type="button" class="btn btn-gray" id="testSmtpBtn">Test Connection</button>
            <span id="smtpResult" class="result-span"></span>
        </div>
    </form>
    </div>
</div>

<!-- Card 5: Send Test Report -->
<div class="card" id="card-test">
    <h2 class="card-header" onclick="toggleCard('card-test')">Send Test Report</h2>
    <div class="card-body">
    <form id="testForm">
        <div class="inline-row">
            <div class="form-group" style="flex:2;">
                <label>Test Recipient Email</label>
                <input type="email" name="test_email" value="{{ settings.get('last_test_recipient', '') or settings.smtp_user }}" required>
            </div>
            <div class="form-group" style="flex:1;">
                <label>Report Type</label>
                <select name="report_type">
                    {% for rt_key, rt_val in report_types.items() %}
                    <option value="{{ rt_key }}" {{ 'selected' if rt_key == default_report_type }}>{{ rt_val.name }}</option>
                    {% endfor %}
                    {% if custom_reports %}
                    <optgroup label="Custom Reports">
                    {% for cr in custom_reports %}
                    <option value="custom:{{ cr.id }}">{{ cr.title }}</option>
                    {% endfor %}
                    </optgroup>
                    {% endif %}
                </select>
            </div>
        </div>
        <button type="submit" class="btn btn-red">Send Now</button>
        <span id="testResult" class="result-span" style="margin-left:12px;"></span>
    </form>
    </div>
</div>

<!-- Timezone Preferences -->
<div class="card tz-card">
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
        <span class="status-label" style="white-space:nowrap;">Timezone</span>
        <select id="timezoneInput" style="width:280px;padding:6px 10px;border:1px solid #d1d5db;border-radius:6px;font-size:14px;">
            {% set tz_options = [
                ("US/Eastern", "US Eastern (New York)"),
                ("US/Central", "US Central (Chicago)"),
                ("US/Mountain", "US Mountain (Denver)"),
                ("US/Pacific", "US Pacific (Los Angeles)"),
                ("US/Alaska", "US Alaska"),
                ("US/Hawaii", "US Hawaii"),
                ("America/New_York", "America/New_York"),
                ("America/Chicago", "America/Chicago"),
                ("America/Denver", "America/Denver"),
                ("America/Los_Angeles", "America/Los_Angeles"),
                ("America/Phoenix", "America/Phoenix"),
                ("America/Anchorage", "America/Anchorage"),
                ("Pacific/Honolulu", "Pacific/Honolulu"),
                ("America/Toronto", "America/Toronto"),
                ("America/Vancouver", "America/Vancouver"),
                ("Europe/London", "Europe/London"),
                ("Europe/Paris", "Europe/Paris"),
                ("Europe/Berlin", "Europe/Berlin"),
                ("Asia/Tokyo", "Asia/Tokyo"),
                ("Asia/Shanghai", "Asia/Shanghai"),
                ("Asia/Kolkata", "Asia/Kolkata"),
                ("Australia/Sydney", "Australia/Sydney"),
                ("UTC", "UTC"),
            ] %}
            {% for val, label in tz_options %}
            <option value="{{ val }}" {{ 'selected' if settings.timezone == val }}>{{ label }}</option>
            {% endfor %}
        </select>
        <button type="button" class="btn btn-red btn-sm" onclick="saveTimezone()">Save</button>
        <span id="tzResult" class="result-span"></span>
    </div>
</div>

</div>

<!-- Inactivity warning modal -->
<div id="inactivityModal" class="modal-overlay" style="z-index:9999;">
    <div class="modal-box" style="padding:32px;text-align:center;">
        <div style="font-size:36px;margin-bottom:12px;">&#9203;</div>
        <h2 style="font-size:18px;font-weight:600;margin-bottom:8px;color:#111827;border:none;padding:0;">Session Expiring</h2>
        <p style="font-size:14px;color:#6b7280;margin-bottom:20px;">You will be logged out in <strong id="inactivityCountdown">60</strong> seconds due to inactivity.</p>
        <button id="keepAliveBtn" class="btn btn-red" style="padding:10px 32px;">Stay Signed In</button>
    </div>
</div>

<script>
// Collapsible cards
function toggleCard(cardId) {
    var card = document.getElementById(cardId);
    card.classList.toggle('collapsed');
    var collapsed = JSON.parse(localStorage.getItem('collapsedCards') || '{}');
    collapsed[cardId] = card.classList.contains('collapsed');
    localStorage.setItem('collapsedCards', JSON.stringify(collapsed));
}
(function() {
    var collapsed = JSON.parse(localStorage.getItem('collapsedCards') || '{}');
    Object.keys(collapsed).forEach(function(cardId) {
        if (collapsed[cardId]) {
            var card = document.getElementById(cardId);
            if (card) card.classList.add('collapsed');
        }
    });
})();

// Toast notifications
function showToast(msg, type) {
    var toast = document.createElement('div');
    toast.className = 'toast toast-' + (type || 'success');
    toast.textContent = msg;
    document.getElementById('toastContainer').appendChild(toast);
    setTimeout(function() { toast.remove(); }, 4000);
}

// Button loading state helpers
function btnLoading(btn) {
    if (!btn) return;
    btn.classList.add('btn-loading');
    btn.disabled = true;
}
function btnDone(btn) {
    if (!btn) return;
    btn.classList.remove('btn-loading');
    btn.disabled = false;
}

// Save timezone
function saveTimezone() {
    var tz = document.getElementById('timezoneInput').value.trim();
    var result = document.getElementById('tzResult');
    var btn = result.previousElementSibling;
    btnLoading(btn);
    result.innerHTML = '<span style="color:#6b7280;">Saving...</span>';
    fetch('/api/reschedule', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({timezone: tz})
    })
    .then(function(r) { return r.json(); })
    .then(function(d) {
        btnDone(btn);
        if (d.ok) {
            result.innerHTML = '<span style="color:#16a34a;">&#10003; Saved</span>';
            showToast('Timezone saved', 'success');
        } else {
            result.innerHTML = '<span style="color:#C8102E;">&#10007; ' + (d.error || 'Error') + '</span>';
            showToast(d.error || 'Error saving timezone', 'error');
        }
    })
    .catch(function() { btnDone(btn); result.innerHTML = '<span style="color:#C8102E;">Network error</span>'; showToast('Network error', 'error'); });
}

// Helper: submit form via fetch
function formFetch(formId, url, resultId) {
    document.getElementById(formId).addEventListener('submit', function(e) {
        e.preventDefault();
        const result = document.getElementById(resultId);
        const btn = this.querySelector('button[type="submit"]');
        btnLoading(btn);
        result.innerHTML = '<span style="color:#6b7280;">Saving...</span>';
        fetch(url, { method: 'POST', body: new FormData(this) })
            .then(r => r.json())
            .then(d => {
                btnDone(btn);
                if (d.ok) {
                    result.innerHTML = '<span style="color:#16a34a;">&#10003; ' + (d.message || 'Saved') + '</span>';
                    showToast(d.message || 'Saved', 'success');
                } else {
                    result.innerHTML = '<span style="color:#C8102E;">&#10007; ' + (d.error || 'Error') + '</span>';
                    showToast(d.error || 'Error', 'error');
                }
            })
            .catch(e => { btnDone(btn); result.innerHTML = '<span style="color:#C8102E;">Network error</span>'; showToast('Network error', 'error'); });
    });
}

formFetch('cookieForm', '/api/save-cookie', 'cookieResult');
formFetch('smtpForm', '/api/save-smtp', 'smtpResult');
formFetch('testForm', '/api/send-test', 'testResult');

// Test SMTP button
document.getElementById('testSmtpBtn').addEventListener('click', function() {
    const result = document.getElementById('smtpResult');
    const btn = this;
    btnLoading(btn);
    result.innerHTML = '<span style="color:#6b7280;">Testing...</span>';
    fetch('/api/test-smtp', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            btnDone(btn);
            if (d.ok) {
                result.innerHTML = '<span style="color:#16a34a;">&#10003; ' + d.message + '</span>';
                showToast(d.message, 'success');
            } else {
                result.innerHTML = '<span style="color:#C8102E;">&#10007; ' + (d.message || d.error) + '</span>';
                showToast(d.message || d.error, 'error');
            }
        })
        .catch(e => { btnDone(btn); result.innerHTML = '<span style="color:#C8102E;">Network error</span>'; showToast('Network error', 'error'); });
});

// --- Schedule management ---

// Toggle schedule card expand/collapse
function toggleSchedCard(event, id) {
    var detail = document.getElementById('detail-' + id);
    var chevron = document.getElementById('chevron-' + id);
    var expanded = detail.style.display !== 'none';
    detail.style.display = expanded ? 'none' : '';
    chevron.style.transform = expanded ? 'rotate(-90deg)' : '';
}
// Collapse all schedule cards on load
(function() {
    document.querySelectorAll('.sched-chevron').forEach(function(el) {
        el.style.transform = 'rotate(-90deg)';
    });
})();

function toggleRecurrenceFields(sel) {
    var form = sel.closest('form');
    var daysRow = form.querySelector('.days-row');
    var monthRow = form.querySelector('.month-row');
    var val = sel.value;
    daysRow.style.display = (val === 'weekly' || val === 'biweekly') ? '' : 'none';
    monthRow.style.display = (val === 'monthly') ? '' : 'none';
}

function scheduleApiCall(url, fd, resultEl, btn) {
    btnLoading(btn);
    resultEl.innerHTML = '<span style="color:#6b7280;">Saving...</span>';
    fetch(url, { method: 'POST', body: fd })
        .then(r => r.json())
        .then(d => {
            btnDone(btn);
            if (d.ok) {
                resultEl.innerHTML = '<span style="color:#16a34a;">&#10003; ' + (d.message || 'Done') + '</span>';
                showToast(d.message || 'Done', 'success');
                setTimeout(() => location.reload(), 800);
            } else {
                resultEl.innerHTML = '<span style="color:#C8102E;">&#10007; ' + (d.error || 'Error') + '</span>';
                showToast(d.error || 'Error', 'error');
            }
        })
        .catch(() => { btnDone(btn); resultEl.innerHTML = '<span style="color:#C8102E;">Network error</span>'; showToast('Network error', 'error'); });
}

// Handle save for existing schedule forms
document.querySelectorAll('form.schedule-form').forEach(function(form) {
    form.addEventListener('submit', function(e) {
        e.preventDefault();
        var id = this.dataset.id;
        var result = this.querySelector('.sched-result');
        var btn = this.querySelector('button[type="submit"]');
        scheduleApiCall('/api/schedules/' + id, new FormData(this), result, btn);
    });
});

// Handle create new schedule form
document.getElementById('newScheduleForm').addEventListener('submit', function(e) {
    e.preventDefault();
    var result = document.getElementById('newSchedResult');
    var btn = this.querySelector('button[type="submit"]');
    scheduleApiCall('/api/schedules', new FormData(this), result, btn);
});

function toggleSchedule(id) {
    fetch('/api/schedules/' + id + '/toggle', { method: 'POST' })
        .then(r => r.json())
        .then(d => { if (d.ok) location.reload(); })
        .catch(() => {});
}

function deleteSchedule(id, name) {
    var card = document.getElementById('sched-' + id);
    var deleteBtn = card.querySelector('.btn-delete');
    var result = card.querySelector('.sched-result');
    deleteBtn.style.display = 'none';
    // Build confirmation buttons via DOM to avoid quoting issues
    result.textContent = '';
    var text = document.createTextNode('Delete "' + name + '"? ');
    var yesBtn = document.createElement('button');
    yesBtn.className = 'btn btn-sm';
    yesBtn.style.cssText = 'background:#991b1b;color:white;padding:4px 12px;';
    yesBtn.textContent = 'Yes, delete';
    yesBtn.onclick = function() { confirmDelete(id); };
    var noBtn = document.createElement('button');
    noBtn.className = 'btn btn-sm btn-gray';
    noBtn.style.cssText = 'padding:4px 12px;margin-left:4px;';
    noBtn.textContent = 'Cancel';
    noBtn.onclick = function() { cancelDelete(id); };
    result.appendChild(text);
    result.appendChild(yesBtn);
    result.appendChild(document.createTextNode(' '));
    result.appendChild(noBtn);
}
function confirmDelete(id) {
    var card = document.getElementById('sched-' + id);
    var result = card.querySelector('.sched-result');
    result.innerHTML = '<span style="color:#6b7280;">Deleting...</span>';
    fetch('/api/schedules/' + id + '/delete', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (d.ok) {
                showToast(d.message || 'Schedule deleted', 'success');
                location.reload();
            } else {
                result.innerHTML = '<span style="color:#C8102E;">&#10007; ' + (d.error || 'Error') + '</span>';
            }
        })
        .catch(function() { result.innerHTML = '<span style="color:#C8102E;">Network error</span>'; });
}
function cancelDelete(id) {
    var card = document.getElementById('sched-' + id);
    card.querySelector('.btn-delete').style.display = '';
    card.querySelector('.sched-result').innerHTML = '';
}

function sendNowSchedule(id, btn) {
    var card = document.getElementById('sched-' + id);
    var result = card.querySelector('.sched-result');
    btnLoading(btn);
    result.innerHTML = '<span style="color:#6b7280;">Sending...</span>';
    fetch('/api/schedules/' + id + '/send-now', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            btnDone(btn);
            if (d.ok) {
                result.innerHTML = '<span style="color:#16a34a;">&#10003; ' + d.message + '</span>';
                showToast(d.message, 'success');
            } else {
                result.innerHTML = '<span style="color:#C8102E;">&#10007; ' + (d.error || 'Error') + '</span>';
                showToast(d.error || 'Error', 'error');
            }
        })
        .catch(() => { btnDone(btn); result.innerHTML = '<span style="color:#C8102E;">Network error</span>'; showToast('Network error', 'error'); });
}

// Check for updates on page load and every 30 minutes
function checkForUpdate() {
    fetch('/api/check-update')
        .then(r => r.json())
        .then(d => {
            const banner = document.getElementById('updateBanner');
            if (d.update_available) {
                document.getElementById('updateVersion').textContent = d.latest_version;
                banner.style.display = 'flex';
            } else {
                banner.style.display = 'none';
            }
        })
        .catch(() => {});
}
checkForUpdate();
setInterval(checkForUpdate, 1800000);

// Poll server until it responds, then reload
function waitForRestart(resultEl, btnEl, maxAttempts) {
    var attempts = 0;
    var max = maxAttempts || 30;
    function poll() {
        attempts++;
        resultEl.innerHTML = '<span style="color:#6b7280;">Restarting... waiting for server (' + attempts + '/' + max + ')</span>';
        fetch('/api/status', { signal: AbortSignal.timeout(3000) })
            .then(function(r) {
                if (r.ok) {
                    resultEl.innerHTML = '<span style="color:#16a34a;">&#10003; Restart complete. Reloading...</span>';
                    setTimeout(function() { location.reload(); }, 500);
                } else if (attempts < max) {
                    setTimeout(poll, 2000);
                } else {
                    resultEl.innerHTML = '<span style="color:#d97706;">Server not responding after ' + max + ' attempts. Try refreshing manually.</span>';
                    btnEl.disabled = false;
                }
            })
            .catch(function() {
                if (attempts < max) {
                    setTimeout(poll, 2000);
                } else {
                    resultEl.innerHTML = '<span style="color:#d97706;">Server not responding after ' + max + ' attempts. Try refreshing manually.</span>';
                    btnEl.disabled = false;
                }
            });
    }
    // Wait a moment for the service to begin shutting down
    setTimeout(poll, 3000);
}

// Install update button
document.getElementById('installUpdateBtn').addEventListener('click', function() {
    const version = document.getElementById('updateVersion').textContent;
    const result = document.getElementById('updateResult');
    const btn = this;
    if (!confirm('Install update v' + version + '? The service will restart automatically.')) return;
    btn.disabled = true;
    result.innerHTML = '<span style="color:#6b7280;">Installing...</span>';
    const fd = new FormData();
    fd.append('version', version);
    fetch('/api/install-update', { method: 'POST', body: fd })
        .then(r => r.json())
        .then(d => {
            if (d.ok) {
                result.innerHTML = '<span style="color:#16a34a;">&#10003; ' + d.message + '</span>';
                if (d.restarting) {
                    btn.textContent = 'Restarting...';
                    waitForRestart(result, btn, 30);
                } else {
                    btn.textContent = 'Updated';
                }
            } else {
                result.innerHTML = '<span style="color:#C8102E;">&#10007; ' + d.message + '</span>';
                btn.disabled = false;
            }
        })
        .catch(() => {
            result.innerHTML = '<span style="color:#C8102E;">Network error</span>';
            btn.disabled = false;
        });
});

// Password change modal
function closePwModal() {
    document.getElementById('pwModal').style.display = 'none';
    document.getElementById('pwForm').reset();
    document.getElementById('pwResult').innerHTML = '';
}
document.getElementById('pwModal').addEventListener('click', function(e) {
    if (e.target === this) closePwModal();
});
document.getElementById('pwForm').addEventListener('submit', function(e) {
    e.preventDefault();
    const result = document.getElementById('pwResult');
    result.innerHTML = '<span style="color:#6b7280;">Saving...</span>';
    fetch('/api/change-password', { method: 'POST', body: new FormData(this) })
        .then(r => r.json())
        .then(d => {
            if (d.ok) {
                result.innerHTML = '<span style="color:#16a34a;">&#10003; ' + d.message + '</span>';
                setTimeout(function() { window.location.href = '/login'; }, 1500);
            } else {
                result.innerHTML = '<span style="color:#C8102E;">&#10007; ' + (d.error || 'Error') + '</span>';
            }
        })
        .catch(() => { result.innerHTML = '<span style="color:#C8102E;">Network error</span>'; });
});

// Auto-refresh status every 30 seconds
setInterval(function() {
    fetch('/api/status')
        .then(r => r.json())
        .then(d => {
            document.getElementById('lastRun').textContent = d.last_run || 'Never';
            document.getElementById('lastStatus').textContent = d.last_status || 'N/A';
            document.getElementById('lastEmail').textContent = d.last_email_sent || 'Never';
            document.getElementById('cookieStatus').innerHTML = d.cookie_set
                ? '&#10003; Set'
                : '<span style="color:#d97706;">&#9888; Not set</span>';
            if (d.timezone) {
                document.getElementById('timezoneInput').value = d.timezone;
            }
        })
        .catch(() => {});
}, 30000);
""" + """
// --- Inactivity auto-logout ---
(function() {
    var WARN_AFTER = 14 * 60 * 1000;   // 14 minutes
    var LOGOUT_AFTER = 60 * 1000;       // 1 minute countdown
    var warnTimer, countdownInterval, secondsLeft;

    var overlay = document.getElementById('inactivityModal');
    var countdownEl = document.getElementById('inactivityCountdown');

    function resetTimer() {
        clearTimeout(warnTimer);
        clearInterval(countdownInterval);
        overlay.style.display = 'none';
        warnTimer = setTimeout(showWarning, WARN_AFTER);
    }

    function showWarning() {
        secondsLeft = LOGOUT_AFTER / 1000;
        countdownEl.textContent = secondsLeft;
        overlay.style.display = 'flex';
        document.getElementById('keepAliveBtn').focus();
        countdownInterval = setInterval(function() {
            secondsLeft--;
            countdownEl.textContent = secondsLeft;
            if (secondsLeft <= 0) {
                clearInterval(countdownInterval);
                // POST to logout then redirect
                fetch('/logout', {method: 'POST'}).finally(function() {
                    window.location.href = '/login';
                });
            }
        }, 1000);
    }

    document.getElementById('keepAliveBtn').addEventListener('click', function() {
        fetch('/api/keep-alive', {method: 'POST'})
            .then(function() { resetTimer(); })
            .catch(function() { resetTimer(); });
    });

    // Track user activity (debounced)
    var debounce;
    function onActivity() {
        clearTimeout(debounce);
        debounce = setTimeout(resetTimer, 200);
    }
    ['mousemove','keydown','click','scroll','touchstart'].forEach(function(evt) {
        document.addEventListener(evt, onActivity, {passive: true});
    });

    // Start the timer
    resetTimer();
})();

// --- Keyboard shortcuts ---
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        if (document.getElementById('pwModal').style.display === 'flex') closePwModal();
        if (document.getElementById('inactivityModal').style.display === 'flex') {
            document.getElementById('keepAliveBtn').click();
        }
    }
});
</script>
</body></html>"""

LOGS_TEMPLATE = """<!DOCTYPE html>
<html><head><meta name="viewport" content="width=device-width, initial-scale=1"><title>Logs — Claude Dashboard Admin</title>
<style>""" + _BASE_CSS + """
    .log-box {
        background: #1a1a1a; color: #d1fae5; font-family: 'Consolas', 'Monaco', monospace;
        font-size: 12px; padding: 16px; border-radius: 8px; overflow-x: auto;
        white-space: pre-wrap; word-wrap: break-word; max-height: 80vh; overflow-y: auto;
    }
</style></head><body>
<div class="navbar">
    <a class="navbar-brand" href="/dashboard"><div class="badge">LM</div> Claude Dashboard Admin</a>
    <div class="nav-dropdown" id="navDropdown">
        <button class="nav-dropdown-btn" onclick="this.parentElement.classList.toggle('open')">Menu &#9662;</button>
        <div class="nav-dropdown-menu">
            <a href="/dashboard">Dashboard</a>
            <a href="/reports">Reports</a>
            <a href="/logs" class="nav-active-item">Logs</a>
            <div class="nav-dropdown-divider"></div>
            <form method="POST" action="/logout"><button type="submit">Logout</button></form>
        </div>
    </div>
</div>
<div class="container">
    <div class="card">
        <h2>Application Logs (last 200 lines)</h2>
        <div class="log-box" id="logBox">{{ log_lines }}</div>
    </div>
</div>
<!-- Inactivity warning modal -->
<div id="inactivityModal" class="modal-overlay" style="z-index:9999;">
    <div class="modal-box" style="padding:32px;text-align:center;">
        <div style="font-size:36px;margin-bottom:12px;">&#9203;</div>
        <h2 style="font-size:18px;font-weight:600;margin-bottom:8px;color:#111827;border:none;padding:0;">Session Expiring</h2>
        <p style="font-size:14px;color:#6b7280;margin-bottom:20px;">You will be logged out in <strong id="inactivityCountdown">60</strong> seconds due to inactivity.</p>
        <button id="keepAliveBtn" class="btn btn-red" style="padding:10px 32px;">Stay Signed In</button>
    </div>
</div>
<script>
// Auto-scroll to bottom
var box = document.getElementById('logBox');
box.scrollTop = box.scrollHeight;
// Auto-refresh every 10 seconds
setInterval(function() {
    fetch('/logs').then(r => r.text()).then(html => {
        var parser = new DOMParser();
        var doc = parser.parseFromString(html, 'text/html');
        var newBox = doc.getElementById('logBox');
        if (newBox) {
            box.textContent = newBox.textContent;
            box.scrollTop = box.scrollHeight;
        }
    }).catch(() => {});
}, 10000);

// --- Inactivity auto-logout ---
(function() {
    var WARN_AFTER = 14 * 60 * 1000;
    var LOGOUT_AFTER = 60 * 1000;
    var warnTimer, countdownInterval, secondsLeft;

    var overlay = document.getElementById('inactivityModal');
    var countdownEl = document.getElementById('inactivityCountdown');

    function resetTimer() {
        clearTimeout(warnTimer);
        clearInterval(countdownInterval);
        overlay.style.display = 'none';
        warnTimer = setTimeout(showWarning, WARN_AFTER);
    }

    function showWarning() {
        secondsLeft = LOGOUT_AFTER / 1000;
        countdownEl.textContent = secondsLeft;
        overlay.style.display = 'flex';
        document.getElementById('keepAliveBtn').focus();
        countdownInterval = setInterval(function() {
            secondsLeft--;
            countdownEl.textContent = secondsLeft;
            if (secondsLeft <= 0) {
                clearInterval(countdownInterval);
                fetch('/logout', {method: 'POST'}).finally(function() {
                    window.location.href = '/login';
                });
            }
        }, 1000);
    }

    document.getElementById('keepAliveBtn').addEventListener('click', function() {
        fetch('/api/keep-alive', {method: 'POST'})
            .then(function() { resetTimer(); })
            .catch(function() { resetTimer(); });
    });

    var debounce;
    function onActivity() {
        clearTimeout(debounce);
        debounce = setTimeout(resetTimer, 200);
    }
    ['mousemove','keydown','click','scroll','touchstart'].forEach(function(evt) {
        document.addEventListener(evt, onActivity, {passive: true});
    });

    resetTimer();
})();
</script>
</body></html>"""
