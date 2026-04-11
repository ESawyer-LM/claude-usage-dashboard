"""
Data collection via claude.ai internal API endpoints.
No browser needed — direct HTTP calls with the sessionKey cookie.
Bypasses Cloudflare Turnstile entirely (it only protects HTML pages, not API calls).

Endpoints discovered from HAR captures:
  /api/organizations/{org}/members_v2          — paginated member list
  /api/organizations/{org}/members/counts       — member/seat summary
  /api/organizations/{org}/members_limit        — seat limits
  /api/organizations/{org}/subscription_details — plan/billing info
  /api/organizations/{org}/analytics/activity/overview       — DAU/WAU/MAU/utilization
  /api/organizations/{org}/analytics/activity/timeseries     — daily activity chart data
  /api/organizations/{org}/analytics/mcp/top-connectors      — MCP connector usage
  /api/organizations/{org}/projects              — project list
"""

import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

import config

logger = config.get_logger()

BASE_URL = "https://claude.ai"


def _get_org_id() -> str:
    """Get the org UUID from settings."""
    settings = config.load_settings()
    org_id = settings.get("org_id", "")
    if not org_id:
        raise AuthenticationError(
            "Organization ID is not configured. Set it in the admin UI. "
            "Find it in claude.ai URL: claude.ai/settings/organization → copy the UUID from the URL."
        )
    return org_id


class AuthenticationError(Exception):
    pass


class ScrapeError(Exception):
    pass


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------
def _api_get(path: str, session_cookie: str) -> dict | list:
    """Authenticated GET request to claude.ai internal API."""
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url)
    req.add_header("Cookie", f"sessionKey={session_cookie}")
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36")
    req.add_header("Referer", "https://claude.ai/analytics/activity")
    req.add_header("Origin", "https://claude.ai")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        if e.code in (401, 403):
            raise AuthenticationError(
                f"Session cookie is expired or invalid (HTTP {e.code}). "
                "Update it in the admin UI."
            )
        raise ScrapeError(f"API error {e.code} on {path}: {body}")
    except urllib.error.URLError as e:
        raise ScrapeError(f"Network error on {path}: {e}")


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------
def _fetch_members(cookie: str, org_id: str) -> list[dict]:
    """Fetch all org members via paginated members_v2 endpoint."""
    members = []
    offset = 0
    limit = 50

    while True:
        data = _api_get(
            f"/api/organizations/{org_id}/members_v2?offset={offset}&limit={limit}",
            cookie,
        )
        for item in data.get("data", []):
            member_type = item.get("type", "member")

            if member_type == "invite":
                # Invites: data lives under item["invite"], not item["member"]
                invite = item.get("invite", {})
                email = invite.get("email_address", "")
                role_raw = invite.get("role", "user")
                seat_tier = invite.get("seat_tier", "")
                name = email.split("@")[0].replace(".", " ").title() if email else "Unknown"
                status = "Pending"
            else:
                # Active members: data lives under item["member"]["account"]
                info = item.get("member", {})
                account = info.get("account", {})
                email = account.get("email_address", "")
                name = account.get("full_name", "")
                role_raw = info.get("role", "user")
                seat_tier = info.get("seat_tier", "")
                status = "Active"

            role_map = {
                "primary_owner": "Primary Owner",
                "owner": "Owner",
                "admin": "Owner",
                "user": "User",
            }
            role = role_map.get(role_raw, role_raw.replace("_", " ").title())

            members.append({
                "name": name, "email": email, "role": role,
                "status": status, "seat_tier": seat_tier,
            })

        if not data.get("pagination", {}).get("has_more", False):
            break
        offset += limit

    return members


def _fetch_member_counts(cookie: str, org_id: str) -> dict:
    """Quick member count summary."""
    return _api_get(f"/api/organizations/{org_id}/members/counts", cookie)


def _fetch_seat_limits(cookie: str, org_id: str) -> dict:
    """Seat limit and tier quantities."""
    return _api_get(f"/api/organizations/{org_id}/members_limit", cookie)


