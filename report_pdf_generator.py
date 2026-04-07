"""
Generates a PDF for a custom report using ReportLab + matplotlib.
Reuses Flowable subclasses and helpers from pdf_generator.py.
"""

import matplotlib
matplotlib.use("Agg")

import os
from collections import Counter
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

import config
from pdf_generator import (
    HeaderBanner,
    SectionHeader,
    StatCardRow,
    StatsSummaryRow,
    _build_cc_user_table,
    _build_member_table,
    _fig_to_image,
    _make_hbar_chart,
    _make_line_chart,
    _make_numbered_canvas_factory,
    _trend_text,
    CC_PURPLE,
    CC_PURPLE_RGB,
    LM_AMBER,
    LM_GREEN,
    LM_RED,
    LM_RED_RGB,
    MARGIN,
    PAGE_HEIGHT,
    PAGE_WIDTH,
    USABLE_WIDTH,
)
from report_html_generator import (
    filter_data_by_range,
    generate_executive_summary,
    resolve_date_range,
)

logger = config.get_logger()


# ---------------------------------------------------------------------------
# Per-component PDF renderers — each returns a list of Flowables
# ---------------------------------------------------------------------------
def _pdf_stats_row(data):
    members = data.get("members", [])
    total_seats = data.get("total_seats", len(members))
    active_count = data.get("active_members", sum(1 for m in members if m.get("status") == "Active"))
    pending_count = data.get("pending_invites", sum(1 for m in members if m.get("status") == "Pending"))
    assigned = active_count + pending_count
    available = total_seats - assigned
    plan_tier = data.get("plan_tier", "Standard")
    return [
        StatCardRow([
            ("Total Seats", str(total_seats), f"{available} available \u00b7 {assigned} assigned", LM_RED),
            ("Active Members", str(active_count), "Onboarded & using Claude", LM_GREEN),
            ("Pending Invites", str(pending_count), "Haven't accepted invite yet", LM_AMBER),
            ("Seat Tier", plan_tier, "Plan type", LM_RED),
        ]),
        Spacer(1, 10),
    ]


def _pdf_status_pie(data):
    members = data.get("members", [])
    active_count = data.get("active_members", sum(1 for m in members if m.get("status") == "Active"))
    pending_count = data.get("pending_invites", sum(1 for m in members if m.get("status") == "Pending"))

    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(3.5, 3.5))
    fig.patch.set_facecolor("white")
    sizes = [active_count, pending_count]
    clrs = ["#16a34a", "#d97706"]
    labels = ["Active", "Pending"]
    if sum(sizes) > 0:
        wedges, texts, autotexts = ax.pie(
            sizes, labels=labels, colors=clrs, autopct="%1.0f%%",
            startangle=90
        )
        for t in autotexts:
            t.set_fontsize(9)
    else:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
    ax.set_title("Member Status Breakdown", fontsize=11, fontweight="bold", pad=10)
    fig.tight_layout()
    img = _fig_to_image(fig, USABLE_WIDTH * 0.33, 2.2 * inch)
    return [img, Spacer(1, 10)]


def _pdf_role_pie(data):
    members = data.get("members", [])
    role_counts = Counter(m.get("role", "User") for m in members)
    owners = sum(v for k, v in role_counts.items() if "owner" in k.lower())
    users = sum(v for k, v in role_counts.items() if "owner" not in k.lower())

    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(3.5, 3.5))
    fig.patch.set_facecolor("white")
    sizes = [owners, users]
    clrs = ["#C8102E", "#6b7280"]
    labels = ["Owners", "Users"]
    if sum(sizes) > 0:
        wedges, texts, autotexts = ax.pie(
            sizes, labels=labels, colors=clrs, autopct="%1.0f%%",
            startangle=90
        )
        for t in autotexts:
            t.set_fontsize(9)
    else:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
    ax.set_title("Role Distribution", fontsize=11, fontweight="bold", pad=10)
    fig.tight_layout()
    img = _fig_to_image(fig, USABLE_WIDTH * 0.33, 2.2 * inch)
    return [img, Spacer(1, 10)]


