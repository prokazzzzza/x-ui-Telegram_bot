#!/bin/bash

# Maxi_VPN_bot Installer
# Compatible with X-UI (MHSanaei fork)
# Repository: https://github.com/prokazzzzza/x-ui.git

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}    Maxi_VPN_bot Deployment Script      ${NC}"
echo -e "${BLUE}========================================${NC}"

# 1. Check Root
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}Error: This script must be run as root.${NC}"
   exit 1
fi

# 2. Check X-UI Installation
XUI_PATH="/usr/local/x-ui"
XUI_BIN="$XUI_PATH/bin/x-ui"

if [[ -f "$XUI_BIN" ]]; then
    echo -e "${GREEN}✅ X-UI is detected.${NC}"
    # Try to detect version
    # MHSanaei fork usually supports ./x-ui version or help
    # Or we can just say "Detected".
    VERSION_OUT=$("$XUI_BIN" version 2>/dev/null)
    if [[ -n "$VERSION_OUT" ]]; then
        echo -e "Version: ${YELLOW}$VERSION_OUT${NC}"
    else
        echo -e "Version: ${YELLOW}Unknown (Binary found)${NC}"
    fi
else
    echo -e "${YELLOW}⚠️ X-UI is NOT installed.${NC}"
    read -p "Do you want to install X-UI (MHSanaei fork) now? [y/N]: " INSTALL_XUI
    if [[ "$INSTALL_XUI" =~ ^[Yy]$ ]]; then
        echo -e "${BLUE}Installing X-UI...${NC}"
        bash <(curl -Ls https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh)
        # Check again
        if [[ ! -f "$XUI_BIN" ]]; then
             echo -e "${RED}X-UI installation failed or was cancelled. Exiting.${NC}"
             exit 1
        fi
        echo -e "${GREEN}✅ X-UI installed successfully.${NC}"
    else
        echo -e "${RED}X-UI is required for this bot. Exiting.${NC}"
        exit 1
    fi
fi

# 3. Collect Credentials
echo -e "\n${BLUE}--- Configuration ---${NC}"
read -p "Enter Telegram Bot Token: " INPUT_TOKEN
while [[ -z "$INPUT_TOKEN" ]]; do
    echo -e "${RED}Token cannot be empty.${NC}"
    read -p "Enter Telegram Bot Token: " INPUT_TOKEN
done

read -p "Enter Admin Telegram ID: " INPUT_ADMIN_ID
while [[ -z "$INPUT_ADMIN_ID" ]]; do
    echo -e "${RED}Admin ID cannot be empty.${NC}"
    read -p "Enter Admin Telegram ID: " INPUT_ADMIN_ID
done

# Try to detect IP
DETECTED_IP=$(curl -s4 ifconfig.me)
read -p "Enter Server IP [$DETECTED_IP]: " INPUT_IP
if [[ -z "$INPUT_IP" ]]; then
    INPUT_IP="$DETECTED_IP"
fi

# 4. Prepare Bot Directory
BOT_DIR="$XUI_PATH/bot"
REPO_URL="https://github.com/prokazzzzza/x-ui.git"

echo -e "\n${BLUE}--- Setting up Bot ---${NC}"

# Install Git if missing
if ! command -v git &> /dev/null; then
    echo -e "Installing git..."
    apt-get update && apt-get install -y git
fi

# Install Python3 venv/pip if missing
if ! command -v python3 &> /dev/null; then
     echo -e "Installing python3..."
     apt-get update && apt-get install -y python3 python3-pip python3-venv
fi
# Ensure venv module is present (sometimes separate package on Debian/Ubuntu)
apt-get install -y python3-venv python3-pip

if [[ -d "$BOT_DIR" ]]; then
    echo -e "${YELLOW}Bot directory exists. Updating...${NC}"
    # We are inside /usr/local/x-ui/bot usually.
    # But the repo root is /usr/local/x-ui (in our dev setup).
    # However, for deployment on a clean machine, X-UI owns /usr/local/x-ui.
    # We should probably clone the repo to a temp dir and copy bot files?
    # OR clone into $BOT_DIR if we structure repo as just the bot?
    # CURRENT REPO STRUCTURE:
    # root/
    #   bot/
    #     bot.py
    #     ...
    #   x-ui.sh
    #   ...
    
    # So if we clone the repo to /tmp/x-ui-repo, we can copy /tmp/x-ui-repo/bot to /usr/local/x-ui/bot.
    
    rm -rf /tmp/x-ui-repo
    git clone "$REPO_URL" /tmp/x-ui-repo
    
    # Create target dir if not exists (it might exist from previous run)
    mkdir -p "$BOT_DIR"
    
    # Copy files
    cp -r /tmp/x-ui-repo/bot/* "$BOT_DIR/"
    rm -rf /tmp/x-ui-repo
else
    echo -e "${GREEN}Cloning repository...${NC}"
    rm -rf /tmp/x-ui-repo
    git clone "$REPO_URL" /tmp/x-ui-repo
    mkdir -p "$BOT_DIR"
    cp -r /tmp/x-ui-repo/bot/* "$BOT_DIR/"
    rm -rf /tmp/x-ui-repo
fi

# 5. Write .env
echo -e "Writing configuration..."
cat > "$BOT_DIR/.env" <<EOL
BOT_TOKEN=$INPUT_TOKEN
ADMIN_ID=$INPUT_ADMIN_ID
HOST_IP=$INPUT_IP
EOL

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
SERVICE_FILE="/etc/systemd/system/x-ui-bot.service"

cat > "$SERVICE_FILE" <<EOL
[Unit]
Description=Telegram Bot for X-UI
After=network.target x-ui.service

[Service]
Type=simple
WorkingDirectory=$BOT_DIR
ExecStart=$BOT_DIR/venv/bin/python3 bot.py
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOL

# Reload and Start
systemctl daemon-reload
systemctl enable x-ui-bot
systemctl restart x-ui-bot

# 8. Final Status
if systemctl is-active --quiet x-ui-bot; then
    echo -e "\n${GREEN}✅ Bot installed and started successfully!${NC}"
    echo -e "Logs: journalctl -u x-ui-bot -f"
else
    echo -e "\n${RED}❌ Bot failed to start.${NC}"
    systemctl status x-ui-bot
fi
