"""
Generates a PDF report using ReportLab + matplotlib.
Custom Flowable subclasses for header banner and stat cards.
"""

import matplotlib
matplotlib.use("Agg")  # Must be before pyplot import

import io
import os
from collections import Counter
from datetime import datetime

import matplotlib.pyplot as plt
import numpy as np
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Flowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

import config

logger = config.get_logger()

# ---------------------------------------------------------------------------
# Brand colors
# ---------------------------------------------------------------------------
LM_RED = "#C8102E"
LM_RED_RGB = (200 / 255, 16 / 255, 46 / 255)
LM_GREEN = "#16a34a"
LM_AMBER = "#d97706"
LM_GRAY = "#6b7280"
LM_LIGHT_GRAY = "#f3f4f6"

PAGE_WIDTH, PAGE_HEIGHT = letter
MARGIN = 0.5 * inch
USABLE_WIDTH = PAGE_WIDTH - 2 * MARGIN


# ---------------------------------------------------------------------------
# Custom Flowable: Header Banner
# ---------------------------------------------------------------------------
class HeaderBanner(Flowable):
    """Red rounded-rect header with LM logo, title, and date badge."""

    def __init__(self, title, subtitle, date_str, width=USABLE_WIDTH):
        super().__init__()
        self.title = title
        self.subtitle = subtitle
        self.date_str = date_str
        self._width = width
        self.height = 72

    def wrap(self, availWidth, availHeight):
        return self._width, self.height

    def draw(self):
        c = self.canv
        w, h = self._width, self.height

        # Red rounded rectangle background
        c.setFillColor(colors.HexColor(LM_RED))
        c.roundRect(0, 0, w, h, 10, fill=1, stroke=0)

        # White circle with "LM"
        cx, cy = 40, h / 2
        c.setFillColor(colors.white)
        c.circle(cx, cy, 18, fill=1, stroke=0)
        c.setFillColor(colors.HexColor(LM_RED))
        c.setFont("Helvetica-Bold", 14)
        c.drawCentredString(cx, cy - 5, "LM")

        # Title
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 18)
        c.drawString(68, h / 2 + 8, self.title)

        # Subtitle
        c.setFillColor(colors.HexColor("#FFB3B3"))
        c.setFont("Helvetica", 10)
        c.drawString(68, h / 2 - 12, self.subtitle)

        # Date badge on the right
        badge_text = f"As of {self.date_str}"
        badge_w = c.stringWidth(badge_text, "Helvetica", 9) + 20
        badge_x = w - badge_w - 16
        badge_y = h / 2 - 9
        c.setFillColor(colors.HexColor("#A00D24"))
        c.roundRect(badge_x, badge_y, badge_w, 18, 9, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("Helvetica", 9)
        c.drawCentredString(badge_x + badge_w / 2, badge_y + 5, badge_text)


# ---------------------------------------------------------------------------
# Custom Flowable: Stat Card Row
# ---------------------------------------------------------------------------
class StatCardRow(Flowable):
    """Four stat cards side by side with colored left accent bars."""

    def __init__(self, cards, width=USABLE_WIDTH):
        """cards: list of (label, value, accent_color_hex)"""
        super().__init__()
        self.cards = cards
        self._width = width
        self.height = 60

    def wrap(self, availWidth, availHeight):
        return self._width, self.height

    def draw(self):
        c = self.canv
        gap = 10
        card_w = (self._width - gap * 3) / 4
        h = self.height

        for i, (label, value, accent) in enumerate(self.cards):
            x = i * (card_w + gap)

            # Card background
            c.setFillColor(colors.white)
            c.setStrokeColor(colors.HexColor("#e5e7eb"))
            c.roundRect(x, 0, card_w, h, 6, fill=1, stroke=1)

            # Left accent bar
            c.setFillColor(colors.HexColor(accent))
            c.roundRect(x, 0, 4, h, 2, fill=1, stroke=0)

            # Value
            c.setFillColor(colors.HexColor(accent))
            c.setFont("Helvetica-Bold", 20)
            c.drawString(x + 16, h / 2 + 2, str(value))

            # Label
            c.setFillColor(colors.HexColor(LM_GRAY))
            c.setFont("Helvetica", 9)
            c.drawString(x + 16, h / 2 - 16, label)


# ---------------------------------------------------------------------------
# Matplotlib chart helpers
# ---------------------------------------------------------------------------
def _fig_to_image(fig, width, height, dpi=150):
    """Convert a matplotlib figure to a ReportLab Image."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return Image(buf, width=width, height=height)


def _make_donut_chart(labels, sizes, title, chart_colors):
    """Create a matplotlib donut chart."""
    fig, ax = plt.subplots(figsize=(4, 3))
    fig.patch.set_facecolor("white")

    if sum(sizes) == 0:
        sizes = [1]
        labels = ["No Data"]
        chart_colors = ["#e5e7eb"]

    wedges, texts, autotexts = ax.pie(
        sizes,
        labels=labels,
        colors=chart_colors,
        autopct="%1.0f%%",
        pctdistance=0.75,
        startangle=90,
        textprops={"fontsize": 8},
    )
    for t in autotexts:
        t.set_fontsize(8)
        t.set_fontweight("bold")

    # White center circle for donut effect
    centre = plt.Circle((0, 0), 0.55, fc="white")
    ax.add_artist(centre)
    ax.set_title(title, fontsize=10, fontweight="bold", pad=10)

    return fig


def _make_line_chart(labels, data, title):
    """Create a matplotlib line chart for daily chats."""
    fig, ax = plt.subplots(figsize=(7, 2.5))
    fig.patch.set_facecolor("white")

    if not data:
        ax.text(0.5, 0.5, "No data available", ha="center", va="center", transform=ax.transAxes)
        ax.set_title(title, fontsize=10, fontweight="bold")
        return fig

    x = range(len(data))
    ax.fill_between(x, data, alpha=0.1, color=LM_RED)
    ax.plot(x, data, color=LM_RED, linewidth=2, marker="o", markersize=6,
            markerfacecolor="white", markeredgecolor=LM_RED, markeredgewidth=2)

    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=7, rotation=0)
    ax.set_title(title, fontsize=10, fontweight="bold", pad=10)
    ax.grid(axis="y", alpha=0.3)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_ylim(bottom=0)

    fig.tight_layout()
    return fig


def _make_hbar_chart(names, counts, title):
    """Create a matplotlib horizontal bar chart."""
    fig, ax = plt.subplots(figsize=(7, max(2, len(names) * 0.35 + 0.8)))
    fig.patch.set_facecolor("white")

    if not names:
        ax.text(0.5, 0.5, "No data available", ha="center", va="center", transform=ax.transAxes)
        ax.set_title(title, fontsize=10, fontweight="bold")
        return fig

    y_pos = range(len(names))
    ax.barh(y_pos, counts, color=LM_RED, height=0.6, edgecolor="none")
    ax.set_yticks(list(y_pos))
    ax.set_yticklabels(names, fontsize=8)
    ax.invert_yaxis()
    ax.set_title(title, fontsize=10, fontweight="bold", pad=10)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="x", alpha=0.3)

    # Value labels on bars
    for i, v in enumerate(counts):
        ax.text(v + max(counts) * 0.02, i, str(v), va="center", fontsize=8, fontweight="bold")

    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Member directory table
# ---------------------------------------------------------------------------
def _build_member_table(members, top_projects, top_artifacts):
    """Build a ReportLab Table for the member directory."""
    styles = getSampleStyleSheet()

    cell_style = ParagraphStyle("cell", parent=styles["Normal"], fontSize=8, leading=10)
    header_style = ParagraphStyle(
        "header", parent=styles["Normal"], fontSize=8, leading=10,
        textColor=colors.HexColor("#374151"), fontName="Helvetica-Bold"
    )

    project_lookup = {u["name"]: u["count"] for u in top_projects}
    artifact_lookup = {u["name"]: u["count"] for u in top_artifacts}

    # Header row
    headers = ["Member", "Role", "Status", "Email", "Projects", "Artifacts"]
    header_row = [Paragraph(h, header_style) for h in headers]

    data_rows = [header_row]
    for m in members:
        name = m.get("name", "")
        role = m.get("role", "User")
        status = m.get("status", "Active")
        email = m.get("email", "")
        projects = project_lookup.get(name, 0)
        artifacts = artifact_lookup.get(name, 0)

        # Color role text
        role_color = LM_RED if "owner" in role.lower() else "#374151"
        role_p = Paragraph(f'<font color="{role_color}">{role}</font>', cell_style)

        # Status badge
        if status == "Active":
            status_p = Paragraph(
                '<font color="#15803d" backColor="#dcfce7">&nbsp;Active&nbsp;</font>', cell_style
            )
        else:
            status_p = Paragraph(
                '<font color="#b45309" backColor="#fef3c7">&nbsp;Pending&nbsp;</font>', cell_style
            )

        data_rows.append([
            Paragraph(name, cell_style),
            role_p,
            status_p,
            Paragraph(email, cell_style),
            Paragraph(str(projects), cell_style),
            Paragraph(str(artifacts), cell_style),
        ])

    # Column widths: 33%, 16%, 13%, 19%, 10%, 9%
    col_widths = [
        USABLE_WIDTH * 0.33,
        USABLE_WIDTH * 0.16,
        USABLE_WIDTH * 0.13,
        USABLE_WIDTH * 0.19,
        USABLE_WIDTH * 0.10,
        USABLE_WIDTH * 0.09,
    ]

    table = Table(data_rows, colWidths=col_widths, repeatRows=1)

    # Table styling
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(LM_LIGHT_GRAY)),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#d1d5db")),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#e5e7eb")),
    ]

    # Alternating row colors
    for i in range(1, len(data_rows)):
        if i % 2 == 0:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fafafa")))

    table.setStyle(TableStyle(style_cmds))
    return table


# ---------------------------------------------------------------------------
# Main PDF generation
# ---------------------------------------------------------------------------
def generate_pdf(data: dict, output_dir: str = None) -> str:
    """Generate the PDF report. Returns the file path."""
    if output_dir is None:
        output_dir = config.OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)

    filepath = os.path.join(output_dir, "claude_usage_dashboard.pdf")

    members = data.get("members", [])
    daily_chats = data.get("daily_chats", {"labels": [], "data": []})
    top_projects = data.get("top_users_projects", [])
    top_artifacts = data.get("top_users_artifacts", [])
    plan_tier = data.get("plan_tier", "Standard")
    total_seats = data.get("total_seats", len(members))
    from_cache = data.get("from_cache", False)
    overview = data.get("activity_overview", {})
    cc = data.get("claude_code", {})
    cc_summary = cc.get("summary", {})
    cc_users = cc.get("users", [])
    cc_activity_chart = cc.get("activity_chart", {"labels": [], "data": []})

    active_count = data.get("active_members", sum(1 for m in members if m.get("status") == "Active"))
    pending_count = data.get("pending_invites", sum(1 for m in members if m.get("status") == "Pending"))

    owners_count = sum(
        1 for m in members if "owner" in m.get("role", "").lower()
    )
    users_count = len(members) - owners_count

    dau = overview.get("dau", {}).get("value", "—")
    wau = overview.get("wau", {}).get("value", "—")
    utilization = overview.get("utilization", {}).get("value", "—")
    if isinstance(utilization, (int, float)):
        utilization_str = f"{utilization:.0f}%"
    else:
        utilization_str = str(utilization)

    today_str = datetime.now().strftime("%B %d, %Y")

    styles = getSampleStyleSheet()

    # Build the document
    doc = SimpleDocTemplate(
        filepath,
        pagesize=letter,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
    )

    story = []

    # --- Header Banner ---
    story.append(HeaderBanner(
        "Claude Usage Dashboard",
        f"Lou Malnati's Pizzeria \u00b7 {plan_tier} Plan",
        today_str,
    ))
    story.append(Spacer(1, 12))

    # --- Stale data warning ---
    if from_cache:
        warn_style = ParagraphStyle(
            "warn", parent=styles["Normal"], fontSize=9,
            textColor=colors.HexColor("#b45309"), backColor=colors.HexColor("#fef3c7"),
            borderPadding=6, leading=12,
        )
        story.append(Paragraph(
            "\u26a0 Data may be stale \u2014 scrape failed. Showing cached data.", warn_style
        ))
        story.append(Spacer(1, 8))

    # --- Stat Cards ---
    story.append(StatCardRow([
        ("Assigned Seats", f"{active_count + pending_count}/{total_seats}", LM_RED),
        ("Daily Active", dau, LM_GREEN),
        ("Weekly Active", wau, "#2563eb"),
        ("Utilization", utilization_str, LM_AMBER),
    ]))
    story.append(Spacer(1, 16))

    # --- Org Overview (donut charts side by side) ---
    section_title = ParagraphStyle(
        "section_title", parent=styles["Normal"], fontSize=12,
        fontName="Helvetica-Bold", spaceBefore=8, spaceAfter=8,
        textColor=colors.HexColor("#111827"),
    )
    story.append(Paragraph("Org Overview", section_title))

    fig_status = _make_donut_chart(
        ["Active", "Pending"], [active_count, pending_count],
        "Member Status", [LM_GREEN, LM_AMBER]
    )
    fig_roles = _make_donut_chart(
        ["Owners", "Users"], [owners_count, users_count],
        "Role Distribution", [LM_RED, LM_GRAY]
    )

    donut_w = USABLE_WIDTH / 2 - 10
    donut_h = donut_w * 0.75
    img_status = _fig_to_image(fig_status, donut_w, donut_h)
    img_roles = _fig_to_image(fig_roles, donut_w, donut_h)

    donut_table = Table([[img_status, img_roles]], colWidths=[USABLE_WIDTH / 2, USABLE_WIDTH / 2])
    donut_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(donut_table)
    story.append(Spacer(1, 16))

    # --- Activity Analytics ---
    story.append(Paragraph("Activity Analytics", section_title))

    # Daily Chats line chart
    fig_chats = _make_line_chart(
        daily_chats.get("labels", []),
        daily_chats.get("data", []),
        "Daily Chats (Last 7 Days)"
    )
    story.append(_fig_to_image(fig_chats, USABLE_WIDTH, 2.0 * inch))
    story.append(Spacer(1, 12))

    # Top Users by Projects
    if top_projects:
        fig_proj = _make_hbar_chart(
            [u["name"] for u in top_projects],
            [u["count"] for u in top_projects],
            "Top Users by Projects MTD"
        )
        proj_h = max(1.5, len(top_projects) * 0.25 + 0.6) * inch
        story.append(_fig_to_image(fig_proj, USABLE_WIDTH, proj_h))
        story.append(Spacer(1, 12))

    # Top Users by Artifacts
    if top_artifacts:
        fig_art = _make_hbar_chart(
            [u["name"] for u in top_artifacts],
            [u["count"] for u in top_artifacts],
            "Top Users by Artifacts MTD"
        )
        art_h = max(1.5, len(top_artifacts) * 0.25 + 0.6) * inch
        story.append(_fig_to_image(fig_art, USABLE_WIDTH, art_h))
        story.append(Spacer(1, 12))

    # --- Claude Code Analytics ---
    if cc_summary:
        story.append(Paragraph("Claude Code Analytics (MTD)", section_title))

        cc_active = cc_summary.get("active_users", 0)
        cc_sessions = cc_summary.get("total_sessions", 0)
        cc_lines = cc_summary.get("total_lines_accepted", 0)
        cc_cost = cc_summary.get("total_cost_usd", "0")
        try:
            cc_cost_str = f"${float(cc_cost):.2f}"
        except (ValueError, TypeError):
            cc_cost_str = "$0.00"

        story.append(StatCardRow([
            ("CC Active Users", cc_active, "#7c3aed"),
            ("CC Sessions", cc_sessions, "#7c3aed"),
            ("Lines Accepted", cc_lines, LM_GREEN),
            ("CC Cost", cc_cost_str, LM_AMBER),
        ]))
        story.append(Spacer(1, 12))

        # Claude Code sessions line chart
        cc_labels = cc_activity_chart.get("labels", [])
        cc_data = cc_activity_chart.get("data", [])
        if cc_labels and cc_data:
            fig_cc = _make_line_chart(cc_labels, cc_data, "Claude Code Sessions (Daily)")
            story.append(_fig_to_image(fig_cc, USABLE_WIDTH, 2.0 * inch))
            story.append(Spacer(1, 12))

        # Top Claude Code users bar chart
        if cc_users:
            cc_names = [u.get("name", "?") for u in cc_users[:10]]
            cc_vals = [u.get("total_sessions", 0) for u in cc_users[:10]]
            fig_cc_users = _make_hbar_chart(cc_names, cc_vals, "Top Claude Code Users (Sessions MTD)")
            cc_h = max(1.5, len(cc_names) * 0.25 + 0.6) * inch
            story.append(_fig_to_image(fig_cc_users, USABLE_WIDTH, cc_h))
            story.append(Spacer(1, 12))

    # --- Member Directory Table ---
    story.append(Paragraph("Member Directory", section_title))
    story.append(Spacer(1, 4))

    if members:
        member_table = _build_member_table(members, top_projects, top_artifacts)
        story.append(member_table)
    else:
        no_data = ParagraphStyle("nodata", parent=styles["Normal"], fontSize=10, textColor=colors.gray)
        story.append(Paragraph("No member data available.", no_data))

    story.append(Spacer(1, 20))

    # --- Footer ---
    footer_style = ParagraphStyle(
        "footer", parent=styles["Normal"], fontSize=8,
        alignment=TA_CENTER, textColor=colors.HexColor("#9ca3af"),
    )
    story.append(Paragraph(
        f"Data sourced from Claude.ai Admin Console and Claude.ai Analytics &middot; "
        f"Lou Malnati's Pizzeria &middot; {today_str}",
        footer_style,
    ))

    # Build PDF
    doc.build(story)
    file_size = os.path.getsize(filepath)
    logger.info(f"PDF report saved to {filepath} ({file_size:,} bytes)")

    if file_size < 35000:
        logger.warning(f"PDF file size ({file_size:,} bytes) is smaller than expected (< 35KB)")

    return filepath
