#!/bin/bash

# Maxi_VPN_bot Installer
# Compatible with X-UI (MHSanaei fork)
# Repository: https://github.com/prokazzzzza/x-ui.git

set -euo pipefail

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

_info() { echo -e "${BLUE}$*${NC}"; }
_ok() { echo -e "${GREEN}$*${NC}"; }
_warn() { echo -e "${YELLOW}$*${NC}"; }
_err() { echo -e "${RED}$*${NC}"; }

_require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    _err "Error: required command not found: $1"
    exit 1
  fi
}

_install_apt_packages() {
  local -a pkgs=("$@")
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y --no-install-recommends "${pkgs[@]}"
}

_detect_public_ip() {
  local ip=""
  ip="$(curl -fsS4 ifconfig.me 2>/dev/null || true)"
  if [[ -z "$ip" ]]; then
    ip="$(curl -fsS4 icanhazip.com 2>/dev/null || true)"
  fi
  if [[ -z "$ip" ]]; then
    ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  fi
  echo "$ip"
}

_escape_env_value() {
  local value="${1:-}"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf "%s" "$value"
}

_slugify_service_suffix() {
  local raw="${1:-}"
  local lowered=""
  local slug=""
  lowered="$(printf "%s" "$raw" | tr '[:upper:]' '[:lower:]')"
  slug="$(printf "%s" "$lowered" | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//')"
  if [[ -z "$slug" ]]; then
    slug="bot"
  fi
  printf "%s" "$slug"
}

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}    VPN_bot Deployment Script      ${NC}"
echo -e "${BLUE}========================================${NC}"

# 1. Check Root
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}Error: This script must be run as root.${NC}"
   exit 1
fi

if [[ -f /etc/os-release ]]; then
  . /etc/os-release
  if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "22.04" ]]; then
    _err "Error: this installer supports Ubuntu 22.04 only. Detected: ${ID:-unknown} ${VERSION_ID:-unknown}"
    exit 1
  fi
else
  _err "Error: /etc/os-release not found; cannot verify Ubuntu 22.04."
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  _err "Error: this installer currently supports Debian/Ubuntu (apt-get)."
  exit 1
fi

_require_cmd systemctl

_info "\n--- Preparing system dependencies ---"
_install_apt_packages ca-certificates curl git python3 python3-venv python3-pip

# 2. Check X-UI Installation
XUI_PATH="/usr/local/x-ui"
XUI_BIN="$XUI_PATH/x-ui"

_info "\n--- Ensuring 3x-ui (MHSanaei/3x-ui) is installed ---"
if [[ -x "$XUI_BIN" ]]; then
  _ok "✅ 3x-ui binary detected: $XUI_BIN"
else
  _warn "⚠️ 3x-ui is not installed. Installing..."
  bash <(curl -Ls https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh)
  if [[ ! -x "$XUI_BIN" ]]; then
    _err "Error: 3x-ui installation failed (binary not found at $XUI_BIN)."
    exit 1
  fi
  _ok "✅ 3x-ui installed."
fi

if ! command -v x-ui >/dev/null 2>&1; then
  _err "Error: x-ui command not found after installation."
  exit 1
fi

_info "\n--- Updating 3x-ui / Xray to latest available ---"
if ! x-ui update; then
  _err "Error: x-ui update failed."
  exit 1
fi

VERSION_OUT="$("$XUI_BIN" -v 2>/dev/null | head -n 1 || true)"
if [[ -n "$VERSION_OUT" ]]; then
  _ok "3x-ui version: ${YELLOW}${VERSION_OUT}${NC}"
fi

# 3. Collect Credentials
echo -e "\n${BLUE}--- Configuration ---${NC}"
read -p "Enter Bot Name [Maxi_VPN_bot]: " INPUT_BOT_NAME
if [[ -z "${INPUT_BOT_NAME:-}" ]]; then
    INPUT_BOT_NAME="Maxi_VPN_bot"
fi

BOT_SLUG="$(_slugify_service_suffix "$INPUT_BOT_NAME")"
SERVICE_NAME="x-ui-bot-${BOT_SLUG}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

read -rs -p "Enter Telegram Bot Token: " INPUT_TOKEN
echo
while [[ -z "$INPUT_TOKEN" ]]; do
    echo -e "${RED}Token cannot be empty.${NC}"
    read -rs -p "Enter Telegram Bot Token: " INPUT_TOKEN
    echo
done

