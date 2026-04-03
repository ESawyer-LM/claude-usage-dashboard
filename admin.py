"""
Flask web admin UI for Claude Usage Dashboard.
Session-based password auth, settings management, and test report triggering.
"""

import os
import re
import threading
from datetime import datetime
from functools import wraps

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


def create_app(scheduler_ref=None):
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.secret_key = config.get_flask_secret()

    # Store scheduler reference for rescheduling
    app.config["SCHEDULER_REF"] = scheduler_ref

    # ---------------------------------------------------------------------------
    # Auth decorator
    # ---------------------------------------------------------------------------
    def login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("authenticated"):
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
                session["authenticated"] = True
                return redirect(url_for("dashboard"))
            return render_template_string(LOGIN_TEMPLATE, error="Invalid password")
        return render_template_string(LOGIN_TEMPLATE, error=None)

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

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

        # Get status info
        last_run = settings.get("last_run", "Never")
        last_status = settings.get("last_status", "N/A")
        last_email_sent = settings.get("last_email_sent", "Never")
        next_runs = sched_module.get_next_run_times()

        # Check for test result in query params
        test_result = request.args.get("test")
        test_msg = request.args.get("msg", "")

        return render_template_string(
            DASHBOARD_TEMPLATE,
            settings=settings,
            cookie_masked=cookie_masked,
            cookie_set=bool(cookie),
            last_run=last_run,
            last_status=last_status,
            last_email_sent=last_email_sent,
            next_weekday=next_runs.get("weekday", "N/A"),
            next_friday=next_runs.get("friday", "N/A"),
            test_result=test_result,
            test_msg=test_msg,
            smtp_pass_set=bool(settings.get("smtp_pass", "")),
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

    @app.route("/api/save-schedule", methods=["POST"])
    @login_required
    def api_save_schedule():
        settings = config.load_settings()

        # Parse schedule
        try:
            wd_hour = int(request.form.get("weekday_hour", 7))
            wd_minute = int(request.form.get("weekday_minute", 0))
            fri_hour = int(request.form.get("friday_hour", 7))
            fri_minute = int(request.form.get("friday_minute", 0))
        except ValueError:
            return jsonify({"ok": False, "error": "Invalid time values"}), 400

        timezone = request.form.get("timezone", "America/Chicago").strip()

        # Parse recipients
        weekday_raw = request.form.get("weekday_recipients", "").strip()
        friday_raw = request.form.get("friday_recipients", "").strip()

        email_pattern = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

        weekday_recipients = [e.strip() for e in weekday_raw.split("\n") if e.strip()]
        friday_recipients = [e.strip() for e in friday_raw.split("\n") if e.strip()]

        for email in weekday_recipients + friday_recipients:
            if not email_pattern.match(email):
                return jsonify({"ok": False, "error": f"Invalid email: {email}"}), 400

        settings["weekday_cron"] = {"hour": wd_hour, "minute": wd_minute}
        settings["friday_cron"] = {"hour": fri_hour, "minute": fri_minute}
        settings["weekday_enabled"] = request.form.get("weekday_enabled") == "on"
        settings["friday_enabled"] = request.form.get("friday_enabled") == "on"
        settings["timezone"] = timezone
        settings["weekday_recipients"] = weekday_recipients
        settings["friday_recipients"] = friday_recipients

        config.save_settings(settings)

        # Reschedule APScheduler jobs
        try:
            sched_module.reschedule(settings)
        except Exception as e:
            logger.warning(f"Failed to reschedule: {e}")

        logger.info("Schedule and recipients updated via admin UI")
        return jsonify({"ok": True, "message": "Schedule and recipients saved"})

    @app.route("/api/send-now", methods=["POST"])
    @login_required
    def api_send_now():
        is_friday = request.form.get("is_friday") == "true"
        label = "Friday" if is_friday else "Weekday"

        def _run():
            try:
                sched_module.run_report_job(is_friday=is_friday)
                logger.info(f"{label} report sent via Send Now")
            except Exception as e:
                logger.error(f"Send Now ({label}) failed: {e}")

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return jsonify({"ok": True, "message": f"{label} report queued — sending to configured recipients"})

    @app.route("/api/reschedule", methods=["POST"])
    @login_required
    def api_reschedule():
        try:
            data = request.get_json()
            settings = config.load_settings()
            if "weekday_cron" in data:
                settings["weekday_cron"] = data["weekday_cron"]
            if "friday_cron" in data:
                settings["friday_cron"] = data["friday_cron"]
            if "timezone" in data:
                settings["timezone"] = data["timezone"]
            config.save_settings(settings)
            sched_module.reschedule(settings)
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/send-test", methods=["POST"])
    @login_required
    def api_send_test():
        recipient = request.form.get("test_email", "").strip()
        if not recipient or "@" not in recipient:
            return jsonify({"ok": False, "error": "Valid email address required"}), 400

        def _run():
            try:
                ok, msg = sched_module.run_test_report(recipient)
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
        next_runs = sched_module.get_next_run_times()
        return jsonify({
            "last_run": settings.get("last_run", "Never"),
            "last_status": settings.get("last_status", "N/A"),
            "last_email_sent": settings.get("last_email_sent", "Never"),
            "next_weekday": next_runs.get("weekday"),
            "next_friday": next_runs.get("friday"),
            "cookie_set": bool(settings.get("session_cookie")),
            "smtp_configured": bool(settings.get("smtp_pass")),
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
            logger.info(f"Update to v{version} successful — restart required")
        else:
            logger.error(f"Update to v{version} failed: {result['message']}")
        return jsonify(result)

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
    .navbar-brand { font-size: 18px; font-weight: 700; display: flex; align-items: center; gap: 10px; }
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
"""

LOGIN_TEMPLATE = """<!DOCTYPE html>
<html><head><title>Login — Claude Dashboard Admin</title>
<style>""" + _BASE_CSS + """
    .login-box { max-width: 400px; margin: 80px auto; }
</style></head><body>
<div class="navbar"><div class="navbar-brand"><div class="badge">LM</div> Claude Dashboard Admin</div></div>
<div class="container"><div class="login-box"><div class="card">
    <h2>Sign In</h2>
    {% if error %}<div class="alert alert-error">{{ error }}</div>{% endif %}
    <form method="POST">
        <div class="form-group">
            <label>Password</label>
            <input type="password" name="password" autofocus required>
        </div>
        <button type="submit" class="btn btn-red" style="width:100%;">Sign In</button>
    </form>
</div></div></div></body></html>"""

DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html><head><title>Claude Dashboard Admin</title>
<style>""" + _BASE_CSS + """</style></head><body>
<div class="navbar">
    <div class="navbar-brand"><div class="badge">LM</div> Claude Dashboard Admin</div>
    <div style="display:flex;gap:16px;align-items:center;">
        <a href="/logs">View Logs</a>
        <form method="POST" action="/logout" style="display:inline;">
            <button type="submit" style="background:none;border:none;color:white;cursor:pointer;font-size:13px;opacity:0.9;">Logout</button>
        </form>
    </div>
</div>
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
        <span id="updateResult" style="font-size:13px;"></span>
        <button type="button" class="btn btn-red" id="installUpdateBtn" style="padding:6px 16px;font-size:13px;">Install Update</button>
    </div>
</div>

<!-- Card 1: Status -->
<div class="card" id="statusCard">
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
            <div class="status-label">Next Weekday Run</div>
            <div class="status-value" id="nextWeekday">{{ next_weekday }}</div>
        </div>
        <div class="status-item">
            <div class="status-label">Next Friday Run</div>
            <div class="status-value" id="nextFriday">{{ next_friday }}</div>
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

<!-- Card 2: Claude.ai Connection -->
<div class="card">
    <h2>Claude.ai Connection</h2>
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
        <span id="cookieResult" style="margin-left:12px;font-size:13px;"></span>
    </form>
</div>

<!-- Card 3: SMTP Settings -->
<div class="card">
    <h2>SMTP / Email Settings</h2>
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
            <span id="smtpResult" style="font-size:13px;"></span>
        </div>
    </form>
</div>

<!-- Card 4: Schedule & Recipients -->
<div class="card">
    <h2>Schedule &amp; Recipients</h2>
    <form id="scheduleForm">
        <div class="inline-row">
            <div class="form-group">
                <label style="display:flex;align-items:center;gap:8px;">
                    <span>Weekday Send (Mon-Thu)</span>
                    <label class="toggle" style="margin-left:auto;">
                        <input type="checkbox" name="weekday_enabled" {{ 'checked' if settings.get('weekday_enabled', true) }}>
                        <span class="toggle-slider"></span>
                    </label>
                </label>
                <div style="display:flex;gap:8px;">
                    <select name="weekday_hour">
                        {% for h in range(24) %}
                        <option value="{{ h }}" {{ 'selected' if h == settings.weekday_cron.hour }}>{{ '%02d'|format(h) }}</option>
                        {% endfor %}
                    </select>
                    <span style="line-height:36px;">:</span>
                    <select name="weekday_minute">
                        {% for m in [0, 15, 30, 45] %}
                        <option value="{{ m }}" {{ 'selected' if m == settings.weekday_cron.minute }}>{{ '%02d'|format(m) }}</option>
                        {% endfor %}
                    </select>
                </div>
            </div>
            <div class="form-group">
                <label style="display:flex;align-items:center;gap:8px;">
                    <span>Friday Send</span>
                    <label class="toggle" style="margin-left:auto;">
                        <input type="checkbox" name="friday_enabled" {{ 'checked' if settings.get('friday_enabled', true) }}>
                        <span class="toggle-slider"></span>
                    </label>
                </label>
                <div style="display:flex;gap:8px;">
                    <select name="friday_hour">
                        {% for h in range(24) %}
                        <option value="{{ h }}" {{ 'selected' if h == settings.friday_cron.hour }}>{{ '%02d'|format(h) }}</option>
                        {% endfor %}
                    </select>
                    <span style="line-height:36px;">:</span>
                    <select name="friday_minute">
                        {% for m in [0, 15, 30, 45] %}
                        <option value="{{ m }}" {{ 'selected' if m == settings.friday_cron.minute }}>{{ '%02d'|format(m) }}</option>
                        {% endfor %}
                    </select>
                </div>
            </div>
        </div>
        <div class="form-group">
            <label>Timezone</label>
            <input type="text" name="timezone" value="{{ settings.timezone }}">
        </div>
        <div class="inline-row">
            <div class="form-group">
                <label>Weekday Recipients (one per line)</label>
                <textarea name="weekday_recipients" rows="3">{{ settings.weekday_recipients | join('\\n') }}</textarea>
            </div>
            <div class="form-group">
                <label>Friday Recipients (one per line)</label>
                <textarea name="friday_recipients" rows="3">{{ settings.friday_recipients | join('\\n') }}</textarea>
            </div>
        </div>
        <button type="submit" class="btn btn-red">Save Schedule &amp; Recipients</button>
        <span id="scheduleResult" style="margin-left:12px;font-size:13px;"></span>
    </form>
    <hr style="border:none;border-top:1px solid #f3f4f6;margin:20px 0;">
    <div style="display:flex;gap:12px;align-items:center;">
        <span style="font-size:13px;color:#6b7280;">Send now:</span>
        <button type="button" class="btn btn-red" id="sendWeekdayBtn">Weekday Report</button>
        <button type="button" class="btn btn-red" id="sendFridayBtn">Friday Report</button>
        <span id="sendNowResult" style="font-size:13px;"></span>
    </div>
</div>

<!-- Card 5: Send Test Report -->
<div class="card">
    <h2>Send Test Report</h2>
    <form id="testForm">
        <div class="form-group">
            <label>Test Recipient Email</label>
            <input type="email" name="test_email" value="{{ settings.smtp_user }}" required>
        </div>
        <button type="submit" class="btn btn-red">Send Now</button>
        <span id="testResult" style="margin-left:12px;font-size:13px;"></span>
    </form>
</div>

</div>

<script>
// Helper: submit form via fetch
function formFetch(formId, url, resultId) {
    document.getElementById(formId).addEventListener('submit', function(e) {
        e.preventDefault();
        const result = document.getElementById(resultId);
        result.innerHTML = '<span style="color:#6b7280;">Saving...</span>';
        fetch(url, { method: 'POST', body: new FormData(this) })
            .then(r => r.json())
            .then(d => {
                if (d.ok) {
                    result.innerHTML = '<span style="color:#16a34a;">&#10003; ' + (d.message || 'Saved') + '</span>';
                } else {
                    result.innerHTML = '<span style="color:#C8102E;">&#10007; ' + (d.error || 'Error') + '</span>';
                }
            })
            .catch(e => { result.innerHTML = '<span style="color:#C8102E;">Network error</span>'; });
    });
}

formFetch('cookieForm', '/api/save-cookie', 'cookieResult');
formFetch('smtpForm', '/api/save-smtp', 'smtpResult');
formFetch('scheduleForm', '/api/save-schedule', 'scheduleResult');
formFetch('testForm', '/api/send-test', 'testResult');

// Test SMTP button
document.getElementById('testSmtpBtn').addEventListener('click', function() {
    const result = document.getElementById('smtpResult');
    result.innerHTML = '<span style="color:#6b7280;">Testing...</span>';
    fetch('/api/test-smtp', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (d.ok) {
                result.innerHTML = '<span style="color:#16a34a;">&#10003; ' + d.message + '</span>';
            } else {
                result.innerHTML = '<span style="color:#C8102E;">&#10007; ' + (d.message || d.error) + '</span>';
            }
        })
        .catch(e => { result.innerHTML = '<span style="color:#C8102E;">Network error</span>'; });
});

// Send Now buttons
function sendNow(isFriday) {
    const result = document.getElementById('sendNowResult');
    const label = isFriday ? 'Friday' : 'Weekday';
    result.innerHTML = '<span style="color:#6b7280;">Sending ' + label + ' report...</span>';
    const fd = new FormData();
    fd.append('is_friday', isFriday);
    fetch('/api/send-now', { method: 'POST', body: fd })
        .then(r => r.json())
        .then(d => {
            if (d.ok) {
                result.innerHTML = '<span style="color:#16a34a;">&#10003; ' + d.message + '</span>';
            } else {
                result.innerHTML = '<span style="color:#C8102E;">&#10007; ' + (d.error || 'Error') + '</span>';
            }
        })
        .catch(() => { result.innerHTML = '<span style="color:#C8102E;">Network error</span>'; });
}
document.getElementById('sendWeekdayBtn').addEventListener('click', () => sendNow('false'));
document.getElementById('sendFridayBtn').addEventListener('click', () => sendNow('true'));

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

// Install update button
document.getElementById('installUpdateBtn').addEventListener('click', function() {
    const version = document.getElementById('updateVersion').textContent;
    const result = document.getElementById('updateResult');
    const btn = this;
    if (!confirm('Install update v' + version + '? The service will need to restart after installation.')) return;
    btn.disabled = true;
    result.innerHTML = '<span style="color:#6b7280;">Installing...</span>';
    const fd = new FormData();
    fd.append('version', version);
    fetch('/api/install-update', { method: 'POST', body: fd })
        .then(r => r.json())
        .then(d => {
            if (d.ok) {
                result.innerHTML = '<span style="color:#16a34a;">&#10003; ' + d.message + '</span>';
                btn.textContent = 'Installed';
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

// Auto-refresh status every 30 seconds
setInterval(function() {
    fetch('/api/status')
        .then(r => r.json())
        .then(d => {
            document.getElementById('lastRun').textContent = d.last_run || 'Never';
            document.getElementById('lastStatus').textContent = d.last_status || 'N/A';
            document.getElementById('lastEmail').textContent = d.last_email_sent || 'Never';
            document.getElementById('nextWeekday').textContent = d.next_weekday || 'N/A';
            document.getElementById('nextFriday').textContent = d.next_friday || 'N/A';
            document.getElementById('cookieStatus').innerHTML = d.cookie_set
                ? '&#10003; Set'
                : '<span style="color:#d97706;">&#9888; Not set</span>';
        })
        .catch(() => {});
}, 30000);
</script>
</body></html>"""

LOGS_TEMPLATE = """<!DOCTYPE html>
<html><head><title>Logs — Claude Dashboard Admin</title>
<style>""" + _BASE_CSS + """
    .log-box {
        background: #1a1a1a; color: #d1fae5; font-family: 'Consolas', 'Monaco', monospace;
        font-size: 12px; padding: 16px; border-radius: 8px; overflow-x: auto;
        white-space: pre-wrap; word-wrap: break-word; max-height: 80vh; overflow-y: auto;
    }
</style></head><body>
<div class="navbar">
    <div class="navbar-brand"><div class="badge">LM</div> Claude Dashboard Admin</div>
    <div style="display:flex;gap:16px;align-items:center;">
        <a href="/dashboard">Dashboard</a>
        <form method="POST" action="/logout" style="display:inline;">
            <button type="submit" style="background:none;border:none;color:white;cursor:pointer;font-size:13px;opacity:0.9;">Logout</button>
        </form>
    </div>
</div>
<div class="container">
    <div class="card">
        <h2>Application Logs (last 200 lines)</h2>
        <div class="log-box" id="logBox">{{ log_lines }}</div>
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
</script>
</body></html>"""
