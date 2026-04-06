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
    if not report_storage.delete_report(report_id):
        return jsonify({"ok": False, "error": "Report not found"}), 404
    _sync_report_schedules()
    return jsonify({"ok": True})


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
    # Try fresh scrape, fall back to cache
    import scraper
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
# HTML Templates
# ---------------------------------------------------------------------------
from admin import _BASE_CSS

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
    .nav-active { opacity: 1 !important; font-weight: 600; border-bottom: 2px solid white; padding-bottom: 2px; }
</style></head><body>
<div class="navbar">
    <a class="navbar-brand" href="/dashboard"><div class="badge">LM</div> Claude Dashboard Admin</a>
    <div style="display:flex;gap:16px;align-items:center;">
        <a href="/dashboard">Dashboard</a>
        <a href="/reports" class="nav-active">Reports</a>
        <a href="/logs">Logs</a>
        <form method="POST" action="/logout" style="display:inline;">
            <button type="submit" style="background:none;border:none;color:white;cursor:pointer;font-size:13px;opacity:0.9;">Logout</button>
        </form>
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
                <button class="btn btn-gray" onclick="window.open('/api/reports/{{ r.id }}/preview')">Preview</button>
                <button class="btn btn-gray" onclick="window.location='/api/reports/{{ r.id }}/pdf'">PDF</button>
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

<script>
var deleteId = null;
function confirmDelete(id, title) {
    deleteId = id;
    document.getElementById('deleteMsg').textContent = 'Delete "' + title + '"? This cannot be undone.';
    document.getElementById('deleteModal').style.display = 'flex';
}
function doDelete() {
    if (!deleteId) return;
    fetch('/api/reports/' + deleteId, {method: 'DELETE'})
        .then(r => r.json())
        .then(d => {
            document.getElementById('deleteModal').style.display = 'none';
            if (d.ok) {
                var card = document.getElementById('card-' + deleteId);
                if (card) card.remove();
                showToast('Report deleted', 'success');
            } else { showToast(d.error || 'Delete failed', 'error'); }
        }).catch(() => showToast('Delete failed', 'error'));
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
    .schedule-fields { display: none; }
    .schedule-fields.visible { display: block; }
    .bottom-bar { display: flex; justify-content: space-between; align-items: center; margin-top: 20px; }
    .nav-active { opacity: 1 !important; font-weight: 600; border-bottom: 2px solid white; padding-bottom: 2px; }
</style></head><body>
<div class="navbar">
    <a class="navbar-brand" href="/dashboard"><div class="badge">LM</div> Claude Dashboard Admin</a>
    <div style="display:flex;gap:16px;align-items:center;">
        <a href="/dashboard">Dashboard</a>
        <a href="/reports" class="nav-active">Reports</a>
        <a href="/logs">Logs</a>
        <form method="POST" action="/logout" style="display:inline;">
            <button type="submit" style="background:none;border:none;color:white;cursor:pointer;font-size:13px;opacity:0.9;">Logout</button>
        </form>
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
        <h3>Global Date Range</h3>
        <div class="inline-row" style="gap:12px;align-items:center;">
            <div class="form-group" style="flex:1;">
                <label>Start</label>
                <input type="date" id="globalStart" value="{{ report.global_date_range.start if report.global_date_range else '' }}">
            </div>
            <div class="form-group" style="flex:1;">
                <label>End</label>
                <input type="date" id="globalEnd" value="{{ report.global_date_range.end if report.global_date_range else '' }}">
            </div>
            <button class="btn btn-gray btn-sm" onclick="document.getElementById('globalStart').value='';document.getElementById('globalEnd').value='';" style="margin-top:18px;">Reset</button>
        </div>
        <div id="dateBoundsInfo" style="font-size:11px;color:#9ca3af;margin-top:4px;"></div>
    </div>

    <!-- Schedule -->
    <div class="section-panel">
        <h3>
            <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:14px;">
                <input type="checkbox" id="schedEnabled" onchange="toggleSchedule()" {{ 'checked' if report.schedule and report.schedule.enabled else '' }}>
                Enable scheduled delivery
            </label>
        </h3>
        <div class="schedule-fields {{ 'visible' if report.schedule and report.schedule.enabled else '' }}" id="schedFields">
            <div class="inline-row" style="gap:12px;">
                <div class="form-group">
                    <label>Days</label>
                    <select id="schedDays">
                        <option value="mon-fri" {{ 'selected' if report.schedule and report.schedule.cron and report.schedule.cron.day_of_week == 'mon-fri' else '' }}>Mon-Fri</option>
                        <option value="fri" {{ 'selected' if report.schedule and report.schedule.cron and report.schedule.cron.day_of_week == 'fri' else '' }}>Friday only</option>
                        <option value="mon" {{ 'selected' if report.schedule and report.schedule.cron and report.schedule.cron.day_of_week == 'mon' else '' }}>Monday only</option>
                        <option value="*" {{ 'selected' if report.schedule and report.schedule.cron and report.schedule.cron.day_of_week == '*' else '' }}>Daily</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Hour</label>
                    <select id="schedHour">
                        {% for h in range(24) %}
                        <option value="{{ h }}" {{ 'selected' if report.schedule and report.schedule.cron and report.schedule.cron.hour == h else '' }}>{{ '%02d' % h }}:00</option>
                        {% endfor %}
                    </select>
                </div>
            </div>
            <div class="form-group">
                <label>Recipients (one email per line)</label>
                <textarea id="schedRecipients" rows="3" style="font-size:13px;">{{ '\n'.join(report.schedule.recipients) if report.schedule and report.schedule.recipients else '' }}</textarea>
            </div>
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
        <p style="color:#6b7280;font-size:13px;margin-bottom:20px;">This cannot be undone.</p>
        <div style="display:flex;gap:12px;justify-content:center;">
            <button class="btn btn-gray" onclick="document.getElementById('deleteModal').style.display='none'">Cancel</button>
            <button class="btn btn-red" onclick="doDelete()">Delete</button>
        </div>
    </div>
</div>

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
            html += '<span class="date-toggle" onclick="toggleDateOverride(\'' + item.key + '\', this)">&#128197;</span>';
        }
        html += '<span class="item-remove" onclick="removeItem(\'' + item.key + '\')">&times;</span>';
        html += '</div>';
        if (supportsDate) {
            var dr = item.date_range || {};
            var vis = (dr.start || dr.end) ? ' visible' : '';
            html += '<div class="date-override' + vis + '" id="dateOverride_' + item.key + '">' +
                '<label>Override date range</label><br>' +
                '<input type="date" data-key="' + item.key + '" data-field="start" value="' + (dr.start || '') + '" onchange="setDateOverride(this)"> &mdash; ' +
                '<input type="date" data-key="' + item.key + '" data-field="end" value="' + (dr.end || '') + '" onchange="setDateOverride(this)">' +
                '</div>';
        }
    });
    el.innerHTML = html;
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
function toggleDateOverride(key, btn) {
    var el = document.getElementById('dateOverride_' + key);
    if (el) el.classList.toggle('visible');
}
function setDateOverride(input) {
    var key = input.getAttribute('data-key');
    var field = input.getAttribute('data-field');
    var item = canvasItems.find(function(c) { return c.key === key; });
    if (!item) return;
    if (!item.date_range) item.date_range = {};
    item.date_range[field] = input.value || null;
    if (!item.date_range.start && !item.date_range.end) item.date_range = null;
}

