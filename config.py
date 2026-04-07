"""
Configuration hub for Claude Usage Dashboard.
Manages .env (bootstrap secrets) and settings.json (runtime settings).
Fernet encryption for smtp_pass at rest.
"""

VERSION = "0.10.6"

import json
import logging
import os
import stat
import subprocess
import tempfile
import urllib.request
import uuid
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# .env bootstrap (loaded once at import time)
# ---------------------------------------------------------------------------
load_dotenv(override=False)

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "./output")
ADMIN_PORT = int(os.getenv("ADMIN_PORT", "8934"))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# Resolve OUTPUT_DIR to absolute path relative to this file's directory
_BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = str((_BASE_DIR / OUTPUT_DIR).resolve())

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SETTINGS_FILE = os.path.join(OUTPUT_DIR, "settings.json")
CACHE_FILE = os.path.join(OUTPUT_DIR, "last_data.json")
REPORTS_FILE = os.path.join(OUTPUT_DIR, "reports.json")
LOG_FILE = os.path.join(OUTPUT_DIR, "dashboard.log")
FERNET_KEY_FILE = os.path.join(OUTPUT_DIR, ".fernet_key")
FLASK_SECRET_FILE = os.path.join(OUTPUT_DIR, ".flask_secret")

# ---------------------------------------------------------------------------
# Default settings (written to settings.json on first run)
# ---------------------------------------------------------------------------
DEFAULT_SETTINGS = {
    "org_id": "",
    "org_display_name": "",  # shown in report headers/footers; blank = "Claude Usage Dashboard"
    "session_cookie": "",
    "smtp_host": "",
    "smtp_port": 587,
    "smtp_user": "",
    "smtp_pass": "",  # stored Fernet-encrypted
    "smtp_from_name": "Claude Dashboard",
    "schedules": [],
    "timezone": "America/Chicago",
}

# ---------------------------------------------------------------------------
# Report type registry
# ---------------------------------------------------------------------------
REPORT_TYPES = {
    "full": {
        "name": "Full Report",
        "description": "All sections including member directory",
        "sections": ["overview", "activity", "usage", "claude_code", "members"],
    },
    "expanded": {
        "name": "Expanded Report",
        "description": "Full report plus trend metrics, stickiness, usage stats, DAU chart, and chat rankings",
        "sections": [
            "overview", "activity", "usage", "claude_code", "members",
            "trends", "stickiness", "usage_stats", "dau_chart", "chat_rankings",
        ],
    },
}

DEFAULT_REPORT_TYPE = "full"


def _migrate_schedules(settings: dict) -> dict:
    """Migrate old weekday/friday schedule format to new multi-schedule list.

    Detects the legacy format (weekday_cron/friday_cron keys without a
    schedules list) and converts to the new schedules array.  Removes the
    old keys after migration.  Returns the settings dict (mutated in place).
    """
    if "schedules" in settings:
        return settings  # already migrated

    now = datetime.now().isoformat()
    schedules = []

    # Convert weekday schedule (Mon-Thu)
    wd_cron = settings.get("weekday_cron", {"hour": 7, "minute": 0})
    schedules.append({
        "id": uuid.uuid4().hex[:8],
        "name": "Weekday Report (Mon-Thu)",
        "enabled": settings.get("weekday_enabled", True),
        "recurrence_type": "weekly",
        "days_of_week": ["mon", "tue", "wed", "thu"],
        "time": {"hour": wd_cron.get("hour", 7), "minute": wd_cron.get("minute", 0)},
        "recipients": settings.get("weekday_recipients", []),
        "created_at": now,
        "last_sent": None,
    })

    # Convert Friday schedule
    fri_cron = settings.get("friday_cron", {"hour": 7, "minute": 0})
    schedules.append({
        "id": uuid.uuid4().hex[:8],
        "name": "Friday Report",
        "enabled": settings.get("friday_enabled", True),
        "recurrence_type": "weekly",
        "days_of_week": ["fri"],
        "time": {"hour": fri_cron.get("hour", 7), "minute": fri_cron.get("minute", 0)},
        "recipients": settings.get("friday_recipients", []),
        "created_at": now,
        "last_sent": None,
    })

    settings["schedules"] = schedules

    # Remove legacy keys
    for key in ("weekday_cron", "friday_cron", "weekday_recipients",
                "friday_recipients", "weekday_enabled", "friday_enabled"):
        settings.pop(key, None)

    return settings


