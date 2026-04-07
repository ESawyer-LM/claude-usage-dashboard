"""
Flask Blueprint for the Report Builder feature.
Provides report manager, builder UI, and API endpoints.
"""

import json
import os
import re
import time
from datetime import datetime
from functools import wraps

from flask import (
    Blueprint,
    current_app,
    jsonify,
    make_response,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)

import config
import report_storage
from report_html_generator import (
    generate_report_html,
    get_date_bounds,
    filter_data_by_range,
    REPORT_COMPONENTS,
)
from report_pdf_generator import generate_report_pdf

logger = config.get_logger()

reports_bp = Blueprint("reports", __name__)


# ---------------------------------------------------------------------------
# Auth — same logic as admin.py's login_required
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
# Helper: load cached scrape data
# ---------------------------------------------------------------------------
def _load_cached_data():
    if not os.path.exists(config.CACHE_FILE):
        return None
    with open(config.CACHE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------
@reports_bp.route("/reports")
@login_required
def report_manager():
    data = report_storage.load_reports()
    reports = data.get("reports", [])
    templates = data.get("templates", [])
    return render_template_string(REPORT_MANAGER_TEMPLATE, reports=reports, templates=templates)


@reports_bp.route("/reports/new")
@login_required
def report_builder_new():
    template_id = request.args.get("template")
    report = None
    if template_id:
        for t in report_storage.get_templates():
            if t["id"] == template_id:
                report = {
                    "id": "",
                    "title": t["title"],
                    "components": t["components"],
                    "global_date_range": None,
                    "schedule": {"enabled": False, "cron": {"day_of_week": "fri", "hour": 8, "minute": 0}, "timezone": "America/Chicago", "recipients": []},
                }
                break
    if not report:
        report = {
            "id": "",
            "title": "",
            "components": [],
            "global_date_range": None,
            "schedule": {"enabled": False, "cron": {"day_of_week": "fri", "hour": 8, "minute": 0}, "timezone": "America/Chicago", "recipients": []},
        }
    return render_template_string(
        REPORT_BUILDER_TEMPLATE,
        report=report,
        components_meta=REPORT_COMPONENTS,
        is_new=True,
    )


@reports_bp.route("/reports/<report_id>/edit")
@login_required
def report_builder_edit(report_id):
    report = report_storage.get_report(report_id)
    if not report:
        return redirect(url_for("reports.report_manager"))
    return render_template_string(
        REPORT_BUILDER_TEMPLATE,
        report=report,
        components_meta=REPORT_COMPONENTS,
        is_new=False,
    )


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------
@reports_bp.route("/api/reports", methods=["GET"])
@login_required
def api_list_reports():
    data = report_storage.load_reports()
    return jsonify({"ok": True, "reports": data.get("reports", [])})


@reports_bp.route("/api/reports", methods=["POST"])
@login_required
def api_create_report():
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"ok": False, "error": "Title is required"}), 400
    if len(title) > 100:
        return jsonify({"ok": False, "error": "Title must be 100 characters or less"}), 400
    components = body.get("components", [])
    if not components:
        return jsonify({"ok": False, "error": "At least one component is required"}), 400
    report = report_storage.create_report(body)
    _sync_report_schedules()
    return jsonify({"ok": True, "report": report})


@reports_bp.route("/api/reports/<report_id>", methods=["PUT"])
@login_required
def api_update_report(report_id):
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"ok": False, "error": "Title is required"}), 400
    if len(title) > 100:
        return jsonify({"ok": False, "error": "Title must be 100 characters or less"}), 400
    components = body.get("components", [])
    if not components:
        return jsonify({"ok": False, "error": "At least one component is required"}), 400
    report = report_storage.update_report(report_id, body)
    if not report:
        return jsonify({"ok": False, "error": "Report not found"}), 404
    _sync_report_schedules()
    return jsonify({"ok": True, "report": report})


@reports_bp.route("/api/reports/<report_id>", methods=["DELETE"])
@login_required
def api_delete_report(report_id):
    # Find and remove any schedules that reference this custom report
    removed_schedules = []
    try:
        settings = config.load_settings()
        custom_key = f"custom:{report_id}"
        original_count = len(settings.get("schedules", []))
        kept = []
        for s in settings.get("schedules", []):
            if s.get("report_type") == custom_key:
                removed_schedules.append(s.get("name", "Unnamed"))
            else:
                kept.append(s)
        if len(kept) < original_count:
            settings["schedules"] = kept
            config.save_settings(settings)
            import scheduler as sched_module
            try:
                sched_module.sync_jobs(settings)
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Error cleaning up schedules for report {report_id}: {e}")

    if not report_storage.delete_report(report_id):
        return jsonify({"ok": False, "error": "Report not found"}), 404
    _sync_report_schedules()
    msg = "Report deleted"
    if removed_schedules:
        msg += f" along with {len(removed_schedules)} schedule(s): {', '.join(removed_schedules)}"
    return jsonify({"ok": True, "message": msg, "removed_schedules": removed_schedules})


@reports_bp.route("/api/reports/<report_id>/linked-schedules")
@login_required
def api_linked_schedules(report_id):
    """Return schedules that reference this custom report."""
    settings = config.load_settings()
    custom_key = f"custom:{report_id}"
    linked = [s.get("name", "Unnamed") for s in settings.get("schedules", []) if s.get("report_type") == custom_key]
    return jsonify({"ok": True, "schedules": linked})


@reports_bp.route("/api/reports/<report_id>/clone", methods=["POST"])
@login_required
def api_clone_report(report_id):
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        original = report_storage.get_report(report_id)
        title = f"{original['title']} (Copy)" if original else "Cloned Report"
    report = report_storage.clone_report(report_id, title)
    if not report:
        return jsonify({"ok": False, "error": "Source report not found"}), 404
    return jsonify({"ok": True, "report": report})


@reports_bp.route("/api/reports/clone-template", methods=["POST"])
@login_required
def api_clone_template():
    body = request.get_json(silent=True) or {}
    template_id = body.get("template_id", "")
    title = (body.get("title") or "").strip()
    if not template_id:
        return jsonify({"ok": False, "error": "template_id is required"}), 400
    if not title:
        for t in report_storage.get_templates():
            if t["id"] == template_id:
                title = t["title"]
                break
        else:
            title = "New Report"
    report = report_storage.clone_template(template_id, title)
    if not report:
        return jsonify({"ok": False, "error": "Template not found"}), 404
    return jsonify({"ok": True, "report": report})


