"""
Generates a standalone HTML preview for a custom report.
Uses the same branding and Chart.js approach as html_generator.py.
"""

import copy
import html
import json
import os
from collections import Counter
from datetime import datetime

import config
from report_storage import REPORT_COMPONENTS

logger = config.get_logger()


# ---------------------------------------------------------------------------
# Helpers (same as html_generator.py)
# ---------------------------------------------------------------------------
def _escape(value) -> str:
    return html.escape(str(value)) if value else ""


def _get_initials(name: str) -> str:
    parts = name.strip().split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    elif parts:
        return parts[0][0].upper()
    return "?"


def _trend_badge(change_percent) -> str:
    if change_percent is None or change_percent == "\u2014":
        return ""
    try:
        val = float(change_percent)
    except (TypeError, ValueError):
        return ""
    if val > 0:
        arrow, color, sign = "&#9650;", "#16a34a", "+"
    elif val < 0:
        arrow, color, sign = "&#9660;", "#dc2626", ""
    else:
        arrow, color, sign = "&#9644;", "#6b7280", ""
    return (
        f'<span style="display:inline-flex;align-items:center;gap:3px;font-size:12px;'
        f'color:{color};font-weight:500;margin-top:2px;">'
        f'{arrow} {sign}{val:.0f}%</span>'
    )


# ---------------------------------------------------------------------------
# Date range utilities
# ---------------------------------------------------------------------------
def get_date_bounds(data: dict) -> dict:
    """Return min/max dates from timeseries data in last_data.json."""
    all_dates = []
    for key in ("daily_chats", "wau_chart", "dau_chart"):
        chart = data.get(key, {})
        for label in chart.get("labels", []):
            all_dates.append(label)
    # Claude code charts
    cc = data.get("claude_code", {})
    for key in ("activity_chart", "lines_chart"):
        chart = cc.get(key, {})
        for label in chart.get("labels", []):
            all_dates.append(label)
    # Try to parse as dates
    parsed = []
    for d in all_dates:
        # Labels can be "Mon 01", "2026-03-15", etc.
        # Try ISO format first
        for fmt in ("%Y-%m-%d", "%b %d", "%a %d"):
            try:
                parsed.append(datetime.strptime(d, fmt).strftime("%Y-%m-%d") if fmt == "%Y-%m-%d" else d)
                break
            except ValueError:
                continue
    # Filter to only ISO-parseable dates
    iso_dates = []
    for d in all_dates:
        if len(d) >= 10:
            try:
                datetime.strptime(d[:10], "%Y-%m-%d")
                iso_dates.append(d[:10])
            except ValueError:
                pass
    if iso_dates:
        return {"min_date": min(iso_dates), "max_date": max(iso_dates)}
    return {"min_date": "", "max_date": ""}


def resolve_date_range(date_range_config: dict, run_date=None) -> tuple:
    """Convert a date range config to absolute (start, end) strings.

    Supports three modes:
      - "relative": rolling window of N days ending on run_date (or today)
      - "absolute": fixed start/end dates
      - None / "all": no filtering (returns None, None)

    Also handles legacy format {"start": "...", "end": "..."} without a mode key
    by treating it as absolute.
    """
    if not date_range_config:
        return None, None

    mode = date_range_config.get("mode")

    # Legacy format: {start, end} without mode — treat as absolute
    if mode is None and date_range_config.get("start"):
        return date_range_config.get("start"), date_range_config.get("end")

    if mode == "relative":
        from datetime import date as date_cls, timedelta
        run = run_date or date_cls.today()
        if isinstance(run, str):
            run = datetime.strptime(run, "%Y-%m-%d").date()
        days = int(date_range_config.get("relative_days", 7))
        start = (run - timedelta(days=days)).isoformat()
        end = run.isoformat()
        return start, end

    if mode == "absolute":
        return date_range_config.get("start"), date_range_config.get("end")

    return None, None  # mode == "all" or unrecognized


def _apply_date_range(data, date_range_config, run_date=None):
    """Resolve a date range config and filter data if applicable."""
    start, end = resolve_date_range(date_range_config, run_date)
    if start and end:
        return filter_data_by_range(data, start, end), start, end
    return data, None, None


def filter_data_by_range(data: dict, start_date: str, end_date: str) -> dict:
    """Return a copy of data with timeseries filtered to [start_date, end_date]."""
    filtered = copy.deepcopy(data)
    for key in ("daily_chats", "wau_chart", "dau_chart"):
        chart = filtered.get(key, {})
        labels = chart.get("labels", [])
        values = chart.get("data", [])
        if not labels:
            continue
        new_labels, new_data = [], []
        for lbl, val in zip(labels, values):
            # Try to extract date from label
            date_str = lbl[:10] if len(lbl) >= 10 else None
            if date_str:
                try:
                    datetime.strptime(date_str, "%Y-%m-%d")
                    if start_date <= date_str <= end_date:
                        new_labels.append(lbl)
                        new_data.append(val)
                    continue
                except ValueError:
                    pass
            # If label isn't a parseable date, include it (short labels like "Mon 01")
            new_labels.append(lbl)
            new_data.append(val)
        chart["labels"] = new_labels
        chart["data"] = new_data
    return filtered


