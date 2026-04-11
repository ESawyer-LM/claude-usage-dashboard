"""
Generates a self-contained HTML dashboard file with Chart.js charts
and configurable org branding.
"""

import html
import json
import os
from collections import Counter
from datetime import datetime

import config

logger = config.get_logger()


def _escape(value: str) -> str:
    """HTML-escape a string."""
    return html.escape(str(value)) if value else ""


def _get_initials(name: str) -> str:
    """Get initials from a full name."""
    parts = name.strip().split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    elif parts:
        return parts[0][0].upper()
    return "?"


def _trend_badge(change_percent) -> str:
    """Return an HTML trend badge with arrow and percentage change."""
    if change_percent is None or change_percent == "\u2014":
        return ""
    try:
        val = float(change_percent)
    except (TypeError, ValueError):
        return ""
    if val > 0:
        arrow = "&#9650;"
        color = "#16a34a"
        sign = "+"
    elif val < 0:
        arrow = "&#9660;"
        color = "#dc2626"
        sign = ""
    else:
        arrow = "&#9644;"
        color = "#6b7280"
        sign = ""
    return (
        f'<span style="display:inline-flex;align-items:center;gap:3px;font-size:12px;'
        f'color:{color};font-weight:500;margin-top:2px;">'
        f'{arrow} {sign}{val:.0f}%</span>'
    )


