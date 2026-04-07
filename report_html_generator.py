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


def _section_header(text: str) -> str:
    """Return HTML for a styled section divider matching the PDF SectionHeader."""
    return f'<div class="section-title">{_escape(text).upper()}</div>'


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
    assigned = active_count + pending_count
    available = total_seats - assigned
    plan_tier = data.get("plan_tier", "Standard")

    # Premium member sub-text (matches PDF logic)
    premium_members = [m for m in members
                       if "tier_1" in m.get("seat_tier", "").lower()
                       or "premium" in m.get("seat_tier", "").lower()]
    if premium_members:
        names = []
        for m in premium_members[:3]:
            parts = m.get("name", "").split()
            if len(parts) > 1:
                names.append(f"{parts[0]} {parts[-1][0]}.")
            else:
                names.append(m.get("name", ""))
        tier_subtitle = f"+{len(premium_members)} Premium ({', '.join(names)})"
    else:
        tier_subtitle = "All standard seats"

    return f"""
    <div class="stats-row" style="grid-template-columns:repeat(4,1fr);">
        <div class="stat-card">
            <div class="stat-label">Total Seats</div>
            <div class="stat-value" style="color:var(--red);">{total_seats}</div>
            <div style="font-size:12px;color:var(--muted);margin-top:2px;">{available} available &middot; {assigned} assigned</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Active Members</div>
            <div class="stat-value" style="color:var(--active);">{active_count}</div>
            <div style="font-size:12px;color:var(--muted);margin-top:2px;">Onboarded &amp; using Claude</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Pending Invites</div>
            <div class="stat-value" style="color:var(--pending);">{pending_count}</div>
            <div style="font-size:12px;color:var(--muted);margin-top:2px;">Haven&#39;t accepted invite yet</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Seat Tier</div>
            <div class="stat-value" style="color:var(--red);">{_escape(plan_tier)}</div>
            <div style="font-size:12px;color:var(--muted);margin-top:2px;">{_escape(tier_subtitle)}</div>
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
    mini_id = f"dcMini_{idx}"
    return f"""
    <div class="chart-card">
        <h3>Daily Chat Activity</h3>
        <canvas id="{canvas_id}"></canvas>
        <div class="stat-mini-row" id="{mini_id}">
            <div class="stat-mini-box"><div class="mini-value" id="{mini_id}_total">—</div><div class="mini-label" id="{mini_id}_total_lbl">Total chats</div></div>
            <div class="stat-mini-box"><div class="mini-value" id="{mini_id}_peak">—</div><div class="mini-label">Peak daily chats</div></div>
            <div class="stat-mini-box"><div class="mini-value" id="{mini_id}_avg">—</div><div class="mini-label">Avg chats / day</div></div>
            <div class="stat-mini-box"><div class="mini-value" id="{mini_id}_eng">—</div><div class="mini-label">Team engagement</div></div>
        </div>
    </div>""", f"""
    (function() {{
        var chatData = {json.dumps(daily_chats.get("data", []))};
        new Chart(document.getElementById('{canvas_id}'), {{
            type: 'line',
            data: {{
                labels: {json.dumps(daily_chats.get("labels", []))},
                datasets: [{{
                    label: 'Daily Chats',
                    data: chatData,
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
        }});
        if (chatData.length > 0) {{
            var total = chatData.reduce(function(a,b){{ return a+b; }}, 0);
            var peak = Math.max.apply(null, chatData);
            var avg = (total / chatData.length).toFixed(1);
            document.getElementById('{mini_id}_total').textContent = total;
            document.getElementById('{mini_id}_total_lbl').textContent = 'Total chats (' + chatData.length + ' days)';
            document.getElementById('{mini_id}_peak').textContent = peak;
            document.getElementById('{mini_id}_avg').textContent = avg;
            var engEl = document.getElementById('{mini_id}_eng');
            if (parseFloat(avg) >= 3) {{
                engEl.textContent = '\\u2191 Active';
                engEl.style.color = 'var(--active)';
            }} else {{
                engEl.textContent = '\\u2192 Moderate';
                engEl.style.color = 'var(--pending)';
            }}
        }}
    }})();"""


def _render_wau_trend(data, comp, idx):
    wau_chart = data.get("wau_chart", {"labels": [], "data": []})
    wau_data = wau_chart.get("data", [])
    wau_labels = wau_chart.get("labels", [])
    total_seats = data.get("total_seats", 0)
    canvas_id = f"wauTrend_{idx}"
    mini_id = f"wauMini_{idx}"

    # Graceful degradation: no data → placeholder
    if not wau_data:
        return f"""
    <div class="chart-card">
        <h3>Weekly Active Users Trend</h3>
        <div style="text-align:center;padding:40px 0;color:var(--muted);font-size:14px;">No WAU data available</div>
    </div>"""

    return f"""
    <div class="chart-card">
        <h3>Weekly Active Users Trend</h3>
        <canvas id="{canvas_id}"></canvas>
        <div class="stat-mini-row" id="{mini_id}">
            <div class="stat-mini-box"><div class="mini-value" id="{mini_id}_cur">—</div><div class="mini-label">Current WAU</div></div>
            <div class="stat-mini-box"><div class="mini-value" id="{mini_id}_wow">—</div><div class="mini-label">WoW change</div></div>
            <div class="stat-mini-box"><div class="mini-value" id="{mini_id}_util">—</div><div class="mini-label">Utilization rate</div></div>
            <div class="stat-mini-box"><div class="mini-value" id="{mini_id}_growth">—</div><div class="mini-label" id="{mini_id}_growth_lbl">Growth</div></div>
        </div>
    </div>""", f"""
    (function() {{
        var wauData = {json.dumps(wau_data)};
        var wauLabels = {json.dumps(wau_labels)};
        var totalSeats = {total_seats};

        var wowPlugin_{idx} = {{
            id: 'wowAnnotations_{idx}',
            afterDraw: function(chart) {{
                var ctx = chart.ctx;
                var xAxis = chart.scales.x;
                var chartArea = chart.chartArea;
                ctx.save();
                ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, sans-serif';
                ctx.textAlign = 'center';
                for (var i = 1; i < wauData.length; i++) {{
                    var prev = wauData[i-1];
                    var cur = wauData[i];
                    if (prev === 0) continue;
                    var pct = ((cur - prev) / prev * 100);
                    var x0 = xAxis.getPixelForValue(i-1);
                    var x1 = xAxis.getPixelForValue(i);
                    var midX = (x0 + x1) / 2;
                    var y = chartArea.bottom + 18;
                    if (pct > 0) {{
                        ctx.fillStyle = '#16a34a';
                        ctx.fillText('+' + pct.toFixed(0) + '%', midX, y);
                    }} else if (pct < 0) {{
                        ctx.fillStyle = '#C8102E';
                        ctx.fillText(pct.toFixed(0) + '%', midX, y);
                    }} else {{
                        ctx.fillStyle = '#6b7280';
                        ctx.fillText('0%', midX, y);
                    }}
                }}
                ctx.restore();
            }}
        }};

        new Chart(document.getElementById('{canvas_id}'), {{
            type: 'line',
            data: {{
                labels: wauLabels,
                datasets: [{{
                    label: 'WAU',
                    data: wauData,
                    borderColor: '#2563eb',
                    backgroundColor: 'rgba(37, 99, 235, 0.08)',
                    fill: true, tension: 0.3,
                    pointBackgroundColor: '#ffffff', pointBorderColor: '#2563eb',
                    pointBorderWidth: 2, pointRadius: 5, pointHoverRadius: 7
                }}]
            }},
            options: {{
                responsive: true, maintainAspectRatio: false,
                layout: {{ padding: {{ bottom: 24 }} }},
                plugins: {{ legend: {{ display: false }} }},
                scales: {{
                    y: {{ beginAtZero: true, grid: {{ color: '#f3f4f6' }} }},
                    x: {{ grid: {{ display: false }} }}
                }}
            }},
            plugins: [wowPlugin_{idx}]
        }});

        // Populate stat mini-boxes
        var curWau = wauData[wauData.length - 1];
        document.getElementById('{mini_id}_cur').textContent = curWau;
        document.getElementById('{mini_id}_cur').style.color = 'var(--red)';

        // WoW change
        if (wauData.length >= 2) {{
            var prevWau = wauData[wauData.length - 2];
            var wowPct = prevWau > 0 ? ((curWau - prevWau) / prevWau * 100) : 0;
            var wowEl = document.getElementById('{mini_id}_wow');
            wowEl.textContent = (wowPct >= 0 ? '+' : '') + wowPct.toFixed(1) + '%';
            wowEl.style.color = wowPct >= 0 ? 'var(--active)' : 'var(--red)';
        }}

        // Utilization rate
        var utilEl = document.getElementById('{mini_id}_util');
        if (totalSeats > 0) {{
            utilEl.textContent = (curWau / totalSeats * 100).toFixed(1) + '%';
        }} else {{
            utilEl.textContent = 'N/A';
        }}
        utilEl.style.color = 'var(--text)';

        // Growth since first data point
        var firstWau = wauData[0];
        if (firstWau > 0) {{
            var growthPct = ((curWau - firstWau) / firstWau * 100);
            var growthEl = document.getElementById('{mini_id}_growth');
            growthEl.textContent = (growthPct >= 0 ? '+' : '') + growthPct.toFixed(0) + '%';
            growthEl.style.color = growthPct >= 0 ? 'var(--active)' : 'var(--red)';
            document.getElementById('{mini_id}_growth_lbl').textContent = 'Growth since ' + wauLabels[0];
        }}
    }})();"""


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
                backgroundColor: ["#C8102E","#e05070","#e87090","#f090a8","#f8afc0"].slice(0, {len(top_chats)}), borderRadius: 4, barThickness: 20
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
                backgroundColor: ["#C8102E","#e05070","#e87090","#f090a8","#f8afc0"].slice(0, {len(top_projects)}), borderRadius: 4, barThickness: 20
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
                backgroundColor: ["#C8102E","#e05070","#e87090","#f090a8","#f8afc0"].slice(0, {len(top_artifacts)}), borderRadius: 4, barThickness: 20
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


def _render_cc_sessions_chart(data, comp, idx):
    cc = data.get("claude_code", {})
    cc_activity_chart = cc.get("activity_chart", {"labels": [], "data": []})
    canvas_id = f"ccSessions_{idx}"
    return f"""
    <div class="chart-card">
        <h3>Claude Code Sessions (Daily)</h3>
        <canvas id="{canvas_id}"></canvas>
    </div>""", f"""
    new Chart(document.getElementById('{canvas_id}'), {{
        type: 'line',
        data: {{
            labels: {json.dumps(cc_activity_chart.get("labels", []))},
            datasets: [{{
                label: 'Sessions',
                data: {json.dumps(cc_activity_chart.get("data", []))},
                borderColor: '#7c3aed',
                backgroundColor: 'rgba(124, 58, 237, 0.08)',
                fill: true, tension: 0.3,
                pointBackgroundColor: '#ffffff', pointBorderColor: '#7c3aed',
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


def _render_cc_lines_chart(data, comp, idx):
    cc = data.get("claude_code", {})
    cc_lines_chart = cc.get("lines_chart", {"labels": [], "data": []})
    canvas_id = f"ccLines_{idx}"
    return f"""
    <div class="chart-card">
        <h3>Claude Code Lines Accepted (Daily)</h3>
        <canvas id="{canvas_id}"></canvas>
    </div>""", f"""
    new Chart(document.getElementById('{canvas_id}'), {{
        type: 'line',
        data: {{
            labels: {json.dumps(cc_lines_chart.get("labels", []))},
            datasets: [{{
                label: 'Lines Accepted',
                data: {json.dumps(cc_lines_chart.get("data", []))},
                borderColor: '#16a34a',
                backgroundColor: 'rgba(22, 163, 74, 0.08)',
                fill: true, tension: 0.3,
                pointBackgroundColor: '#ffffff', pointBorderColor: '#16a34a',
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


def _render_cc_top_users(data, comp, idx):
    cc = data.get("claude_code", {})
    cc_users = cc.get("users", [])
    names = json.dumps([u.get("name", "?") for u in cc_users[:10]])
    sessions = json.dumps([u.get("total_sessions", 0) for u in cc_users[:10]])
    lines = json.dumps([u.get("total_lines_accepted", 0) for u in cc_users[:10]])
    cid1 = f"ccTopSessions_{idx}"
    cid2 = f"ccTopLines_{idx}"
    return f"""
    <div class="charts-row-2">
        <div class="chart-card">
            <h3>Top Claude Code Users (Sessions MTD)</h3>
            <canvas id="{cid1}"></canvas>
        </div>
        <div class="chart-card">
            <h3>Top Claude Code Users (Lines Accepted MTD)</h3>
            <canvas id="{cid2}"></canvas>
        </div>
    </div>""", f"""
    new Chart(document.getElementById('{cid1}'), {{
        type: 'bar',
        data: {{
            labels: {names},
            datasets: [{{ label: 'Sessions', data: {sessions}, backgroundColor: '#7c3aed', borderRadius: 4, barThickness: 20 }}]
        }},
        options: {{
            responsive: true, maintainAspectRatio: false, indexAxis: 'y',
            plugins: {{ legend: {{ display: false }} }},
            scales: {{ x: {{ beginAtZero: true, grid: {{ color: '#f3f4f6' }} }}, y: {{ grid: {{ display: false }} }} }}
        }}
    }});
    new Chart(document.getElementById('{cid2}'), {{
        type: 'bar',
        data: {{
            labels: {names},
            datasets: [{{ label: 'Lines Accepted', data: {lines}, backgroundColor: '#16a34a', borderRadius: 4, barThickness: 20 }}]
        }},
        options: {{
            responsive: true, maintainAspectRatio: false, indexAxis: 'y',
            plugins: {{ legend: {{ display: false }} }},
            scales: {{ x: {{ beginAtZero: true, grid: {{ color: '#f3f4f6' }} }}, y: {{ grid: {{ display: false }} }} }}
        }}
    }});"""


def _render_cc_user_table(data, comp, idx):
    cc = data.get("claude_code", {})
    cc_users = cc.get("users", [])
    if not cc_users:
        return ""
    rows = ""
    for u in cc_users:
        u_name = _escape(u.get("name", ""))
        u_email = _escape(u.get("email", ""))
        u_initials = _escape(_get_initials(u.get("name", "")))
        u_sessions = u.get("total_sessions", 0)
        u_lines = u.get("total_lines_accepted", 0)
        u_commits = u.get("commits_created", 0)
        u_prs_val = u.get("pull_requests_created", 0)
        u_last = _escape(u.get("last_active", "\u2014") or "\u2014")
        if u_last != "\u2014" and len(u_last) >= 10:
            u_last = u_last[:10]
        rows += f"""
            <tr>
                <td style="padding:12px 16px;">
                    <div style="display:flex;align-items:center;gap:12px;">
                        <div style="width:36px;height:36px;border-radius:50%;background:#7c3aed;color:white;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:600;flex-shrink:0;">{u_initials}</div>
                        <div>
                            <div style="font-weight:500;color:#111827;">{u_name}</div>
                            <div style="font-size:12px;color:#6b7280;">{u_email}</div>
                        </div>
                    </div>
                </td>
                <td style="padding:12px 16px;text-align:center;color:#374151;">{u_sessions}</td>
                <td style="padding:12px 16px;text-align:center;color:#374151;">{u_lines:,}</td>
                <td style="padding:12px 16px;text-align:center;color:#374151;">{u_commits}</td>
                <td style="padding:12px 16px;text-align:center;color:#374151;">{u_prs_val}</td>
                <td style="padding:12px 16px;color:#374151;">{u_last}</td>
            </tr>"""
    return f"""
    <div class="table-card">
        <h3 style="font-size:16px;font-weight:600;color:#111827;margin-bottom:16px;">Claude Code User Breakdown (MTD)</h3>
        <table>
            <thead>
                <tr>
                    <th>User</th>
                    <th style="text-align:center;">Sessions</th>
                    <th style="text-align:center;">Lines Accepted</th>
                    <th style="text-align:center;">Commits</th>
                    <th style="text-align:center;">PRs</th>
                    <th>Last Active</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>
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
        seat_tier = m.get("seat_tier", "team_standard")
        is_premium = "tier_1" in seat_tier.lower() or "premium" in seat_tier.lower()
        tier_label = "Premium" if is_premium else "Standard"
        projects_mtd = project_lookup.get(m.get("name", ""), 0)
        artifacts_mtd = artifact_lookup.get(m.get("name", ""), 0)

        # Premium badge inline with name
        premium_badge = '<span class="tier-tag">Premium</span>' if is_premium else ""

        # Status badge
        badge = ('<span style="background:#dcfce7;color:#15803d;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:500;">Active</span>'
                 if status == "Active" else
                 '<span style="background:#fef3c7;color:#b45309;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:500;">Pending</span>')

        # Role styling
        role_style = 'color:var(--red);font-weight:600;' if 'owner' in role.lower() else 'color:var(--text);'

        # Tier cell styling
        tier_style = 'color:var(--purple);font-weight:600;' if is_premium else 'color:var(--muted);font-size:12px;'

        # Projects/Artifacts conditional formatting
        proj_style = 'color:var(--red);font-weight:700;' if projects_mtd > 0 else 'color:var(--muted);'
        art_style = 'color:var(--red);font-weight:700;' if artifacts_mtd > 0 else 'color:var(--muted);'

        rows += f"""
            <tr data-name="{_escape(m.get('name', '').lower())}" data-email="{_escape(m.get('email', '').lower())}" data-status="{status.lower()}">
                <td style="padding:10px 16px;">
                    <div style="font-weight:600;font-size:13px;color:#111827;">{name}{premium_badge}</div>
                    <div style="font-size:11px;color:var(--muted);margin-top:1px;">{email_val}</div>
                </td>
                <td style="padding:10px 16px;{role_style}">{role}</td>
                <td style="padding:10px 16px;{tier_style}">{tier_label}</td>
                <td style="padding:10px 16px;">{badge}</td>
                <td style="padding:10px 16px;text-align:center;{proj_style}">{projects_mtd}</td>
                <td style="padding:10px 16px;text-align:center;{art_style}">{artifacts_mtd}</td>
            </tr>"""

    html = f"""
    <div class="table-card">
        <h3 style="font-size:16px;font-weight:600;color:#111827;margin-bottom:16px;">Member Directory</h3>
        <div class="table-controls">
            <input type="text" id="memberSearch_{idx}" placeholder="Search by name or email...">
            <select id="memberStatusFilter_{idx}">
                <option value="">All Status</option>
                <option value="active">Active</option>
                <option value="pending">Pending</option>
            </select>
        </div>
        <table class="member-table">
            <thead>
                <tr>
                    <th onclick="sortMemberTable_{idx}(0)">Member</th>
                    <th onclick="sortMemberTable_{idx}(1)">Role</th>
                    <th onclick="sortMemberTable_{idx}(2)">Tier</th>
                    <th onclick="sortMemberTable_{idx}(3)">Status</th>
                    <th onclick="sortMemberTable_{idx}(4)" style="text-align:center;">Projects MTD</th>
                    <th onclick="sortMemberTable_{idx}(5)" style="text-align:center;">Artifacts MTD</th>
                </tr>
            </thead>
            <tbody id="memberTbody_{idx}">{rows}</tbody>
        </table>
    </div>"""

    script = f"""
    (function() {{
        var sortDir = [1, 1, 1, 1, 1, 1];
        window.sortMemberTable_{idx} = function(col) {{
            var tbody = document.getElementById('memberTbody_{idx}');
            var rows = Array.from(tbody.querySelectorAll('tr'));
            sortDir[col] *= -1;
            rows.sort(function(a, b) {{
                var aVal = a.cells[col].textContent.trim();
                var bVal = b.cells[col].textContent.trim();
                if (col >= 4) {{
                    return (parseInt(aVal) - parseInt(bVal)) * sortDir[col];
                }}
                return aVal.localeCompare(bVal) * sortDir[col];
            }});
            rows.forEach(function(r) {{ tbody.appendChild(r); }});
        }};
        function filterMembers() {{
            var search = document.getElementById('memberSearch_{idx}').value.toLowerCase();
            var status = document.getElementById('memberStatusFilter_{idx}').value;
            var rows = document.querySelectorAll('#memberTbody_{idx} tr');
            rows.forEach(function(row) {{
                var name = row.getAttribute('data-name') || '';
                var email = row.getAttribute('data-email') || '';
                var rowStatus = row.getAttribute('data-status') || '';
                var matchesSearch = name.indexOf(search) !== -1 || email.indexOf(search) !== -1;
                var matchesStatus = !status || rowStatus === status;
                row.style.display = (matchesSearch && matchesStatus) ? '' : 'none';
            }});
        }}
        document.getElementById('memberSearch_{idx}').addEventListener('input', filterMembers);
        document.getElementById('memberStatusFilter_{idx}').addEventListener('change', filterMembers);
    }})();"""

    return html, script


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

    # Section header mapping — group key → header text
    _SECTION_GROUP = {
        "activity_metrics": "activity",
        "daily_chats": "activity",
        "usage_stats": "activity",
        "dau_chart": "activity",
        "wau_trend": "wau",
        "wau_stats_tile": "wau",
        "claude_code_stats": "claude_code",
        "member_directory": "members",
    }
    _SECTION_TEXT = {
        "activity": "Activity Analytics \u00b7 Claude.ai/Analytics \u00b7 MTD \u00b7 Updated Daily",
        "wau": "Weekly Active Users \u00b7 Claude.ai/Analytics \u00b7 Rolling 7-Day Window",
        "members": "All Members",
    }
    seen_sections = set()

    for idx, comp in enumerate(components):
        key = comp.get("key")

        # Inject section header if this component starts a new section
        section_group = _SECTION_GROUP.get(key)
        if section_group and section_group not in seen_sections:
            seen_sections.add(section_group)
            if section_group == "claude_code":
                cc_summary = data.get("claude_code", {}).get("summary", {})
                cc_active = cc_summary.get("active_users", 0)
                month_str = datetime.now().strftime("%B %Y")
                body_parts.append(_section_header(
                    f"Claude Code \u00b7 {month_str} \u00b7 {cc_active} Active User(s)"
                ))
            else:
                body_parts.append(_section_header(_SECTION_TEXT[section_group]))

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
        elif key == "cc_sessions_chart":
            result = _render_cc_sessions_chart(comp_data, comp, idx)
        elif key == "cc_lines_chart":
            result = _render_cc_lines_chart(comp_data, comp, idx)
        elif key == "cc_top_users":
            result = _render_cc_top_users(comp_data, comp, idx)
        elif key == "cc_user_table":
            result = _render_cc_user_table(comp_data, comp, idx)
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
        :root {{
            --red: #C8102E; --dark-red: #a00d24; --active: #16a34a;
            --pending: #d97706; --muted: #6b7280; --bg: #f5f5f5;
            --border: #e5e7eb; --purple: #7c3aed; --text: #374151;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg); color: #1a1a1a; line-height: 1.5;
        }}
        .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
        .header {{
            background: var(--red); padding: 20px 32px;
            display: flex; align-items: center; justify-content: space-between; color: white;
        }}
        .header-left {{ display: flex; align-items: center; gap: 16px; }}
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
        .stat-label {{ font-size: 13px; color: var(--muted); margin-bottom: 4px; }}
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
            font-size: 15px; font-weight: 600; color: var(--text); margin-bottom: 12px;
        }}
        .chart-card canvas {{ height: 220px !important; }}
        .pie-row .chart-card canvas {{ height: 180px !important; }}
        /* --- Stat mini-boxes below charts --- */
        .stat-mini-row {{
            display: flex; border-top: 1px solid var(--border);
            margin-top: 16px; padding-top: 14px;
        }}
        .stat-mini-box {{
            flex: 1; text-align: center; padding: 8px 0;
            border-right: 1px solid var(--border);
        }}
        .stat-mini-box:last-child {{ border-right: none; }}
        .stat-mini-box .mini-value {{
            font-size: 24px; font-weight: 800; color: var(--red);
        }}
        .stat-mini-box .mini-label {{
            font-size: 11px; color: var(--muted); text-transform: uppercase; margin-top: 2px;
        }}
        /* --- Section headers --- */
        .section-title {{
            border-top: 1px solid var(--border); padding-top: 10px;
            margin-top: 24px; margin-bottom: 12px;
            font-size: 11px; font-weight: 700; text-transform: uppercase;
            letter-spacing: 1.2px; color: var(--muted);
        }}
        /* --- Tier badge --- */
        .tier-tag {{
            display: inline-block; font-size: 11px; font-weight: 600;
            color: var(--purple); background: #ede9fe;
            padding: 1px 7px; border-radius: 8px; margin-left: 6px;
            vertical-align: middle;
        }}
        /* --- Table styles --- */
        .table-card {{
            background: #ffffff; border-radius: 12px; padding: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-bottom: 20px;
        }}
        .table-card table {{ border: 1px solid var(--border); }}
        table {{ width: 100%; border-collapse: collapse; }}
        thead th {{
            padding: 10px 16px; text-align: left; font-size: 12px;
            font-weight: 600; color: var(--muted); text-transform: uppercase;
            letter-spacing: 0.05em; border-bottom: 1px solid var(--border);
            cursor: pointer; user-select: none;
        }}
        thead th:hover {{ color: var(--text); }}
        tbody tr {{ border-bottom: 1px solid #f3f4f6; }}
        tbody tr:nth-child(odd) {{ background: #ffffff; }}
        tbody tr:nth-child(even) {{ background: #f3f4f6; }}
        tbody tr:hover {{ background: #eef0f3; }}
        /* Member table column widths */
        .member-table thead th:nth-child(1) {{ width: 30%; }}
        .member-table thead th:nth-child(2) {{ width: 14%; }}
        .member-table thead th:nth-child(3) {{ width: 12%; }}
        .member-table thead th:nth-child(4) {{ width: 14%; }}
        .member-table thead th:nth-child(5) {{ width: 15%; }}
        .member-table thead th:nth-child(6) {{ width: 15%; }}
        /* --- Table controls --- */
        .table-controls {{
            display: flex; gap: 12px; margin-bottom: 16px;
        }}
        .table-controls input {{
            flex: 1; padding: 8px 12px; border: 1px solid var(--border);
            border-radius: 8px; font-size: 13px; outline: none;
        }}
        .table-controls input:focus {{ border-color: var(--red); }}
        .table-controls select {{
            padding: 8px 12px; border: 1px solid var(--border);
            border-radius: 8px; font-size: 13px; outline: none; background: white;
        }}
        .footer {{
            text-align: center; padding: 24px; color: #9ca3af; font-size: 12px;
        }}
        @media (max-width: 768px) {{
            .stats-row {{ grid-template-columns: repeat(2, 1fr); }}
            .charts-row-2 {{ grid-template-columns: 1fr; }}
            .pie-row {{ grid-template-columns: 1fr; }}
            .header {{ flex-direction: column; gap: 12px; text-align: center; }}
            .stat-mini-row {{ flex-wrap: wrap; }}
            .stat-mini-box {{ min-width: 50%; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="header-left">
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
