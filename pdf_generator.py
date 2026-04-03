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
    """Four stat cards with uppercase label, large value, and subtitle."""

    def __init__(self, cards, width=USABLE_WIDTH):
        """cards: list of (label, value, subtitle)"""
        super().__init__()
        self.cards = cards
        self._width = width
        self.height = 80

    def wrap(self, availWidth, availHeight):
        return self._width, self.height

    def draw(self):
        c = self.canv
        gap = 10
        card_w = (self._width - gap * (len(self.cards) - 1)) / len(self.cards)
        h = self.height

        for i, card in enumerate(self.cards):
            label, value, subtitle = card[0], card[1], card[2] if len(card) > 2 else ""
            x = i * (card_w + gap)

            # Card border
            c.setFillColor(colors.white)
            c.setStrokeColor(colors.HexColor("#e5e7eb"))
            c.setLineWidth(0.5)
            c.roundRect(x, 0, card_w, h, 6, fill=1, stroke=1)

            # Uppercase label
            c.setFillColor(colors.HexColor(LM_GRAY))
            c.setFont("Helvetica-Bold", 7)
            c.drawString(x + 12, h - 18, label.upper())

            # Large value
            c.setFillColor(colors.HexColor("#111827"))
            c.setFont("Helvetica-Bold", 22)
            c.drawString(x + 12, h - 44, str(value))

            # Subtitle
            if subtitle:
                c.setFillColor(colors.HexColor("#9ca3af"))
                c.setFont("Helvetica", 7)
                # Truncate subtitle if too long
                max_w = card_w - 24
                text = subtitle
                while c.stringWidth(text, "Helvetica", 7) > max_w and len(text) > 10:
                    text = text[:-4] + "..."
                c.drawString(x + 12, 10, text)


# ---------------------------------------------------------------------------
# Custom Flowable: Section Header
# ---------------------------------------------------------------------------
class SectionHeader(Flowable):
    """Gray uppercase section divider with red left accent bar."""

    def __init__(self, text, width=USABLE_WIDTH):
        super().__init__()
        self.text = text
        self._width = width
        self.height = 24

    def wrap(self, availWidth, availHeight):
        return self._width, self.height

    def draw(self):
        c = self.canv
        # Red accent bar
        c.setFillColor(colors.HexColor(LM_RED))
        c.rect(0, 4, 3, self.height - 8, fill=1, stroke=0)
        # Text
        c.setFillColor(colors.HexColor(LM_GRAY))
        c.setFont("Helvetica-Bold", 7)
        c.drawString(12, 9, self.text.upper())


# ---------------------------------------------------------------------------
# Custom Flowable: Stats Summary Row (below charts)
# ---------------------------------------------------------------------------
class StatsSummaryRow(Flowable):
    """Row of summary stats displayed below a chart."""

    def __init__(self, items, width=USABLE_WIDTH):
        """items: list of (value, label, color_hex_or_None)"""
        super().__init__()
        self.items = items
        self._width = width
        self.height = 40

    def wrap(self, availWidth, availHeight):
        return self._width, self.height

    def draw(self):
        c = self.canv
        n = len(self.items)
        col_w = self._width / n

        for i, (value, label, color) in enumerate(self.items):
            x = i * col_w + 12
            val_color = color or "#111827"
            c.setFillColor(colors.HexColor(val_color))
            c.setFont("Helvetica-Bold", 16)
            c.drawString(x, 18, str(value))
            c.setFillColor(colors.HexColor("#9ca3af"))
            c.setFont("Helvetica", 7)
            c.drawString(x, 6, label)


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