def generate_html(data: dict, report_type: str = None) -> str:
    """Generate the complete HTML dashboard string.

    report_type selects which sections to include (see config.REPORT_TYPES).
    """
    if report_type is None:
        report_type = config.DEFAULT_REPORT_TYPE
    rt_config = config.REPORT_TYPES.get(report_type, config.REPORT_TYPES[config.DEFAULT_REPORT_TYPE])
    sections = set(rt_config["sections"])
    logger.debug(f"Generating HTML: report_type={report_type}, sections={sections}, from_cache={data.get('from_cache', False)}")
    members = data.get("members", [])
    daily_chats = data.get("daily_chats", {"labels": [], "data": []})
    top_projects = data.get("top_users_projects", [])
    top_artifacts = data.get("top_users_artifacts", [])
    plan_tier = data.get("plan_tier", "Standard")
    total_seats = data.get("total_seats", len(members))
    from_cache = data.get("from_cache", False)
    cache_reason = data.get("cache_reason", "")
    scraped_at = data.get("scraped_at", "")
    overview = data.get("activity_overview", {})
    logger.debug(f"HTML data: {len(members)} members, {len(daily_chats.get('data', []))} chat points, "
                 f"{len(top_projects)} top projects, {len(top_artifacts)} top artifacts")

    # Compute stats — prefer API counts over counting members list
    active_count = data.get("active_members", sum(1 for m in members if m.get("status") == "Active"))
    pending_count = data.get("pending_invites", sum(1 for m in members if m.get("status") == "Pending"))
    role_counts = Counter(m.get("role", "User") for m in members)

    # Role aggregation for pie chart
    owners_count = sum(v for k, v in role_counts.items() if "owner" in k.lower())
    users_count = sum(v for k, v in role_counts.items() if "owner" not in k.lower())

    # Tier aggregation for pie chart
    def _tier_label(m):
        st = m.get("seat_tier", "team_standard").lower()
        return "Premium" if ("tier_1" in st or "premium" in st) else "Standard"
    tier_counts = Counter(_tier_label(m) for m in members)

    today_str = datetime.now().strftime("%B %-d, %Y") if os.name != "nt" else datetime.now().strftime("%B %d, %Y")

    # Organization display name from settings
    _settings = config.load_settings()
    org_name = _settings.get("org_display_name") or "Claude Usage Dashboard"

    # Activity overview metrics
    dau = overview.get("dau", {}).get("value", "—")
    wau = overview.get("wau", {}).get("value", "—")
    mau = overview.get("mau", {}).get("value", "—")
    utilization = overview.get("utilization", {}).get("value", "—")
    if isinstance(utilization, (int, float)):
        utilization_str = f"{utilization:.0f}%"
    else:
        utilization_str = str(utilization)

    # Trend percentages (expanded report)
    dau_trend = _trend_badge(overview.get("dau", {}).get("change_percent"))
    wau_trend = _trend_badge(overview.get("wau", {}).get("change_percent"))
    mau_trend = _trend_badge(overview.get("mau", {}).get("change_percent"))
    utilization_trend = _trend_badge(overview.get("utilization", {}).get("change_percent"))

    # Stickiness (expanded report)
    stickiness = overview.get("stickiness", {})
    stickiness_val = stickiness.get("value") if isinstance(stickiness, dict) else stickiness
    if isinstance(stickiness_val, (int, float)):
        stickiness_str = f"{stickiness_val:.0f}%"
    elif stickiness_val is not None:
        stickiness_str = str(stickiness_val)
    else:
        stickiness_str = "\u2014"
    stickiness_trend = _trend_badge(stickiness.get("change_percent") if isinstance(stickiness, dict) else None)

    # Usage overview metrics
    usage_overview = data.get("usage_overview", {})
    chats_per_day = usage_overview.get("chats_per_day", {}).get("value", "\u2014")
    projects_created = usage_overview.get("projects_created", {}).get("value", "\u2014")
    artifacts_created = usage_overview.get("artifacts_created", {}).get("value", "\u2014")
    chats_per_day_trend = _trend_badge(usage_overview.get("chats_per_day", {}).get("change_percent"))
    projects_created_trend = _trend_badge(usage_overview.get("projects_created", {}).get("change_percent"))
    artifacts_created_trend = _trend_badge(usage_overview.get("artifacts_created", {}).get("change_percent"))

    # Top users by chats + DAU chart (expanded report)
    top_chats = data.get("top_users_chats", [])
    dau_chart = data.get("dau_chart", {"labels": [], "data": []})

    # Claude Code metrics
    cc = data.get("claude_code", {})
    cc_summary = cc.get("summary", {})
    cc_users = cc.get("users", [])
    cc_activity_chart = cc.get("activity_chart", {"labels": [], "data": []})
    cc_lines_chart = cc.get("lines_chart", {"labels": [], "data": []})
    cc_active_users = cc_summary.get("active_users", 0)
    cc_sessions = cc_summary.get("total_sessions", 0)
    cc_lines = cc_summary.get("total_lines_accepted", 0)
    cc_commits = cc_summary.get("commits_created", 0)
    cc_prs = cc_summary.get("pull_requests_created", 0)

    # Build member project/artifact lookup from top_users data
    project_lookup = {u["name"]: u["count"] for u in top_projects}
    artifact_lookup = {u["name"]: u["count"] for u in top_artifacts}

    # Chart data as JSON
    chat_labels_json = json.dumps(daily_chats.get("labels", []))
    chat_data_json = json.dumps(daily_chats.get("data", []))

    # Claude Code chart data
    cc_activity_labels_json = json.dumps(cc_activity_chart.get("labels", []))
    cc_activity_data_json = json.dumps(cc_activity_chart.get("data", []))
    cc_lines_labels_json = json.dumps(cc_lines_chart.get("labels", []))
    cc_lines_data_json = json.dumps(cc_lines_chart.get("data", []))
    cc_user_names_json = json.dumps([u.get("name", "?") for u in cc_users[:10]])
    cc_user_sessions_json = json.dumps([u.get("total_sessions", 0) for u in cc_users[:10]])
    cc_user_lines_json = json.dumps([u.get("total_lines_accepted", 0) for u in cc_users[:10]])
    project_names_json = json.dumps([u["name"] for u in top_projects])
    project_counts_json = json.dumps([u["count"] for u in top_projects])
    artifact_names_json = json.dumps([u["name"] for u in top_artifacts])
    artifact_counts_json = json.dumps([u["count"] for u in top_artifacts])

    # Expanded report chart data
    chat_user_names_json = json.dumps([u["name"] for u in top_chats])
    chat_user_counts_json = json.dumps([u["count"] for u in top_chats])
    dau_labels_json = json.dumps(dau_chart.get("labels", []))
    dau_data_json = json.dumps(dau_chart.get("data", []))

    # WAU chart data (used in activity section)
    wau_chart_data = data.get("wau_chart", {"labels": [], "data": []})

    # Pie chart data
    status_labels_json = json.dumps(["Active", "Pending"])
    status_data_json = json.dumps([active_count, pending_count])
    role_labels_json = json.dumps(["Owners", "Users"])
    role_data_json = json.dumps([owners_count, users_count])
    tier_labels_json = json.dumps(list(tier_counts.keys()))
    tier_data_json = json.dumps(list(tier_counts.values()))

    # Stale data warning banner
    stale_banner = ""
    if from_cache:
        stale_banner = f"""
        <div style="background:#fef3c7;border:1px solid #f59e0b;border-radius:8px;padding:12px 20px;margin-bottom:20px;display:flex;align-items:center;gap:10px;">
            <span style="font-size:20px;">&#9888;</span>
            <div>
                <strong style="color:#b45309;">Data may be stale</strong>
                <span style="color:#92400e;margin-left:8px;">Scrape failed{' — ' + _escape(cache_reason) if cache_reason else ''}. Showing cached data from {_escape(scraped_at[:10]) if scraped_at else 'unknown date'}.</span>
            </div>
        </div>"""

    # Member table rows
    member_rows = ""
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

        premium_badge = '<span style="display:inline-block;font-size:11px;font-weight:600;color:#7c3aed;background:#ede9fe;padding:1px 7px;border-radius:8px;margin-left:6px;vertical-align:middle;">Premium</span>' if is_premium else ""

        if status == "Active":
            badge = '<span style="background:#dcfce7;color:#15803d;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:500;">Active</span>'
        else:
            badge = '<span style="background:#fef3c7;color:#b45309;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:500;">Pending</span>'

        role_style = 'color:#C8102E;font-weight:600;' if 'owner' in role.lower() else 'color:#374151;'
        tier_style = 'color:#7c3aed;font-weight:600;' if is_premium else 'color:#6b7280;font-size:12px;'
        proj_style = 'color:#C8102E;font-weight:700;' if projects_mtd > 0 else 'color:#6b7280;'
        art_style = 'color:#C8102E;font-weight:700;' if artifacts_mtd > 0 else 'color:#6b7280;'

        member_rows += f"""
                <tr data-name="{name.lower()}" data-email="{email_val.lower()}" data-status="{_escape(status).lower()}">
                    <td style="padding:10px 16px;">
                        <div style="font-weight:600;font-size:13px;color:#111827;">{name}{premium_badge}</div>
                        <div style="font-size:11px;color:#6b7280;margin-top:1px;">{email_val}</div>
                    </td>
                    <td style="padding:10px 16px;{role_style}">{role}</td>
                    <td style="padding:10px 16px;{tier_style}">{tier_label}</td>
                    <td style="padding:10px 16px;">{badge}</td>
                    <td style="padding:10px 16px;text-align:center;{proj_style}">{projects_mtd}</td>
                    <td style="padding:10px 16px;text-align:center;{art_style}">{artifacts_mtd}</td>
                </tr>"""

    # Claude Code user table rows
    cc_user_rows = ""
    for u in cc_users:
        u_name = _escape(u.get("name", ""))
        u_email = _escape(u.get("email", ""))
        u_initials = _escape(_get_initials(u.get("name", "")))
        u_sessions = u.get("total_sessions", 0)
        u_lines = u.get("total_lines_accepted", 0)
        u_avg_lines = float(u.get("avg_lines_accepted_per_day", 0) or 0)
        u_prs_val = u.get("total_prs", u.get("prs_with_cc", 0))
        u_last_active = _escape(u.get("last_active", "—") or "—")
        # Format last_active date if it looks like ISO
        if u_last_active and u_last_active != "—" and len(u_last_active) >= 10:
            u_last_active = u_last_active[:10]

        cc_user_rows += f"""
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
                    <td style="padding:12px 16px;text-align:center;color:#374151;">{u_avg_lines:,.0f}</td>
                    <td style="padding:12px 16px;text-align:center;color:#374151;">{u_prs_val}</td>
                    <td style="padding:12px 16px;color:#374151;">{u_last_active}</td>
                </tr>"""

    # Pre-build expanded report conditional HTML (avoids backslash-in-fstring issues)
    stats_columns = '5' if 'stickiness' in sections else '4'
    stickiness_card = (
        f'<div class="stat-card"><div class="stat-label">Stickiness (DAU/MAU)</div>'
        f'<div class="stat-value" style="color:#8b5cf6;">{stickiness_str}</div>'
        f'{stickiness_trend}</div>'
    ) if 'stickiness' in sections else ''

    show_trends = 'trends' in sections
    dau_trend_html = dau_trend if show_trends else ''
    wau_trend_html = wau_trend if show_trends else ''
    mau_trend_html = mau_trend if show_trends else ''
    utilization_trend_html = utilization_trend if show_trends else ''

    usage_stats_html = ''
    if 'usage_stats' in sections:
        usage_stats_html = (
            f'<div class="stats-row" style="grid-template-columns:repeat(3,1fr);">'
            f'<div class="stat-card"><div class="stat-label">Avg. Chats / Day</div>'
            f'<div class="stat-value" style="color:#C8102E;">{chats_per_day}</div>{chats_per_day_trend}</div>'
            f'<div class="stat-card"><div class="stat-label">Projects Created (MTD)</div>'
            f'<div class="stat-value" style="color:#C8102E;">{projects_created}</div>{projects_created_trend}</div>'
            f'<div class="stat-card"><div class="stat-label">Artifacts Created (MTD)</div>'
            f'<div class="stat-value" style="color:#C8102E;">{artifacts_created}</div>{artifacts_created_trend}</div>'
            f'</div>'
        )

    dau_chart_card = (
        '<div class="chart-card"><h3>Daily Active Users (Last 30 Days)</h3>'
        '<canvas id="dauChart"></canvas></div>'
    ) if 'dau_chart' in sections else ''

    chats_ranking_card = (
        '<div class="chart-card"><h3>Top Users by Chats MTD</h3>'
        '<canvas id="chatsRankingChart"></canvas></div>'
    ) if 'chat_rankings' in sections else ''

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Claude Usage Dashboard — {_escape(org_name)}</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f5f5f5;
            color: #1a1a1a;
            line-height: 1.5;
        }}
        .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}

        /* Header */
        .header {{
            background: #C8102E;
            padding: 20px 32px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            color: white;
        }}
        .header-left {{ display: flex; align-items: center; gap: 16px; }}
        .header-title {{ font-size: 22px; font-weight: 700; }}
        .header-subtitle {{ font-size: 13px; opacity: 0.9; }}
        .date-badge {{
            background: rgba(255,255,255,0.2); padding: 6px 16px;
            border-radius: 20px; font-size: 13px; font-weight: 500;
        }}

        /* Stat Cards */
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

        /* Chart Cards */
        .charts-row-2 {{
            display: grid; grid-template-columns: repeat(2, 1fr);
            gap: 16px; margin-bottom: 20px;
        }}
        .pie-row {{
            display: grid; grid-template-columns: repeat(3, 1fr);
            gap: 16px; margin-bottom: 20px;
        }}
        .pie-row .chart-card canvas {{ height: 180px !important; }}
        .charts-row-3 {{
            display: flex; flex-direction: column; gap: 20px; margin-bottom: 20px;
        }}
        .chart-card {{
            background: #ffffff; border-radius: 12px; padding: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        }}
        .chart-card h3 {{
            font-size: 15px; font-weight: 600; color: #374151;
            margin-bottom: 12px;
        }}
        .chart-card canvas {{ height: 220px !important; }}

        /* Member Table */
        .table-card {{
            background: #ffffff; border-radius: 12px; padding: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-bottom: 20px;
        }}
        .table-controls {{
            display: flex; gap: 12px; margin-bottom: 16px; align-items: center;
        }}
        .table-controls input {{
            flex: 1; padding: 8px 14px; border: 1px solid #d1d5db;
            border-radius: 8px; font-size: 14px; outline: none;
        }}
        .table-controls input:focus {{ border-color: #C8102E; }}
        .table-controls select {{
            padding: 8px 14px; border: 1px solid #d1d5db;
            border-radius: 8px; font-size: 14px; outline: none; background: white;
        }}
        .table-card table {{ border: 1px solid #e5e7eb; }}
        table {{ width: 100%; border-collapse: collapse; }}
        thead th {{
            padding: 10px 16px; text-align: left; font-size: 12px;
            font-weight: 600; color: #6b7280; text-transform: uppercase;
            letter-spacing: 0.05em; border-bottom: 1px solid #e5e7eb;
            cursor: pointer; user-select: none;
        }}
        thead th:hover {{ color: #C8102E; }}
        tbody tr {{ border-bottom: 1px solid #f3f4f6; }}
        tbody tr:nth-child(odd) {{ background: #ffffff; }}
        tbody tr:nth-child(even) {{ background: #f3f4f6; }}
        tbody tr:hover {{ background: #eef0f3; }}

        /* Footer */
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
    <!-- Header -->
    <div class="header">
        <div class="header-left">
            <div>
                <div class="header-title">Claude Usage Dashboard</div>
                <div class="header-subtitle">{_escape(org_name)}</div>
            </div>
        </div>
        <div class="date-badge">As of {_escape(today_str)}</div>
    </div>

    <div class="container">
        {stale_banner}

        {"" if "overview" not in sections else f'''<!-- Stats Row -->
        <div class="stats-row" style="grid-template-columns:repeat({stats_columns},1fr);">
            <div class="stat-card">
                <div class="stat-label">Assigned Seats</div>
                <div class="stat-value" style="color:#C8102E;">{active_count + pending_count}/{total_seats}</div>
                <div style="font-size:12px;color:#6b7280;margin-top:2px;">{active_count} active &middot; {pending_count} pending</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Daily Active Users</div>
                <div class="stat-value" style="color:#16a34a;">{dau}</div>
                {dau_trend_html}
            </div>
            <div class="stat-card">
                <div class="stat-label">Weekly Active Users</div>
                <div class="stat-value" style="color:#2563eb;">{wau}</div>
                {wau_trend_html}
            </div>
            <div class="stat-card">
                <div class="stat-label">Utilization</div>
                <div class="stat-value" style="color:#d97706;">{utilization_str}</div>
                <div style="font-size:12px;color:#6b7280;margin-top:2px;">MAU: {mau} {mau_trend_html} &middot; {_escape(plan_tier)}</div>
                {utilization_trend_html}
            </div>
            {stickiness_card}
        </div>

        {usage_stats_html}

        <!-- Org Overview (3-column pie charts) -->
        <div class="pie-row">
            <div class="chart-card">
                <h3>Member Status</h3>
                <canvas id="statusChart"></canvas>
            </div>
            <div class="chart-card">
                <h3>Role Distribution</h3>
                <canvas id="roleChart"></canvas>
            </div>
            <div class="chart-card">
                <h3>Account Type Distribution</h3>
                <canvas id="tierChart"></canvas>
            </div>
        </div>
        '''}

        {"" if "activity" not in sections else f'''<!-- Activity Analytics -->
        <div class="charts-row-2">
            <div class="chart-card">
                <h3>Daily Chats (Last 7 Days)</h3>
                <canvas id="dailyChatsChart"></canvas>
            </div>
            {dau_chart_card}
        </div>
        '''}

        {"" if "usage" not in sections else f'''<!-- Usage Analytics -->
        <div class="charts-row-3">
            {chats_ranking_card}
            <div class="chart-card">
                <h3>Top Users by Projects MTD</h3>
                <canvas id="projectsChart"></canvas>
            </div>
            <div class="chart-card">
                <h3>Top Users by Artifacts MTD</h3>
                <canvas id="artifactsChart"></canvas>
            </div>
        </div>
        '''}

        {"" if "claude_code" not in sections else f'''<!-- Claude Code Analytics -->
        <div style="margin-bottom:20px;">
            <h3 style="font-size:16px;font-weight:600;color:#111827;margin-bottom:16px;">Claude Code Analytics (MTD)</h3>
            <div class="stats-row" style="grid-template-columns:repeat(5,1fr);">
                <div class="stat-card">
                    <div class="stat-label">Active Users</div>
                    <div class="stat-value" style="color:#7c3aed;font-size:24px;">{cc_active_users}</div>
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
            <div class="charts-row-2" style="margin-top:16px;">
                <div class="chart-card">
                    <h3>Claude Code Sessions (Daily)</h3>
                    <canvas id="ccActivityChart"></canvas>
                </div>
                <div class="chart-card">
                    <h3>Claude Code Lines Accepted (Daily)</h3>
                    <canvas id="ccLinesChart"></canvas>
                </div>
            </div>
            <div class="charts-row-2" style="margin-top:16px;">
                <div class="chart-card">
                    <h3>Top Claude Code Users (Sessions MTD)</h3>
                    <canvas id="ccUsersChart"></canvas>
                </div>
                <div class="chart-card">
                    <h3>Top Claude Code Users (Lines Accepted MTD)</h3>
                    <canvas id="ccUsersLinesChart"></canvas>
                </div>
            </div>
        </div>

        <!-- Claude Code User Breakdown -->
        <div class="table-card">
            <h3 style="font-size:16px;font-weight:600;color:#111827;margin-bottom:16px;">Claude Code User Breakdown (MTD)</h3>
            <table>
                <thead>
                    <tr>
                        <th onclick="sortCcTable(0)">User</th>
                        <th onclick="sortCcTable(1)" style="text-align:center;">Sessions</th>
                        <th onclick="sortCcTable(2)" style="text-align:center;">Lines Accepted</th>
                        <th onclick="sortCcTable(3)" style="text-align:center;">Avg Lines/Day</th>
                        <th onclick="sortCcTable(4)" style="text-align:center;">PRs</th>
                        <th onclick="sortCcTable(5)">Last Active</th>
                    </tr>
                </thead>
                <tbody id="ccUserTableBody">
                    {cc_user_rows}
                </tbody>
            </table>
        </div>
        '''}

        {"" if "members" not in sections else f'''<!-- Member Directory -->
        <div class="table-card">
            <h3 style="font-size:16px;font-weight:600;color:#111827;margin-bottom:16px;">Member Directory</h3>
            <div class="table-controls">
                <input type="text" id="searchInput" placeholder="Search by name or email...">
                <select id="statusFilter">
                    <option value="">All Status</option>
                    <option value="active">Active</option>
                    <option value="pending">Pending</option>
                </select>
            </div>
            <table class="member-table">
                <thead>
                    <tr>
                        <th onclick="sortTable(0)" style="width:30%;">Member</th>
                        <th onclick="sortTable(1)" style="width:14%;">Role</th>
                        <th onclick="sortTable(2)" style="width:12%;">Tier</th>
                        <th onclick="sortTable(3)" style="width:14%;">Status</th>
                        <th onclick="sortTable(4)" style="text-align:center;width:15%;">Projects MTD</th>
                        <th onclick="sortTable(5)" style="text-align:center;width:15%;">Artifacts MTD</th>
                    </tr>
                </thead>
                <tbody id="memberTableBody">
                    {member_rows}
                </tbody>
            </table>
        </div>
        '''}

        <!-- Footer -->
        <div class="footer">
            Data sourced from Claude.ai Admin Console and Claude.ai Analytics &middot; {_escape(org_name)} &middot; {_escape(today_str)} &middot; v{config.VERSION}
        </div>
    </div>

    <script>
        // --- Charts (conditionally initialized based on report sections) ---

        {"" if "overview" not in sections else f'''
        // Member Status Pie
        new Chart(document.getElementById('statusChart'), {{
            type: 'pie',
            data: {{
                labels: {status_labels_json},
                datasets: [{{ data: {status_data_json}, backgroundColor: ['#16a34a', '#d97706'], borderWidth: 0 }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{ legend: {{ position: 'bottom' }} }}
            }}
        }});

        // Role Distribution Pie
        new Chart(document.getElementById('roleChart'), {{
            type: 'pie',
            data: {{
                labels: {role_labels_json},
                datasets: [{{ data: {role_data_json}, backgroundColor: ['#C8102E', '#6b7280'], borderWidth: 0 }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{ legend: {{ position: 'bottom' }} }}
            }}
        }});

        // Account Type Distribution Pie
        new Chart(document.getElementById('tierChart'), {{
            type: 'pie',
            data: {{
                labels: {tier_labels_json},
                datasets: [{{ data: {tier_data_json}, backgroundColor: ['#C8102E', '#2563eb', '#6b7280'], borderWidth: 0 }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{ legend: {{ position: 'bottom' }} }}
            }}
        }});
        '''}

        {"" if "activity" not in sections else f'''
        // Daily Chats Line Chart
        new Chart(document.getElementById('dailyChatsChart'), {{
            type: 'line',
            data: {{
                labels: {chat_labels_json},
                datasets: [{{
                    label: 'Daily Chats',
                    data: {chat_data_json},
                    borderColor: '#C8102E',
                    backgroundColor: 'rgba(200, 16, 46, 0.08)',
                    fill: true,
                    tension: 0.3,
                    pointBackgroundColor: '#ffffff',
                    pointBorderColor: '#C8102E',
                    pointBorderWidth: 2,
                    pointRadius: 5,
                    pointHoverRadius: 7
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{ legend: {{ display: false }} }},
                scales: {{
                    y: {{ beginAtZero: true, grid: {{ color: '#f3f4f6' }} }},
                    x: {{ grid: {{ display: false }} }}
                }}
            }}
        }});
        '''}

        {"" if "usage" not in sections else f'''
        // Top Users by Projects (Horizontal Bar)
        new Chart(document.getElementById('projectsChart'), {{
            type: 'bar',
            data: {{
                labels: {project_names_json},
                datasets: [{{
                    label: 'Projects',
                    data: {project_counts_json},
                    backgroundColor: '#C8102E',
                    borderRadius: 4,
                    barThickness: 20
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: 'y',
                plugins: {{ legend: {{ display: false }} }},
                scales: {{
                    x: {{ beginAtZero: true, grid: {{ color: '#f3f4f6' }} }},
                    y: {{ grid: {{ display: false }} }}
                }}
            }}
        }});

        // Top Users by Artifacts (Horizontal Bar)
        new Chart(document.getElementById('artifactsChart'), {{
            type: 'bar',
            data: {{
                labels: {artifact_names_json},
                datasets: [{{
                    label: 'Artifacts',
                    data: {artifact_counts_json},
                    backgroundColor: '#C8102E',
                    borderRadius: 4,
                    barThickness: 20
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: 'y',
                plugins: {{ legend: {{ display: false }} }},
                scales: {{
                    x: {{ beginAtZero: true, grid: {{ color: '#f3f4f6' }} }},
                    y: {{ grid: {{ display: false }} }}
                }}
            }}
        }});
        '''}

        {"" if "dau_chart" not in sections else f'''
        // Daily Active Users (Line Chart)
        new Chart(document.getElementById('dauChart'), {{
            type: 'line',
            data: {{
                labels: {dau_labels_json},
                datasets: [{{
                    label: 'DAU',
                    data: {dau_data_json},
                    borderColor: '#16a34a',
                    backgroundColor: 'rgba(22, 163, 74, 0.08)',
                    fill: true,
                    tension: 0.3,
                    pointBackgroundColor: '#ffffff',
                    pointBorderColor: '#16a34a',
                    pointBorderWidth: 2,
                    pointRadius: 3,
                    pointHoverRadius: 5
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{ legend: {{ display: false }} }},
                scales: {{
                    y: {{ beginAtZero: true, grid: {{ color: '#f3f4f6' }} }},
                    x: {{ grid: {{ display: false }} }}
                }}
            }}
        }});
        '''}

        {"" if "chat_rankings" not in sections else f'''
        // Top Users by Chats (Horizontal Bar)
        new Chart(document.getElementById('chatsRankingChart'), {{
            type: 'bar',
            data: {{
                labels: {chat_user_names_json},
                datasets: [{{
                    label: 'Chats',
                    data: {chat_user_counts_json},
                    backgroundColor: '#C8102E',
                    borderRadius: 4,
                    barThickness: 20
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: 'y',
                plugins: {{ legend: {{ display: false }} }},
                scales: {{
                    x: {{ beginAtZero: true, grid: {{ color: '#f3f4f6' }} }},
                    y: {{ grid: {{ display: false }} }}
                }}
            }}
        }});
        '''}

        {"" if "claude_code" not in sections else f'''
        // Claude Code Sessions (Line Chart)
        new Chart(document.getElementById('ccActivityChart'), {{
            type: 'line',
            data: {{
                labels: {cc_activity_labels_json},
                datasets: [{{
                    label: 'Sessions',
                    data: {cc_activity_data_json},
                    borderColor: '#7c3aed',
                    backgroundColor: 'rgba(124, 58, 237, 0.08)',
                    fill: true,
                    tension: 0.3,
                    pointBackgroundColor: '#ffffff',
                    pointBorderColor: '#7c3aed',
                    pointBorderWidth: 2,
                    pointRadius: 5,
                    pointHoverRadius: 7
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{ legend: {{ display: false }} }},
                scales: {{
                    y: {{ beginAtZero: true, grid: {{ color: '#f3f4f6' }} }},
                    x: {{ grid: {{ display: false }} }}
                }}
            }}
        }});

        // Claude Code Lines Accepted (Line Chart)
        new Chart(document.getElementById('ccLinesChart'), {{
            type: 'line',
            data: {{
                labels: {cc_lines_labels_json},
                datasets: [{{
                    label: 'Lines Accepted',
                    data: {cc_lines_data_json},
                    borderColor: '#16a34a',
                    backgroundColor: 'rgba(22, 163, 74, 0.08)',
                    fill: true,
                    tension: 0.3,
                    pointBackgroundColor: '#ffffff',
                    pointBorderColor: '#16a34a',
                    pointBorderWidth: 2,
                    pointRadius: 5,
                    pointHoverRadius: 7
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{ legend: {{ display: false }} }},
                scales: {{
                    y: {{ beginAtZero: true, grid: {{ color: '#f3f4f6' }} }},
                    x: {{ grid: {{ display: false }} }}
                }}
            }}
        }});

        // Top Claude Code Users (Horizontal Bar)
        new Chart(document.getElementById('ccUsersChart'), {{
            type: 'bar',
            data: {{
                labels: {cc_user_names_json},
                datasets: [{{
                    label: 'Sessions',
                    data: {cc_user_sessions_json},
                    backgroundColor: '#7c3aed',
                    borderRadius: 4,
                    barThickness: 20
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: 'y',
                plugins: {{ legend: {{ display: false }} }},
                scales: {{
                    x: {{ beginAtZero: true, grid: {{ color: '#f3f4f6' }} }},
                    y: {{ grid: {{ display: false }} }}
                }}
            }}
        }});

        // Top Claude Code Users by Lines Accepted (Horizontal Bar)
        new Chart(document.getElementById('ccUsersLinesChart'), {{
            type: 'bar',
            data: {{
                labels: {cc_user_names_json},
                datasets: [{{
                    label: 'Lines Accepted',
                    data: {cc_user_lines_json},
                    backgroundColor: '#16a34a',
                    borderRadius: 4,
                    barThickness: 20
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: 'y',
                plugins: {{ legend: {{ display: false }} }},
                scales: {{
                    x: {{ beginAtZero: true, grid: {{ color: '#f3f4f6' }} }},
                    y: {{ grid: {{ display: false }} }}
                }}
            }}
        }});

        // --- Claude Code Table Sorting ---
        let ccSortDir = [1, 1, 1, 1, 1, 1];
        function sortCcTable(col) {{
            const tbody = document.getElementById('ccUserTableBody');
            const rows = Array.from(tbody.querySelectorAll('tr'));
            ccSortDir[col] *= -1;
            rows.sort((a, b) => {{
                let aVal = a.cells[col].textContent.trim();
                let bVal = b.cells[col].textContent.trim();
                if (col >= 1 && col <= 4) {{ // Numeric columns
                    return (parseInt(aVal.replace(/,/g, '')) - parseInt(bVal.replace(/,/g, ''))) * ccSortDir[col];
                }}
                return aVal.localeCompare(bVal) * ccSortDir[col];
            }});
            rows.forEach(r => tbody.appendChild(r));
        }}
        '''}

        {"" if "members" not in sections else f'''
        // --- Table Sorting ---
        let sortDir = [1, 1, 1, 1, 1, 1];
        function sortTable(col) {{
            const tbody = document.getElementById('memberTableBody');
            const rows = Array.from(tbody.querySelectorAll('tr'));
            sortDir[col] *= -1;
            rows.sort((a, b) => {{
                let aVal = a.cells[col].textContent.trim();
                let bVal = b.cells[col].textContent.trim();
                if (col >= 4) {{ // Numeric columns (Projects, Artifacts)
                    return (parseInt(aVal) - parseInt(bVal)) * sortDir[col];
                }}
                return aVal.localeCompare(bVal) * sortDir[col];
            }});
            rows.forEach(r => tbody.appendChild(r));
        }}

        // --- Table Filtering ---
        function filterTable() {{
            const search = document.getElementById('searchInput').value.toLowerCase();
            const status = document.getElementById('statusFilter').value;
            const rows = document.querySelectorAll('#memberTableBody tr');
            rows.forEach(row => {{
                const name = row.getAttribute('data-name') || '';
                const email = row.getAttribute('data-email') || '';
                const rowStatus = row.getAttribute('data-status') || '';
                const matchesSearch = name.includes(search) || email.includes(search);
                const matchesStatus = !status || rowStatus === status;
                row.style.display = (matchesSearch && matchesStatus) ? '' : 'none';
            }});
        }}
        document.getElementById('searchInput').addEventListener('input', filterTable);
        document.getElementById('statusFilter').addEventListener('change', filterTable);
        '''}
    </script>
</body>
</html>"""


def save_html(data: dict, output_dir: str = None, report_type: str = None) -> str:
    """Generate and save the HTML dashboard. Returns the file path.

    report_type selects which sections to include (see config.REPORT_TYPES).
    """
    if output_dir is None:
        output_dir = config.OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)

    if report_type is None:
        report_type = config.DEFAULT_REPORT_TYPE

    html_content = generate_html(data, report_type=report_type)
    filepath = os.path.join(output_dir, f"claude_usage_dashboard_{report_type}.html")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_content)

    logger.info(f"HTML dashboard saved to {filepath}")
    logger.debug(f"HTML size: {len(html_content) / 1024:.1f} KB")
    return filepath