def _fetch_subscription(cookie: str, org_id: str) -> dict:
    """Subscription/plan details."""
    try:
        return _api_get(f"/api/organizations/{org_id}/subscription_details", cookie)
    except ScrapeError as e:
        logger.warning(f"Could not fetch subscription: {e}")
        return {}


# ---------------------------------------------------------------------------
# Analytics — Activity page (/analytics/activity)
# ---------------------------------------------------------------------------
def _fetch_activity_overview(cookie: str, org_id: str) -> dict:
    """DAU, WAU, MAU, utilization, stickiness."""
    try:
        return _api_get(
            f"/api/organizations/{org_id}/analytics/activity/overview",
            cookie,
        )
    except ScrapeError as e:
        logger.warning(f"Could not fetch activity overview: {e}")
        return {}


def _fetch_activity_timeseries(cookie: str, org_id: str, metric: str = "dau", days: int = 30) -> list[dict]:
    """Daily time-series for activity metrics (dau, wau)."""
    try:
        data = _api_get(
            f"/api/organizations/{org_id}/analytics/activity/timeseries"
            f"?metric={metric}&days={days}",
            cookie,
        )
        return data.get("data_points", [])
    except ScrapeError as e:
        logger.warning(f"Could not fetch activity {metric} timeseries: {e}")
        return []


# ---------------------------------------------------------------------------
# Analytics — Usage page (/analytics/usage)
# ---------------------------------------------------------------------------
def _fetch_usage_overview(cookie: str, org_id: str) -> dict:
    """
    Chats/day, projects created, artifacts created, plus user percentages.
    Endpoint: /analytics/usage/overview
    """
    try:
        return _api_get(
            f"/api/organizations/{org_id}/analytics/usage/overview",
            cookie,
        )
    except ScrapeError as e:
        logger.warning(f"Could not fetch usage overview: {e}")
        return {}


def _fetch_usage_timeseries(cookie: str, org_id: str, metric: str = "chats", days: int = 7) -> list[dict]:
    """
    Daily time-series for usage metrics (chats, projects, artifacts).
    Endpoint: /analytics/usage/timeseries?metric=chats&days=7
    """
    try:
        data = _api_get(
            f"/api/organizations/{org_id}/analytics/usage/timeseries"
            f"?metric={metric}&days={days}",
            cookie,
        )
        return data.get("data_points", [])
    except ScrapeError as e:
        logger.warning(f"Could not fetch usage {metric} timeseries: {e}")
        return []


def _fetch_user_rankings(cookie: str, org_id: str, metric: str = "projects", limit: int = 10) -> list[dict]:
    """
    Top users by metric (projects, artifacts, chats).
    Endpoint: /analytics/users/rankings?metric=projects&start_date=...&limit=10
    Returns: {users: [{account_uuid, email_address, seat_tier, value}], total_count, data_as_of}
    """
    today = datetime.now(timezone.utc).date()
    first_of_month = today.replace(day=1).strftime("%Y-%m-%d")
    try:
        data = _api_get(
            f"/api/organizations/{org_id}/analytics/users/rankings"
            f"?metric={metric}&start_date={first_of_month}&limit={limit}",
            cookie,
        )
        return data.get("users", [])
    except ScrapeError as e:
        logger.warning(f"Could not fetch user rankings for {metric}: {e}")
        return []


# ---------------------------------------------------------------------------
# Analytics — Claude Code page (/analytics/claude-code)
# ---------------------------------------------------------------------------
def _fetch_claude_code_overview(cookie: str, org_id: str) -> dict:
    """
    Claude Code metrics: sessions, lines accepted, commits, PRs, cost, active users.
    Endpoint: /api/claude_code/metrics_aggs/overview
    """
    today = datetime.now(timezone.utc).date()
    first_of_month = today.replace(day=1).strftime("%Y-%m-%d")
    last_of_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    end_date = last_of_month.strftime("%Y-%m-%d")
    try:
        return _api_get(
            f"/api/claude_code/metrics_aggs/overview"
            f"?start_date={first_of_month}&end_date={end_date}"
            f"&granularity=daily&organization_uuid={org_id}"
            f"&customer_type=claude_ai&subscription_type=team",
            cookie,
        )
    except ScrapeError as e:
        logger.warning(f"Could not fetch Claude Code overview: {e}")
        return {}