def _pdf_tier_pie(data):
    members = data.get("members", [])
    def _tier_label(m):
        st = m.get("seat_tier", "team_standard").lower()
        return "Premium" if ("tier_1" in st or "premium" in st) else "Standard"
    tier_counts = Counter(_tier_label(m) for m in members)

    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(3.5, 3.5))
    fig.patch.set_facecolor("white")
    sizes = list(tier_counts.values())
    labels = list(tier_counts.keys())
    clrs = ["#C8102E", "#2563eb", "#6b7280"]
    if sum(sizes) > 0:
        wedges, texts, autotexts = ax.pie(
            sizes, labels=labels, colors=clrs[:len(sizes)], autopct="%1.0f%%",
            startangle=90
        )
        for t in autotexts:
            t.set_fontsize(9)
    else:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
    ax.set_title("Account Type Distribution", fontsize=11, fontweight="bold", pad=10)
    fig.tight_layout()
    img = _fig_to_image(fig, USABLE_WIDTH * 0.33, 2.2 * inch)
    return [img, Spacer(1, 10)]


def _pdf_daily_chats(data):
    daily_chats = data.get("daily_chats", {"labels": [], "data": []})
    chat_data = daily_chats.get("data", [])
    chat_labels = daily_chats.get("labels", [])
    if not chat_data:
        return []
    fig = _make_line_chart(chat_labels, chat_data, "Daily Chat Activity")
    img = _fig_to_image(fig, USABLE_WIDTH - 8, 2.2 * inch)

    # Summary row
    total = sum(chat_data)
    peak = max(chat_data)
    avg = total / len(chat_data) if chat_data else 0
    summary = StatsSummaryRow([
        (str(total), f"Total chats ({len(chat_data)} days)", None),
        (str(peak), "Peak daily chats", None),
        (f"{avg:.1f}", "Avg chats / day", None),
    ], width=USABLE_WIDTH - 8)

    chart_table = Table([[img], [summary]], colWidths=[USABLE_WIDTH])
    chart_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
        ("LINEBEFORE", (0, 0), (0, -1), 5, colors.HexColor(LM_RED)),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (0, 0), 10),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
    ]))
    return [chart_table, Spacer(1, 14)]


def _pdf_wau_trend(data):
    wau_chart = data.get("wau_chart", {"labels": [], "data": []})
    wau_data = wau_chart.get("data", [])
    wau_labels = wau_chart.get("labels", [])
    if not wau_data:
        return []
    if len(wau_data) > 7:
        wau_data = wau_data[-7:]
        wau_labels = wau_labels[-7:]
    fig = _make_line_chart(wau_labels, wau_data, "Weekly Active Users (WAU)")
    img = _fig_to_image(fig, USABLE_WIDTH - 8, 2.2 * inch)
    chart_table = Table([[img]], colWidths=[USABLE_WIDTH])
    chart_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#1a1a1a")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return [
        SectionHeader("Weekly Active Users \u00b7 Rolling 7-Day Window"),
        Spacer(1, 10),
        chart_table,
        Spacer(1, 14),
    ]


def _pdf_wau_stats_tile(data):
    overview = data.get("activity_overview", {})
    wau = overview.get("wau", {})
    wau_val = wau.get("value", "\u2014")
    wau_change = wau.get("change_percent")
    utilization = overview.get("utilization", {}).get("value", "\u2014")
    if isinstance(utilization, (int, float)):
        util_str = f"{utilization:.1f}%"
    else:
        util_str = str(utilization)
    wau_sub = f"Weekly active {_trend_text(wau_change)}" if wau_change is not None else "Weekly active"
    return [
        StatCardRow([
            ("WAU", str(wau_val), wau_sub, "#2563eb"),
            ("Utilization", util_str, "Seat utilization rate", LM_AMBER),
        ]),
        Spacer(1, 10),
    ]