@reports_bp.route("/api/reports/<report_id>/preview")
@login_required
def api_preview_report(report_id):
    report = report_storage.get_report(report_id)
    if not report:
        return jsonify({"ok": False, "error": "Report not found"}), 404
    data = _load_cached_data()
    if not data:
        return "<html><body><h2>No data available</h2><p>Run a scrape from the Dashboard page first.</p></body></html>", 200
    html = generate_report_html(data, report)
    return html, 200, {"Content-Type": "text/html"}


@reports_bp.route("/api/reports/<report_id>/pdf")
@login_required
def api_pdf_report(report_id):
    report = report_storage.get_report(report_id)
    if not report:
        return jsonify({"ok": False, "error": "Report not found"}), 404
    data = _load_cached_data()
    if not data:
        return jsonify({"ok": False, "error": "No data available. Run a scrape first."}), 400
    filepath = generate_report_pdf(data, report)
    with open(filepath, "rb") as f:
        pdf_bytes = f.read()
    safe_title = re.sub(r'[^a-zA-Z0-9_\- ]', '', report.get("title", "report"))[:50]
    resp = make_response(pdf_bytes)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f'attachment; filename="{safe_title}.pdf"'
    return resp


@reports_bp.route("/api/reports/<report_id>/send", methods=["POST"])
@login_required
def api_send_report(report_id):
    report = report_storage.get_report(report_id)
    if not report:
        return jsonify({"ok": False, "error": "Report not found"}), 404
    schedule = report.get("schedule", {})
    recipients = schedule.get("recipients", [])
    if not recipients:
        return jsonify({"ok": False, "error": "No recipients configured"}), 400
    # Use fresh cache if available (from recent UI-triggered scrape), else scrape
    from admin import get_cached_data_if_fresh
    import scraper
    data = get_cached_data_if_fresh()
    if not data:
        try:
            data = scraper.scrape()
        except Exception:
            data = _load_cached_data()
    if not data:
        return jsonify({"ok": False, "error": "No data available"}), 400
    filepath = generate_report_pdf(data, report)
    import emailer
    try:
        emailer.send_report(filepath, data, recipients, report_type="custom")
    except Exception as e:
        return jsonify({"ok": False, "error": f"Email failed: {e}"}), 500
    return jsonify({"ok": True, "message": "Report sent successfully"})


@reports_bp.route("/api/reports/date-bounds")
@login_required
def api_date_bounds():
    data = _load_cached_data()
    if not data:
        return jsonify({"min_date": "", "max_date": ""})
    bounds = get_date_bounds(data)
    return jsonify(bounds)


@reports_bp.route("/api/reports/components")
@login_required
def api_list_components():
    return jsonify({"ok": True, "components": REPORT_COMPONENTS})


# ---------------------------------------------------------------------------
# Scheduler sync helper
# ---------------------------------------------------------------------------
def _sync_report_schedules():
    """Sync APScheduler jobs for reports with enabled schedules."""
    try:
        import scheduler as sched_module
        sched_module.sync_report_jobs()
    except Exception as e:
        logger.error(f"Failed to sync report schedules: {e}")


# ---------------------------------------------------------------------------
# Shared Refresh Modal (injected into both manager and builder templates)
# ---------------------------------------------------------------------------
_REFRESH_MODAL_HTML = """
<!-- Scrape Refresh Modal -->
<div id="refreshModal" class="modal-overlay" style="display:none;">
  <div class="modal-box" style="max-width:460px;text-align:center;padding:32px;">
    <div id="refreshAsk">
      <div style="font-size:36px;margin-bottom:12px;">&#128260;</div>
      <h2 style="font-size:18px;font-weight:700;color:#1a1a1a;margin:0 0 8px 0;border:none;padding:0;">Refresh data?</h2>
      <p style="font-size:14px;color:#6b7280;margin:0 0 6px 0;line-height:1.5;">
        Would you like to pull the latest data from Claude.ai before generating this report?
      </p>
      <p style="font-size:12px;color:#9ca3af;margin:0 0 20px 0;">
        Last scraped: <strong id="lastScrapeTime">&mdash;</strong>
      </p>
      <div style="display:flex;gap:10px;justify-content:center;margin-top:20px;">
        <button id="btnRefreshYes" class="btn btn-red">Yes, refresh first</button>
        <button id="btnRefreshNo" class="btn btn-gray">No, use cached data</button>
        <button id="btnRefreshCancel" class="btn" style="background:transparent;color:#9ca3af;">Cancel</button>
      </div>
    </div>
    <div id="refreshProgress" style="display:none;">
      <div style="font-size:36px;margin-bottom:12px;">&#9203;</div>
      <h2 style="font-size:18px;font-weight:700;color:#1a1a1a;margin:0 0 8px 0;border:none;padding:0;">Refreshing data&hellip;</h2>
      <div style="margin:20px 0 16px 0;">
        <div style="width:100%;height:12px;background:#f3f4f6;border-radius:6px;overflow:hidden;">
          <div id="progressFill" style="height:100%;background:linear-gradient(90deg,#C8102E,#e05070);border-radius:6px;transition:width 0.4s ease;width:0%;"></div>
        </div>
        <div style="display:flex;justify-content:space-between;margin-top:8px;font-size:12px;">
          <span id="progressLabel" style="color:#6b7280;">Starting scrape...</span>
          <span id="progressPercent" style="color:#C8102E;font-weight:700;">0%</span>
        </div>
      </div>
      <div id="progressStages" style="text-align:left;margin-top:16px;padding:12px 16px;background:#f9fafb;border-radius:8px;max-height:180px;overflow-y:auto;"></div>
    </div>
    <div id="refreshDone" style="display:none;">
      <div style="font-size:36px;margin-bottom:12px;">&#9989;</div>
      <h2 style="font-size:18px;font-weight:700;color:#1a1a1a;margin:0 0 8px 0;border:none;padding:0;">Data refreshed!</h2>
      <p style="font-size:14px;color:#6b7280;">Generating your report now&hellip;</p>
    </div>
    <div id="refreshError" style="display:none;">
      <div style="font-size:36px;margin-bottom:12px;">&#10060;</div>
      <h2 style="font-size:18px;font-weight:700;color:#1a1a1a;margin:0 0 8px 0;border:none;padding:0;">Scrape failed</h2>
      <p id="refreshErrorMsg" style="font-size:14px;color:#C8102E;margin:0 0 6px 0;"></p>
      <div style="display:flex;gap:10px;justify-content:center;margin-top:20px;">
        <button id="btnErrorRetry" class="btn btn-red">Retry</button>
        <button id="btnErrorContinue" class="btn btn-gray">Continue with cached data</button>
        <button id="btnErrorCancel" class="btn" style="background:transparent;color:#9ca3af;">Cancel</button>
      </div>
    </div>
  </div>
</div>
"""