# ---------------------------------------------------------------------------
# Executive summary generation
# ---------------------------------------------------------------------------
def generate_executive_summary(data: dict, components: list, date_range: dict = None) -> str:
    """Generate a deterministic 3-5 sentence narrative from available data."""
    sentences = []
    members = data.get("members", [])
    total_seats = data.get("total_seats", len(members))
    active_count = data.get("active_members", sum(1 for m in members if m.get("status") == "Active"))
    overview = data.get("activity_overview", {})
    wau = overview.get("wau", {})
    wau_val = wau.get("value", 0)
    wau_change = wau.get("change_percent")
    utilization = overview.get("utilization", {}).get("value")

    # Seat utilization
    if total_seats > 0:
        util_pct = (active_count / total_seats) * 100
        sentences.append(
            f"The organization has {active_count} of {total_seats} seats active ({util_pct:.0f}% utilization)."
        )

    # Chat activity
    daily_chats = data.get("daily_chats", {})
    chat_data = daily_chats.get("data", [])
    if chat_data:
        total = sum(chat_data)
        days = len(chat_data)
        avg = total / days if days else 0
        sentences.append(
            f"The team logged {total:,} chats over the past {days} days, averaging {avg:.1f} per day."
        )

    # WAU highlight
    if wau_val and wau_val != "\u2014":
        if wau_change is not None:
            direction = "rose" if wau_change >= 0 else "fell"
            sentences.append(
                f"Weekly active users {direction} to {wau_val}, a {wau_change:+.0f}% change week-over-week."
            )
        else:
            sentences.append(f"Weekly active users stood at {wau_val}.")

    # Top contributor
    top_projects = data.get("top_users_projects", [])
    top_artifacts = data.get("top_users_artifacts", [])
    if top_artifacts:
        top = top_artifacts[0]
        sentences.append(
            f"The most active user was {top['name']} with {top['count']} artifacts created."
        )
    elif top_projects:
        top = top_projects[0]
        sentences.append(
            f"The top contributor was {top['name']} with {top['count']} projects created."
        )

    # Claude Code
    component_keys = {c["key"] for c in components if c.get("enabled", True)}
    cc = data.get("claude_code", {})
    cc_summary = cc.get("summary", {})
    if "claude_code_stats" in component_keys and cc_summary.get("total_lines_accepted"):
        lines = cc_summary["total_lines_accepted"]
        users = cc_summary.get("active_users", 0)
        sentences.append(
            f"Claude Code saw {lines:,} lines accepted across {users} active users."
        )

    return " ".join(sentences) if sentences else "No data available to generate a summary."


# ---------------------------------------------------------------------------
# Component HTML renderers
# ---------------------------------------------------------------------------
def _render_stats_row(data, comp, idx):
    members = data.get("members", [])
    total_seats = data.get("total_seats", len(members))
    active_count = data.get("active_members", sum(1 for m in members if m.get("status") == "Active"))
    pending_count = data.get("pending_invites", sum(1 for m in members if m.get("status") == "Pending"))
    overview = data.get("activity_overview", {})
    dau = overview.get("dau", {}).get("value", "\u2014")
    wau = overview.get("wau", {}).get("value", "\u2014")
    utilization = overview.get("utilization", {}).get("value", "\u2014")
    plan_tier = data.get("plan_tier", "Standard")
    if isinstance(utilization, (int, float)):
        utilization_str = f"{utilization:.0f}%"
    else:
        utilization_str = str(utilization)
    return f"""
    <div class="stats-row" style="grid-template-columns:repeat(4,1fr);">
        <div class="stat-card">
            <div class="stat-label">Assigned Seats</div>
            <div class="stat-value" style="color:#C8102E;">{active_count + pending_count}/{total_seats}</div>
            <div style="font-size:12px;color:#6b7280;margin-top:2px;">{active_count} active &middot; {pending_count} pending</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Daily Active Users</div>
            <div class="stat-value" style="color:#16a34a;">{dau}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Weekly Active Users</div>
            <div class="stat-value" style="color:#2563eb;">{wau}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Utilization</div>
            <div class="stat-value" style="color:#d97706;">{utilization_str}</div>
            <div style="font-size:12px;color:#6b7280;margin-top:2px;">{_escape(plan_tier)}</div>
        </div>
    </div>"""