def _pdf_activity_metrics(data):
    overview = data.get("activity_overview", {})
    dau_val = overview.get("dau", {}).get("value", "\u2014")
    dau_change = overview.get("dau", {}).get("change_percent")
    wau_val = overview.get("wau", {}).get("value", "\u2014")
    wau_change = overview.get("wau", {}).get("change_percent")
    mau_val = overview.get("mau", {}).get("value", "\u2014")
    mau_change = overview.get("mau", {}).get("change_percent")
    utilization = overview.get("utilization", {}).get("value", "\u2014")
    utilization_change = overview.get("utilization", {}).get("change_percent")
    if isinstance(utilization, (int, float)):
        util_str = f"{utilization:.1f}%"
    else:
        util_str = str(utilization)
    stickiness = overview.get("stickiness", {})
    stickiness_val = stickiness.get("value") if isinstance(stickiness, dict) else stickiness
    stickiness_change = stickiness.get("change_percent") if isinstance(stickiness, dict) else None
    if isinstance(stickiness_val, (int, float)):
        stickiness_str = f"{stickiness_val:.0f}%"
    else:
        stickiness_str = str(stickiness_val) if stickiness_val else "\u2014"

    dau_sub = f"Daily active {_trend_text(dau_change)}" if dau_change is not None else "Daily active"
    wau_sub = f"Weekly active {_trend_text(wau_change)}" if wau_change is not None else "Weekly active"
    mau_sub = f"Monthly active {_trend_text(mau_change)}" if mau_change is not None else "Monthly active"
    util_sub = f"Seat utilization {_trend_text(utilization_change)}" if utilization_change is not None else "Seat utilization"
    sticky_sub = f"DAU/MAU ratio {_trend_text(stickiness_change)}" if stickiness_change is not None else "DAU/MAU ratio"
    return [
        StatCardRow([
            ("DAU", str(dau_val), dau_sub, LM_GREEN),
            ("WAU", str(wau_val), wau_sub, "#2563eb"),
            ("MAU", str(mau_val), mau_sub, "#2563eb"),
            ("Utilization", util_str, util_sub, LM_AMBER),
            ("Stickiness", stickiness_str, sticky_sub, "#8b5cf6"),
        ]),
        Spacer(1, 10),
    ]


def _pdf_usage_stats(data):
    usage = data.get("usage_overview", {})
    chats_per_day = usage.get("chats_per_day", {}).get("value", "\u2014")
    projects_created = usage.get("projects_created", {}).get("value", "\u2014")
    artifacts_created = usage.get("artifacts_created", {}).get("value", "\u2014")
    cpd_change = usage.get("chats_per_day", {}).get("change_percent")
    proj_change = usage.get("projects_created", {}).get("change_percent")
    art_change = usage.get("artifacts_created", {}).get("change_percent")
    cpd_sub = f"Avg per day {_trend_text(cpd_change)}" if cpd_change is not None else "Avg per day"
    proj_sub = f"MTD {_trend_text(proj_change)}" if proj_change is not None else "MTD"
    art_sub = f"MTD {_trend_text(art_change)}" if art_change is not None else "MTD"
    return [
        StatCardRow([
            ("Chats / Day", str(chats_per_day), cpd_sub, LM_RED),
            ("Projects Created", str(projects_created), proj_sub, LM_RED),
            ("Artifacts Created", str(artifacts_created), art_sub, LM_RED),
        ]),
        Spacer(1, 10),
    ]


def _pdf_dau_chart(data):
    dau_chart_data = data.get("dau_chart", {"labels": [], "data": []})
    dau_data = dau_chart_data.get("data", [])
    dau_labels = dau_chart_data.get("labels", [])
    if not dau_data:
        return []
    if len(dau_data) > 14:
        dau_data = dau_data[-14:]
        dau_labels = dau_labels[-14:]
    fig = _make_line_chart(
        dau_labels, dau_data,
        "Daily Active Users (DAU)",
        color=LM_GREEN, color_rgb=(22 / 255, 163 / 255, 74 / 255),
        show_labels=len(dau_data) <= 14,
    )
    img = _fig_to_image(fig, USABLE_WIDTH - 8, 2.2 * inch)
    chart_table = Table([[img]], colWidths=[USABLE_WIDTH])
    chart_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#1a1a1a")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return [
        SectionHeader("Daily Active Users \u00b7 Last 30 Days"),
        Spacer(1, 10),
        chart_table,
        Spacer(1, 14),
    ]


def _pdf_top_users_chats(data):
    top_chats = data.get("top_users_chats", [])
    if not top_chats:
        return []
    fig = _make_hbar_chart(
        [u["name"] for u in top_chats],
        [u["count"] for u in top_chats],
        "Top Users by Chats (MTD)",
    )
    h = max(1.5, len(top_chats) * 0.35 + 0.8) * inch
    img = _fig_to_image(fig, USABLE_WIDTH - 8, h)
    chart_table = Table([[img]], colWidths=[USABLE_WIDTH])
    chart_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#1a1a1a")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return [chart_table, Spacer(1, 14)]