// --- Save ---
function saveReport() {
    var title = document.getElementById('reportTitle').value.trim();
    if (!title) { showToast('Title is required', 'error'); return; }
    var enabled = canvasItems.filter(function(c) { return c.enabled; });
    if (enabled.length === 0) { showToast('Select at least one component', 'error'); return; }

    var globalStart = document.getElementById('globalStart').value;
    var globalEnd = document.getElementById('globalEnd').value;
    var globalRange = (globalStart || globalEnd) ? {start: globalStart, end: globalEnd} : null;

    var schedEnabled = document.getElementById('schedEnabled').checked;
    var recipients = document.getElementById('schedRecipients').value.split('\\n').map(function(s) { return s.trim(); }).filter(function(s) { return s; });
    var schedule = {
        enabled: schedEnabled,
        cron: {
            day_of_week: document.getElementById('schedDays').value,
            hour: parseInt(document.getElementById('schedHour').value),
            minute: 0
        },
        timezone: 'America/Chicago',
        recipients: recipients
    };

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

// --- Preview ---
function previewReport() {
    if (isNew) {
        // Save first, then preview
        var title = document.getElementById('reportTitle').value.trim();
        if (!title) { showToast('Title is required', 'error'); return; }
        var enabled = canvasItems.filter(function(c) { return c.enabled; });
        if (enabled.length === 0) { showToast('Select at least one component', 'error'); return; }
        var globalStart = document.getElementById('globalStart').value;
        var globalEnd = document.getElementById('globalEnd').value;
        var globalRange = (globalStart || globalEnd) ? {start: globalStart, end: globalEnd} : null;
        var body = {title: title, components: canvasItems, global_date_range: globalRange, schedule: {enabled: false, cron: {day_of_week: 'fri', hour: 8, minute: 0}, timezone: 'America/Chicago', recipients: []}};
        fetch('/api/reports', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})
            .then(function(r) { return r.json(); })
            .then(function(d) {
                if (d.ok && d.report) {
                    reportId = d.report.id;
                    isNew = false;
                    history.replaceState(null, '', '/reports/' + reportId + '/edit');
                    window.open('/api/reports/' + reportId + '/preview');
                } else { showToast(d.error || 'Save failed', 'error'); }
            }).catch(function() { showToast('Save failed', 'error'); });
    } else {
        window.open('/api/reports/' + reportId + '/preview');
    }
}

// --- Delete ---
function deleteReport() {
    document.getElementById('deleteModal').style.display = 'flex';
}
function doDelete() {
    fetch('/api/reports/' + reportId, {method: 'DELETE'})
        .then(function(r) { return r.json(); })
        .then(function(d) {
            if (d.ok) { window.location.href = '/reports'; }
            else { showToast(d.error || 'Delete failed', 'error'); }
        }).catch(function() { showToast('Delete failed', 'error'); });
}

// --- Schedule toggle ---
function toggleSchedule() {
    var f = document.getElementById('schedFields');
    f.classList.toggle('visible', document.getElementById('schedEnabled').checked);
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
</script>
</body></html>"""