def _render_status_pie(data, comp, idx):
    members = data.get("members", [])
    active_count = data.get("active_members", sum(1 for m in members if m.get("status") == "Active"))
    pending_count = data.get("pending_invites", sum(1 for m in members if m.get("status") == "Pending"))
    canvas_id = f"statusPie_{idx}"
    return f"""
    <div class="chart-card">
        <h3>Member Status</h3>
        <canvas id="{canvas_id}"></canvas>
    </div>""", f"""
    new Chart(document.getElementById('{canvas_id}'), {{
        type: 'pie',
        data: {{
            labels: {json.dumps(["Active", "Pending"])},
            datasets: [{{ data: {json.dumps([active_count, pending_count])}, backgroundColor: ['#16a34a', '#d97706'], borderWidth: 0 }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{ legend: {{ position: 'bottom' }} }}
        }}
    }});"""


def _render_role_pie(data, comp, idx):
    members = data.get("members", [])
    role_counts = Counter(m.get("role", "User") for m in members)
    owners = sum(v for k, v in role_counts.items() if "owner" in k.lower())
    users = sum(v for k, v in role_counts.items() if "owner" not in k.lower())
    canvas_id = f"rolePie_{idx}"
    return f"""
    <div class="chart-card">
        <h3>Role Distribution</h3>
        <canvas id="{canvas_id}"></canvas>
    </div>""", f"""
    new Chart(document.getElementById('{canvas_id}'), {{
        type: 'pie',
        data: {{
            labels: {json.dumps(["Owners", "Users"])},
            datasets: [{{ data: {json.dumps([owners, users])}, backgroundColor: ['#C8102E', '#6b7280'], borderWidth: 0 }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{ legend: {{ position: 'bottom' }} }}
        }}
    }});"""


def _render_tier_pie(data, comp, idx):
    members = data.get("members", [])
    def _tier_label(m):
        st = m.get("seat_tier", "team_standard").lower()
        return "Premium" if ("tier_1" in st or "premium" in st) else "Standard"
    tier_counts = Counter(_tier_label(m) for m in members)
    canvas_id = f"tierPie_{idx}"
    colors = ['#C8102E', '#2563eb', '#6b7280']
    return f"""
    <div class="chart-card">
        <h3>Account Type Distribution</h3>
        <canvas id="{canvas_id}"></canvas>
    </div>""", f"""
    new Chart(document.getElementById('{canvas_id}'), {{
        type: 'pie',
        data: {{
            labels: {json.dumps(list(tier_counts.keys()))},
            datasets: [{{ data: {json.dumps(list(tier_counts.values()))}, backgroundColor: {json.dumps(colors[:len(tier_counts)])}, borderWidth: 0 }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{ legend: {{ position: 'bottom' }} }}
        }}
    }});"""


def _render_daily_chats(data, comp, idx):
    daily_chats = data.get("daily_chats", {"labels": [], "data": []})
    canvas_id = f"dailyChats_{idx}"
    return f"""
    <div class="chart-card">
        <h3>Daily Chat Activity</h3>
        <canvas id="{canvas_id}"></canvas>
    </div>""", f"""
    new Chart(document.getElementById('{canvas_id}'), {{
        type: 'line',
        data: {{
            labels: {json.dumps(daily_chats.get("labels", []))},
            datasets: [{{
                label: 'Daily Chats',
                data: {json.dumps(daily_chats.get("data", []))},
                borderColor: '#C8102E',
                backgroundColor: 'rgba(200, 16, 46, 0.08)',
                fill: true, tension: 0.3,
                pointBackgroundColor: '#ffffff', pointBorderColor: '#C8102E',
                pointBorderWidth: 2, pointRadius: 5, pointHoverRadius: 7
            }}]
        }},
        options: {{
            responsive: true, maintainAspectRatio: false,
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
                y: {{ beginAtZero: true, grid: {{ color: '#f3f4f6' }} }},
                x: {{ grid: {{ display: false }} }}
            }}
        }}
    }});"""


def _render_wau_trend(data, comp, idx):
    wau_chart = data.get("wau_chart", {"labels": [], "data": []})
    canvas_id = f"wauTrend_{idx}"
    return f"""
    <div class="chart-card">
        <h3>Weekly Active Users Trend</h3>
        <canvas id="{canvas_id}"></canvas>
    </div>""", f"""
    new Chart(document.getElementById('{canvas_id}'), {{
        type: 'line',
        data: {{
            labels: {json.dumps(wau_chart.get("labels", []))},
            datasets: [{{
                label: 'WAU',
                data: {json.dumps(wau_chart.get("data", []))},
                borderColor: '#2563eb',
                backgroundColor: 'rgba(37, 99, 235, 0.08)',
                fill: true, tension: 0.3,
                pointBackgroundColor: '#ffffff', pointBorderColor: '#2563eb',
                pointBorderWidth: 2, pointRadius: 5, pointHoverRadius: 7
            }}]
        }},
        options: {{
            responsive: true, maintainAspectRatio: false,
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
                y: {{ beginAtZero: true, grid: {{ color: '#f3f4f6' }} }},
                x: {{ grid: {{ display: false }} }}
            }}
        }}
    }});"""


