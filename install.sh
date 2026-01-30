#!/usr/bin/env bash
set -euo pipefail

url="${BOT_INSTALL_URL:-https://raw.githubusercontent.com/prokazzzzza/x-ui-Telegram_bot/main/bot/install_bot.sh}"

if ! command -v curl >/dev/null 2>&1; then
  echo "curl не найден. Установите curl и повторите попытку." >&2
  exit 1
fi

bash <(curl -fsSL "$url")