def _make_line_chart(labels, data, title=None, subtitle=None, show_labels=True):
    """Create a matplotlib line chart with optional data point labels."""
    fig, ax = plt.subplots(figsize=(7, 2.5))
    fig.patch.set_facecolor("white")

    if not data:
        ax.text(0.5, 0.5, "No data available", ha="center", va="center", transform=ax.transAxes)
        if title:
            ax.set_title(title, fontsize=10, fontweight="bold")
        return fig

    x = range(len(data))
    ax.fill_between(x, data, alpha=0.08, color=LM_RED)
    ax.plot(x, data, color=LM_RED, linewidth=2, marker="o", markersize=7,
            markerfacecolor="white", markeredgecolor=LM_RED, markeredgewidth=2)

    # Data point labels
    if show_labels:
        y_range = max(data) - min(data) if max(data) != min(data) else max(data) or 1
        for i, v in enumerate(data):
            ax.annotate(str(int(v)), (i, v), textcoords="offset points",
                        xytext=(0, 10), ha="center", fontsize=7, fontweight="bold",
                        color=LM_RED)

    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=7, rotation=0)
    if title:
        ax.set_title(title, fontsize=11, fontweight="bold", loc="left", pad=10)
    ax.grid(axis="y", alpha=0.3)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_ylim(bottom=0, top=max(data) * 1.3 if data else 1)

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
    cell_bold = ParagraphStyle("cellbold", parent=styles["Normal"], fontSize=8, leading=10,
                               fontName="Helvetica-Bold")
    cell_center = ParagraphStyle("cellcenter", parent=cell_style, alignment=TA_CENTER)
    cell_center_bold = ParagraphStyle("cellcenterbold", parent=cell_bold, alignment=TA_CENTER)
    header_style = ParagraphStyle(
        "header", parent=styles["Normal"], fontSize=7, leading=10,
        textColor=colors.HexColor("#374151"), fontName="Helvetica-Bold"
    )
    header_center = ParagraphStyle("headercenter", parent=header_style, alignment=TA_CENTER)
    email_style = ParagraphStyle("email", parent=styles["Normal"], fontSize=7, leading=9,
                                 textColor=colors.HexColor("#9ca3af"))

    project_lookup = {u["name"]: u["count"] for u in top_projects}
    artifact_lookup = {u["name"]: u["count"] for u in top_artifacts}

    # Header row
    headers = [
        Paragraph("MEMBER", header_style),
        Paragraph("ROLE", header_center),
        Paragraph("TIER", header_center),
        Paragraph("STATUS", header_center),
        Paragraph("PROJECTS<br/>MTD", header_center),
        Paragraph("ARTIFACTS<br/>MTD", header_center),
    ]

    data_rows = [headers]
    for m in members:
        name = m.get("name", "")
        role = m.get("role", "User")
        status = m.get("status", "Active")
        email = m.get("email", "")
        seat_tier = m.get("seat_tier", "team_standard")
        is_premium = "tier_1" in seat_tier.lower() or "premium" in seat_tier.lower()
        tier_label = "Premium" if is_premium else "Standard"
        projects = project_lookup.get(name, 0)
        artifacts = artifact_lookup.get(name, 0)

        # Member: name (bold) + premium badge + email
        premium_badge = ' <font color="#7c3aed" backColor="#ede9fe" size="6">&nbsp;Premium&nbsp;</font>' if is_premium else ""
        name_p = Paragraph(
            f'<b>{name}</b>{premium_badge}<br/><font color="#9ca3af" size="7">{email}</font>',
            cell_style,
        )

        # Role badge
        if "primary" in role.lower() and "owner" in role.lower():
            role_p = Paragraph(
                f'<font color="white" backColor="{LM_RED}" size="7">&nbsp;Primary Owner&nbsp;</font>',
                cell_center,
            )
        elif "owner" in role.lower():
            role_p = Paragraph(
                f'<font color="white" backColor="{LM_GREEN}" size="7">&nbsp;Owner&nbsp;</font>',
                cell_center,
            )
        else:
            role_p = Paragraph(
                '<font color="#374151" backColor="#f3f4f6" size="7">&nbsp;User&nbsp;</font>',
                cell_center,
            )

        # Status badge
        if status == "Active":
            status_p = Paragraph(
                '<font color="#15803d"><b>Active</b></font>', cell_center,
            )
        else:
            status_p = Paragraph(
                '<font color="#b45309" backColor="#fef3c7" size="7">&nbsp;Pending&nbsp;</font>',
                cell_center,
            )

        # Projects/Artifacts - bold if > 0
        proj_style = cell_center_bold if projects > 0 else cell_center
        art_style = cell_center_bold if artifacts > 0 else cell_center

        data_rows.append([
            name_p,
            role_p,
            Paragraph(tier_label, cell_center),
            status_p,
            Paragraph(str(projects), proj_style),
            Paragraph(str(artifacts), art_style),
        ])

    # Column widths
    col_widths = [
        USABLE_WIDTH * 0.30,
        USABLE_WIDTH * 0.15,
        USABLE_WIDTH * 0.12,
        USABLE_WIDTH * 0.13,
        USABLE_WIDTH * 0.15,
        USABLE_WIDTH * 0.15,
    ]

    table = Table(data_rows, colWidths=col_widths, repeatRows=1)

    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(LM_LIGHT_GRAY)),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#d1d5db")),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#e5e7eb")),
    ]

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
    wau_chart = data.get("wau_chart", {"labels": [], "data": []})
    top_projects = data.get("top_users_projects", [])
    top_artifacts = data.get("top_users_artifacts", [])
    plan_tier = data.get("plan_tier", "Standard")
    total_seats = data.get("total_seats", len(members))
    from_cache = data.get("from_cache", False)
    overview = data.get("activity_overview", {})
    cc = data.get("claude_code", {})
    cc_summary = cc.get("summary", {})
    cc_users = cc.get("users", [])

    active_count = data.get("active_members", sum(1 for m in members if m.get("status") == "Active"))
    pending_count = data.get("pending_invites", sum(1 for m in members if m.get("status") == "Pending"))
    assigned = active_count + pending_count
    available = total_seats - assigned

    wau_val = overview.get("wau", {}).get("value", "—")
    wau_change = overview.get("wau", {}).get("change_percent", None)
    utilization = overview.get("utilization", {}).get("value", "—")
    if isinstance(utilization, (int, float)):
        utilization_str = f"{utilization:.1f}%"
    else:
        utilization_str = str(utilization)

    # Premium members for seat tier subtitle
    premium_members = [m for m in members
                       if "tier_1" in m.get("seat_tier", "").lower()
                       or "premium" in m.get("seat_tier", "").lower()]
    if premium_members:
        names = [m.get("name", "").split()[0] + " " + m.get("name", "").split()[-1][0] + "."
                 if len(m.get("name", "").split()) > 1 else m.get("name", "")
                 for m in premium_members[:3]]
        tier_subtitle = f"+{len(premium_members)} Premium ({', '.join(names)})"
    else:
        tier_subtitle = "All standard seats"

    now = datetime.now()
    today_str = now.strftime("%B %d, %Y")
    month_str = now.strftime("%B %Y")

    styles = getSampleStyleSheet()

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
        f"Lou Malnati\u2019s Pizzeria \u00b7 {plan_tier} Plan",
        today_str,
    ))
    story.append(Spacer(1, 14))

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
        ("Total Seats", str(total_seats), f"{available} available \u00b7 {assigned} assigned"),
        ("Active Members", str(active_count), "Onboarded & using Claude"),
        ("Pending Invites", str(pending_count), "Haven't accepted invite yet"),
        ("Seat Tier", plan_tier, tier_subtitle),
    ]))
    story.append(Spacer(1, 18))

    # =======================================================================
    # ACTIVITY ANALYTICS
    # =======================================================================
    story.append(SectionHeader(
        "Activity Analytics \u00b7 Claude.ai/Analytics \u00b7 MTD \u00b7 Updated Daily"
    ))
    story.append(Spacer(1, 10))

    # --- Daily Chat Activity ---
    chat_data = daily_chats.get("data", [])
    chat_labels = daily_chats.get("labels", [])
    fig_chats = _make_line_chart(chat_labels, chat_data, "Daily Chat Activity")
    story.append(_fig_to_image(fig_chats, USABLE_WIDTH, 2.2 * inch))

    # Chat summary stats
    if chat_data:
        total_chats = sum(chat_data)
        peak_chats = max(chat_data)
        num_days = len(chat_data)
        avg_chats = total_chats / num_days if num_days else 0
        engagement = "\u2191 Active" if total_chats > 0 else "\u2014 No activity"
        story.append(StatsSummaryRow([
            (str(total_chats), f"Total chats ({num_days} days)", None),
            (str(peak_chats), "Peak daily chats", None),
            (f"{avg_chats:.1f}", "Avg chats / day", None),
            (engagement, "Team is engaged" if total_chats > 0 else "", LM_GREEN if total_chats > 0 else LM_GRAY),
        ]))
    story.append(Spacer(1, 18))

    # =======================================================================
    # WEEKLY ACTIVE USERS
    # =======================================================================
    story.append(SectionHeader(
        "Weekly Active Users \u00b7 Claude.ai/Analytics \u00b7 Rolling 7-Day Window"
    ))
    story.append(Spacer(1, 10))

    wau_data = wau_chart.get("data", [])
    wau_labels = wau_chart.get("labels", [])
    if wau_data:
        fig_wau = _make_line_chart(wau_labels, wau_data, f"Weekly Active Users (WAU)")
        story.append(_fig_to_image(fig_wau, USABLE_WIDTH, 2.2 * inch))

        # WAU summary
        current_wau = wau_data[-1] if wau_data else 0
        first_wau = wau_data[0] if wau_data else 0
        if first_wau and first_wau > 0:
            growth_pct = ((current_wau - first_wau) / first_wau) * 100
            growth_str = f"+{growth_pct:.0f}%" if growth_pct >= 0 else f"{growth_pct:.0f}%"
        else:
            growth_str = "—"
        wow_str = f"+{wau_change:.1f}%" if wau_change and wau_change >= 0 else (f"{wau_change:.1f}%" if wau_change else "—")
        first_label = wau_labels[0] if wau_labels else "start"

        story.append(StatsSummaryRow([
            (str(int(current_wau)), "Current WAU", None),
            (wow_str, "WoW change", LM_GREEN if wau_change and wau_change >= 0 else LM_RED),
            (utilization_str, "Utilization rate", None),
            (growth_str, f"Growth since {first_label}", LM_GREEN if growth_str.startswith("+") else LM_RED),
        ]))
    story.append(Spacer(1, 18))

    # --- Top Users by Projects ---
    if top_projects:
        fig_proj = _make_hbar_chart(
            [u["name"] for u in top_projects],
            [u["count"] for u in top_projects],
            "Top Users by Projects (MTD)"
        )
        proj_h = max(1.5, len(top_projects) * 0.35 + 0.8) * inch
        story.append(_fig_to_image(fig_proj, USABLE_WIDTH, proj_h))
        story.append(Spacer(1, 14))

    # --- Top Users by Artifacts ---
    if top_artifacts:
        fig_art = _make_hbar_chart(
            [u["name"] for u in top_artifacts],
            [u["count"] for u in top_artifacts],
            "Top Users by Artifacts (MTD)"
        )
        art_h = max(1.5, len(top_artifacts) * 0.35 + 0.8) * inch
        story.append(_fig_to_image(fig_art, USABLE_WIDTH, art_h))
        story.append(Spacer(1, 18))

    # =======================================================================
    # CLAUDE CODE
    # =======================================================================
    if cc_summary:
        cc_active = cc_summary.get("active_users", 0)
        cc_lines = cc_summary.get("total_lines_accepted", 0)
        cc_accept = cc_summary.get("tool_accept_rate", 0)
        try:
            cc_accept_str = f"{float(cc_accept) * 100:.1f}%" if float(cc_accept) <= 1 else f"{float(cc_accept):.1f}%"
        except (ValueError, TypeError):
            cc_accept_str = "—"

        user_label = "Active User" if cc_active == 1 else "Active Users"
        story.append(SectionHeader(
            f"Claude Code \u00b7 {month_str} \u00b7 {cc_active} {user_label}"
        ))
        story.append(Spacer(1, 10))

        # Top CC user
        top_cc = cc_users[0] if cc_users else {}
        top_cc_name = top_cc.get("name", "—")
        top_cc_email = top_cc.get("email", "")
        top_cc_lines = top_cc.get("total_lines_accepted", 0)
        top_cc_subtitle = f"{top_cc_email} \u00b7 {top_cc_lines:,} lines" if top_cc_email else ""

        story.append(StatCardRow([
            ("Lines Accepted", f"{cc_lines:,}", f"{month_str} MTD"),
            ("Acceptance Rate", cc_accept_str, "Suggestion accept rate"),
            ("Top User", top_cc_name, top_cc_subtitle),
        ]))
        story.append(Spacer(1, 18))

    # =======================================================================
    # ALL MEMBERS
    # =======================================================================
    story.append(SectionHeader("All Members"))
    story.append(Spacer(1, 10))

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
        f"Data sourced from Claude.ai Admin Console \u00b7 "
        f"Lou Malnati\u2019s Pizzeria organization \u00b7 v{config.VERSION}",
        footer_style,
    ))

    # Build PDF
    doc.build(story)
    file_size = os.path.getsize(filepath)
    logger.info(f"PDF report saved to {filepath} ({file_size:,} bytes)")

    if file_size < 35000:
        logger.warning(f"PDF file size ({file_size:,} bytes) is smaller than expected (< 35KB)")

    return filepath