def _render_wau_stats_tile(data, comp, idx):
    overview = data.get("activity_overview", {})
    wau = overview.get("wau", {})
    wau_val = wau.get("value", "\u2014")
    wau_change = wau.get("change_percent")
    utilization = overview.get("utilization", {}).get("value", "\u2014")
    if isinstance(utilization, (int, float)):
        util_str = f"{utilization:.0f}%"
    else:
        util_str = str(utilization)
    wow_str = f"{wau_change:+.0f}%" if wau_change is not None else "\u2014"
    wow_color = "#16a34a" if wau_change and wau_change >= 0 else "#dc2626" if wau_change else "#6b7280"
    return f"""
    <div class="stats-row" style="grid-template-columns:repeat(3,1fr);">
        <div class="stat-card">
            <div class="stat-label">Weekly Active Users</div>
            <div class="stat-value" style="color:#2563eb;">{wau_val}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">WoW Change</div>
            <div class="stat-value" style="color:{wow_color};">{wow_str}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Utilization</div>
            <div class="stat-value" style="color:#d97706;">{util_str}</div>
        </div>
    </div>"""


def _render_activity_metrics(data, comp, idx):
    overview = data.get("activity_overview", {})
    dau = overview.get("dau", {}).get("value", "\u2014")
    wau = overview.get("wau", {}).get("value", "\u2014")
    mau = overview.get("mau", {}).get("value", "\u2014")
    utilization = overview.get("utilization", {}).get("value", "\u2014")
    if isinstance(utilization, (int, float)):
        util_str = f"{utilization:.0f}%"
    else:
        util_str = str(utilization)
    stickiness = overview.get("stickiness", {})
    stickiness_val = stickiness.get("value") if isinstance(stickiness, dict) else stickiness
    if isinstance(stickiness_val, (int, float)):
        stickiness_str = f"{stickiness_val:.0f}%"
    else:
        stickiness_str = str(stickiness_val) if stickiness_val else "\u2014"
    dau_trend = _trend_badge(overview.get("dau", {}).get("change_percent"))
    wau_trend = _trend_badge(overview.get("wau", {}).get("change_percent"))
    mau_trend = _trend_badge(overview.get("mau", {}).get("change_percent"))
    util_trend = _trend_badge(overview.get("utilization", {}).get("change_percent"))
    stk_trend = _trend_badge(stickiness.get("change_percent") if isinstance(stickiness, dict) else None)
    return f"""
    <div class="stats-row" style="grid-template-columns:repeat(5,1fr);">
        <div class="stat-card">
            <div class="stat-label">Daily Active Users</div>
            <div class="stat-value" style="color:#16a34a;">{dau}</div>
            {dau_trend}
        </div>
        <div class="stat-card">
            <div class="stat-label">Weekly Active Users</div>
            <div class="stat-value" style="color:#2563eb;">{wau}</div>
            {wau_trend}
        </div>
        <div class="stat-card">
            <div class="stat-label">Monthly Active Users</div>
            <div class="stat-value" style="color:#2563eb;">{mau}</div>
            {mau_trend}
        </div>
        <div class="stat-card">
            <div class="stat-label">Utilization</div>
            <div class="stat-value" style="color:#d97706;">{util_str}</div>
            {util_trend}
        </div>
        <div class="stat-card">
            <div class="stat-label">Stickiness (DAU/MAU)</div>
            <div class="stat-value" style="color:#8b5cf6;">{stickiness_str}</div>
            {stk_trend}
        </div>
    </div>"""


def _render_usage_stats(data, comp, idx):
    usage = data.get("usage_overview", {})
    chats_per_day = usage.get("chats_per_day", {}).get("value", "\u2014")
    projects_created = usage.get("projects_created", {}).get("value", "\u2014")
    artifacts_created = usage.get("artifacts_created", {}).get("value", "\u2014")
    cpd_trend = _trend_badge(usage.get("chats_per_day", {}).get("change_percent"))
    proj_trend = _trend_badge(usage.get("projects_created", {}).get("change_percent"))
    art_trend = _trend_badge(usage.get("artifacts_created", {}).get("change_percent"))
    return f"""
    <div class="stats-row" style="grid-template-columns:repeat(3,1fr);">
        <div class="stat-card">
            <div class="stat-label">Avg. Chats / Day</div>
            <div class="stat-value" style="color:#C8102E;">{chats_per_day}</div>
            {cpd_trend}
        </div>
        <div class="stat-card">
            <div class="stat-label">Projects Created (MTD)</div>
            <div class="stat-value" style="color:#C8102E;">{projects_created}</div>
            {proj_trend}
        </div>
        <div class="stat-card">
            <div class="stat-label">Artifacts Created (MTD)</div>
            <div class="stat-value" style="color:#C8102E;">{artifacts_created}</div>
            {art_trend}
        </div>
    </div>"""


