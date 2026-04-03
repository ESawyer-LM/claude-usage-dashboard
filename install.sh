#!/usr/bin/env bash
set -euo pipefail

# Claude Usage Dashboard — Ubuntu Installer
# Idempotent: safe to re-run for updates without breaking existing installs.

APP_DIR="/opt/claude-dashboard"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_USER="claude-dashboard"

echo ""
echo "  Claude Usage Dashboard — Installer"
echo "  ==================================="
echo ""

# -----------------------------------------------------------------------
# Step 1: System dependencies
# -----------------------------------------------------------------------
echo "  [1/8] Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-pip python3-venv git
echo "  ✓ System dependencies installed"

# -----------------------------------------------------------------------
# Step 2: App directory (git clone for auto-updates, file copy fallback)
# -----------------------------------------------------------------------
GITHUB_REPO="https://github.com/ESawyer-LM/claude-usage-dashboard.git"
echo "  [2/8] Setting up app directory..."
if [ -d "$APP_DIR/.git" ]; then
    # Existing git install — pull latest
    echo "  → Git repo found, pulling latest..."
    sudo -u "$SERVICE_USER" git -C "$APP_DIR" fetch --tags origin 2>/dev/null || true
    sudo -u "$SERVICE_USER" git -C "$APP_DIR" pull origin main 2>/dev/null || true
elif git ls-remote "$GITHUB_REPO" HEAD &>/dev/null; then
    # Fresh install with network — clone for auto-update support
    echo "  → Cloning repository for auto-update support..."
    sudo mkdir -p "$APP_DIR"
    sudo git clone "$GITHUB_REPO" "$APP_DIR" 2>/dev/null || {
        # Clone failed (private repo without credentials) — fall back to copy
        echo "  → Clone failed, falling back to file copy..."
        for f in main.py config.py scraper.py html_generator.py pdf_generator.py \
                 emailer.py scheduler.py admin.py requirements.txt .env.example \
                 claude-dashboard.service CHANGELOG.md CLAUDE.md README.md; do
            if [ -f "$SRC_DIR/$f" ]; then
                sudo cp "$SRC_DIR/$f" "$APP_DIR/$f"
            fi
        done
    }
else
    # No network or repo unreachable — copy files
    sudo mkdir -p "$APP_DIR"
    for f in main.py config.py scraper.py html_generator.py pdf_generator.py \
             emailer.py scheduler.py admin.py requirements.txt .env.example \
             claude-dashboard.service CHANGELOG.md CLAUDE.md README.md; do
        if [ -f "$SRC_DIR/$f" ]; then
            sudo cp "$SRC_DIR/$f" "$APP_DIR/$f"
        fi
    done
fi
echo "  ✓ Project files installed at $APP_DIR"

# -----------------------------------------------------------------------
# Step 3: Dedicated service user
# -----------------------------------------------------------------------
echo "  [3/8] Creating service user..."
sudo useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER" 2>/dev/null || true
sudo chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"
echo "  ✓ Service user '$SERVICE_USER' ready"

# -----------------------------------------------------------------------
# Step 4: Python virtual environment
# -----------------------------------------------------------------------
echo "  [4/8] Setting up Python virtual environment..."
if [ ! -d "$APP_DIR/venv" ]; then
    sudo -u "$SERVICE_USER" python3 -m venv "$APP_DIR/venv"
fi
# --no-cache-dir avoids warnings about missing /home/claude-dashboard/.cache/pip
sudo -u "$SERVICE_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip --no-cache-dir -q
sudo -u "$SERVICE_USER" "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" --no-cache-dir -q
echo "  ✓ Python dependencies installed"

# -----------------------------------------------------------------------
# Step 5: Environment file
# -----------------------------------------------------------------------
echo "  [5/8] Checking environment file..."
if [ ! -f "$APP_DIR/.env" ]; then
    sudo cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo ""
    echo "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "    SETUP REQUIRED — enter your credentials below"
    echo "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    read -rsp "  Admin UI password (ADMIN_PASSWORD): " admin_pass
    echo ""
    sudo sed -i "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=${admin_pass}|" "$APP_DIR/.env"
    echo "  ✓ .env saved"
    echo "  → SMTP credentials and session cookie are configured in the admin UI after startup."
else
    echo "  ✓ .env already exists (preserved)"
fi
sudo chmod 600 "$APP_DIR/.env"
sudo chown "$SERVICE_USER:$SERVICE_USER" "$APP_DIR/.env"

# -----------------------------------------------------------------------
# Step 6: Output directory
# -----------------------------------------------------------------------
echo "  [6/8] Creating output directory..."
sudo -u "$SERVICE_USER" mkdir -p "$APP_DIR/output"
echo "  ✓ Output directory ready"

# -----------------------------------------------------------------------
# Step 7: Firewall rule
# -----------------------------------------------------------------------
echo "  [7/8] Configuring firewall..."
if command -v ufw &>/dev/null && sudo ufw status | grep -q "Status: active"; then
    sudo ufw allow 8934/tcp comment 'claude-dashboard admin UI' 2>/dev/null || true
    echo "  ✓ UFW rule added for port 8934"
else
    echo "  ℹ UFW not active — ensure port 8934 is open in your firewall/network settings"
fi

# -----------------------------------------------------------------------
# Step 8: systemd service
# -----------------------------------------------------------------------
echo "  [8/8] Installing systemd service..."
sudo cp "$APP_DIR/claude-dashboard.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable claude-dashboard
sudo systemctl restart claude-dashboard
sleep 2
echo "  ✓ Service installed and started"
sudo systemctl status claude-dashboard --no-pager 2>/dev/null || true

# -----------------------------------------------------------------------
# Done
# -----------------------------------------------------------------------
echo ""
echo "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "    ✓  claude-dashboard installed and running"
echo "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  Admin UI  →  http://$(hostname -I | awk '{print $1}'):8934"
echo "              (or http://localhost:8934 if accessing from the VM itself)"
echo ""
echo "  Logs      →  journalctl -u claude-dashboard -f"
echo "              $APP_DIR/output/dashboard.log"
echo ""
echo "  Next step →  Open the admin UI and paste your"
echo "               claude.ai session cookie to activate data collection."
echo ""
echo "  To update →  Use the admin UI (auto-update) or: cd $SRC_DIR && sudo bash install.sh"
echo ""