def _ensure_report_type(settings: dict) -> bool:
    """Backfill report_type on schedules that predate this field.

    Returns True if any schedule was modified (caller should save).
    """
    changed = False
    for s in settings.get("schedules", []):
        if "report_type" not in s:
            s["report_type"] = DEFAULT_REPORT_TYPE
            changed = True
    return changed

# ---------------------------------------------------------------------------
# Fernet encryption helpers
# ---------------------------------------------------------------------------
_fernet_instance = None


def _get_fernet() -> Fernet:
    """Load or generate the Fernet key. Cached after first call."""
    global _fernet_instance
    if _fernet_instance is not None:
        return _fernet_instance

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if os.path.exists(FERNET_KEY_FILE):
        with open(FERNET_KEY_FILE, "rb") as f:
            key = f.read().strip()
    else:
        key = Fernet.generate_key()
        # Write with restrictive permissions
        fd = os.open(FERNET_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, key)
        finally:
            os.close(fd)

    _fernet_instance = Fernet(key)
    return _fernet_instance


def encrypt_value(plaintext: str) -> str:
    """Encrypt a string value. Returns base64 Fernet token as string."""
    if not plaintext:
        return ""
    f = _get_fernet()
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_value(ciphertext: str) -> str:
    """Decrypt a Fernet token back to plaintext. Returns empty string if input is empty."""
    if not ciphertext:
        return ""
    f = _get_fernet()
    try:
        return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Flask secret key