_REFRESH_MODAL_JS = """
// ==========================================================================
// Refresh Modal — scrape progress tracking
// ==========================================================================
var SCRAPER_STAGES = [
  {key: "init",         label: "Initializing session"},
  {key: "members",      label: "Fetching member data"},
  {key: "seats",        label: "Fetching seats & subscription"},
  {key: "activity",     label: "Fetching activity metrics"},
  {key: "usage",        label: "Fetching usage metrics"},
  {key: "claude_code",  label: "Fetching Claude Code stats"},
  {key: "processing",   label: "Processing & caching data"},
  {key: "complete",     label: "Scrape complete"}
];
var _refreshPollTimer = null;
var _refreshOnProceed = null;

function showRefreshModal(onProceed) {
    _refreshOnProceed = onProceed;
    document.getElementById('refreshAsk').style.display = '';
    document.getElementById('refreshProgress').style.display = 'none';
    document.getElementById('refreshDone').style.display = 'none';
    document.getElementById('refreshError').style.display = 'none';
    fetch('/api/status')
        .then(function(r) { return r.json(); })
        .then(function(d) { document.getElementById('lastScrapeTime').textContent = d.last_scrape || 'Never'; })
        .catch(function() { document.getElementById('lastScrapeTime').textContent = 'Unknown'; });
    var stagesDiv = document.getElementById('progressStages');
    stagesDiv.innerHTML = SCRAPER_STAGES.map(function(s) {
        return '<div class="progress-stage" data-stage="' + s.key + '">' +
               '<span class="stage-icon" style="width:16px;text-align:center;flex-shrink:0;">&#9675;</span>' +
               '<span>' + s.label + '</span></div>';
    }).join('');
    document.getElementById('refreshModal').style.display = 'flex';
}
function hideRefreshModal() {
    document.getElementById('refreshModal').style.display = 'none';
    if (_refreshPollTimer) { clearInterval(_refreshPollTimer); _refreshPollTimer = null; }
    _refreshOnProceed = null;
}
function startScrapeAndTrack() {
    document.getElementById('refreshAsk').style.display = 'none';
    document.getElementById('refreshProgress').style.display = '';
    document.getElementById('progressFill').style.width = '0%';
    document.getElementById('progressPercent').textContent = '0%';
    document.getElementById('progressLabel').textContent = 'Starting scrape...';
    fetch('/api/scrape/start', {method: 'POST'})
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (!data.ok) { showScrapeError(data.error || 'Failed to start scrape'); return; }
            pollScrapeProgress(data.job_id);
        })
        .catch(function(err) { showScrapeError(err.message); });
}
function pollScrapeProgress(jobId) {
    _refreshPollTimer = setInterval(function() {
        fetch('/api/scrape/progress/' + jobId)
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (!data.ok) return;
                document.getElementById('progressFill').style.width = data.percent + '%';
                document.getElementById('progressPercent').textContent = data.percent + '%';
                document.getElementById('progressLabel').textContent = data.label;
                updateStageChecklist(data.stage);
                if (data.status === 'complete') {
                    clearInterval(_refreshPollTimer); _refreshPollTimer = null;
                    document.getElementById('refreshProgress').style.display = 'none';
                    document.getElementById('refreshDone').style.display = '';
                    var cb = _refreshOnProceed;
                    setTimeout(function() { hideRefreshModal(); if (cb) cb(); }, 1200);
                } else if (data.status === 'error') {
                    clearInterval(_refreshPollTimer); _refreshPollTimer = null;
                    showScrapeError(data.error || 'Scrape failed');
                }
            })
            .catch(function() {});
    }, 500);
}
function updateStageChecklist(currentStage) {
    var stages = document.querySelectorAll('#refreshModal .progress-stage');
    var reachedCurrent = false;
    for (var i = 0; i < stages.length; i++) {
        var el = stages[i];
        var key = el.getAttribute('data-stage');
        if (key === currentStage) {
            reachedCurrent = true;
            el.style.color = '#C8102E'; el.style.fontWeight = '600';
            el.querySelector('.stage-icon').innerHTML = '&#9673;';
        } else if (!reachedCurrent) {
            el.style.color = '#16a34a'; el.style.fontWeight = '';
            el.querySelector('.stage-icon').innerHTML = '&#10003;';
        } else {
            el.style.color = '#9ca3af'; el.style.fontWeight = '';
            el.querySelector('.stage-icon').innerHTML = '&#9675;';
        }
    }
}
function showScrapeError(msg) {
    document.getElementById('refreshProgress').style.display = 'none';
    document.getElementById('refreshAsk').style.display = 'none';
    document.getElementById('refreshError').style.display = '';
    document.getElementById('refreshErrorMsg').textContent = msg;
}
document.getElementById('btnRefreshYes').addEventListener('click', startScrapeAndTrack);
document.getElementById('btnRefreshNo').addEventListener('click', function() {
    var cb = _refreshOnProceed; hideRefreshModal(); if (cb) cb();
});
document.getElementById('btnRefreshCancel').addEventListener('click', hideRefreshModal);
document.getElementById('btnErrorRetry').addEventListener('click', function() {
    document.getElementById('refreshError').style.display = 'none';
    startScrapeAndTrack();
});
document.getElementById('btnErrorContinue').addEventListener('click', function() {
    var cb = _refreshOnProceed; hideRefreshModal(); if (cb) cb();
});
document.getElementById('btnErrorCancel').addEventListener('click', hideRefreshModal);
document.getElementById('refreshModal').addEventListener('click', function(e) {
    if (e.target === e.currentTarget) hideRefreshModal();
});
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && document.getElementById('refreshModal').style.display === 'flex') {
        hideRefreshModal();
    }
});
"""