def _fetch_claude_code_users(cookie: str, org_id: str) -> list[dict]:
    """
    Per-user Claude Code metrics: cost, lines, sessions, PRs.
    Endpoint: /api/claude_code/metrics_aggs/users
    """
    today = datetime.now(timezone.utc).date()
    first_of_month = today.replace(day=1).strftime("%Y-%m-%d")
    last_of_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    end_date = last_of_month.strftime("%Y-%m-%d")
    try:
        data = _api_get(
            f"/api/claude_code/metrics_aggs/users"
            f"?start_date={first_of_month}&end_date={end_date}"
            f"&limit=50&offset=0&sort_by=total_lines_accepted&sort_order=desc"
            f"&organization_uuid={org_id}"
            f"&customer_type=claude_ai&subscription_type=team",
            cookie,
        )
        return data.get("users", [])
    except ScrapeError as e:
        logger.warning(f"Could not fetch Claude Code users: {e}")
        return []


# ---------------------------------------------------------------------------
# Build dashboard data from API responses
# ---------------------------------------------------------------------------
def _timeseries_to_chart(data_points: list[dict]) -> dict:
    """Convert API timeseries data_points to chart labels + data."""
    labels = []
    data = []
    for dp in data_points:
        date_str = dp.get("date", "")
        value = dp.get("value", 0)
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            labels.append(dt.strftime("%b %d"))
        except (ValueError, TypeError):
            labels.append(date_str)
        data.append(int(value) if value == int(value) else value)
    return {"labels": labels, "data": data}


def _rankings_to_top_users(rankings: list[dict], members: list[dict]) -> list[dict]:
    """
    Convert user rankings (email + value) to display-friendly top users list.
    Joins with members list to get full names.
    """
    email_to_name = {m["email"]: m["name"] for m in members if m.get("email")}
    result = []
    for r in rankings:
        email = r.get("email_address", "")
        name = email_to_name.get(email, email.split("@")[0].replace(".", " ").title())
        count = int(r.get("value", 0))
        result.append({"name": name, "count": count})
    return result


# ---------------------------------------------------------------------------
# Progress tracking stages
# ---------------------------------------------------------------------------
SCRAPER_STAGES = [
    {"key": "init",         "label": "Initializing session",        "weight": 10},
    {"key": "members",      "label": "Fetching member data",        "weight": 15},
    {"key": "seats",        "label": "Fetching seats & subscription","weight": 10},
    {"key": "activity",     "label": "Fetching activity metrics",   "weight": 15},
    {"key": "usage",        "label": "Fetching usage metrics",      "weight": 15},
    {"key": "claude_code",  "label": "Fetching Claude Code stats",  "weight": 20},
    {"key": "processing",   "label": "Processing & caching data",   "weight": 10},
    {"key": "complete",     "label": "Scrape complete",             "weight":  5},
]
# Weights sum to 100