def _render_dau_chart(data, comp, idx):
    dau_chart = data.get("dau_chart", {"labels": [], "data": []})
    canvas_id = f"dauChart_{idx}"
    return f"""
    <div class="chart-card">
        <h3>Daily Active Users (Last 30 Days)</h3>
        <canvas id="{canvas_id}"></canvas>
    </div>""", f"""
    new Chart(document.getElementById('{canvas_id}'), {{
        type: 'line',
        data: {{
            labels: {json.dumps(dau_chart.get("labels", []))},
            datasets: [{{
                label: 'DAU',
                data: {json.dumps(dau_chart.get("data", []))},
                borderColor: '#16a34a',
                backgroundColor: 'rgba(22, 163, 74, 0.08)',
                fill: true, tension: 0.3,
                pointBackgroundColor: '#ffffff', pointBorderColor: '#16a34a',
                pointBorderWidth: 2, pointRadius: 3, pointHoverRadius: 5
            }}]
        }},
        options: {{
            responsive: true, maintainAspectRatio: false,
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
                y: {{ beginAtZero: true, grid: {{ color: '#f3f4f6' }} }},
                x: {{ grid: {{ display: false }} }}
            }}
        }}
    }});"""


def _render_top_users_chats(data, comp, idx):
    top_chats = data.get("top_users_chats", [])
    canvas_id = f"topChats_{idx}"
    return f"""
    <div class="chart-card">
        <h3>Top Users by Chats MTD</h3>
        <canvas id="{canvas_id}"></canvas>
    </div>""", f"""
    new Chart(document.getElementById('{canvas_id}'), {{
        type: 'bar',
        data: {{
            labels: {json.dumps([u["name"] for u in top_chats])},
            datasets: [{{
                label: 'Chats', data: {json.dumps([u["count"] for u in top_chats])},
                backgroundColor: '#C8102E', borderRadius: 4, barThickness: 20
            }}]
        }},
        options: {{
            responsive: true, maintainAspectRatio: false, indexAxis: 'y',
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
                x: {{ beginAtZero: true, grid: {{ color: '#f3f4f6' }} }},
                y: {{ grid: {{ display: false }} }}
            }}
        }}
    }});"""


def _render_top_users_projects(data, comp, idx):
    top_projects = data.get("top_users_projects", [])
    canvas_id = f"topProjects_{idx}"
    return f"""
    <div class="chart-card">
        <h3>Top Users by Projects MTD</h3>
        <canvas id="{canvas_id}"></canvas>
    </div>""", f"""
    new Chart(document.getElementById('{canvas_id}'), {{
        type: 'bar',
        data: {{
            labels: {json.dumps([u["name"] for u in top_projects])},
            datasets: [{{
                label: 'Projects', data: {json.dumps([u["count"] for u in top_projects])},
                backgroundColor: '#C8102E', borderRadius: 4, barThickness: 20
            }}]
        }},
        options: {{
            responsive: true, maintainAspectRatio: false, indexAxis: 'y',
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
                x: {{ beginAtZero: true, grid: {{ color: '#f3f4f6' }} }},
                y: {{ grid: {{ display: false }} }}
            }}
        }}
    }});"""


def _render_top_users_artifacts(data, comp, idx):
    top_artifacts = data.get("top_users_artifacts", [])
    canvas_id = f"topArtifacts_{idx}"
    return f"""
    <div class="chart-card">
        <h3>Top Users by Artifacts MTD</h3>
        <canvas id="{canvas_id}"></canvas>
    </div>""", f"""
    new Chart(document.getElementById('{canvas_id}'), {{
        type: 'bar',
        data: {{
            labels: {json.dumps([u["name"] for u in top_artifacts])},
            datasets: [{{
                label: 'Artifacts', data: {json.dumps([u["count"] for u in top_artifacts])},
                backgroundColor: '#C8102E', borderRadius: 4, barThickness: 20
            }}]
        }},
        options: {{
            responsive: true, maintainAspectRatio: false, indexAxis: 'y',
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
                x: {{ beginAtZero: true, grid: {{ color: '#f3f4f6' }} }},
                y: {{ grid: {{ display: false }} }}
            }}
        }}
    }});"""