def _pdf_top_users_projects(data):
    top_projects = data.get("top_users_projects", [])
    if not top_projects:
        return []
    fig = _make_hbar_chart(
        [u["name"] for u in top_projects],
        [u["count"] for u in top_projects],
        "Top Users by Projects (MTD)",
    )
    h = max(1.5, len(top_projects) * 0.35 + 0.8) * inch
    img = _fig_to_image(fig, USABLE_WIDTH - 8, h)
    chart_table = Table([[img]], colWidths=[USABLE_WIDTH])
    chart_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#1a1a1a")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return [chart_table, Spacer(1, 14)]


def _pdf_top_users_artifacts(data):
    top_artifacts = data.get("top_users_artifacts", [])
    if not top_artifacts:
        return []
    fig = _make_hbar_chart(
        [u["name"] for u in top_artifacts],
        [u["count"] for u in top_artifacts],
        "Top Users by Artifacts (MTD)",
    )
    h = max(1.5, len(top_artifacts) * 0.35 + 0.8) * inch
    img = _fig_to_image(fig, USABLE_WIDTH - 8, h)
    chart_table = Table([[img]], colWidths=[USABLE_WIDTH])
    chart_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#1a1a1a")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return [chart_table, Spacer(1, 14)]


def _pdf_claude_code_stats(data):
    cc = data.get("claude_code", {})
    cc_summary = cc.get("summary", {})
    cc_users = cc.get("users", [])
    if not cc_summary:
        return []

    month_str = datetime.now().strftime("%B %Y")
    cc_active = cc_summary.get("active_users", 0)
    cc_sessions = cc_summary.get("total_sessions", 0)
    cc_lines = cc_summary.get("total_lines_accepted", 0)
    cc_commits = cc_summary.get("commits_created", 0)
    cc_prs = cc_summary.get("pull_requests_created", 0)

    flowables = [
        SectionHeader(f"Claude Code \u00b7 {month_str}", color=CC_PURPLE),
        Spacer(1, 10),
        StatCardRow([
            ("Active Users", str(cc_active), f"{month_str} MTD", CC_PURPLE),
            ("Sessions", str(cc_sessions), f"{month_str} MTD", CC_PURPLE),
            ("Lines Accepted", f"{cc_lines:,}", f"{month_str} MTD", LM_GREEN),
            ("Commits", str(cc_commits), f"{month_str} MTD", "#2563eb"),
            ("Pull Requests", str(cc_prs), f"{month_str} MTD", "#2563eb"),
        ]),
        Spacer(1, 14),
    ]

    # Top users table
    if cc_users:
        cc_table = _build_cc_user_table(cc_users)
        flowables.extend([
            SectionHeader("Claude Code User Breakdown", color=CC_PURPLE),
            Spacer(1, 10),
            cc_table,
            Spacer(1, 14),
        ])
    return flowables


def _pdf_cc_sessions_chart(data):
    cc = data.get("claude_code", {})
    chart_data = cc.get("activity_chart", {"labels": [], "data": []})
    vals = chart_data.get("data", [])
    labels = chart_data.get("labels", [])
    if not vals:
        return []
    fig = _make_line_chart(labels, vals, "Claude Code Daily Sessions",
                           color=CC_PURPLE, color_rgb=CC_PURPLE_RGB)
    img = _fig_to_image(fig, USABLE_WIDTH - 8, 2.2 * inch)
    chart_table = Table([[img]], colWidths=[USABLE_WIDTH])
    chart_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return [chart_table, Spacer(1, 14)]


def _pdf_cc_lines_chart(data):
    cc = data.get("claude_code", {})
    chart_data = cc.get("lines_chart", {"labels": [], "data": []})
    vals = chart_data.get("data", [])
    labels = chart_data.get("labels", [])
    if not vals:
        return []
    fig = _make_line_chart(labels, vals, "Claude Code Daily Lines Accepted",
                           color=CC_PURPLE, color_rgb=CC_PURPLE_RGB)
    img = _fig_to_image(fig, USABLE_WIDTH - 8, 2.2 * inch)
    chart_table = Table([[img]], colWidths=[USABLE_WIDTH])
    chart_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return [chart_table, Spacer(1, 14)]