def _report_progress(callback, stage_key, cumulative_percent, label):
    """Call progress callback if provided."""
    if callback:
        callback(stage_key, cumulative_percent, label)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def scrape(progress_callback=None) -> dict:
    """Collect all dashboard data. Falls back to cache on failure.

    Args:
        progress_callback: Optional callable(stage_key, percent_complete, label)
            called at each stage boundary to report progress.
    """
    _report_progress(progress_callback, "init", 0, "Initializing session")

    settings = config.load_settings()
    cookie = settings.get("session_cookie", "")
    if not cookie:
        cached = config.load_cache()
        if cached:
            cached["from_cache"] = True
            cached["cache_reason"] = "Session cookie not configured"
            return cached
        raise AuthenticationError("Session cookie not configured. Set it in the admin UI.")

    try:
        org_id = _get_org_id()
        logger.info(f"Organization ID: {org_id}")

        # --- Members ---
        _report_progress(progress_callback, "members", 10, "Fetching member data")
        logger.info("Fetching members...")
        members = _fetch_members(cookie, org_id)
        logger.info(f"Found {len(members)} members")

        # --- Counts & limits ---
        _report_progress(progress_callback, "seats", 25, "Fetching seats & subscription")
        logger.info("Fetching counts and seat limits...")
        counts = _fetch_member_counts(cookie, org_id)
        limits = _fetch_seat_limits(cookie, org_id)
        seat_tiers = limits.get("seat_tier_quantities", {})
        # Total assigned seats = sum of all tier quantities (e.g. team_standard:22 + team_tier_1:2 = 24)
        total_seats = sum(seat_tiers.values()) if seat_tiers else counts.get("total", len(members))
        # Active members (not including pending invites)
        active_members = counts.get("total", 0)
        # Pending invites
        pending_invites = counts.get("pending_invites_total", 0)
        logger.info(f"Seats: {active_members} active + {pending_invites} pending / {total_seats} assigned")

        # --- Subscription ---
        logger.info("Fetching subscription...")
        subscription = _fetch_subscription(cookie, org_id)
        plan_tier = "Team" if ("team_standard" in seat_tiers or "team_tier_1" in seat_tiers) else "Standard"

        # --- Activity overview (DAU/WAU/MAU/utilization) ---
        _report_progress(progress_callback, "activity", 35, "Fetching activity metrics")
        logger.info("Fetching activity overview...")
        activity_overview = _fetch_activity_overview(cookie, org_id)
        if activity_overview:
            logger.info(
                f"Activity: DAU={activity_overview.get('dau', {}).get('value')}, "
                f"WAU={activity_overview.get('wau', {}).get('value')}, "
                f"MAU={activity_overview.get('mau', {}).get('value')}, "
                f"Utilization={activity_overview.get('utilization', {}).get('value')}%"
            )

        # --- Usage overview (chats/day, projects, artifacts) ---
        _report_progress(progress_callback, "usage", 50, "Fetching usage metrics")
        logger.info("Fetching usage overview...")
        usage_overview = _fetch_usage_overview(cookie, org_id)
        if usage_overview:
            logger.info(
                f"Usage: chats/day={usage_overview.get('chats_per_day', {}).get('value')}, "
                f"projects={usage_overview.get('projects_created', {}).get('value')}, "
                f"artifacts={usage_overview.get('artifacts_created', {}).get('value')}"
            )

        # --- Daily chats timeseries (line chart) ---
        logger.info("Fetching daily chats timeseries...")
        chats_points = _fetch_usage_timeseries(cookie, org_id, metric="chats", days=7)
        daily_chats = _timeseries_to_chart(chats_points)
        logger.info(f"Daily chats: {len(daily_chats['data'])} data points: {daily_chats['data']}")

        # --- WAU timeseries (line chart) ---
        logger.info("Fetching WAU timeseries...")
        wau_points = _fetch_activity_timeseries(cookie, org_id, metric="wau", days=30)
        wau_chart = _timeseries_to_chart(wau_points)
        logger.info(f"WAU timeseries: {len(wau_chart['data'])} data points")

        # --- DAU timeseries (line chart) ---
        logger.info("Fetching DAU timeseries...")
        dau_points = _fetch_activity_timeseries(cookie, org_id, metric="dau", days=30)
        dau_chart = _timeseries_to_chart(dau_points)
        logger.info(f"DAU timeseries: {len(dau_chart['data'])} data points")

        # --- Top users by projects (MTD) ---
        logger.info("Fetching top users by projects...")
        project_rankings = _fetch_user_rankings(cookie, org_id, metric="projects", limit=10)
        top_projects = _rankings_to_top_users(project_rankings, members)
        logger.info(f"Top users by projects: {len(top_projects)}")

        # --- Top users by artifacts (MTD) ---
        logger.info("Fetching top users by artifacts...")
        artifact_rankings = _fetch_user_rankings(cookie, org_id, metric="artifacts", limit=10)
        top_artifacts = _rankings_to_top_users(artifact_rankings, members)
        logger.info(f"Top users by artifacts: {len(top_artifacts)}")

        # --- Top users by chats (MTD) ---
        logger.info("Fetching top users by chats...")
        chat_rankings = _fetch_user_rankings(cookie, org_id, metric="chats", limit=10)
        top_chats = _rankings_to_top_users(chat_rankings, members)
        logger.info(f"Top users by chats: {len(top_chats)}")

        # --- Claude Code overview (sessions, lines, cost, commits, PRs) ---
        _report_progress(progress_callback, "claude_code", 65, "Fetching Claude Code stats")
        logger.info("Fetching Claude Code metrics...")
        cc_overview = _fetch_claude_code_overview(cookie, org_id)
        cc_summary = cc_overview.get("summary", {})
        cc_timeseries = cc_overview.get("time_series", {})
        if cc_summary:
            logger.info(
                f"Claude Code: {cc_summary.get('active_users', 0)} active users, "
                f"{cc_summary.get('total_sessions', 0)} sessions, "
                f"${float(cc_summary.get('total_cost_usd', 0)):.2f} cost, "
                f"{cc_summary.get('total_lines_accepted', 0)} lines accepted"
            )

        # --- Claude Code per-user metrics ---
        logger.info("Fetching Claude Code per-user metrics...")
        cc_users = _fetch_claude_code_users(cookie, org_id)
        # Join with member names
        email_to_name = {m["email"]: m["name"] for m in members if m.get("email")}
        for u in cc_users:
            email = u.get("email", "")
            u["name"] = email_to_name.get(email, email.split("@")[0].replace(".", " ").title())
        logger.info(f"Claude Code users: {len(cc_users)}")

        # Build Claude Code activity timeseries for chart
        cc_activity_chart = {"labels": [], "data": []}
        for dp in cc_timeseries.get("activity", []):
            date_str = dp.get("date", "")
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                cc_activity_chart["labels"].append(dt.strftime("%b %d"))
            except (ValueError, TypeError):
                cc_activity_chart["labels"].append(date_str)
            cc_activity_chart["data"].append(dp.get("sessions_count", 0) or 0)

        # Build Claude Code lines-of-code timeseries for chart
        logger.debug(f"CC timeseries keys: {list(cc_timeseries.keys())}")
        cc_lines_chart = {"labels": [], "data": []}
        lines_series = cc_timeseries.get("lines_of_code", [])
        if lines_series:
            logger.debug(f"CC lines_of_code sample: {lines_series[:2]}")
        for dp in lines_series:
            date_str = dp.get("date", "")
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                cc_lines_chart["labels"].append(dt.strftime("%b %d"))
            except (ValueError, TypeError):
                cc_lines_chart["labels"].append(date_str)
            value = dp.get("total_lines_accepted", dp.get("lines_accepted", dp.get("value", 0)))
            cc_lines_chart["data"].append(int(value) if value else 0)

        _report_progress(progress_callback, "processing", 85, "Processing & caching data")

        now = datetime.now(timezone.utc).isoformat()
        result = {
            "scraped_at": now,
            "plan_tier": plan_tier,
            "total_seats": total_seats,
            "active_members": active_members,
            "pending_invites": pending_invites,
            "members": members,
            "daily_chats": daily_chats,
            "wau_chart": wau_chart,
            "dau_chart": dau_chart,
            "top_users_projects": top_projects,
            "top_users_artifacts": top_artifacts,
            "top_users_chats": top_chats,
            "activity_overview": activity_overview,
            "usage_overview": usage_overview,
            "claude_code": {
                "summary": cc_summary,
                "users": cc_users,
                "activity_chart": cc_activity_chart,
                "lines_chart": cc_lines_chart,
            },
            "from_cache": False,
        }

        config.save_cache(result)
        _report_progress(progress_callback, "complete", 100, "Scrape complete")
        logger.info(
            f"Data collection complete: {len(members)} members, "
            f"{len(daily_chats['data'])} days of chats, "
            f"{len(top_projects)} top project users, "
            f"{len(top_artifacts)} top artifact users, "
            f"{len(cc_users)} Claude Code users, "
            f"seats: {total_seats}, plan: {plan_tier}"
        )
        return result

    except AuthenticationError:
        raise
    except Exception as e:
        logger.error(f"Data collection failed: {e}")
        cached = config.load_cache()
        if cached:
            logger.warning(f"Using cached data: {e}")
            cached["from_cache"] = True
            cached["cache_reason"] = str(e)
            return cached
        raise ScrapeError(f"Data collection failed and no cached data: {e}")