def _render_claude_code_stats(data, comp, idx):
    cc = data.get("claude_code", {})
    cc_summary = cc.get("summary", {})
    cc_users = cc.get("users", [])
    cc_active = cc_summary.get("active_users", 0)
    cc_sessions = cc_summary.get("total_sessions", 0)
    cc_lines = cc_summary.get("total_lines_accepted", 0)
    cc_commits = cc_summary.get("commits_created", 0)
    cc_prs = cc_summary.get("pull_requests_created", 0)

    # Top user by lines
    top_user_html = ""
    if cc_users:
        top = cc_users[0]
        top_user_html = f"""
        <div style="margin-top:12px;padding:12px;background:#f9fafb;border-radius:8px;">
            <div style="font-size:12px;color:#6b7280;margin-bottom:4px;">Top Claude Code User</div>
            <div style="font-weight:600;color:#7c3aed;">{_escape(top.get('name', ''))}</div>
            <div style="font-size:12px;color:#374151;">{top.get('total_lines_accepted', 0):,} lines accepted &middot; {top.get('total_sessions', 0)} sessions</div>
        </div>"""

    return f"""
    <div style="margin-bottom:20px;">
        <h3 style="font-size:16px;font-weight:600;color:#111827;margin-bottom:16px;">Claude Code Analytics (MTD)</h3>
        <div class="stats-row" style="grid-template-columns:repeat(5,1fr);">
            <div class="stat-card">
                <div class="stat-label">Active Users</div>
                <div class="stat-value" style="color:#7c3aed;font-size:24px;">{cc_active}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Sessions</div>
                <div class="stat-value" style="color:#7c3aed;font-size:24px;">{cc_sessions}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Lines Accepted</div>
                <div class="stat-value" style="color:#16a34a;font-size:24px;">{cc_lines:,}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Commits</div>
                <div class="stat-value" style="color:#2563eb;font-size:24px;">{cc_commits}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Pull Requests</div>
                <div class="stat-value" style="color:#2563eb;font-size:24px;">{cc_prs}</div>
            </div>
        </div>
        {top_user_html}
    </div>"""


def _render_member_directory(data, comp, idx):
    members = data.get("members", [])
    top_projects = data.get("top_users_projects", [])
    top_artifacts = data.get("top_users_artifacts", [])
    project_lookup = {u["name"]: u["count"] for u in top_projects}
    artifact_lookup = {u["name"]: u["count"] for u in top_artifacts}

    rows = ""
    for m in members:
        name = _escape(m.get("name", ""))
        email_val = _escape(m.get("email", ""))
        role = _escape(m.get("role", "User"))
        status = m.get("status", "Active")
        initials = _escape(_get_initials(m.get("name", "")))
        projects_mtd = project_lookup.get(m.get("name", ""), 0)
        artifacts_mtd = artifact_lookup.get(m.get("name", ""), 0)
        badge = ('<span style="background:#dcfce7;color:#15803d;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:500;">Active</span>'
                 if status == "Active" else
                 '<span style="background:#fef3c7;color:#b45309;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:500;">Pending</span>')
        role_style = 'color:#C8102E;font-weight:600;' if 'owner' in role.lower() else 'color:#374151;'
        rows += f"""
            <tr>
                <td style="padding:12px 16px;">
                    <div style="display:flex;align-items:center;gap:12px;">
                        <div style="width:36px;height:36px;border-radius:50%;background:#C8102E;color:white;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:600;flex-shrink:0;">{initials}</div>
                        <div>
                            <div style="font-weight:500;color:#111827;">{name}</div>
                            <div style="font-size:12px;color:#6b7280;">{email_val}</div>
                        </div>
                    </div>
                </td>
                <td style="padding:12px 16px;{role_style}">{role}</td>
                <td style="padding:12px 16px;">{badge}</td>
                <td style="padding:12px 16px;text-align:center;color:#374151;">{projects_mtd}</td>
                <td style="padding:12px 16px;text-align:center;color:#374151;">{artifacts_mtd}</td>
            </tr>"""

    return f"""
    <div class="table-card">
        <h3 style="font-size:16px;font-weight:600;color:#111827;margin-bottom:16px;">Member Directory</h3>
        <table>
            <thead>
                <tr>
                    <th>Member</th><th>Role</th><th>Status</th>
                    <th style="text-align:center;">Projects MTD</th>
                    <th style="text-align:center;">Artifacts MTD</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>
    </div>"""


def _render_executive_summary(data, comp, idx, components=None):
    summary = generate_executive_summary(data, components or [], None)
    return f"""
    <div style="background:white;border-radius:12px;padding:20px;box-shadow:0 1px 3px rgba(0,0,0,0.08);margin-bottom:20px;border-left:4px solid #e5e7eb;">
        <div style="font-size:11px;color:#9ca3af;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:8px;">Auto-generated summary</div>
        <div style="color:#374151;line-height:1.7;font-size:14px;">{_escape(summary)}</div>
    </div>"""


