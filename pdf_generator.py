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
import matplotlib.colors as mcolors
import numpy as np
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
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
CC_PURPLE = "#7c3aed"
CC_PURPLE_RGB = (124 / 255, 58 / 255, 237 / 255)
LM_GRAY = "#6b7280"
LM_LIGHT_GRAY = "#f3f4f6"

PAGE_WIDTH, PAGE_HEIGHT = letter
MARGIN = 0.5 * inch
USABLE_WIDTH = PAGE_WIDTH - 2 * MARGIN


# ---------------------------------------------------------------------------
# Page-level drawing helpers
# ---------------------------------------------------------------------------
def _make_numbered_canvas_factory(date_str):
    """Return a NumberedCanvas class that captures the report date string."""

    class NumberedCanvas(canvas.Canvas):
        """Custom canvas: draws light-grey background + 'Page X of Y' footer."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_page_states = []
            # Draw background on the very first page
            self._draw_background()

        def _draw_background(self):
            """Light-grey content area background with white margins."""
            self.saveState()
            self.setFillColor(colors.HexColor("#f5f5f5"))
            self.rect(MARGIN, MARGIN, USABLE_WIDTH, PAGE_HEIGHT - 2 * MARGIN,
                      fill=1, stroke=0)
            self.restoreState()

        def showPage(self):
            # Save page state but do NOT commit the page yet — _startPage
            # just resets the canvas for the next page without writing output.
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()
            # Draw background for the next page
            self._draw_background()

        def save(self):
            # Second pass: restore each page, add footer, then commit
            num_pages = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self._draw_footer(num_pages)
                canvas.Canvas.showPage(self)
            canvas.Canvas.save(self)

        def _draw_footer(self, total_pages):
            page_num = self._pageNumber
            footer_text = (
                f"Page {page_num} of {total_pages}  \u00b7  "
                f"Data sourced from Claude.ai Admin Console  \u00b7  "
                f"As of {date_str}  \u00b7  "
                f"Lou Malnati\u2019s Pizzeria  \u00b7  v{config.VERSION}"
            )
            self.saveState()
            self.setFont("Helvetica", 7)
            self.setFillColor(colors.HexColor("#9ca3af"))
            self.drawCentredString(PAGE_WIDTH / 2, MARGIN / 2, footer_text)
            self.restoreState()

    return NumberedCanvas


# ---------------------------------------------------------------------------
# Custom Flowable: Header Banner
# ---------------------------------------------------------------------------
class HeaderBanner(Flowable):
    """Red header bar with LM logo, title, and date badge."""

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

        # Red rectangle background (no rounded corners)
        c.setFillColor(colors.HexColor(LM_RED))
        c.rect(0, 0, w, h, fill=1, stroke=0)

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
        """cards: list of (label, value, subtitle) or (label, value, subtitle, color_hex)"""
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
            label = card[0]
            value = card[1]
            subtitle = card[2] if len(card) > 2 else ""
            val_color = card[3] if len(card) > 3 else LM_RED
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

            # Large value (colored)
            c.setFillColor(colors.HexColor(val_color))
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
    """Gray uppercase section divider with colored left accent bar."""

    def __init__(self, text, width=USABLE_WIDTH, color=LM_RED):
        super().__init__()
        self.text = text
        self._width = width
        self.height = 24
        self.color = color

    def wrap(self, availWidth, availHeight):
        return self._width, self.height

    def draw(self):
        c = self.canv
        # Accent bar
        c.setFillColor(colors.HexColor(self.color))
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
            val_color = color or LM_RED
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



def _make_line_chart(labels, data, title=None, subtitle=None, show_labels=True,
                     color=LM_RED, color_rgb=LM_RED_RGB):
    """Create a matplotlib line chart with gradient fill and data point labels."""
    fig, ax = plt.subplots(figsize=(7, 2.5))
    fig.patch.set_facecolor("white")

    if not data:
        ax.text(0.5, 0.5, "No data available", ha="center", va="center", transform=ax.transAxes)
        if title:
            ax.set_title(title, fontsize=10, fontweight="bold")
        return fig

    n_points = len(data)
    x = range(n_points)
    y_max = max(data) * 1.3 if data else 1
    ax.set_ylim(bottom=0, top=y_max)
    ax.set_xlim(-0.3, n_points - 0.7)

    # Gradient fill under the line (top opacity 0.18, fades to 0.01)
    ax.plot(x, data, color=color, linewidth=2.5, marker="o", markersize=7,
            markerfacecolor="white", markeredgecolor=color, markeredgewidth=2.5,
            zorder=3)
    # Create gradient via imshow behind the line
    z = np.empty((100, 1, 4), dtype=float)
    r, g, b = color_rgb
    for row in range(100):
        alpha = 0.18 * (1 - row / 100)  # fade from 0.18 at top to ~0 at bottom
        z[row, 0] = [r, g, b, alpha]
    # Fill area: draw gradient from line down to 0
    ax.fill_between(x, data, 0, alpha=0.0)  # invisible fill to set data limits
    y_min_plot, y_max_plot = ax.get_ylim()
    x_min_plot, x_max_plot = ax.get_xlim()
    # Clip gradient to the area under the line
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path
    verts = [(xi, yi) for xi, yi in zip(x, data)]
    verts += [(n_points - 1, 0), (0, 0)]
    codes = [Path.MOVETO] + [Path.LINETO] * (len(verts) - 1)
    clip_path = PathPatch(Path(verts, codes), transform=ax.transData, facecolor='none',
                          edgecolor='none')
    ax.add_patch(clip_path)
    im = ax.imshow(z, aspect='auto', extent=[x_min_plot, x_max_plot, 0, y_max_plot],
                   origin='upper', zorder=1)
    im.set_clip_path(clip_path)

    # Data point labels
    if show_labels:
        for i, v in enumerate(data):
            ax.annotate(str(int(v)), (i, v), textcoords="offset points",
                        xytext=(0, 10), ha="center", fontsize=8, fontweight="bold",
                        color=color, zorder=4)

    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=7, rotation=0)
    if title:
        ax.set_title(title, fontsize=11, fontweight="bold", loc="left", pad=10)
    ax.grid(axis="y", alpha=0.3)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.tight_layout()
    return fig


def _make_hbar_chart(names, counts, title, color=LM_RED):
    """Create a matplotlib horizontal bar chart."""
    fig, ax = plt.subplots(figsize=(7, max(2, len(names) * 0.35 + 0.8)))
    fig.patch.set_facecolor("white")

    if not names:
        ax.text(0.5, 0.5, "No data available", ha="center", va="center", transform=ax.transAxes)
        ax.set_title(title, fontsize=10, fontweight="bold")
        return fig

    y_pos = range(len(names))
    ax.barh(y_pos, counts, color=color, height=0.6, edgecolor="none")
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

    cell_style = ParagraphStyle("cell", parent=styles["Normal"], fontSize=7, leading=9)
    cell_bold = ParagraphStyle("cellbold", parent=styles["Normal"], fontSize=7, leading=9,
                               fontName="Helvetica-Bold")
    cell_center = ParagraphStyle("cellcenter", parent=cell_style, alignment=TA_CENTER)
    cell_center_bold = ParagraphStyle("cellcenterbold", parent=cell_bold, alignment=TA_CENTER)
    header_style = ParagraphStyle(
        "header", parent=styles["Normal"], fontSize=6.5, leading=8,
        textColor=colors.HexColor("#374151"), fontName="Helvetica-Bold"
    )
    header_center = ParagraphStyle("headercenter", parent=header_style, alignment=TA_CENTER)

    project_lookup = {u["name"]: u["count"] for u in top_projects}
    artifact_lookup = {u["name"]: u["count"] for u in top_artifacts}

    # Header row
    headers = [
        Paragraph("MEMBER", header_style),
        Paragraph("ROLE", header_center),
        Paragraph("TIER", header_center),
        Paragraph("STATUS", header_center),
        Paragraph("PROJ", header_center),
        Paragraph("ARTIFACTS", header_center),
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

        # Member: name (bold) + email inline in gray — single line
        premium_badge = ' <font color="#7c3aed" backColor="#ede9fe" size="5.5">&nbsp;P&nbsp;</font>' if is_premium else ""
        name_p = Paragraph(
            f'<b>{name}</b>{premium_badge} <font color="#9ca3af" size="6">{email}</font>',
            cell_style,
        )

        # Role badge
        if "primary" in role.lower() and "owner" in role.lower():
            role_p = Paragraph(
                f'<font color="white" backColor="{LM_RED}" size="6">&nbsp;Primary Owner&nbsp;</font>',
                cell_center,
            )
        elif "owner" in role.lower():
            role_p = Paragraph(
                f'<font color="white" backColor="{LM_GREEN}" size="6">&nbsp;Owner&nbsp;</font>',
                cell_center,
            )
        else:
            role_p = Paragraph(
                '<font color="#374151" backColor="#f3f4f6" size="6">&nbsp;User&nbsp;</font>',
                cell_center,
            )

        # Status badge
        if status == "Active":
            status_p = Paragraph(
                '<font color="#15803d" size="6.5"><b>Active</b></font>', cell_center,
            )
        else:
            status_p = Paragraph(
                '<font color="#b45309" backColor="#fef3c7" size="6">&nbsp;Pending&nbsp;</font>',
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

    # Column widths — wide MEMBER col for inline name + email
    col_widths = [
        USABLE_WIDTH * 0.38,
        USABLE_WIDTH * 0.13,
        USABLE_WIDTH * 0.10,
        USABLE_WIDTH * 0.11,
        USABLE_WIDTH * 0.10,
        USABLE_WIDTH * 0.18,
    ]

    table = Table(data_rows, colWidths=col_widths, repeatRows=1)

    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d5d7db")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#d1d5db")),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#e5e7eb")),
    ]

    for i in range(1, len(data_rows)):
        if i % 2 == 0:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#dfe0e3")))
        else:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#e8e9eb")))

    table.setStyle(TableStyle(style_cmds))
    return table


# ---------------------------------------------------------------------------
# Claude Code user table
# ---------------------------------------------------------------------------
def _build_cc_user_table(cc_users, max_users=25):
    """Build a ReportLab Table for the Claude Code user breakdown."""
    styles = getSampleStyleSheet()

    cell_style = ParagraphStyle("cc_cell", parent=styles["Normal"], fontSize=7, leading=9)
    cell_bold = ParagraphStyle("cc_cellbold", parent=styles["Normal"], fontSize=7, leading=9,
                               fontName="Helvetica-Bold")
    cell_center = ParagraphStyle("cc_cellcenter", parent=cell_style, alignment=TA_CENTER)
    cell_center_bold = ParagraphStyle("cc_cellcenterbold", parent=cell_bold, alignment=TA_CENTER)
    header_style = ParagraphStyle(
        "cc_header", parent=styles["Normal"], fontSize=6.5, leading=8,
        textColor=colors.HexColor("#374151"), fontName="Helvetica-Bold"
    )
    header_center = ParagraphStyle("cc_headercenter", parent=header_style, alignment=TA_CENTER)

    headers = [
        Paragraph("USER", header_style),
        Paragraph("SESSIONS", header_center),
        Paragraph("LINES", header_center),
        Paragraph("COMMITS", header_center),
        Paragraph("PRs", header_center),
        Paragraph("LAST ACTIVE", header_center),
    ]

    data_rows = [headers]
    for u in cc_users[:max_users]:
        name = u.get("name", "")
        email = u.get("email", "")
        sessions = u.get("total_sessions", 0)
        lines_val = u.get("total_lines_accepted", 0)
        commits = u.get("commits_created", 0)
        prs = u.get("pull_requests_created", 0)
        last_active = u.get("last_active", "—") or "—"
        if last_active != "—" and len(last_active) >= 10:
            last_active = last_active[:10]

        name_p = Paragraph(
            f'<b>{name}</b> <font color="#9ca3af" size="6">{email}</font>',
            cell_style,
        )

        sessions_style = cell_center_bold if sessions > 0 else cell_center
        lines_style = cell_center_bold if lines_val > 0 else cell_center
        commits_style = cell_center_bold if commits > 0 else cell_center
        prs_style = cell_center_bold if prs > 0 else cell_center

        data_rows.append([
            name_p,
            Paragraph(str(sessions), sessions_style),
            Paragraph(f"{lines_val:,}", lines_style),
            Paragraph(str(commits), commits_style),
            Paragraph(str(prs), prs_style),
            Paragraph(last_active, cell_center),
        ])

    col_widths = [
        USABLE_WIDTH * 0.32,  # USER
        USABLE_WIDTH * 0.12,  # SESSIONS
        USABLE_WIDTH * 0.14,  # LINES
        USABLE_WIDTH * 0.12,  # COMMITS
        USABLE_WIDTH * 0.10,  # PRs
        USABLE_WIDTH * 0.20,  # LAST ACTIVE
    ]

    table = Table(data_rows, colWidths=col_widths, repeatRows=1)

    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d5d7db")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#d1d5db")),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#e5e7eb")),
    ]

    for i in range(1, len(data_rows)):
        if i % 2 == 0:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#dfe0e3")))
        else:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#e8e9eb")))

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
    cc_activity_chart = cc.get("activity_chart", {"labels": [], "data": []})
    cc_lines_chart = cc.get("lines_chart", {"labels": [], "data": []})

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
    today_str = now.strftime("%B %-d, %Y %-I:%M %p")
    month_str = now.strftime("%B %Y")

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
        ("Total Seats", str(total_seats), f"{available} available \u00b7 {assigned} assigned", LM_RED),
        ("Active Members", str(active_count), "Onboarded & using Claude", LM_GREEN),
        ("Pending Invites", str(pending_count), "Haven't accepted invite yet", LM_AMBER),
        ("Seat Tier", plan_tier, tier_subtitle, LM_RED),
    ]))
    story.append(Spacer(1, 18))

    # =======================================================================
    # ACTIVITY ANALYTICS — Featured Daily Chat Activity
    # =======================================================================
    chat_data = daily_chats.get("data", [])
    chat_labels = daily_chats.get("labels", [])
    # Always show exactly 7 days — pad from the front if fewer
    if chat_data and len(chat_data) < 7:
        deficit = 7 - len(chat_data)
        chat_data = [0] * deficit + list(chat_data)
        chat_labels = [""] * deficit + list(chat_labels)
    featured_inner_w = USABLE_WIDTH - 22  # room for red border + padding
    fig_chats = _make_line_chart(chat_labels, chat_data, "Daily Chat Activity")
    chart_img = _fig_to_image(fig_chats, featured_inner_w, 2.2 * inch)

    # Section label inside the featured card
    section_label_style = ParagraphStyle(
        "featured_label", parent=styles["Normal"], fontSize=7,
        fontName="Helvetica-Bold", textColor=colors.HexColor(LM_GRAY),
        spaceAfter=6,
    )
    section_label = Paragraph(
        "ACTIVITY ANALYTICS \u00b7 CLAUDE.AI/ANALYTICS \u00b7 MTD \u00b7 UPDATED DAILY",
        section_label_style,
    )

    featured_rows = [[section_label], [chart_img]]
    if chat_data:
        total_chats = sum(chat_data)
        peak_chats = max(chat_data)
        num_days = len(chat_data)
        avg_chats = total_chats / num_days if num_days else 0
        engagement = "\u2191 Active" if total_chats > 0 else "\u2014 No activity"
        summary_row = StatsSummaryRow([
            (str(total_chats), f"Total chats ({num_days} days)", None),
            (str(peak_chats), "Peak daily chats", None),
            (f"{avg_chats:.1f}", "Avg chats / day", None),
            (engagement, "Team is engaged" if total_chats > 0 else "", LM_GREEN if total_chats > 0 else LM_GRAY),
        ], width=featured_inner_w)
        featured_rows.append([summary_row])

    featured_table = Table(featured_rows, colWidths=[USABLE_WIDTH])
    featured_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
        ("LINEBEFORE", (0, 0), (0, -1), 5, colors.HexColor(LM_RED)),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (0, 0), 10),
        ("TOPPADDING", (0, 1), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
    ]))
    story.append(featured_table)
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
    # Limit WAU to last 7 data points (matching daily chat timeframe)
    if len(wau_data) > 7:
        wau_data = wau_data[-7:]
        wau_labels = wau_labels[-7:]
    if wau_data:
        fig_wau = _make_line_chart(wau_labels, wau_data, "Weekly Active Users (WAU)")
        wau_chart_img = _fig_to_image(fig_wau, USABLE_WIDTH - 8, 2.2 * inch)

        # WAU summary
        current_wau = wau_data[-1] if wau_data else 0
        first_wau = wau_data[0] if wau_data else 0
        if first_wau and first_wau > 0:
            growth_pct = ((current_wau - first_wau) / first_wau) * 100
            growth_str = f"+{growth_pct:.0f}%" if growth_pct >= 0 else f"{growth_pct:.0f}%"
        else:
            growth_str = "\u2014"
        wow_str = f"+{wau_change:.1f}%" if wau_change and wau_change >= 0 else (f"{wau_change:.1f}%" if wau_change else "\u2014")
        first_label = wau_labels[0] if wau_labels else "start"

        wau_summary = StatsSummaryRow([
            (str(int(current_wau)), "Current WAU", None),
            (wow_str, "WoW change", LM_GREEN if wau_change and wau_change >= 0 else LM_RED),
            (utilization_str, "Utilization rate", None),
            (growth_str, f"Growth since {first_label}", LM_GREEN if growth_str.startswith("+") else LM_RED),
        ], width=USABLE_WIDTH - 8)

        # Wrap chart + stats in a bordered table
        wau_table = Table([[wau_chart_img], [wau_summary]], colWidths=[USABLE_WIDTH])
        wau_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#1a1a1a")),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(wau_table)
    story.append(Spacer(1, 18))

    # --- Top Users by Projects ---
    if top_projects:
        fig_proj = _make_hbar_chart(
            [u["name"] for u in top_projects],
            [u["count"] for u in top_projects],
            "Top Users by Projects (MTD)"
        )
        proj_h = max(1.5, len(top_projects) * 0.35 + 0.8) * inch
        proj_img = _fig_to_image(fig_proj, USABLE_WIDTH - 8, proj_h)
        proj_table = Table([[proj_img]], colWidths=[USABLE_WIDTH])
        proj_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#1a1a1a")),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(proj_table)
        story.append(Spacer(1, 14))

    # --- Top Users by Artifacts ---
    if top_artifacts:
        fig_art = _make_hbar_chart(
            [u["name"] for u in top_artifacts],
            [u["count"] for u in top_artifacts],
            "Top Users by Artifacts (MTD)"
        )
        art_h = max(1.5, len(top_artifacts) * 0.35 + 0.8) * inch
        art_img = _fig_to_image(fig_art, USABLE_WIDTH - 8, art_h)
        art_table = Table([[art_img]], colWidths=[USABLE_WIDTH])
        art_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#1a1a1a")),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(art_table)
        story.append(Spacer(1, 18))

    # =======================================================================
    # CLAUDE CODE
    # =======================================================================
    if cc_summary:
        cc_active = cc_summary.get("active_users", 0)
        cc_sessions = cc_summary.get("total_sessions", 0)
        cc_lines = cc_summary.get("total_lines_accepted", 0)
        cc_commits = cc_summary.get("commits_created", 0)
        cc_prs = cc_summary.get("pull_requests_created", 0)

        user_label = "Active User" if cc_active == 1 else "Active Users"
        story.append(SectionHeader(
            f"Claude Code \u00b7 {month_str} \u00b7 {cc_active} {user_label}",
            color=CC_PURPLE,
        ))
        story.append(Spacer(1, 10))

        # 5 stat cards
        story.append(StatCardRow([
            ("Active Users", str(cc_active), f"{month_str} MTD", CC_PURPLE),
            ("Sessions", str(cc_sessions), f"{month_str} MTD", CC_PURPLE),
            ("Lines Accepted", f"{cc_lines:,}", f"{month_str} MTD", LM_GREEN),
            ("Commits", str(cc_commits), f"{month_str} MTD", "#2563eb"),
            ("Pull Requests", str(cc_prs), f"{month_str} MTD", "#2563eb"),
        ]))
        story.append(Spacer(1, 14))

        # Daily Sessions line chart
        cc_activity_data = cc_activity_chart.get("data", [])
        cc_activity_labels = cc_activity_chart.get("labels", [])
        if cc_activity_data:
            fig_cc_sessions = _make_line_chart(
                cc_activity_labels, cc_activity_data,
                "Claude Code Daily Sessions",
                color=CC_PURPLE, color_rgb=CC_PURPLE_RGB,
            )
            cc_sessions_img = _fig_to_image(fig_cc_sessions, USABLE_WIDTH - 8, 2.2 * inch)
            chart_table = Table(
                [[cc_sessions_img]],
                colWidths=[USABLE_WIDTH],
            )
            chart_table.setStyle(TableStyle([
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(chart_table)
            story.append(Spacer(1, 14))

        # Daily Lines Accepted line chart
        cc_lines_data = cc_lines_chart.get("data", [])
        cc_lines_labels = cc_lines_chart.get("labels", [])
        if cc_lines_data:
            fig_cc_lines = _make_line_chart(
                cc_lines_labels, cc_lines_data,
                "Claude Code Daily Lines Accepted",
                color=CC_PURPLE, color_rgb=CC_PURPLE_RGB,
            )
            cc_lines_img = _fig_to_image(fig_cc_lines, USABLE_WIDTH - 8, 2.2 * inch)
            chart_table = Table(
                [[cc_lines_img]],
                colWidths=[USABLE_WIDTH],
            )
            chart_table.setStyle(TableStyle([
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(chart_table)
            story.append(Spacer(1, 14))

        # Top Users by Lines Accepted horizontal bar chart
        if cc_users:
            top_cc_names = [u.get("name", "?") for u in cc_users[:10]]
            top_cc_lines_vals = [u.get("total_lines_accepted", 0) for u in cc_users[:10]]
            fig_cc_users = _make_hbar_chart(
                top_cc_names, top_cc_lines_vals,
                "Top Claude Code Users by Lines Accepted (MTD)",
                color=CC_PURPLE,
            )
            cc_users_img = _fig_to_image(
                fig_cc_users, USABLE_WIDTH - 8,
                max(2, len(top_cc_names) * 0.35 + 0.8) * inch,
            )
            chart_table = Table(
                [[cc_users_img]],
                colWidths=[USABLE_WIDTH],
            )
            chart_table.setStyle(TableStyle([
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(chart_table)
            story.append(Spacer(1, 14))

        # Claude Code user breakdown table
        if cc_users:
            story.append(SectionHeader("Claude Code User Breakdown", color=CC_PURPLE))
            story.append(Spacer(1, 10))
            cc_table = _build_cc_user_table(cc_users)
            story.append(cc_table)
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

    # Build PDF with page background and numbered footer
    NumberedCanvas = _make_numbered_canvas_factory(today_str)
    doc.build(story, canvasmaker=NumberedCanvas)
    file_size = os.path.getsize(filepath)
    logger.info(f"PDF report saved to {filepath} ({file_size:,} bytes)")

    if file_size < 35000:
        logger.warning(f"PDF file size ({file_size:,} bytes) is smaller than expected (< 35KB)")

    return filepath