# ---------------------------------------------------------------------------
# HTML Templates
# ---------------------------------------------------------------------------
# Base CSS shared with admin.py — duplicated here to avoid circular import
# (admin.py imports report_builder blueprint, report_builder needs the CSS)
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
    .inline-row { display: flex; gap: 12px; align-items: end; }
    .inline-row .form-group { flex: 1; }
    .modal-overlay {
        display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(0,0,0,0.5); z-index: 1000; align-items: center; justify-content: center;
    }
    .modal-box {
        background: white; border-radius: 12px; padding: 24px; width: 400px;
        max-width: 90vw; box-shadow: 0 8px 30px rgba(0,0,0,0.2);
    }
    .modal-box h2 { font-size: 16px; font-weight: 600; margin-bottom: 16px; border-bottom: 1px solid #f3f4f6; padding-bottom: 10px; }
    .progress-stage { display: flex; align-items: center; gap: 8px; padding: 4px 0; font-size: 12px; color: #9ca3af; }
    .btn-sm { padding: 6px 16px; font-size: 13px; }
    .btn-danger-text { color: #991b1b; }
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
    @media (max-width: 640px) {
        .container { padding: 12px; }
        .inline-row { flex-direction: column; }
        .navbar { padding: 10px 14px; }
        .navbar-brand { font-size: 15px; }
        .card { padding: 16px; }
        .modal-box { width: auto; margin: 16px; }
    }
"""

REPORT_MANAGER_TEMPLATE = """<!DOCTYPE html>
<html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reports — Claude Dashboard Admin</title>
<style>""" + _BASE_CSS + """
    .reports-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }
    .report-card { background: white; border-radius: 12px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); border: 1px solid #e5e7eb; transition: box-shadow 0.2s; }
    .report-card:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.12); }
    .report-card h3 { font-size: 15px; font-weight: 600; color: #111827; margin-bottom: 6px; }
    .report-meta { font-size: 12px; color: #6b7280; margin-bottom: 12px; }
    .report-actions { display: flex; gap: 8px; flex-wrap: wrap; }
    .report-actions .btn { font-size: 12px; padding: 5px 12px; }
    .action-bar { display: flex; gap: 12px; align-items: center; margin-bottom: 20px; flex-wrap: wrap; }
    .tpl-dropdown { position: relative; display: inline-block; }
    .tpl-dropdown-content { display: none; position: absolute; background: white; min-width: 240px; box-shadow: 0 4px 16px rgba(0,0,0,0.15); border-radius: 8px; z-index: 10; border: 1px solid #e5e7eb; }
    .tpl-dropdown:hover .tpl-dropdown-content { display: block; }
    .tpl-item { padding: 10px 16px; cursor: pointer; font-size: 13px; color: #374151; }
    .tpl-item:hover { background: #f3f4f6; }
    .tpl-item small { display: block; color: #9ca3af; font-size: 11px; margin-top: 2px; }
    .sched-badge { display: inline-block; font-size: 11px; padding: 2px 8px; border-radius: 4px; }
    .sched-badge-on { background: #dcfce7; color: #15803d; }
    .sched-badge-off { background: #f3f4f6; color: #6b7280; }
    .empty-state { text-align: center; padding: 48px 20px; color: #6b7280; }
    .empty-state h3 { font-size: 18px; color: #374151; margin-bottom: 8px; }
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
</style></head><body>
<div class="navbar">
    <a class="navbar-brand" href="/dashboard"><div class="badge">LM</div> Claude Dashboard Admin</a>
    <div class="nav-dropdown" id="navDropdown">
        <button class="nav-dropdown-btn" onclick="this.parentElement.classList.toggle('open')">Menu &#9662;</button>
        <div class="nav-dropdown-menu">
            <a href="/dashboard">Dashboard</a>
            <a href="/reports" class="nav-active-item">Reports</a>
            <a href="/logs">Logs</a>
            <div class="nav-dropdown-divider"></div>
            <form method="POST" action="/logout"><button type="submit">Logout</button></form>
        </div>
    </div>
</div>
<div class="toast-container" id="toastContainer"></div>
<div class="container">
    <div class="action-bar">
        <a href="/reports/new" class="btn btn-red">+ New Report</a>
        <div class="tpl-dropdown">
            <button class="btn btn-gray">From Template &#9662;</button>
            <div class="tpl-dropdown-content">
                {% for t in templates %}
                <div class="tpl-item" onclick="createFromTemplate('{{ t.id }}', '{{ t.title }}')">
                    <strong>{{ t.title }}</strong>
                    <small>{{ t.description }}</small>
                </div>
                {% endfor %}
            </div>
        </div>
    </div>

    {% if reports %}
    <div class="reports-grid">
        {% for r in reports %}
        <div class="report-card" id="card-{{ r.id }}">
            <h3>{{ r.title }}</h3>
            <div class="report-meta">
                {{ r.components|length }} components &middot;
                Updated {{ r.updated_at[:10] if r.updated_at else 'N/A' }}
                <br>
                {% if r.schedule and r.schedule.enabled %}
                <span class="sched-badge sched-badge-on">Scheduled</span>
                {% else %}
                <span class="sched-badge sched-badge-off">Not scheduled</span>
                {% endif %}
            </div>
            <div class="report-actions">
                <a href="/reports/{{ r.id }}/edit" class="btn btn-gray">Edit</a>
                <button class="btn btn-gray" onclick="cloneReport('{{ r.id }}')">Clone</button>
                <button class="btn btn-gray" onclick="previewReport('{{ r.id }}')">Preview</button>
                <button class="btn btn-gray" onclick="exportPdf('{{ r.id }}')">PDF</button>
                <button class="btn btn-gray btn-danger-text" onclick="confirmDelete('{{ r.id }}', '{{ r.title|e }}')">Delete</button>
            </div>
        </div>
        {% endfor %}
    </div>
    {% else %}
    <div class="empty-state">
        <h3>No reports yet</h3>
        <p>Create a new report or start from a template above.</p>
    </div>
    {% endif %}
</div>

<!-- Delete Confirmation Modal -->
<div id="deleteModal" class="modal-overlay">
    <div class="modal-box" style="text-align:center;">
        <h2 style="border:none;padding:0;margin-bottom:8px;">Delete Report?</h2>
        <p id="deleteMsg" style="color:#6b7280;font-size:13px;margin-bottom:20px;"></p>
        <div style="display:flex;gap:12px;justify-content:center;">
            <button class="btn btn-gray" onclick="document.getElementById('deleteModal').style.display='none'">Cancel</button>
            <button class="btn btn-red" id="deleteConfirmBtn" onclick="doDelete()">Delete</button>
        </div>
    </div>
</div>

""" + _REFRESH_MODAL_HTML + """

<script>
var deleteId = null;
function confirmDelete(id, title) {
    deleteId = id;
    var msgEl = document.getElementById('deleteMsg');
    msgEl.innerHTML = 'Loading...';
    document.getElementById('deleteModal').style.display = 'flex';
    fetch('/api/reports/' + id + '/linked-schedules')
        .then(function(r) { return r.json(); })
        .then(function(d) {
            var msg = 'Delete "' + title + '"? This cannot be undone.';
            if (d.schedules && d.schedules.length > 0) {
                msg += '<br><br><strong style="color:#991b1b;">Warning:</strong> This will also delete ' +
                    d.schedules.length + ' email schedule(s) using this report:<br>' +
                    d.schedules.map(function(s) { return '&bull; ' + s; }).join('<br>');
            }
            msgEl.innerHTML = msg;
        }).catch(function() {
            msgEl.innerHTML = 'Delete "' + title + '"? This cannot be undone.';
        });
}
function doDelete() {
    if (!deleteId) return;
    fetch('/api/reports/' + deleteId, {method: 'DELETE'})
        .then(function(r) { return r.json(); })
        .then(function(d) {
            document.getElementById('deleteModal').style.display = 'none';
            if (d.ok) {
                var card = document.getElementById('card-' + deleteId);
                if (card) card.remove();
                showToast(d.message || 'Report deleted', 'success');
            } else { showToast(d.error || 'Delete failed', 'error'); }
        }).catch(function() { showToast('Delete failed', 'error'); });
}
function cloneReport(id) {
    fetch('/api/reports/' + id + '/clone', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({})})
        .then(r => r.json())
        .then(d => {
            if (d.ok) { window.location.href = '/reports/' + d.report.id + '/edit'; }
            else { showToast(d.error || 'Clone failed', 'error'); }
        }).catch(() => showToast('Clone failed', 'error'));
}
function createFromTemplate(tplId, title) {
    fetch('/api/reports/clone-template', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({template_id: tplId, title: title})})
        .then(r => r.json())
        .then(d => {
            if (d.ok) { window.location.href = '/reports/' + d.report.id + '/edit'; }
            else { showToast(d.error || 'Failed', 'error'); }
        }).catch(() => showToast('Failed to create from template', 'error'));
}
function showToast(msg, type) {
    var c = document.getElementById('toastContainer');
    var t = document.createElement('div');
    t.className = 'toast toast-' + type;
    t.textContent = msg;
    c.appendChild(t);
    setTimeout(function() { t.remove(); }, 3000);
}
function previewReport(id) {
    showRefreshModal(function() { window.open('/api/reports/' + id + '/preview'); });
}
function exportPdf(id) {
    showRefreshModal(function() { window.location.href = '/api/reports/' + id + '/pdf'; });
}
""" + _REFRESH_MODAL_JS + """
</script>
</body></html>"""


REPORT_BUILDER_TEMPLATE = """<!DOCTYPE html>
<html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% if is_new %}New Report{% else %}Edit Report{% endif %} — Claude Dashboard Admin</title>
<style>""" + _BASE_CSS + """
    .builder-top { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }
    .builder-top input[type=text] { flex: 1; min-width: 200px; padding: 10px 14px; border: 1px solid #d1d5db; border-radius: 8px; font-size: 16px; font-weight: 600; }
    .builder-top input[type=text]:focus { border-color: #C8102E; outline: none; }
    .builder-layout { display: flex; gap: 20px; align-items: flex-start; }
    .palette { width: 280px; flex-shrink: 0; background: white; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); max-height: 70vh; overflow-y: auto; }
    .palette-category { font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: #9ca3af; font-weight: 600; margin: 12px 0 6px; padding-bottom: 4px; border-bottom: 1px solid #f3f4f6; }
    .palette-category:first-child { margin-top: 0; }
    .palette-item { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-radius: 6px; cursor: pointer; font-size: 13px; color: #374151; transition: background 0.15s; }
    .palette-item:hover { background: #f3f4f6; }
    .palette-item input[type=checkbox] { accent-color: #C8102E; }
    .canvas-wrap { flex: 1; min-height: 300px; }
    .canvas { background: white; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); min-height: 250px; }
    .canvas-empty { border: 2px dashed #d1d5db; border-radius: 12px; padding: 40px; text-align: center; color: #9ca3af; font-size: 14px; }
    .canvas-item { display: flex; align-items: center; gap: 10px; padding: 10px 12px; margin-bottom: 8px; background: #fafbfc; border: 1px solid #e5e7eb; border-radius: 8px; cursor: grab; transition: box-shadow 0.15s, border-color 0.15s; }
    .canvas-item:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
    .canvas-item.dragging { opacity: 0.5; }
    .canvas-item.drag-over { border-color: #C8102E; border-style: dashed; }
    .canvas-item .drag-handle { color: #9ca3af; cursor: grab; font-size: 16px; flex-shrink: 0; }
    .canvas-item .item-label { flex: 1; font-size: 13px; font-weight: 500; color: #374151; }
    .canvas-item .item-remove { color: #9ca3af; cursor: pointer; font-size: 16px; padding: 2px 6px; border-radius: 4px; }
    .canvas-item .item-remove:hover { color: #dc2626; background: #fee2e2; }
    .date-override { margin-top: 6px; padding: 8px 10px; background: #f9fafb; border-radius: 6px; font-size: 12px; display: none; }
    .date-override.visible { display: block; }
    .date-override label { color: #6b7280; font-size: 11px; }
    .date-override input[type=date] { padding: 4px 8px; border: 1px solid #d1d5db; border-radius: 4px; font-size: 12px; }
    .date-toggle { font-size: 11px; color: #6b7280; cursor: pointer; margin-left: 8px; }
    .date-toggle:hover { color: #C8102E; }
    .section-panel { background: white; border-radius: 12px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-top: 20px; }
    .section-panel h3 { font-size: 14px; font-weight: 600; color: #374151; margin-bottom: 12px; }
    .bottom-bar { display: flex; justify-content: space-between; align-items: center; margin-top: 20px; }
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
</style></head><body>
<div class="navbar">
    <a class="navbar-brand" href="/dashboard"><div class="badge">LM</div> Claude Dashboard Admin</a>
    <div class="nav-dropdown" id="navDropdown">
        <button class="nav-dropdown-btn" onclick="this.parentElement.classList.toggle('open')">Menu &#9662;</button>
        <div class="nav-dropdown-menu">
            <a href="/dashboard">Dashboard</a>
            <a href="/reports" class="nav-active-item">Reports</a>
            <a href="/logs">Logs</a>
            <div class="nav-dropdown-divider"></div>
            <form method="POST" action="/logout"><button type="submit">Logout</button></form>
        </div>
    </div>
</div>
<div class="toast-container" id="toastContainer"></div>
<div class="container" style="max-width:1100px;">
    <!-- Top Bar -->
    <div class="builder-top">
        <input type="text" id="reportTitle" placeholder="Report Title" value="{{ report.title|e }}" maxlength="100">
        <button class="btn btn-gray" onclick="saveReport()">Save</button>
        <button class="btn btn-red" onclick="previewReport()">Preview</button>
    </div>

    <!-- Two-column layout -->
    <div class="builder-layout">
        <!-- Component Palette -->
        <div class="palette">
            <div style="font-size:14px;font-weight:600;color:#111827;margin-bottom:10px;">Components</div>
            <div id="paletteList"></div>
        </div>

        <!-- Report Canvas -->
        <div class="canvas-wrap">
            <div class="canvas" id="reportCanvas"></div>
        </div>
    </div>

    <!-- Global Date Range -->
    <div class="section-panel">
        <h3>Date Range</h3>
        <div style="display:flex;flex-direction:column;gap:10px;">
            <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:13px;">
                <input type="radio" name="dateMode" value="all" onchange="updateDateMode()" {{ 'checked' if not report.global_date_range or (report.global_date_range and report.global_date_range.get('mode') in (None, 'all')) }}>
                All available data
            </label>
            <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:13px;">
                <input type="radio" name="dateMode" value="relative" onchange="updateDateMode()" {{ 'checked' if report.global_date_range and report.global_date_range.get('mode') == 'relative' }}>
                Rolling window: last
                <select id="relativeDays" style="width:80px;padding:4px 8px;border:1px solid #d1d5db;border-radius:6px;font-size:13px;" onchange="document.querySelector('input[name=dateMode][value=relative]').checked=true;updateDateMode();">
                    {% for d in [7, 14, 30, 60, 90] %}
                    <option value="{{ d }}" {{ 'selected' if report.global_date_range and report.global_date_range.get('relative_days')|string == d|string }}>{{ d }}</option>
                    {% endfor %}
                </select>
                days
            </label>
            <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:13px;">
                <input type="radio" name="dateMode" value="absolute" onchange="updateDateMode()" {{ 'checked' if report.global_date_range and report.global_date_range.get('mode') == 'absolute' }}>
                Fixed dates
            </label>
            <div id="absoluteDateFields" style="display:none;margin-left:24px;">
                <div class="inline-row" style="gap:12px;align-items:center;">
                    <div class="form-group" style="flex:1;margin-bottom:0;">
                        <input type="date" id="globalStart" value="{{ report.global_date_range.start if report.global_date_range and report.global_date_range.start else '' }}">
                    </div>
                    <span style="color:#9ca3af;">to</span>
                    <div class="form-group" style="flex:1;margin-bottom:0;">
                        <input type="date" id="globalEnd" value="{{ report.global_date_range.end if report.global_date_range and report.global_date_range.end else '' }}">
                    </div>
                </div>
            </div>
        </div>
        <div id="dateBoundsInfo" style="font-size:11px;color:#9ca3af;margin-top:8px;"></div>
    </div>

    <!-- Schedule -->
    <div class="section-panel">
        <h3>Scheduling</h3>
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:14px;">
            <input type="checkbox" id="schedEnabled" {{ 'checked' if report.schedule and report.schedule.enabled else '' }}>
            Available for scheduled delivery
        </label>
        <div style="font-size:12px;color:#9ca3af;margin-top:6px;">
            When enabled, this report appears in the <strong>Report Type</strong> dropdown on the
            <a href="/dashboard" style="color:#C8102E;">Dashboard</a> email schedules.
            Configure delivery days, times, and recipients there.
        </div>
    </div>

    <!-- Bottom Bar -->
    <div class="bottom-bar">
        {% if not is_new %}
        <button class="btn btn-gray btn-danger-text" onclick="deleteReport()">Delete Report</button>
        {% else %}
        <div></div>
        {% endif %}
        <button class="btn btn-red" onclick="saveReport()">Save Report</button>
    </div>
</div>

<!-- Delete Modal -->
<div id="deleteModal" class="modal-overlay">
    <div class="modal-box" style="text-align:center;">
        <h2 style="border:none;padding:0;margin-bottom:8px;">Delete this report?</h2>
        <p id="builderDeleteMsg" style="color:#6b7280;font-size:13px;margin-bottom:20px;">This cannot be undone.</p>
        <div style="display:flex;gap:12px;justify-content:center;">
            <button class="btn btn-gray" onclick="document.getElementById('deleteModal').style.display='none'">Cancel</button>
            <button class="btn btn-red" onclick="doDelete()">Delete</button>
        </div>
    </div>
</div>

""" + _REFRESH_MODAL_HTML + """

<script>
// --- State ---
var reportId = "{{ report.id }}";
var isNew = {{ 'true' if is_new else 'false' }};
var componentsMeta = {{ components_meta | tojson }};
var canvasItems = {{ report.components | tojson }};

// --- Palette ---
function renderPalette() {
    var el = document.getElementById('paletteList');
    var html = '';
    var categories = {};
    componentsMeta.forEach(function(c) {
        if (!categories[c.category]) categories[c.category] = [];
        categories[c.category].push(c);
    });
    for (var cat in categories) {
        html += '<div class="palette-category">' + cat + '</div>';
        categories[cat].forEach(function(c) {
            var checked = canvasItems.some(function(ci) { return ci.key === c.key && ci.enabled; });
            html += '<div class="palette-item">' +
                '<input type="checkbox" data-key="' + c.key + '" ' + (checked ? 'checked' : '') +
                ' onchange="toggleComponent(this)">' +
                '<span>' + c.label + '</span></div>';
        });
    }
    el.innerHTML = html;
}

function toggleComponent(checkbox) {
    var key = checkbox.getAttribute('data-key');
    if (checkbox.checked) {
        canvasItems.push({key: key, enabled: true, order: canvasItems.length, date_range: null});
    } else {
        canvasItems = canvasItems.filter(function(ci) { return ci.key !== key; });
    }
    reindex();
    renderCanvas();
}

// --- Canvas ---
function renderCanvas() {
    var el = document.getElementById('reportCanvas');
    var enabled = canvasItems.filter(function(c) { return c.enabled; });
    enabled.sort(function(a, b) { return a.order - b.order; });
    if (enabled.length === 0) {
        el.innerHTML = '<div class="canvas-empty">Select components from the left panel, then drag to reorder.</div>';
        return;
    }
    var html = '';
    enabled.forEach(function(item, idx) {
        var meta = componentsMeta.find(function(m) { return m.key === item.key; });
        var label = meta ? meta.label : item.key;
        var supportsDate = meta && meta.supports_date_range;
        html += '<div class="canvas-item" draggable="true" data-idx="' + idx + '" data-key="' + item.key + '"' +
            ' ondragstart="dragStart(event)" ondragover="dragOver(event)" ondrop="dropItem(event)" ondragend="dragEnd(event)">' +
            '<span class="drag-handle">&#9776;</span>' +
            '<span class="item-label">' + label + '</span>';
        if (supportsDate) {
            html += '<span class="date-toggle" data-key="' + item.key + '" data-action="date">&#128197;</span>';
        }
        html += '<span class="item-remove" data-key="' + item.key + '" data-action="remove">&times;</span>';
        html += '</div>';
        if (supportsDate) {
            var dr = item.date_range || {};
            var hasOverride = dr.mode === 'relative' || dr.mode === 'absolute';
            var vis = hasOverride ? ' visible' : '';
            var isRel = dr.mode === 'relative';
            var isAbs = dr.mode === 'absolute';
            html += '<div class="date-override' + vis + '" id="dateOverride_' + item.key + '">' +
                '<label style="font-size:11px;color:#6b7280;">Override date range</label>' +
                '<div style="display:flex;flex-direction:column;gap:4px;margin-top:4px;">' +
                '<label style="font-size:12px;cursor:pointer;display:flex;align-items:center;gap:4px;">' +
                '<input type="radio" name="compDate_' + item.key + '" value="relative" data-key="' + item.key + '" data-action="compmode" ' + (isRel ? 'checked' : '') + '> Last ' +
                '<select data-key="' + item.key + '" data-action="compdays" style="width:60px;padding:2px 4px;border:1px solid #d1d5db;border-radius:4px;font-size:11px;">';
            [7,14,30,60,90].forEach(function(d) {
                html += '<option value="' + d + '"' + (dr.relative_days == d ? ' selected' : '') + '>' + d + '</option>';
            });
            html += '</select> days</label>' +
                '<label style="font-size:12px;cursor:pointer;display:flex;align-items:center;gap:4px;">' +
                '<input type="radio" name="compDate_' + item.key + '" value="absolute" data-key="' + item.key + '" data-action="compmode" ' + (isAbs ? 'checked' : '') + '> Fixed: ' +
                '<input type="date" data-key="' + item.key + '" data-field="start" value="' + (dr.start || '') + '" style="font-size:11px;padding:2px;border:1px solid #d1d5db;border-radius:4px;"> &ndash; ' +
                '<input type="date" data-key="' + item.key + '" data-field="end" value="' + (dr.end || '') + '" style="font-size:11px;padding:2px;border:1px solid #d1d5db;border-radius:4px;">' +
                '</label></div></div>';
        }
    });
    el.innerHTML = html;
    // Attach click handlers via delegation (avoids quote-escaping issues in inline handlers)
    el.querySelectorAll('[data-action="remove"]').forEach(function(btn) {
        btn.addEventListener('click', function() { removeItem(btn.getAttribute('data-key')); });
    });
    el.querySelectorAll('[data-action="date"]').forEach(function(btn) {
        btn.addEventListener('click', function() { toggleDateOverride(btn.getAttribute('data-key')); });
    });
    // Per-component date mode/days/fixed change handlers
    el.querySelectorAll('[data-action="compmode"]').forEach(function(radio) {
        radio.addEventListener('change', function() { updateCompDateRange(radio.getAttribute('data-key')); });
    });
    el.querySelectorAll('[data-action="compdays"]').forEach(function(sel) {
        sel.addEventListener('change', function() {
            var key = sel.getAttribute('data-key');
            var relRadio = document.querySelector('input[name="compDate_' + key + '"][value="relative"]');
            if (relRadio) relRadio.checked = true;
            updateCompDateRange(key);
        });
    });
    el.querySelectorAll('[data-field="start"], [data-field="end"]').forEach(function(inp) {
        inp.addEventListener('change', function() {
            var key = inp.getAttribute('data-key');
            if (key) {
                var absRadio = document.querySelector('input[name="compDate_' + key + '"][value="absolute"]');
                if (absRadio) absRadio.checked = true;
                updateCompDateRange(key);
            }
        });
    });
}

function removeItem(key) {
    canvasItems = canvasItems.filter(function(c) { return c.key !== key; });
    reindex();
    renderCanvas();
    renderPalette();
}

function reindex() {
    canvasItems.forEach(function(c, i) { c.order = i; });
}

// --- Drag and Drop ---
var dragIdx = null;
function dragStart(e) {
    dragIdx = parseInt(e.target.getAttribute('data-idx'));
    e.target.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
}
function dragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    var item = e.target.closest('.canvas-item');
    if (item) item.classList.add('drag-over');
}
function dropItem(e) {
    e.preventDefault();
    var target = e.target.closest('.canvas-item');
    if (!target) return;
    var targetIdx = parseInt(target.getAttribute('data-idx'));
    if (dragIdx === null || dragIdx === targetIdx) return;
    var enabled = canvasItems.filter(function(c) { return c.enabled; });
    enabled.sort(function(a, b) { return a.order - b.order; });
    var moved = enabled.splice(dragIdx, 1)[0];
    enabled.splice(targetIdx, 0, moved);
    enabled.forEach(function(c, i) { c.order = i; });
    renderCanvas();
}
function dragEnd(e) {
    dragIdx = null;
    document.querySelectorAll('.canvas-item').forEach(function(el) {
        el.classList.remove('dragging', 'drag-over');
    });
}

// --- Date overrides ---
function toggleDateOverride(key) {
    var el = document.getElementById('dateOverride_' + key);
    if (el) el.classList.toggle('visible');
}
function updateCompDateRange(key) {
    var item = canvasItems.find(function(c) { return c.key === key; });
    if (!item) return;
    var modeEl = document.querySelector('input[name="compDate_' + key + '"]:checked');
    if (!modeEl) { item.date_range = null; return; }
    var mode = modeEl.value;
    if (!item.date_range) item.date_range = {};
    item.date_range.mode = mode;
    if (mode === 'relative') {
        var daysEl = document.querySelector('select[data-key="' + key + '"][data-action="compdays"]');
        item.date_range.relative_days = parseInt(daysEl.value);
    } else if (mode === 'absolute') {
        var startEl = document.querySelector('input[data-key="' + key + '"][data-field="start"]');
        var endEl = document.querySelector('input[data-key="' + key + '"][data-field="end"]');
        item.date_range.start = startEl.value || null;
        item.date_range.end = endEl.value || null;
    }
}

// --- Global date range mode ---
function updateDateMode() {
    var mode = document.querySelector('input[name="dateMode"]:checked').value;
    var absFields = document.getElementById('absoluteDateFields');
    absFields.style.display = mode === 'absolute' ? 'block' : 'none';
}
// Init on load
(function() {
    var checked = document.querySelector('input[name="dateMode"]:checked');
    if (checked && checked.value === 'absolute') {
        document.getElementById('absoluteDateFields').style.display = 'block';
    }
})();

function getGlobalDateRange() {
    var mode = document.querySelector('input[name="dateMode"]:checked').value;
    if (mode === 'all') return null;
    if (mode === 'relative') {
        return {mode: 'relative', relative_days: parseInt(document.getElementById('relativeDays').value)};
    }
    if (mode === 'absolute') {
        var s = document.getElementById('globalStart').value;
        var e = document.getElementById('globalEnd').value;
        return {mode: 'absolute', start: s || null, end: e || null};
    }
    return null;
}

// --- Save ---
function saveReport() {
    var title = document.getElementById('reportTitle').value.trim();
    if (!title) { showToast('Title is required', 'error'); return; }
    var enabled = canvasItems.filter(function(c) { return c.enabled; });
    if (enabled.length === 0) { showToast('Select at least one component', 'error'); return; }

    var globalRange = getGlobalDateRange();

    var schedEnabled = document.getElementById('schedEnabled').checked;
    var schedule = {enabled: schedEnabled};

    var body = {title: title, components: canvasItems, global_date_range: globalRange, schedule: schedule};
    var url = isNew ? '/api/reports' : '/api/reports/' + reportId;
    var method = isNew ? 'POST' : 'PUT';

    fetch(url, {method: method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
        .then(function(r) { return r.json(); })
        .then(function(d) {
            if (d.ok) {
                showToast('Report saved', 'success');
                if (isNew && d.report && d.report.id) {
                    reportId = d.report.id;
                    isNew = false;
                    history.replaceState(null, '', '/reports/' + reportId + '/edit');
                }
            } else { showToast(d.error || 'Save failed', 'error'); }
        }).catch(function() { showToast('Save failed', 'error'); });
}

// --- Preview (with refresh modal) ---
function previewReport() {
    if (isNew) {
        // Save first, then show refresh modal, then preview
        var title = document.getElementById('reportTitle').value.trim();
        if (!title) { showToast('Title is required', 'error'); return; }
        var enabled = canvasItems.filter(function(c) { return c.enabled; });
        if (enabled.length === 0) { showToast('Select at least one component', 'error'); return; }
        var globalRange = getGlobalDateRange();
        var body = {title: title, components: canvasItems, global_date_range: globalRange, schedule: {enabled: false}};
        fetch('/api/reports', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
            .then(function(r) { return r.json(); })
            .then(function(d) {
                if (d.ok && d.report) {
                    reportId = d.report.id;
                    isNew = false;
                    history.replaceState(null, '', '/reports/' + reportId + '/edit');
                    showRefreshModal(function() {
                        window.open('/api/reports/' + reportId + '/preview');
                    });
                } else { showToast(d.error || 'Save failed', 'error'); }
            }).catch(function() { showToast('Save failed', 'error'); });
    } else {
        showRefreshModal(function() {
            window.open('/api/reports/' + reportId + '/preview');
        });
    }
}

// --- Delete ---
function deleteReport() {
    var msgEl = document.getElementById('builderDeleteMsg');
    msgEl.innerHTML = 'Loading...';
    document.getElementById('deleteModal').style.display = 'flex';
    fetch('/api/reports/' + reportId + '/linked-schedules')
        .then(function(r) { return r.json(); })
        .then(function(d) {
            var msg = 'This cannot be undone.';
            if (d.schedules && d.schedules.length > 0) {
                msg += '<br><br><strong style="color:#991b1b;">Warning:</strong> This will also delete ' +
                    d.schedules.length + ' email schedule(s) using this report:<br>' +
                    d.schedules.map(function(s) { return '&bull; ' + s; }).join('<br>');
            }
            msgEl.innerHTML = msg;
        }).catch(function() {
            msgEl.innerHTML = 'This cannot be undone.';
        });
}
function doDelete() {
    fetch('/api/reports/' + reportId, {method: 'DELETE'})
        .then(function(r) { return r.json(); })
        .then(function(d) {
            if (d.ok) { window.location.href = '/reports'; }
            else { showToast(d.error || 'Delete failed', 'error'); }
        }).catch(function() { showToast('Delete failed', 'error'); });
}

// --- Toast ---
function showToast(msg, type) {
    var c = document.getElementById('toastContainer');
    var t = document.createElement('div');
    t.className = 'toast toast-' + type;
    t.textContent = msg;
    c.appendChild(t);
    setTimeout(function() { t.remove(); }, 3000);
}

// --- Date bounds ---
fetch('/api/reports/date-bounds').then(function(r) { return r.json(); }).then(function(d) {
    if (d.min_date && d.max_date) {
        document.getElementById('dateBoundsInfo').textContent = 'Available range: ' + d.min_date + ' to ' + d.max_date;
        document.getElementById('globalStart').min = d.min_date;
        document.getElementById('globalStart').max = d.max_date;
        document.getElementById('globalEnd').min = d.min_date;
        document.getElementById('globalEnd').max = d.max_date;
    }
}).catch(function() {});

// --- Init ---
renderPalette();
renderCanvas();

""" + _REFRESH_MODAL_JS + """
</script>
</body></html>"""
