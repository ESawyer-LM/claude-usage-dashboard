"""
Configuration hub for Claude Usage Dashboard.
Manages .env (bootstrap secrets) and settings.json (runtime settings).
Fernet encryption for smtp_pass at rest.
"""

import json
import logging
import os
import stat
import tempfile
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
LOG_FILE = os.path.join(OUTPUT_DIR, "dashboard.log")
FERNET_KEY_FILE = os.path.join(OUTPUT_DIR, ".fernet_key")
FLASK_SECRET_FILE = os.path.join(OUTPUT_DIR, ".flask_secret")

# ---------------------------------------------------------------------------
# Default settings (written to settings.json on first run)
# ---------------------------------------------------------------------------
DEFAULT_SETTINGS = {
    "org_id": "",
    "session_cookie": "",
    "smtp_host": "smtp.office365.com",
    "smtp_port": 587,
    "smtp_user": "esawyer@loumalnatis.com",
    "smtp_pass": "",  # stored Fernet-encrypted
    "smtp_from_name": "Claude Dashboard",
    "weekday_recipients": ["esawyer@loumalnatis.com", "nscott@loumalnatis.com"],
    "friday_recipients": [
        "esawyer@loumalnatis.com",
        "nscott@loumalnatis.com",
        "jdouglas@loumalnatis.com",
    ],
    "weekday_cron": {"hour": 7, "minute": 0},
    "friday_cron": {"hour": 7, "minute": 0},
    "weekday_enabled": True,
    "friday_enabled": True,
    "timezone": "America/Chicago",
}

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
# Settings file management
# ---------------------------------------------------------------------------
def _ensure_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def load_settings() -> dict:
    """Read settings.json, creating it with defaults if missing."""
    _ensure_output_dir()
    if not os.path.exists(SETTINGS_FILE):
        save_settings(DEFAULT_SETTINGS)
        return dict(DEFAULT_SETTINGS)
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
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