def _render_email_highlights(data, comp, idx):
    members = data.get("members", [])
    total_seats = data.get("total_seats", len(members))
    active_count = data.get("active_members", sum(1 for m in members if m.get("status") == "Active"))
    pending_count = data.get("pending_invites", sum(1 for m in members if m.get("status") == "Pending"))
    overview = data.get("activity_overview", {})
    dau = overview.get("dau", {}).get("value", "\u2014")
    wau = overview.get("wau", {}).get("value", "\u2014")
    utilization = overview.get("utilization", {}).get("value", "\u2014")
    if isinstance(utilization, (int, float)):
        util_str = f"{utilization:.0f}%"
    else:
        util_str = str(utilization)

    cc = data.get("claude_code", {})
    cc_summary = cc.get("summary", {})
    cc_users_count = cc_summary.get("active_users", 0)
    cc_sessions = cc_summary.get("total_sessions", 0)

    return f"""
    <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:12px;padding:20px;margin-bottom:20px;">
        <div style="font-size:14px;font-weight:600;color:#C8102E;margin-bottom:12px;">Key Stats</div>
        <div style="color:#374151;font-size:13px;line-height:1.8;">
            &bull; {total_seats} total seats ({active_count} active, {pending_count} pending)<br>
            &bull; DAU: {dau} | WAU: {wau} | Utilization: {util_str}<br>
            &bull; Claude Code: {cc_users_count} active users, {cc_sessions} sessions
        </div>
    </div>"""


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------
def generate_report_html(data: dict, report_config: dict) -> str:
    """Generate a complete standalone HTML page for a custom report."""
    title = _escape(report_config.get("title", "Custom Report"))
    components = sorted(
        [c for c in report_config.get("components", []) if c.get("enabled", True)],
        key=lambda c: c.get("order", 0),
    )
    global_range_config = report_config.get("global_date_range")

    today_str = datetime.now().strftime("%B %-d, %Y") if os.name != "nt" else datetime.now().strftime("%B %d, %Y")

    # Organization display name
    _settings = config.load_settings()
    org_name = _settings.get("org_display_name") or "Claude Usage Dashboard"

    # Resolve global date range (handles relative/absolute/all modes)
    global_start, global_end = resolve_date_range(global_range_config)

    # Date range display
    date_display = ""
    if global_start and global_end:
        try:
            s_fmt = datetime.strptime(global_start, "%Y-%m-%d").strftime("%B %-d, %Y")
            e_fmt = datetime.strptime(global_end, "%Y-%m-%d").strftime("%B %-d, %Y")
            date_display = f"{s_fmt} \u2013 {e_fmt}"
        except ValueError:
            date_display = f"{global_start} \u2013 {global_end}"

    _PIE_KEYS = {"status_pie", "status_donut", "role_pie", "role_donut", "tier_pie"}

    body_parts = []
    chart_scripts = []
    pie_buf = []  # buffer for consecutive pie chart HTML

    def _flush_pie_buf():
        """Wrap buffered pie cards in a grid row."""
        if not pie_buf:
            return
        body_parts.append(f'<div class="pie-row">{"".join(pie_buf)}</div>')
        pie_buf.clear()

    for idx, comp in enumerate(components):
        key = comp.get("key")
        # Apply date range filtering — per-component override or global
        comp_data = data
        comp_range = comp.get("date_range")
        comp_start, comp_end = resolve_date_range(comp_range)
        if comp_start and comp_end:
            comp_data = filter_data_by_range(data, comp_start, comp_end)
        elif global_start and global_end:
            comp_data = filter_data_by_range(data, global_start, global_end)

        result = None
        if key == "stats_row":
            result = _render_stats_row(comp_data, comp, idx)
        elif key in ("status_pie", "status_donut"):
            result = _render_status_pie(comp_data, comp, idx)
        elif key in ("role_pie", "role_donut"):
            result = _render_role_pie(comp_data, comp, idx)
        elif key == "tier_pie":
            result = _render_tier_pie(comp_data, comp, idx)
        elif key == "daily_chats":
            result = _render_daily_chats(comp_data, comp, idx)
        elif key == "wau_trend":
            result = _render_wau_trend(comp_data, comp, idx)
        elif key == "wau_stats_tile":
            result = _render_wau_stats_tile(comp_data, comp, idx)
        elif key == "activity_metrics":
            result = _render_activity_metrics(comp_data, comp, idx)
        elif key == "usage_stats":
            result = _render_usage_stats(comp_data, comp, idx)
        elif key == "dau_chart":
            result = _render_dau_chart(comp_data, comp, idx)
        elif key == "top_users_chats":
            result = _render_top_users_chats(comp_data, comp, idx)
        elif key == "top_users_projects":
            result = _render_top_users_projects(comp_data, comp, idx)
        elif key == "top_users_artifacts":
            result = _render_top_users_artifacts(comp_data, comp, idx)
        elif key == "claude_code_stats":
            result = _render_claude_code_stats(comp_data, comp, idx)
        elif key == "member_directory":
            result = _render_member_directory(comp_data, comp, idx)
        elif key == "executive_summary":
            result = _render_executive_summary(comp_data, comp, idx, components=components)
        elif key == "email_highlights":
            result = _render_email_highlights(comp_data, comp, idx)

        if result is None:
            continue
        is_pie = key in _PIE_KEYS
        if not is_pie:
            _flush_pie_buf()
        if isinstance(result, tuple):
            if is_pie:
                pie_buf.append(result[0])
            else:
                body_parts.append(result[0])
            chart_scripts.append(result[1])
        else:
            if is_pie:
                pie_buf.append(result)
            else:
                body_parts.append(result)

    _flush_pie_buf()
    body_html = "\n".join(body_parts)
    scripts_html = "\n".join(chart_scripts)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} \u2014 Claude Usage Dashboard</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f5f5f5; color: #1a1a1a; line-height: 1.5;
        }}
        .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
        .header {{
            background: #C8102E; padding: 20px 32px;
            display: flex; align-items: center; justify-content: space-between; color: white;
        }}
        .header-left {{ display: flex; align-items: center; gap: 16px; }}
        .logo-badge {{
            width: 48px; height: 48px; border-radius: 50%; background: white;
            display: flex; align-items: center; justify-content: center;
            font-weight: 700; font-size: 18px; color: #C8102E;
        }}
        .header-title {{ font-size: 22px; font-weight: 700; }}
        .header-subtitle {{ font-size: 13px; opacity: 0.9; }}
        .header-report-title {{ font-size: 15px; color: #FFB3B3; margin-top: 2px; }}
        .date-badge {{
            background: rgba(255,255,255,0.2); padding: 6px 16px;
            border-radius: 20px; font-size: 13px; font-weight: 500;
        }}
        .stats-row {{
            display: grid; grid-template-columns: repeat(4, 1fr);
            gap: 16px; margin: 20px 0;
        }}
        .stat-card {{
            background: #ffffff; border-radius: 12px; padding: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        }}
        .stat-label {{ font-size: 13px; color: #6b7280; margin-bottom: 4px; }}
        .stat-value {{ font-size: 28px; font-weight: 700; }}
        .charts-row-2 {{
            display: grid; grid-template-columns: repeat(2, 1fr);
            gap: 16px; margin-bottom: 20px;
        }}
        .pie-row {{
            display: grid; grid-template-columns: repeat(3, 1fr);
            gap: 16px; margin-bottom: 20px;
        }}
        .chart-card {{
            background: #ffffff; border-radius: 12px; padding: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-bottom: 20px;
        }}
        .pie-row .chart-card {{ margin-bottom: 0; }}
        .chart-card h3 {{
            font-size: 15px; font-weight: 600; color: #374151; margin-bottom: 12px;
        }}
        .chart-card canvas {{ height: 220px !important; }}
        .table-card {{
            background: #ffffff; border-radius: 12px; padding: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-bottom: 20px;
        }}
        table {{ width: 100%; border-collapse: collapse; }}
        thead th {{
            padding: 10px 16px; text-align: left; font-size: 12px;
            font-weight: 600; color: #6b7280; text-transform: uppercase;
            letter-spacing: 0.05em; border-bottom: 1px solid #e5e7eb;
        }}
        tbody tr {{ border-bottom: 1px solid #f3f4f6; }}
        tbody tr:hover {{ background: #fafafa; }}
        .footer {{
            text-align: center; padding: 24px; color: #9ca3af; font-size: 12px;
        }}
        @media (max-width: 768px) {{
            .stats-row {{ grid-template-columns: repeat(2, 1fr); }}
            .charts-row-2 {{ grid-template-columns: 1fr; }}
            .pie-row {{ grid-template-columns: 1fr; }}
            .header {{ flex-direction: column; gap: 12px; text-align: center; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="header-left">
            <div class="logo-badge">LM</div>
            <div>
                <div class="header-title">Claude Usage Dashboard</div>
                <div class="header-subtitle">{_escape(org_name)}</div>
                <div class="header-report-title">{title}</div>
            </div>
        </div>
        <div class="date-badge">{_escape(date_display) if date_display else f'As of {_escape(today_str)}'}</div>
    </div>

    <div class="container">
        {body_html}

        <div class="footer">
            Data sourced from Claude.ai Admin Console and Claude.ai Analytics &middot;
            {_escape(org_name)} &middot; {_escape(today_str)} &middot; v{config.VERSION}
        </div>
    </div>

    <script>
        {scripts_html}
    </script>
</body>
</html>"""
