"""
Email delivery via SMTP. Attaches the PDF report.
Reads settings fresh from settings.json at send time.
"""

import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr

import config

logger = config.get_logger()


def send_report(
    pdf_path: str,
    data: dict,
    recipients: list[str],
    is_test: bool = False,
) -> bool:
    """
    Send the dashboard report email with PDF attachment.
    Reads SMTP settings fresh from settings.json.
    Returns True on success.
    """
    settings = config.load_settings()
    smtp_host = settings.get("smtp_host", "smtp.office365.com")
    smtp_port = int(settings.get("smtp_port", 587))
    smtp_user = settings.get("smtp_user", "")
    smtp_pass_encrypted = settings.get("smtp_pass", "")
    smtp_from_name = settings.get("smtp_from_name", "Claude Dashboard")

    smtp_pass = config.decrypt_value(smtp_pass_encrypted)
    if not smtp_pass:
        raise ValueError("SMTP password is not configured. Set it in the admin UI.")
    if not smtp_user:
        raise ValueError("SMTP username is not configured. Set it in the admin UI.")
    if not recipients:
        raise ValueError("No recipients configured.")

    # Build the email
    today_str = datetime.now().strftime("%A, %B %d, %Y")
    subject = f"Claude Usage Dashboard \u2014 Lou Malnati's \u2014 {today_str}"
    if is_test:
        subject = f"[TEST] {subject}"

    # Compute key stats for body
    members = data.get("members", [])
    total_seats = data.get("total_seats", len(members))
    active_count = sum(1 for m in members if m.get("status") == "Active")
    pending_count = sum(1 for m in members if m.get("status") == "Pending")

    top_user = "N/A"
    top_projects = data.get("top_users_projects", [])
    top_artifacts = data.get("top_users_artifacts", [])
    if top_projects:
        tu = top_projects[0]
        art_count = 0
        if top_artifacts:
            for a in top_artifacts:
                if a["name"] == tu["name"]:
                    art_count = a["count"]
                    break
        top_user = f"{tu['name']} ({tu['count']} projects, {art_count} artifacts)"

    # Activity overview
    overview = data.get("activity_overview", {})
    dau = overview.get("dau", {}).get("value", "—")
    wau = overview.get("wau", {}).get("value", "—")
    utilization = overview.get("utilization", {}).get("value", "—")
    if isinstance(utilization, (int, float)):
        util_str = f"{utilization:.0f}%"
    else:
        util_str = str(utilization)

    # Claude Code
    cc = data.get("claude_code", {}).get("summary", {})
    cc_users_count = cc.get("active_users", 0)
    cc_sessions = cc.get("total_sessions", 0)
    cc_cost = cc.get("total_cost_usd", "0")
    try:
        cc_cost_str = f"${float(cc_cost):.2f}"
    except (ValueError, TypeError):
        cc_cost_str = "$0.00"

    output_dir = config.OUTPUT_DIR

    body = f"""Hi team,

Your Claude usage dashboard for {today_str} is attached.

Key stats:
\u2022 {total_seats} total seats ({active_count} active, {pending_count} pending)
\u2022 DAU: {dau} | WAU: {wau} | Utilization: {util_str}
\u2022 Top user this period: {top_user}

Claude Code (MTD):
\u2022 {cc_users_count} active users, {cc_sessions} sessions, {cc_cost_str} cost

See the attached PDF for the full report.
Interactive HTML version saved to: {output_dir}

\u2014Automated report \u00b7 Claude.ai Admin"""

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((smtp_from_name, smtp_user))
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    # Attach the PDF
    if os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            pdf_data = f.read()
        msg.add_attachment(
            pdf_data,
            maintype="application",
            subtype="pdf",
            filename="claude_usage_dashboard.pdf",
        )
    else:
        logger.warning(f"PDF file not found at {pdf_path}, sending email without attachment")

    # Send via SMTP
    logger.info(f"Sending email to {recipients} via {smtp_host}:{smtp_port}")

    if smtp_port == 465:
        # SSL/TLS
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
    else:
        # STARTTLS (port 587 or 25)
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.ehlo()
            if smtp_port != 25:
                server.starttls()
                server.ehlo()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)

    logger.info(f"Email sent successfully to {recipients}")
    return True


def test_smtp_connection() -> tuple[bool, str]:
    """
    Test SMTP connection and authentication without sending an email.
    Returns (success: bool, message: str).
    """
    settings = config.load_settings()
    smtp_host = settings.get("smtp_host", "smtp.office365.com")
    smtp_port = int(settings.get("smtp_port", 587))
    smtp_user = settings.get("smtp_user", "")
    smtp_pass_encrypted = settings.get("smtp_pass", "")

    smtp_pass = config.decrypt_value(smtp_pass_encrypted)
    if not smtp_pass:
        return False, "SMTP password is not configured"
    if not smtp_user:
        return False, "SMTP username is not configured"
    if not smtp_host:
        return False, "SMTP host is not configured"

    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15) as server:
                server.login(smtp_user, smtp_pass)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                server.ehlo()
                if smtp_port != 25:
                    server.starttls()
                    server.ehlo()
                server.login(smtp_user, smtp_pass)

        return True, "Connection successful"
    except smtplib.SMTPAuthenticationError:
        return False, "Authentication failed — check username and password"
    except smtplib.SMTPConnectError as e:
        return False, f"Could not connect to {smtp_host}:{smtp_port} — {e}"
    except TimeoutError:
        return False, f"Connection timed out to {smtp_host}:{smtp_port}"
    except Exception as e:
        return False, f"Connection failed: {e}"
