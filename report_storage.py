"""
CRUD operations for custom report configurations stored in reports.json.
"""

import copy
import json
import os
import uuid
from datetime import datetime, timezone

import config

logger = config.get_logger()

REPORTS_FILE = os.path.join(config.OUTPUT_DIR, "reports.json")

# ---------------------------------------------------------------------------
# Available report components
# ---------------------------------------------------------------------------
REPORT_COMPONENTS = [
    # --- Overview ---
    {"key": "stats_row", "label": "Stats Row (Seats, Active, Pending, Tier)", "category": "Overview", "supports_date_range": False},
    {"key": "status_donut", "label": "Member Status Breakdown (Donut)", "category": "Overview", "supports_date_range": False},
    {"key": "role_donut", "label": "Role Distribution (Donut)", "category": "Overview", "supports_date_range": False},
    # --- Activity ---
    {"key": "daily_chats", "label": "Daily Chat Activity (Line Chart)", "category": "Activity", "supports_date_range": True},
    {"key": "wau_trend", "label": "Weekly Active Users Trend (Line Chart)", "category": "Activity", "supports_date_range": True},
    {"key": "wau_stats_tile", "label": "WAU Stats Tile (WAU, WoW%, Utilization, Growth)", "category": "Activity", "supports_date_range": True},
    {"key": "top_users_projects", "label": "Top Users by Projects (Bar Chart)", "category": "Activity", "supports_date_range": True},
    {"key": "top_users_artifacts", "label": "Top Users by Artifacts (Bar Chart)", "category": "Activity", "supports_date_range": True},
    # --- Claude Code ---
    {"key": "claude_code_stats", "label": "Claude Code Stats (Lines, Rate, Top User)", "category": "Claude Code", "supports_date_range": False},
    # --- People ---
    {"key": "member_directory", "label": "Member Directory Table", "category": "People", "supports_date_range": False},
    # --- Narrative ---
    {"key": "executive_summary", "label": "Executive Summary (Auto-Generated)", "category": "Narrative", "supports_date_range": False},
    {"key": "email_highlights", "label": "Email Highlights (Key Stats Text)", "category": "Narrative", "supports_date_range": False},
]

# ---------------------------------------------------------------------------
# Built-in templates
# ---------------------------------------------------------------------------
_BUILTIN_TEMPLATES = [
    {
        "id": "tpl-executive",
        "title": "Executive Summary",
        "description": "High-level overview with auto-generated narrative and key activity trends",
        "components": [
            {"key": "executive_summary", "enabled": True, "order": 0, "date_range": None},
            {"key": "stats_row", "enabled": True, "order": 1, "date_range": None},
            {"key": "wau_stats_tile", "enabled": True, "order": 2, "date_range": None},
            {"key": "wau_trend", "enabled": True, "order": 3, "date_range": None},
            {"key": "daily_chats", "enabled": True, "order": 4, "date_range": None},
        ],
    },
    {
        "id": "tpl-full",
        "title": "Full Dashboard",
        "description": "Complete replica of the standard dashboard with all components",
        "components": [
            {"key": "stats_row", "enabled": True, "order": 0, "date_range": None},
            {"key": "status_donut", "enabled": True, "order": 1, "date_range": None},
            {"key": "role_donut", "enabled": True, "order": 2, "date_range": None},
            {"key": "daily_chats", "enabled": True, "order": 3, "date_range": None},
            {"key": "wau_trend", "enabled": True, "order": 4, "date_range": None},
            {"key": "wau_stats_tile", "enabled": True, "order": 5, "date_range": None},
            {"key": "top_users_projects", "enabled": True, "order": 6, "date_range": None},
            {"key": "top_users_artifacts", "enabled": True, "order": 7, "date_range": None},
            {"key": "claude_code_stats", "enabled": True, "order": 8, "date_range": None},
            {"key": "member_directory", "enabled": True, "order": 9, "date_range": None},
        ],
    },
    {
        "id": "tpl-activity",
        "title": "Activity Deep Dive",
        "description": "Focus on usage activity \u2014 chat trends, WAU, top users, and code stats",
        "components": [
            {"key": "daily_chats", "enabled": True, "order": 0, "date_range": None},
            {"key": "wau_trend", "enabled": True, "order": 1, "date_range": None},
            {"key": "wau_stats_tile", "enabled": True, "order": 2, "date_range": None},
            {"key": "top_users_projects", "enabled": True, "order": 3, "date_range": None},
            {"key": "top_users_artifacts", "enabled": True, "order": 4, "date_range": None},
            {"key": "claude_code_stats", "enabled": True, "order": 5, "date_range": None},
        ],
    },
    {
        "id": "tpl-team",
        "title": "Team Overview",
        "description": "Seat allocation, roles, statuses, and the full member directory",
        "components": [
            {"key": "stats_row", "enabled": True, "order": 0, "date_range": None},
            {"key": "status_donut", "enabled": True, "order": 1, "date_range": None},
            {"key": "role_donut", "enabled": True, "order": 2, "date_range": None},
            {"key": "member_directory", "enabled": True, "order": 3, "date_range": None},
        ],
    },
]


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Core I/O
# ---------------------------------------------------------------------------
def load_reports() -> dict:
    """Load reports.json, creating with defaults if missing."""
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    if not os.path.exists(REPORTS_FILE):
        data = {"reports": [], "templates": copy.deepcopy(_BUILTIN_TEMPLATES)}
        save_reports(data)
        return data
    with open(REPORTS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Seed templates if empty
    if not data.get("templates"):
        data["templates"] = copy.deepcopy(_BUILTIN_TEMPLATES)
        save_reports(data)
    return data


def save_reports(data: dict):
    """Atomically write reports.json (write to tmp then rename)."""
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    tmp_path = REPORTS_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, REPORTS_FILE)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def get_report(report_id: str) -> dict | None:
    data = load_reports()
    for r in data.get("reports", []):
        if r["id"] == report_id:
            return r
    return None