# ---------------------------------------------------------------------------
def get_flask_secret() -> bytes:
    """Load or generate a Flask secret key."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if os.path.exists(FLASK_SECRET_FILE):
        with open(FLASK_SECRET_FILE, "rb") as f:
            return f.read()
    secret = os.urandom(32)
    fd = os.open(FLASK_SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, secret)
    finally:
        os.close(fd)
    return secret


# ---------------------------------------------------------------------------
# Admin password management
# ---------------------------------------------------------------------------
def update_env_password(new_password: str):
    """Update ADMIN_PASSWORD in .env file and in-memory config."""
    global ADMIN_PASSWORD
    env_path = _BASE_DIR / ".env"
    lines = []
    found = False
    if env_path.exists():
        with open(env_path, "r") as f:
            for line in f:
                if line.startswith("ADMIN_PASSWORD="):
                    lines.append(f"ADMIN_PASSWORD={new_password}\n")
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f"ADMIN_PASSWORD={new_password}\n")
    with open(env_path, "w") as f:
        f.writelines(lines)
    ADMIN_PASSWORD = new_password


# ---------------------------------------------------------------------------
# Settings file management
# ---------------------------------------------------------------------------
def _ensure_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def load_settings() -> dict:
    """Read settings.json, creating it with defaults if missing.

    Automatically migrates legacy weekday/friday format to the new
    multi-schedule format on first load.
    """
    _ensure_output_dir()
    if not os.path.exists(SETTINGS_FILE):
        save_settings(DEFAULT_SETTINGS)
        return dict(DEFAULT_SETTINGS)
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Migrate legacy schedule format before merging defaults
    # (DEFAULT_SETTINGS has "schedules": [] which would mask the old keys)
    needs_migration = "schedules" not in data and "weekday_cron" in data
    if needs_migration:
        _migrate_schedules(data)
        save_settings(data)
    # Backfill report_type on schedules that predate multi-version support
    if _ensure_report_type(data):
        save_settings(data)
    # Merge any missing defaults (for forward compatibility)
    merged = dict(DEFAULT_SETTINGS)
    merged.update(data)
    return merged


def save_settings(data: dict):
    """Atomic write to settings.json (write to tmp, then os.replace)."""
    _ensure_output_dir()
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=OUTPUT_DIR, prefix="settings_", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, SETTINGS_FILE)
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def update_setting(key: str, value):
    """Update a single setting key."""
    settings = load_settings()
    settings[key] = value
    save_settings(settings)


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------
def save_cache(data: dict):
    """Save scraped data to cache file."""
    _ensure_output_dir()
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=OUTPUT_DIR, prefix="cache_", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, CACHE_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_cache() -> dict | None:
    """Load cached scrape data. Returns None if no cache exists."""
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


# ---------------------------------------------------------------------------
# Update checking
# ---------------------------------------------------------------------------
GITHUB_REPO = "ESawyer-LM/claude-usage-dashboard"
_GITHUB_TAGS_URL = f"https://api.github.com/repos/{GITHUB_REPO}/tags"


def _parse_version(v: str) -> tuple:
    """Parse version string like '0.1.3' into a comparable tuple (0, 1, 3)."""
    v = v.lstrip("v")
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def check_for_updates() -> dict:
    """Check GitHub for a newer version tag.

    Returns dict with keys:
        update_available (bool), latest_version (str), current_version (str),
        error (str or None)
    """
    result = {
        "update_available": False,
        "latest_version": VERSION,
        "current_version": VERSION,
        "error": None,
    }
    try:
        req = urllib.request.Request(_GITHUB_TAGS_URL)
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("User-Agent", f"claude-dashboard/{VERSION}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
        if not tags:
            return result
        # Tags are returned newest-first; find the latest semver tag
        for tag in tags:
            name = tag.get("name", "")
            if name.startswith("v") and name.count(".") >= 1:
                latest = name.lstrip("v")
                result["latest_version"] = latest
                if _parse_version(latest) > _parse_version(VERSION):
                    result["update_available"] = True
                break
    except Exception as e:
        result["error"] = str(e)
    return result


def _find_pip() -> str | None:
    """Locate the pip executable for the current environment."""
    import sys
    # Check for venv pip relative to the running interpreter
    pip = os.path.join(os.path.dirname(sys.executable), "pip")
    if os.path.exists(pip):
        return pip
    # Check for venv pip relative to app dir
    pip = os.path.join(str(_BASE_DIR), "venv", "bin", "pip")
    if os.path.exists(pip):
        return pip
    return None


def _pip_install(app_dir: str):
    """Install dependencies from requirements.txt if pip and the file exist."""
    pip = _find_pip()
    req_file = os.path.join(app_dir, "requirements.txt")
    if pip and os.path.exists(req_file):
        subprocess.run(
            [pip, "install", "-r", req_file, "--no-cache-dir", "-q"],
            cwd=app_dir, capture_output=True, text=True, timeout=120,
        )


def _reload_systemd_service(app_dir: str):
    """Copy updated service file to systemd and reload, if running as a service.

    Requires sudoers entry (created by install.sh) for the service user.
    """
    service_src = os.path.join(app_dir, "claude-dashboard.service")
    if not os.path.exists(service_src):
        return
    try:
        subprocess.run(
            ["sudo", "/usr/bin/cp", service_src,
             "/etc/systemd/system/claude-dashboard.service"],
            capture_output=True, text=True, timeout=10,
        )
        subprocess.run(
            ["sudo", "/usr/bin/systemctl", "daemon-reload"],
            capture_output=True, text=True, timeout=10,
        )
    except (PermissionError, subprocess.SubprocessError):
        # Sudoers not configured or systemctl unavailable — skip silently
        pass


def _update_via_git(app_dir: str, tag: str) -> dict:
    """Update when app dir is a git repository."""
    subprocess.run(
        ["git", "fetch", "--tags", "origin"],
        cwd=app_dir, capture_output=True, text=True, timeout=30, check=True,
    )
    subprocess.run(
        ["git", "checkout", tag],
        cwd=app_dir, capture_output=True, text=True, timeout=15, check=True,
    )
    _pip_install(app_dir)
    _reload_systemd_service(app_dir)
    return {"ok": True, "message": f"Updated to {tag}. Restart the service to apply."}


def _update_via_download(app_dir: str, tag: str) -> dict:
    """Update when app dir has no git repo — clone to temp dir and copy files."""
    import shutil

    logger = get_logger()
    clone_dir = tempfile.mkdtemp(prefix="claude-dashboard-update-")
    try:
        clone_url = f"https://github.com/{GITHUB_REPO}.git"
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", tag, clone_url, clone_dir],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return {"ok": False, "message": f"Clone failed: {result.stderr.strip()}"}

        # Copy application files (preserve .env, output/, venv/)
        update_files = []
        for f in os.listdir(clone_dir):
            if f.endswith(".py") or f in ("requirements.txt", "claude-dashboard.service",
                                          ".env.example", "CHANGELOG.md", "CLAUDE.md",
                                          "README.md"):
                update_files.append(f)

        copied = 0
        for f in update_files:
            src = os.path.join(clone_dir, f)
            dst = os.path.join(app_dir, f)
            try:
                shutil.copy2(src, dst)
                copied += 1
                logger.info(f"Updated file: {f}")
            except PermissionError as e:
                logger.error(f"Permission denied copying {f}: {e}")
                return {"ok": False, "message": f"Permission denied writing {dst}. Check file ownership."}
            except Exception as e:
                logger.error(f"Error copying {f}: {e}")
                return {"ok": False, "message": f"Error copying {f}: {e}"}

        # Verify the update actually took effect
        version_file = os.path.join(app_dir, "config.py")
        if os.path.exists(version_file):
            with open(version_file, "r") as vf:
                content = vf.read()
                if f'VERSION = "{tag.lstrip("v")}"' not in content:
                    logger.warning(f"Version mismatch after copy — config.py may not have been updated")

        _pip_install(app_dir)
        _reload_systemd_service(app_dir)
        return {"ok": True, "message": f"Updated to {tag} ({copied} files). Restart the service to apply."}
    finally:
        shutil.rmtree(clone_dir, ignore_errors=True)


def install_update(target_version: str) -> dict:
    """Update to the target version. Handles both git repos and file-copy installs.

    Returns dict with keys: ok (bool), message (str)
    """
    app_dir = str(_BASE_DIR)
    tag = f"v{target_version}" if not target_version.startswith("v") else target_version

    try:
        git_dir = os.path.join(app_dir, ".git")
        if os.path.isdir(git_dir):
            return _update_via_git(app_dir, tag)
        else:
            return _update_via_download(app_dir, tag)
    except subprocess.CalledProcessError as e:
        return {"ok": False, "message": f"Git error: {e.stderr.strip() or e.stdout.strip()}"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


def restart_service() -> bool:
    """Restart the systemd service. Tries multiple methods."""
    logger = get_logger()
    # Method 1: sudo with NOPASSWD (requires sudoers from install.sh)
    for cmd in [
        ["sudo", "-n", "/usr/bin/systemctl", "restart", "claude-dashboard"],
        ["sudo", "-n", "systemctl", "restart", "claude-dashboard"],
        ["systemctl", "restart", "claude-dashboard"],
    ]:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                logger.info(f"Service restarted via: {' '.join(cmd)}")
                return True
            logger.warning(f"Restart attempt failed ({' '.join(cmd)}): {result.stderr.strip()}")
        except Exception as e:
            logger.warning(f"Restart attempt error ({' '.join(cmd)}): {e}")
    logger.error("All restart methods failed")
    return False


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_logger = None


def get_logger() -> logging.Logger:
    """Get the application logger with file + console handlers."""
    global _logger
    if _logger is not None:
        return _logger

    _ensure_output_dir()

    logger = logging.getLogger("claude_dashboard")
    logger.setLevel(logging.INFO)

    # File handler (rotating, 5MB, 3 backups)
    fh = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setLevel(logging.INFO)
    fh.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(ch)

    _logger = logger
    return logger