def _pdf_cc_top_users(data):
    cc = data.get("claude_code", {})
    cc_users = cc.get("users", [])[:10]
    if not cc_users:
        return []
    names = [u.get("name", "?") for u in cc_users]
    sessions = [u.get("total_sessions", 0) for u in cc_users]
    lines = [u.get("total_lines_accepted", 0) for u in cc_users]
    flowables = []
    fig1 = _make_hbar_chart(names, sessions, "Top Claude Code Users (Sessions MTD)",
                            color=CC_PURPLE)
    h1 = max(1.5 * inch, len(names) * 0.28 * inch)
    img1 = _fig_to_image(fig1, USABLE_WIDTH - 8, h1)
    flowables.extend([img1, Spacer(1, 14)])
    fig2 = _make_hbar_chart(names, lines, "Top Claude Code Users (Lines Accepted MTD)",
                            color="#16a34a")
    img2 = _fig_to_image(fig2, USABLE_WIDTH - 8, h1)
    flowables.extend([img2, Spacer(1, 14)])
    return flowables


def _pdf_cc_user_table(data):
    cc = data.get("claude_code", {})
    cc_users = cc.get("users", [])
    if not cc_users:
        return []
    cc_table = _build_cc_user_table(cc_users)
    return [
        SectionHeader("Claude Code User Breakdown", color=CC_PURPLE),
        Spacer(1, 10),
        cc_table,
        Spacer(1, 14),
    ]


def _pdf_member_directory(data):
    members = data.get("members", [])
    top_projects = data.get("top_users_projects", [])
    top_artifacts = data.get("top_users_artifacts", [])
    if not members:
        return []
    table = _build_member_table(members, top_projects, top_artifacts)
    return [
        SectionHeader("Member Directory"),
        Spacer(1, 10),
        table,
        Spacer(1, 14),
    ]


def _pdf_executive_summary(data, components):
    summary_text = generate_executive_summary(data, components, None)
    styles = getSampleStyleSheet()
    body_style = ParagraphStyle(
        "exec_summary_body", parent=styles["Normal"],
        fontSize=10, leading=14, textColor=colors.HexColor("#374151"),
    )
    label_style = ParagraphStyle(
        "exec_summary_label", parent=styles["Normal"],
        fontSize=7, textColor=colors.HexColor("#9ca3af"),
        fontName="Helvetica-Bold", spaceAfter=4,
    )
    content_table = Table(
        [[Paragraph("AUTO-GENERATED SUMMARY", label_style)],
         [Paragraph(summary_text, body_style)]],
        colWidths=[USABLE_WIDTH - 20],
    )
    content_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
        ("LINEBEFORE", (0, 0), (0, -1), 4, colors.HexColor(LM_RED)),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 10),
    ]))
    return [content_table, Spacer(1, 14)]


def _pdf_email_highlights(data):
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

    styles = getSampleStyleSheet()
    highlight_style = ParagraphStyle(
        "highlights", parent=styles["Normal"],
        fontSize=9, leading=14, textColor=colors.HexColor("#374151"),
    )
    text = (
        f"\u2022 {total_seats} total seats ({active_count} active, {pending_count} pending)<br/>"
        f"\u2022 DAU: {dau} | WAU: {wau} | Utilization: {util_str}"
    )
    highlight_table = Table(
        [[Paragraph(text, highlight_style)]],
        colWidths=[USABLE_WIDTH - 20],
    )
    highlight_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fef2f2")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#fecaca")),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    return [highlight_table, Spacer(1, 14)]


# ---------------------------------------------------------------------------
# Component dispatcher
# ---------------------------------------------------------------------------
_COMPONENT_RENDERERS = {
    "stats_row": lambda data, comps: _pdf_stats_row(data),
    "status_pie": lambda data, comps: _pdf_status_pie(data),
    "status_donut": lambda data, comps: _pdf_status_pie(data),
    "role_pie": lambda data, comps: _pdf_role_pie(data),
    "role_donut": lambda data, comps: _pdf_role_pie(data),
    "tier_pie": lambda data, comps: _pdf_tier_pie(data),
    "activity_metrics": lambda data, comps: _pdf_activity_metrics(data),
    "usage_stats": lambda data, comps: _pdf_usage_stats(data),
    "daily_chats": lambda data, comps: _pdf_daily_chats(data),
    "dau_chart": lambda data, comps: _pdf_dau_chart(data),
    "wau_trend": lambda data, comps: _pdf_wau_trend(data),
    "wau_stats_tile": lambda data, comps: _pdf_wau_stats_tile(data),
    "top_users_projects": lambda data, comps: _pdf_top_users_projects(data),
    "top_users_artifacts": lambda data, comps: _pdf_top_users_artifacts(data),
    "top_users_chats": lambda data, comps: _pdf_top_users_chats(data),
    "claude_code_stats": lambda data, comps: _pdf_claude_code_stats(data),
    "cc_sessions_chart": lambda data, comps: _pdf_cc_sessions_chart(data),
    "cc_lines_chart": lambda data, comps: _pdf_cc_lines_chart(data),
    "cc_top_users": lambda data, comps: _pdf_cc_top_users(data),
    "cc_user_table": lambda data, comps: _pdf_cc_user_table(data),
    "member_directory": lambda data, comps: _pdf_member_directory(data),
    "executive_summary": lambda data, comps: _pdf_executive_summary(data, comps),
    "email_highlights": lambda data, comps: _pdf_email_highlights(data),
}