read -rs -p "Enter Support Bot Token (optional, press Enter to skip): " INPUT_SUPPORT_TOKEN
echo

read -p "Enter Admin Telegram ID: " INPUT_ADMIN_ID
while [[ -z "$INPUT_ADMIN_ID" ]]; do
    echo -e "${RED}Admin ID cannot be empty.${NC}"
    read -p "Enter Admin Telegram ID: " INPUT_ADMIN_ID
done

# Try to detect IP
DETECTED_IP="$(_detect_public_ip)"
read -p "Enter Server IP [$DETECTED_IP]: " INPUT_IP
if [[ -z "$INPUT_IP" ]]; then
    INPUT_IP="$DETECTED_IP"
fi
while [[ -z "$INPUT_IP" ]]; do
    _warn "Server IP cannot be empty."
    read -p "Enter Server IP: " INPUT_IP
done

# 4. Prepare Bot Directory
BOT_DIR="$XUI_PATH/bot"
REPO_URL="https://github.com/prokazzzzza/x-ui-Telegram_bot.git"
ENV_FILE="$BOT_DIR/.env.${BOT_SLUG}"
BOT_DB_PATH="$BOT_DIR/bot_data_${BOT_SLUG}.db"
BOT_LOG_DIR="/usr/local/x-ui/logs"
BOT_LOG_FILE="$BOT_LOG_DIR/bot_${BOT_SLUG}.log"
mkdir -p "$BOT_LOG_DIR"

echo -e "\n${BLUE}--- Setting up Bot ---${NC}"
tmp_dir="$(mktemp -d)"
cleanup() { rm -rf "$tmp_dir"; }
trap cleanup EXIT

if [[ -d "$BOT_DIR" ]]; then
    echo -e "${YELLOW}Bot directory exists. Updating...${NC}"
else
    echo -e "${GREEN}Installing bot...${NC}"
    mkdir -p "$BOT_DIR"
fi

git clone "$REPO_URL" "$tmp_dir/x-ui-repo"
shopt -s dotglob
cp -r "$tmp_dir/x-ui-repo/bot/"* "$BOT_DIR/"
shopt -u dotglob

# 5. Write .env
echo -e "Writing configuration..."
old_umask="$(umask)"
umask 077
ESCAPED_BOT_NAME="$(_escape_env_value "$INPUT_BOT_NAME")"
cat > "$ENV_FILE" <<EOL
BOT_NAME="$ESCAPED_BOT_NAME"
BOT_TOKEN=$INPUT_TOKEN
SUPPORT_BOT_TOKEN=$INPUT_SUPPORT_TOKEN
ADMIN_ID=$INPUT_ADMIN_ID
HOST_IP=$INPUT_IP
BOT_DB_PATH=$BOT_DB_PATH
BOT_LOG_DIR=$BOT_LOG_DIR
BOT_LOG_FILE=$BOT_LOG_FILE
BOT_SYSTEMD_SERVICE=$SERVICE_NAME
EOL
chmod 600 "$ENV_FILE"
umask "$old_umask"

# 6. Setup Python Environment
echo -e "Setting up Python virtual environment..."
cd "$BOT_DIR"
if [[ ! -d "venv" ]]; then
    python3 -m venv venv
fi

# Install requirements
"$BOT_DIR/venv/bin/pip" install --upgrade pip
if [[ -f "requirements.txt" ]]; then
    "$BOT_DIR/venv/bin/pip" install -r requirements.txt
else
    echo -e "${YELLOW}requirements.txt not found! Installing default packages...${NC}"
    "$BOT_DIR/venv/bin/pip" install python-telegram-bot apscheduler python-dotenv requests
fi

# 7. Setup Systemd Service
echo -e "Configuring Systemd service..."

cat > "$SERVICE_FILE" <<EOL
[Unit]
Description=Telegram Bot for X-UI ($INPUT_BOT_NAME)
After=network-online.target x-ui.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$BOT_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$BOT_DIR/venv/bin/python3 $BOT_DIR/bot.py
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOL

# Reload and Start
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

# 8. Final Status
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo -e "\n${GREEN}✅ Bot installed and started successfully!${NC}"
    echo -e "Service: ${YELLOW}${SERVICE_NAME}${NC}"
    echo -e "Logs: journalctl -u ${SERVICE_NAME} -f"
else
    echo -e "\n${RED}❌ Bot failed to start.${NC}"
    echo -e "Service: ${YELLOW}${SERVICE_NAME}${NC}"
    systemctl status "$SERVICE_NAME"
fi