def create_report(report_data: dict) -> dict:
    data = load_reports()
    report = {
        "id": str(uuid.uuid4()),
        "title": report_data.get("title", "Untitled Report"),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "is_template": False,
        "components": report_data.get("components", []),
        "global_date_range": report_data.get("global_date_range"),
        "schedule": report_data.get("schedule", {
            "enabled": False,
            "cron": {"day_of_week": "fri", "hour": 8, "minute": 0},
            "timezone": "America/Chicago",
            "recipients": [],
        }),
    }
    data["reports"].append(report)
    save_reports(data)
    logger.info(f"Created report '{report['title']}' ({report['id']})")
    return report


def update_report(report_id: str, report_data: dict) -> dict | None:
    data = load_reports()
    for i, r in enumerate(data["reports"]):
        if r["id"] == report_id:
            r["title"] = report_data.get("title", r["title"])
            r["components"] = report_data.get("components", r["components"])
            r["global_date_range"] = report_data.get("global_date_range", r.get("global_date_range"))
            r["schedule"] = report_data.get("schedule", r.get("schedule", {}))
            r["updated_at"] = _now_iso()
            data["reports"][i] = r
            save_reports(data)
            logger.info(f"Updated report '{r['title']}' ({report_id})")
            return r
    return None


def delete_report(report_id: str) -> bool:
    data = load_reports()
    original_len = len(data["reports"])
    data["reports"] = [r for r in data["reports"] if r["id"] != report_id]
    if len(data["reports"]) < original_len:
        save_reports(data)
        logger.info(f"Deleted report {report_id}")
        return True
    return False


def clone_report(source_id: str, new_title: str) -> dict | None:
    data = load_reports()
    source = None
    for r in data["reports"]:
        if r["id"] == source_id:
            source = r
            break
    if not source:
        return None
    new_report = copy.deepcopy(source)
    new_report["id"] = str(uuid.uuid4())
    new_report["title"] = new_title
    new_report["created_at"] = _now_iso()
    new_report["updated_at"] = _now_iso()
    new_report["is_template"] = False
    # Disable schedule on clones
    if "schedule" in new_report:
        new_report["schedule"]["enabled"] = False
    data["reports"].append(new_report)
    save_reports(data)
    logger.info(f"Cloned report '{new_title}' from {source_id}")
    return new_report


def clone_template(template_id: str, new_title: str) -> dict:
    data = load_reports()
    source = None
    for t in data.get("templates", []):
        if t["id"] == template_id:
            source = t
            break
    if not source:
        return None
    new_report = {
        "id": str(uuid.uuid4()),
        "title": new_title,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "is_template": False,
        "components": copy.deepcopy(source["components"]),
        "global_date_range": None,
        "schedule": {
            "enabled": False,
            "cron": {"day_of_week": "fri", "hour": 8, "minute": 0},
            "timezone": "America/Chicago",
            "recipients": [],
        },
    }
    data["reports"].append(new_report)
    save_reports(data)
    logger.info(f"Created report '{new_title}' from template {template_id}")
    return new_report


def get_templates() -> list:
    data = load_reports()
    return data.get("templates", [])