# ---------------------------------------------------------------------------
# Main PDF generation
# ---------------------------------------------------------------------------
def generate_report_pdf(data: dict, report_config: dict, output_dir: str = None) -> str:
    """Generate PDF for a custom report. Returns the file path."""
    if output_dir is None:
        output_dir = config.OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)

    report_id = report_config.get("id", "custom")
    title = report_config.get("title", "Custom Report")
    components = sorted(
        [c for c in report_config.get("components", []) if c.get("enabled", True)],
        key=lambda c: c.get("order", 0),
    )
    global_range_config = report_config.get("global_date_range")

    filepath = os.path.join(output_dir, f"report_{report_id}.pdf")

    now = datetime.now()
    today_str = now.strftime("%B %-d, %Y %-I:%M %p")

    styles = getSampleStyleSheet()

    doc = SimpleDocTemplate(
        filepath,
        pagesize=letter,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN + 14,
    )

    story = []

    # Resolve global date range (handles relative/absolute/all modes)
    global_start, global_end = resolve_date_range(global_range_config)

    # --- Header Banner with report title as subtitle ---
    _rpt_settings = config.load_settings()
    _rpt_org = _rpt_settings.get("org_display_name") or "Claude Usage Dashboard"
    story.append(HeaderBanner(
        "Claude Usage Dashboard",
        f"{title} \u00b7 {_rpt_org}",
        today_str,
    ))
    story.append(Spacer(1, 10))

    # --- Date range line ---
    if global_start and global_end:
        try:
            s_fmt = datetime.strptime(global_start, "%Y-%m-%d").strftime("%B %-d, %Y")
            e_fmt = datetime.strptime(global_end, "%Y-%m-%d").strftime("%B %-d, %Y")
            date_text = f"Report period: {s_fmt} \u2013 {e_fmt}"
        except ValueError:
            date_text = f"Report period: {global_start} \u2013 {global_end}"
        date_style = ParagraphStyle(
            "date_range", parent=styles["Normal"],
            fontSize=9, textColor=colors.HexColor("#6b7280"),
            alignment=TA_CENTER,
        )
        story.append(Paragraph(date_text, date_style))
        story.append(Spacer(1, 10))

    _PIE_KEYS = {"status_pie", "status_donut", "role_pie", "role_donut", "tier_pie"}
    pie_buf = []  # buffer for consecutive pie flowables

    def _flush_pie_buf():
        """Arrange buffered pie charts side-by-side in a table row."""
        if not pie_buf:
            return
        col_width = USABLE_WIDTH / len(pie_buf)
        # Each entry in pie_buf is a list of flowables — pass as-is for Table cells
        t = Table([pie_buf], colWidths=[col_width] * len(pie_buf))
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(t)
        story.append(Spacer(1, 10))
        pie_buf.clear()

    # --- Render each component ---
    for comp in components:
        key = comp.get("key")
        renderer = _COMPONENT_RENDERERS.get(key)
        if not renderer:
            continue

        # Apply date range filtering — per-component override or global
        comp_data = data
        comp_range = comp.get("date_range")
        comp_start, comp_end = resolve_date_range(comp_range)
        if comp_start and comp_end:
            comp_data = filter_data_by_range(data, comp_start, comp_end)
        elif global_start and global_end:
            comp_data = filter_data_by_range(data, global_start, global_end)

        flowables = renderer(comp_data, components)
        if key in _PIE_KEYS:
            pie_buf.append(flowables)
        else:
            _flush_pie_buf()
            story.extend(flowables)

    _flush_pie_buf()

    # Build PDF
    NumberedCanvas = _make_numbered_canvas_factory(today_str)
    doc.build(story, canvasmaker=NumberedCanvas)
    file_size = os.path.getsize(filepath)
    logger.info(f"Custom report PDF saved to {filepath} ({file_size:,} bytes)")
    return filepath
