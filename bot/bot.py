import logging
import base64
import sqlite3
import json
import uuid
import time
import datetime
import shutil
import os
import asyncio
import math
import html
import importlib
import random
import string
import re
import platform
import ipaddress
import socket
import hashlib
import threading
import http.server
from urllib.parse import urlparse
import zipfile
from collections import deque
from typing import Optional, Any, Dict, Iterable, Mapping, Protocol, TypeAlias, TypedDict
from io import BytesIO
from dotenv import load_dotenv
from zoneinfo import ZoneInfo
import httpx
import paramiko
from telegram import Update as TelegramUpdate, CallbackQuery as TelegramCallbackQuery, Message as TelegramMessage, PreCheckoutQuery as TelegramPreCheckoutQuery, SuccessfulPayment as TelegramSuccessfulPayment, User as TelegramUser, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButtonRequestUsers
from telegram.error import BadRequest, Forbidden, NetworkError, TimedOut
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, PreCheckoutQueryHandler, MessageHandler, filters
from telegram.request import HTTPXRequest

qrcode = importlib.import_module("qrcode")

class BotContext(Protocol):
    user_data: Dict[str, Any]
    chat_data: Dict[str, Any]
    bot_data: Dict[str, Any]
    bot: Any
    args: list[str]

class ContextTypes:
    DEFAULT_TYPE: TypeAlias = BotContext

class Message(TelegramMessage):
    from_user: TelegramUser
    successful_payment: TelegramSuccessfulPayment

class CallbackQuery(TelegramCallbackQuery):
    data: str
    message: Message

class Update(TelegramUpdate):
    callback_query: CallbackQuery
    message: Message
    pre_checkout_query: TelegramPreCheckoutQuery

class LogEntry(TypedDict):
    ip: str
    ts: int
    cc: Optional[str]

class SuspiciousUser(TypedDict):
    email: str
    ips: set[tuple[str, Optional[str]]]
    minutes: int

# Load environment variables
load_dotenv()

def log_action(message):
    try:
        if "Multi-sub server started on" in str(message):
            return
        timestamp = datetime.datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
        entry = f"{timestamp} - {message}\n"

        content = ""
        log_dir = os.path.dirname(LOG_FILE)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                content = f.read()

        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            f.write(entry + content)

        # Also print to console for debugging/journalctl
        print(f"LOG: {message}")
    except Exception as e:
        print(f"Logging failed: {e}")

def _purge_log_file_lines(needle: str) -> None:
    if not needle:
        return
    try:
        if not LOG_FILE or not os.path.exists(LOG_FILE):
            return
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        kept = [line for line in lines if needle not in line]
        if kept == lines:
            return
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.writelines(kept)
    except Exception:
        return

async def _systemctl(*args: str) -> None:
    try:
        proc = await asyncio.create_subprocess_exec("systemctl", *args)
        await proc.wait()
        if proc.returncode != 0:
            logging.error(f"systemctl {' '.join(args)} failed with code {proc.returncode}")
    except Exception as e:
        logging.error(f"systemctl {' '.join(args)} failed: {e}")

async def _systemctl_status(*args: str) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        stdout_s = stdout_b.decode(errors="replace").strip() if stdout_b else ""
        stderr_s = stderr_b.decode(errors="replace").strip() if stderr_b else ""
        combined = "\n".join([s for s in (stdout_s, stderr_s) if s]).strip()
        return int(proc.returncode or 0), combined
    except Exception as e:
        return 1, str(e)

async def _cmd_status(*args: str) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        stdout_b, stderr_b = await proc.communicate()
        stdout_s = stdout_b.decode(errors="replace").strip() if stdout_b else ""
        stderr_s = stderr_b.decode(errors="replace").strip() if stderr_b else ""
        combined = "\n".join([s for s in (stdout_s, stderr_s) if s]).strip()
        return int(proc.returncode or 0), combined
    except Exception as e:
        return 1, str(e)

def _extract_semver(text: str) -> Optional[str]:
    match = re.search(r"(?P<v>v?\d+\.\d+\.\d+)", text)
    if not match:
        return None
    value = match.group("v").lstrip("v")
    return value or None

def _version_tuple(version: str) -> tuple[int, int, int]:
    parts = version.split(".", 2)
    major = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0
    minor = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    patch = 0
    if len(parts) > 2:
        patch_match = re.match(r"(\d+)", parts[2])
        if patch_match:
            patch = int(patch_match.group(1))
    return major, minor, patch

def _slugify_filename(value: str) -> str:
    lowered = value.strip().lower()
    lowered = re.sub(r"[^a-z0-9]+", "_", lowered)
    lowered = lowered.strip("_")
    return lowered

def _escape_markdown(text: str) -> str:
    value = text.replace("\\", "\\\\")
    value = re.sub(r"([_*`\\[])", r"\\\1", value)
    return value

async def _get_local_xui_version() -> Optional[str]:
    candidates: list[tuple[str, ...]] = [
        ("x-ui", "-v"),
        ("/usr/local/x-ui/x-ui", "-v"),
        ("/usr/bin/x-ui", "-v"),
    ]
    for cmd in candidates:
        rc, out = await _cmd_status(*cmd)
        if rc != 0 or not out:
            continue
        first = out.splitlines()[0].strip()
        ver = _extract_semver(first)
        if ver:
            return ver
    return None

async def _get_local_xray_version() -> Optional[str]:
    candidates: list[tuple[str, ...]] = [
        ("/usr/local/x-ui/bin/xray-linux-amd64", "version"),
        ("xray", "version"),
    ]
    for cmd in candidates:
        rc, out = await _cmd_status(*cmd)
        if rc != 0 or not out:
            continue
        first = out.splitlines()[0].strip()
        ver = _extract_semver(first)
        if ver:
            return ver
    return None

async def _github_latest_version(owner: str, repo: str) -> Optional[str]:
    headers = {"Accept": "application/vnd.github+json"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            rel = await client.get(f"https://api.github.com/repos/{owner}/{repo}/releases/latest", headers=headers)
            if rel.status_code == 200:
                data = rel.json()
                tag = str(data.get("tag_name") or data.get("name") or "")
                ver = _extract_semver(tag)
                if ver:
                    return ver
            tags = await client.get(f"https://api.github.com/repos/{owner}/{repo}/tags?per_page=1", headers=headers)
            if tags.status_code == 200:
                items = tags.json()
                if isinstance(items, list) and items:
                    tag = str(items[0].get("name") or "")
                    ver = _extract_semver(tag)
                    if ver:
                        return ver
    except Exception:
        return None
    return None

def _get_xray_target_path() -> Optional[str]:
    candidates = (
        "/usr/local/x-ui/bin/xray-linux-amd64",
        "/usr/local/x-ui/bin/xray",
        "/usr/bin/xray",
    )
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None

def _select_xray_asset_name(machine: str) -> Optional[str]:
    m = machine.strip().lower()
    if m in {"x86_64", "amd64"}:
        return "Xray-linux-64.zip"
    if m in {"aarch64", "arm64"}:
        return "Xray-linux-arm64-v8a.zip"
    if m in {"armv7l", "armv7"}:
        return "Xray-linux-arm32-v7a.zip"
    if m in {"armv6l", "armv6"}:
        return "Xray-linux-arm32-v6.zip"
    if m in {"i386", "i686"}:
        return "Xray-linux-32.zip"
    return None

async def _update_xray_binary() -> tuple[bool, str]:
    target_path = _get_xray_target_path()
    if not target_path:
        return False, "Не найден путь к бинарнику Xray"

    before_rc, before_out = await _cmd_status(target_path, "version")
    before_ver = None
    if before_rc == 0 and before_out:
        before_ver = _extract_semver(before_out.splitlines()[0].strip())

    headers = {"Accept": "application/vnd.github+json"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            rel = await client.get(
                "https://api.github.com/repos/XTLS/Xray-core/releases/latest",
                headers=headers,
            )
            if rel.status_code != 200:
                return False, f"GitHub API вернул {rel.status_code}"
            release = rel.json()

            assets = release.get("assets") or []
            if not isinstance(assets, list) or not assets:
                return False, "Не найдены assets в релизе Xray-core"

            preferred_name = _select_xray_asset_name(platform.machine())
            chosen = None
            for asset in assets:
                if not isinstance(asset, dict):
                    continue
                name = str(asset.get("name") or "")
                url = str(asset.get("browser_download_url") or "")
                if not name.endswith(".zip"):
                    continue
                if preferred_name and name == preferred_name and url:
                    chosen = (name, url)
                    break
            if not chosen:
                for asset in assets:
                    if not isinstance(asset, dict):
                        continue
                    name = str(asset.get("name") or "")
                    url = str(asset.get("browser_download_url") or "")
                    if not url or not name.endswith(".zip"):
                        continue
                    if name.startswith("Xray-linux-") and "dgst" not in name:
                        chosen = (name, url)
                        break

            if not chosen:
                return False, "Не удалось выбрать архив Xray под текущую архитектуру"

            _, url = chosen
            resp = await client.get(url, follow_redirects=True)
            if resp.status_code != 200:
                return False, f"Скачивание Xray не удалось ({resp.status_code})"

        zip_bytes = resp.content
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            xray_member = None
            for info in zf.infolist():
                name = info.filename
                if name.endswith("/"):
                    continue
                base = name.rsplit("/", 1)[-1]
                if base == "xray":
                    xray_member = name
                    break
            if not xray_member:
                return False, "В архиве релиза не найден файл xray"
            xray_bin = zf.read(xray_member)

        dir_name = os.path.dirname(target_path)
        tmp_path = os.path.join(dir_name, f".xray.tmp.{uuid.uuid4().hex}")
        with open(tmp_path, "wb") as f:
            f.write(xray_bin)
        os.chmod(tmp_path, 0o755)
        os.replace(tmp_path, target_path)

        after_rc, after_out = await _cmd_status(target_path, "version")
        if after_rc != 0:
            return False, "Бинарник Xray обновлён, но команда version завершилась ошибкой"
        after_first = after_out.splitlines()[0].strip() if after_out else ""
        after_ver = _extract_semver(after_first) or after_first

        before_disp = before_ver or (before_out.splitlines()[0].strip() if before_out else "unknown")
        return True, f"{before_disp} → {after_ver}"
    except Exception as e:
        return False, str(e)

def _format_update_status(local_v: Optional[str], remote_v: Optional[str], lang: str) -> str:
    if not local_v:
        return t("updates_local_unknown", lang)
    if not remote_v:
        return t("updates_remote_unknown", lang).format(local=local_v)
    if _version_tuple(local_v) < _version_tuple(remote_v):
        return t("updates_available", lang).format(local=local_v, remote=remote_v)
    return t("updates_uptodate", lang).format(local=local_v)

# Disable root logger file handler to avoid noise
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.WARNING, # Only warnings/errors in console
    handlers=[
        logging.StreamHandler()
    ]
)

# Config
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID") or ""
ADMIN_ID_INT: Optional[int] = int(ADMIN_ID) if ADMIN_ID and ADMIN_ID.isdigit() else None

if not TOKEN:
    logging.error("BOT_TOKEN not found in environment variables")
    exit(1)

if not ADMIN_ID:
    logging.warning("ADMIN_ID not found in environment variables")

BOT_NAME = os.getenv("BOT_NAME") or ""
BOT_SLUG = _slugify_filename(BOT_NAME) if BOT_NAME else ""

DB_PATH = os.getenv("XUI_DB_PATH", "/etc/x-ui/x-ui.db")
BOT_DB_PATH = os.getenv(
    "BOT_DB_PATH",
    os.path.join("/usr/local/x-ui/bot", f"bot_data_{BOT_SLUG}.db" if BOT_SLUG else "bot_data.db"),
)
INBOUND_ID = 1
RU_BRIDGE_INBOUND_ID_RAW = os.getenv("RU_BRIDGE_INBOUND_ID")
RU_BRIDGE_INBOUND_ID: Optional[int] = (
    int(RU_BRIDGE_INBOUND_ID_RAW) if RU_BRIDGE_INBOUND_ID_RAW and RU_BRIDGE_INBOUND_ID_RAW.isdigit() else None
)
RU_BRIDGE_INBOUND_REMARK = (os.getenv("RU_BRIDGE_INBOUND_REMARK") or "").strip()
PUBLIC_KEY = os.getenv("PUBLIC_KEY")
IP = os.getenv("HOST_IP")
PORT_STR = os.getenv("HOST_PORT")
PORT: Optional[int] = int(PORT_STR) if PORT_STR and PORT_STR.isdigit() else None

SNI = os.getenv("SNI")
SID = os.getenv("SID")
RU_BRIDGE_HOST = (os.getenv("RU_BRIDGE_HOST") or "").strip()
RU_BRIDGE_PORT_RAW = os.getenv("RU_BRIDGE_PORT")
RU_BRIDGE_PORT: Optional[int] = int(RU_BRIDGE_PORT_RAW) if RU_BRIDGE_PORT_RAW and RU_BRIDGE_PORT_RAW.isdigit() else None
RU_BRIDGE_PUBLIC_KEY = (os.getenv("RU_BRIDGE_PUBLIC_KEY") or "").strip()
RU_BRIDGE_SNI = (os.getenv("RU_BRIDGE_SNI") or "").strip()
RU_BRIDGE_SID = (os.getenv("RU_BRIDGE_SID") or "").strip()
RU_BRIDGE_FLOW = (os.getenv("RU_BRIDGE_FLOW") or "").strip()
RU_BRIDGE_SPX = (os.getenv("RU_BRIDGE_SPX") or "").strip()
RU_BRIDGE_NETWORK = (os.getenv("RU_BRIDGE_NETWORK") or "xhttp").strip()
RU_BRIDGE_XHTTP_PATH = (os.getenv("RU_BRIDGE_XHTTP_PATH") or "/api/v1/update").strip()
RU_BRIDGE_XHTTP_MODE = (os.getenv("RU_BRIDGE_XHTTP_MODE") or "packet-up").strip()
RU_BRIDGE_SUB_HOST = (os.getenv("RU_BRIDGE_SUB_HOST") or "").strip()
RU_BRIDGE_SUB_PORT_RAW = os.getenv("RU_BRIDGE_SUB_PORT")
RU_BRIDGE_SUB_PORT: Optional[int] = int(RU_BRIDGE_SUB_PORT_RAW) if RU_BRIDGE_SUB_PORT_RAW and RU_BRIDGE_SUB_PORT_RAW.isdigit() else None
RU_BRIDGE_SUB_PATH = (os.getenv("RU_BRIDGE_SUB_PATH") or "").strip()
MOBILE_INBOUND_ID_RAW = os.getenv("MOBILE_INBOUND_ID")
MOBILE_INBOUND_ID = int(MOBILE_INBOUND_ID_RAW) if MOBILE_INBOUND_ID_RAW and MOBILE_INBOUND_ID_RAW.isdigit() else 0
MOBILE_SSH_HOST = (os.getenv("MOBILE_SSH_HOST") or "").strip()
MOBILE_SSH_PORT_RAW = os.getenv("MOBILE_SSH_PORT")
MOBILE_SSH_PORT = int(MOBILE_SSH_PORT_RAW) if MOBILE_SSH_PORT_RAW and MOBILE_SSH_PORT_RAW.isdigit() else 22
MOBILE_SSH_USER = (os.getenv("MOBILE_SSH_USER") or "").strip()
MOBILE_SSH_PASSWORD = (os.getenv("MOBILE_SSH_PASSWORD") or "").strip()
MOBILE_FLOW = (os.getenv("MOBILE_FLOW") or "").strip()
MOBILE_SUB_PUBLIC_URL = (os.getenv("MOBILE_SUB_PUBLIC_URL") or "").strip()
TIMEZONE = ZoneInfo("Europe/Moscow")
LOG_DIR = os.getenv("BOT_LOG_DIR", "/usr/local/x-ui/logs")
LOG_FILE = os.getenv(
    "BOT_LOG_FILE",
    os.path.join(LOG_DIR, f"bot_{BOT_SLUG}.log" if BOT_SLUG else "bot.log"),
)
REF_BONUS_DAYS = int(os.getenv("REF_BONUS_DAYS", 7))
XUI_SYSTEMD_SERVICE = os.getenv("XUI_SYSTEMD_SERVICE", "x-ui")
BOT_SYSTEMD_SERVICE = os.getenv("BOT_SYSTEMD_SERVICE", "x-ui-bot")
BACKUP_KEEP_FILES = int(os.getenv("BACKUP_KEEP_FILES", "20"))
BACKUP_KEEP_SETS = int(os.getenv("BACKUP_KEEP_SETS", "20"))
AUTO_SYNC_INTERVAL_SEC = int(os.getenv("AUTO_SYNC_INTERVAL_SEC", "300"))
MULTI_SUB_ENABLE_RAW = os.getenv("MULTI_SUB_ENABLE", "1")
MULTI_SUB_ENABLE = str(MULTI_SUB_ENABLE_RAW).strip().lower() in ("1", "true", "yes", "on")
MULTI_SUB_HOST = os.getenv("MULTI_SUB_HOST", "0.0.0.0")
MULTI_SUB_PORT_RAW = os.getenv("MULTI_SUB_PORT", "8788")
MULTI_SUB_PUBLIC_URL = (os.getenv("MULTI_SUB_PUBLIC_URL") or "").strip()
try:
    MULTI_SUB_PORT = int(MULTI_SUB_PORT_RAW)
except ValueError:
    MULTI_SUB_PORT = 8788

_RESTORE_LOCK = asyncio.Lock()

def _mobile_feature_enabled() -> bool:
    return bool(MOBILE_SSH_HOST and MOBILE_SSH_USER and MOBILE_SSH_PASSWORD)

def _mobile_missing_env_keys() -> list[str]:
    missing: list[str] = []
    if not MOBILE_SSH_HOST:
        missing.append("MOBILE_SSH_HOST")
    if not MOBILE_SSH_USER:
        missing.append("MOBILE_SSH_USER")
    if not MOBILE_SSH_PASSWORD:
        missing.append("MOBILE_SSH_PASSWORD")
    return missing

def _mobile_not_configured_text(tg_id: str, lang: str) -> str:
    base = t("mobile_not_configured", lang)
    is_admin = bool(ADMIN_ID and str(tg_id) == str(ADMIN_ID))
    if not is_admin:
        return base
    missing = _mobile_missing_env_keys()
    if missing:
        keys = "\n".join(f"- `{k}`" for k in missing)
        return f"{base}\n\nНе хватает переменных окружения в .env:\n{keys}"
    return f"{base}\n\nПеременные окружения заданы. Проверьте доступ по SSH и наличие VLESS+REALITY inbound на VPS."

ACCESS_LOG_PATH = "/usr/local/x-ui/access.log"
SUSPICIOUS_EVENTS_LOOKBACK_SEC = int(os.getenv("SUSPICIOUS_EVENTS_LOOKBACK_SEC", "86400"))

def load_config_from_db():
    global PUBLIC_KEY, PORT, SNI, SID
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT settings, stream_settings FROM inbounds WHERE id=?", (INBOUND_ID,))
        row = cursor.fetchone()
        conn.close()

        if row:
            # Parse settings for Port (wait, port is in 'port' column, need to fetch it)
            # Re-query with port
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT port, stream_settings FROM inbounds WHERE id=?", (INBOUND_ID,))
            row = cursor.fetchone()
            conn.close()

            if row:
                db_port = row[0]
                stream_settings = json.loads(row[1])
                reality = stream_settings.get('realitySettings', {})
                settings_inner = reality.get('settings', {})

                db_public_key = settings_inner.get('publicKey')
                db_sni_list = reality.get('serverNames', [])
                db_short_ids = reality.get('shortIds', [])

                # Update globals if found
                if db_port:
                    PORT = int(db_port)
                    logging.info(f"Loaded PORT from DB: {PORT}")
                if db_public_key:
                    PUBLIC_KEY = db_public_key
                    logging.info(f"Loaded PUBLIC_KEY from DB: {PUBLIC_KEY}")
                if db_sni_list:
                    SNI = db_sni_list[0]
                    logging.info(f"Loaded SNI from DB: {SNI}")
                if db_short_ids:
                    SID = db_short_ids[0]
                    logging.info(f"Loaded SID from DB: {SID}")

    except Exception as e:
        logging.error(f"Error loading config from DB: {e}")

# Try to load from DB to override defaults/env if available
load_config_from_db()


# Prices in Telegram Stars (XTR)
PRICES = {
    "1_week": {"amount": 40, "days": 7},
    "2_weeks": {"amount": 60, "days": 14},
    "1_month": {"amount": 1, "days": 30},
    "3_months": {"amount": 3, "days": 90},
    "ru_bridge": {"amount": 0, "days": 1},
    "1_year": {"amount": 5, "days": 365},
    "m_1_month": {"amount": 149, "days": 30},
    "m_3_months": {"amount": 399, "days": 90},
    "m_6_months": {"amount": 799, "days": 180},
    "m_1_year": {"amount": 1399, "days": 365},
}

# Localization
TEXTS = {
    "en": {
        "welcome": "Welcome to Maxi_VPN Bot! 🛡️\n\nPlease select your language:",
        "main_menu": "Welcome to Maxi_VPN! 🛡️\n\nPurchase a subscription using Telegram Stars to get high-speed secure access.",
        "btn_buy": "💎 Buy Subscription",
        "btn_mobile": "📱 3G/4G (Whitelist bypass 🔐)",
        "btn_config": "🚀 My Config",
        "btn_ru_bridge": "🧩 RU-Bridge",
        "btn_stats": "📊 My Stats",
        "btn_trial": "🆓 Free Trial",
        "btn_trial_3d": "🆓 Trial (3 Days)",
        "btn_mobile_trial_1d": "📱 3G/4G Trial (1 Day)",
        "trial_menu_title": "Choose a trial option:",
        "mobile_trial_used": "❌ *3G/4G trial already used*\n\nActivated: {date}",
        "btn_ref": "👥 Referrals",
        "btn_promo": "🎁 Redeem Promo",
        "shop_title": "🛒 Select a Plan:\n\nPay safely with Telegram Stars.",
        "btn_back": "🔙 Back",
        "mobile_menu_title": "📱 3G/4G Menu",
        "mobile_menu_desc": "🔐 **Whitelist bypass for mobile operators**\n\nHelps bypass mobile operator restrictions (\"white lists\") so your internet and favorite services keep working.",
        "btn_mobile_buy": "🛒 Buy 3G/4G",
        "btn_mobile_config": "📱 My 3G/4G Config",
        "btn_mobile_stats": "📶 My 3G/4G Stats",
        "mobile_shop_title": "🛒 Select a 3G/4G Plan:\n\nPay safely with Telegram Stars.",
        "mobile_not_configured": "📱 3G/4G is not configured. Please contact support.",
        "btn_how_to_buy_stars": "⭐️ How to buy Stars?",
        "how_to_buy_stars_text": "⭐️ **How to buy Telegram Stars?**\n\nTelegram Stars is a digital currency for payments.\n\n1. **Via @PremiumBot:** The best way. Just start the bot and choose a stars package.\n2. **In-app:** Purchase via Apple/Google (might be more expensive).\n3. **Fragment:** Buy with TON on Fragment.\n\nAfter buying stars, come back here and select a plan!",
        "label_1_week": "1 Week Subscription",
        "label_2_weeks": "2 Weeks Subscription",
        "label_1_month": "1 Month Subscription",
        "label_3_months": "3 Months Subscription",
        "label_6_months": "6 Months Subscription",
        "label_ru_bridge": "RU-Bridge (1 Day)",
        "label_1_year": "1 Year Subscription",
        "label_m_1_week": "3G/4G: 1 Week",
        "label_m_1_month": "3G/4G: 1 Month",
        "label_m_3_months": "3G/4G: 3 Months",
        "label_m_6_months": "3G/4G: 6 Months",
        "label_m_1_year": "3G/4G: 1 Year",
        "invoice_title": "Maxi_VPN Subscription",
        "invoice_title_mobile": "3G/4G Subscription",
        "success_created": "✅ Success! Subscription created.\n\n📅 New Expiry: {expiry}\n\nUse '🚀 My Config' to get your connection key.",
        "success_extended": "✅ Success! Subscription extended.\n\n📅 New Expiry: {expiry}\n\nUse '🚀 My Config' to get your connection key.",
        "success_updated": "✅ Success! Subscription updated.\n\n📅 New Expiry: {expiry}\n\nUse '🚀 My Config' to get your connection key.",
        "mobile_success_created": "✅ Success! 3G/4G subscription created.\n\n📅 New Expiry: {expiry}\n\nOpen '📱 3G/4G' to get your key.",
        "mobile_success_extended": "✅ Success! 3G/4G subscription extended.\n\n📅 New Expiry: {expiry}\n\nOpen '📱 3G/4G' to get your key.",
        "mobile_success_updated": "✅ Success! 3G/4G subscription updated.\n\n📅 New Expiry: {expiry}\n\nOpen '📱 3G/4G' to get your key.",
        "error_generic": "An error occurred. Please contact support.",
        "sub_expired": "⚠️ Subscription Expired\n\nYour subscription has expired. Please buy a new plan to restore access.",
        "sub_active": "✅ Your Subscription is Active\n\n📅 Expires: {expiry}\n\nKey:\n`{link}`",
        "sub_not_found": "❌ No Subscription Found\n\nYou don't have an active subscription. Please visit the shop.",
        "ru_bridge_sub_active": "✅ RU-Bridge Active\n\n📅 Expires: {expiry}\n\n{sub_block}",
        "ru_bridge_sub_expired": "⚠️ RU-Bridge Subscription Expired\n\nPlease buy a new plan to restore access.",
        "ru_bridge_sub_not_found": "❌ RU-Bridge Subscription Not Found\n\nPlease visit the shop.",
        "ru_bridge_sub_block": "Subscription:\n<code>{sub}</code>",
        "ru_bridge_sub_empty": "Subscription: not available",
        "ru_bridge_not_configured": "RU-Bridge is not configured. Please contact support.",
        "ru_bridge_success_created": "✅ RU-Bridge subscription created.\n\n📅 New expiry: {expiry}\n\nUse '🧩 RU-Bridge' to get your connection key.",
        "ru_bridge_success_extended": "✅ RU-Bridge subscription extended.\n\n📅 New expiry: {expiry}\n\nUse '🧩 RU-Bridge' to get your connection key.",
        "ru_bridge_success_updated": "✅ RU-Bridge subscription updated.\n\n📅 New expiry: {expiry}\n\nUse '🧩 RU-Bridge' to get your connection key.",
        "stats_title": "📊 Your Stats\n\n⬇️ Download: {down:.2f} GB\n⬆️ Upload: {up:.2f} GB\n📦 Total: {total:.2f} GB",
        "stats_no_sub": "No stats found. Subscription required.",
        "expiry_warning": "⚠️ Subscription Expiring Soon!\n\nYour VPN subscription will expire in less than 24 hours.\nPlease renew it to avoid service interruption.",
        "expiry_warning_7d": "⏳ **Your subscription ends in 7 hours**\n\nRenew now to keep your connection uninterrupted.\n\n👇 **Tap below to renew:**",
        "expiry_warning_3d": "⏳ **Your subscription ends in 3 hours**\n\nRenew now to keep your connection uninterrupted.\n\n👇 **Tap below to renew:**",
        "btn_renew": "💎 Renew Now",
        "btn_qrcode": "📱 QR code",
        "btn_instructions": "📚 Setup Instructions",
        "lang_sel": "Language selected: English 🇬🇧",
        "trial_used": "⚠️ Trial Already Used\n\nYou have already used your trial period.\nActivated: {date}",
        "trial_activated": "🎉 Trial Activated!\n\nYou have received 3 days of free access.\nCheck '🚀 My Config' to connect.",
        "ref_title": "👥 <b>Referral Program</b>\n\nInvite friends and get bonuses!\n10% of their deposits will be returned to you!\n\n🔗 Your Link:\n<code>{link}</code>\n\n🎁 You have invited: {count} users.",
        "promo_prompt": "🎁 Redeem Promo Code\n\nPlease enter your promo code:",
        "promo_success": "✅ Promo Code Redeemed! 😊\n\nAdded {days} days to your subscription.",
        "promo_invalid": "❌ Invalid or Expired Code",
        "promo_used": "⚠️ Code Already Used",
        "instr_menu": "📚 *Setup Instructions*\n\nChoose your device:",
        "btn_android": "📱 Android (v2RayTun)",
        "btn_ios": "🍎 iOS (V2Box)",
        "btn_pc": "💻 PC (Amnezia/Hiddify)",
        "btn_happ_ios": "💠 Happ iOS",
        "btn_happ_android": "💠 Happ Android",
        "btn_happ_desktop": "💠 Happ Desktop",
        "btn_happ_tv": "💠 Happ TV",
        "instr_android": "📱 *Android Setup*\n\n1. Install *[v2RayTun](https://play.google.com/store/apps/details?id=com.v2raytun.android)* from Google Play.\n2. Copy your key from '🚀 My Config'.\n3. Open v2RayTun -> Tap 'Import' -> 'Import from Clipboard'.\n4. Tap the connection button.",
        "instr_ios": "🍎 *iOS Setup*\n\n1. Install *[V2Box](https://apps.apple.com/app/v2box-v2ray-client/id6446814690)* from App Store.\n2. Copy your key from '🚀 My Config'.\n3. Open V2Box, it should detect the key automatically.\n4. Tap 'Import' and then swipe to connect.",
        "instr_pc": "💻 *PC Setup*\n\n1. Install *[AmneziaVPN](https://amnezia.org/)* or *[Hiddify](https://github.com/hiddify/hiddify-next/releases)*.\n2. Copy your key from '🚀 My Config'.\n3. Open the app and paste the key (Import from Clipboard).\n4. Connect.",
        "instr_happ_ios": "💠 *Happ for iOS*\n\nApp Store:\n- *[Happ Proxy Utility](https://apps.apple.com/us/app/happ-proxy-utility/id6504287215)*\n- *[Happ Proxy Utility Plus](https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973)*\n\nTestFlight:\n- *[Happ TestFlight](https://testflight.apple.com/join/XMls6Ckd)*\n- *[Happ Plus TestFlight](https://testflight.apple.com/join/1bKEcMub)*\n\n1. Copy your key from '🚀 My Config'.\n2. Open Happ and import the key from clipboard.\n3. Connect.",
        "instr_happ_android": "💠 *Happ for Android*\n\nGoogle Play:\n- *[Happ](https://play.google.com/store/apps/details?id=com.happproxy)*\n\nAPK:\n- *[Happ APK](https://github.com/Happ-proxy/happ-android/releases/latest/download/Happ.apk)*\n- *[Happ Beta APK](https://github.com/Happ-proxy/happ-android/releases/latest/download/Happ_beta.apk)*\n\n1. Copy your key from '🚀 My Config'.\n2. Open Happ and import the key from clipboard.\n3. Connect.",
        "instr_happ_desktop": "💠 *Happ for Desktop*\n\nWindows:\n- *[Happ Windows](https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe)*\n\nmacOS:\n- *[Happ Proxy Utility](https://apps.apple.com/us/app/happ-proxy-utility/id6504287215)*\n- *[Happ Proxy Utility Plus](https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973)*\n- *[Happ macOS DMG](https://github.com/Happ-proxy/happ-desktop/releases/latest/download/Happ.macOS.universal.dmg)*\n\nLinux:\n- *[Happ Linux DEB](https://github.com/Happ-proxy/happ-desktop/releases/latest/download/Happ.linux.x64.deb)*\n- *[Happ Linux RPM](https://github.com/Happ-proxy/happ-desktop/releases/latest/download/Happ.linux.x64.rpm)*\n\n1. Copy your key from '🚀 My Config'.\n2. Open Happ and import the key from clipboard.\n3. Connect.",
        "instr_happ_tv": "💠 *Happ for TV*\n\nAndroid TV:\n- *[Happ TV](https://play.google.com/store/apps/details?id=com.happproxy)*\n- *[Happ TV APK](https://github.com/Happ-proxy/happ-android/releases/latest/download/Happ.apk)*\n\n1. Copy your key from '🚀 My Config'.\n2. Open Happ and import the key from clipboard.\n3. Connect.",
        "plan_1_week": "1 Week",
        "plan_2_weeks": "2 Weeks",
        "plan_1_month": "1 Month",
        "plan_3_months": "3 Months",
        "plan_6_months": "6 Months",
        "plan_ru_bridge": "RU-Bridge (1 Day)",
        "plan_1_year": "1 Year",
        "plan_m_1_week": "3G/4G 1 Week",
        "plan_m_1_month": "3G/4G 1 Month",
        "plan_m_1_year": "3G/4G 1 Year",
        "plan_trial": "Trial (3 Days)",
        "plan_manual": "Manual",
        "plan_unlimited": "Unlimited",
        "sub_type_unknown": "Unknown",
        "mobile_sub_active_html": "✅ Your 3G/4G subscription is active\n\n📅 Expires: {expiry}",
        "mobile_sub_not_found": "❌ 3G/4G Subscription Not Found\n\nPlease visit the shop.",
        "mobile_sub_expired": "⚠️ 3G/4G Subscription Expired\n\nPlease buy a new plan to restore access.",
        "mobile_stats_title": "📊 3G/4G Stats\n\n⬇️ Download: {down}\n⬆️ Upload: {up}\n📦 Total: {total}",
        "stats_sub_type": "💳 Plan: {plan}",
        "rank_info_traffic": "\n🏆 You downloaded {traffic} via VPN.\nYour rank: #{rank} of {total}.",
        "traffic_info": "\n🏆 You downloaded {traffic} via VPN.",
        "rank_info_sub": "\n🏆 Your Rank (Subscription): #{rank} of {total}\n(Extend subscription to rank up!)",
        "btn_admin_stats": "📊 Statistics",
        "btn_admin_server": "🖥 Server",
        "btn_admin_ru_bridge": "🧩 RU-Bridge (test)",
        "btn_admin_health": "🩺 Health Check",
        "btn_admin_prices": "💰 Pricing",
        "btn_admin_promos": "🎁 Promo Codes",
        "btn_suspicious": "⚠️ Multi-IP",
        "suspicious_title": "⚠️ *Multi-IP History* (Page {page}/{total})\n\n",
        "suspicious_empty": "✅ No suspicious activity found.",
        "suspicious_entry": "📧 `{email}`\n🔌 IP: {count}\n{ips}\n\n",
        "btn_support": "🆘 Support",
        "support_title": "🆘 *Support*\n\nDescribe your problem in one message (you can attach a photo).\nAdministrator will answer you as soon as possible.",
        "support_sent": "✅ Message sent to support!",
        "support_reply_template": "🔔 *Reply from Support:*\n\n{text}",
        "admin_support_alert": "🆘 *New Support Ticket*\nUser: {user} (`{id}`)\n\n{text}",
        "admin_reply_hint": "↩️ Reply to this message to answer the user.",
        "admin_reply_sent": "✅ Answer sent to user.",
        "btn_leaderboard": "🏆 Leaderboard",
        "leaderboard_title_traffic": "🏆 *Traffic Leaderboard (Month)* (Page {page}/{total})\n\nRanking by traffic usage this month:",
        "leaderboard_title_sub": "🏆 *Subscription Leaderboard* (Page {page}/{total})\n\nRanking by remaining days:",
        "leaderboard_empty": "No data available.",
        "btn_admin_poll": "📊 Polls",
        "btn_admin_broadcast": "📢 Broadcast",
        "btn_admin_sales": "📜 Sales Log",
        "btn_admin_backup": "💾 Backup",
        "btn_admin_restore": "♻️ Restore",
        "btn_admin_logs": "📜 Logs",
        "btn_admin_remote_panels": "🧩 Panels",
        "btn_admin_remote_locations": "🌍 Locations",
        "btn_admin_remote_nodes": "🛰 Nodes/VPS",
        "remote_panels_title": "🧩 *Remote Panels*",
        "remote_locations_title": "🌍 *Locations*",
        "remote_nodes_title": "🛰 *Nodes/VPS*",
        "local_node_label": "🏠 Local VPS",
        "btn_remote_add": "➕ Add",
        "btn_remote_list": "📜 List",
        "btn_remote_check": "✅ Check",
        "btn_remote_sync": "🔄 Sync",
        "remote_nodes_sync_title": "🔄 *Sync Nodes*",
        "remote_panel_prompt": "Enter panel data:\n\n`Name(optional) | URL | Token(optional)`\n\nAuto name by IP:\n`| https://panel.example.com | token123`\n\nExample:\n`EU-Panel | https://panel.example.com | token123`",
        "remote_location_prompt": "Enter location data:\n\n`Name(optional) | HOST | PORT(optional) | SUB_HOST(optional) | SUB_PORT(optional) | SUB_PATH(optional)`\n\nOr full override:\n`Name(optional) | HOST | PORT | PBK | SNI | SID | FLOW(optional) | SUB_HOST(optional) | SUB_PORT(optional) | SUB_PATH(optional)`\n\nAuto name by IP:\n`| 1.2.3.4 | 443`\n\nExample:\n`Germany | de.example.com | 443`",
        "remote_node_prompt": "Enter node data:\n\n`Name | HOST or HOST:PORT | SSH_USER | SSH_PASSWORD`\n`Name | HOST | PORT | SSH_USER | SSH_PASSWORD`\n\nExample:\n`VPS-1 | 1.2.3.4:22 | root | pass`",
        "remote_panel_added": "✅ Panel added.",
        "remote_location_added": "✅ Location added.",
        "remote_node_added": "✅ Node added.",
        "remote_node_added_auto": "✅ Node added. Panel and location created automatically.",
        "remote_panel_deleted": "✅ Panel deleted.",
        "remote_location_deleted": "✅ Location deleted.",
        "remote_node_deleted": "✅ Node deleted.",
        "remote_node_sync_ok": "✅ Node synced.",
        "remote_node_sync_failed": "❌ Sync failed.",
        "remote_node_sync_missing_ssh": "❌ SSH credentials are missing for this node.",
        "remote_list_empty": "List is empty.",
        "remote_check_ok": "ok",
        "remote_check_fail": "fail",
        "btn_user_locations": "🌍 Other Locations",
        "user_locations_title": "🌍 *Other Locations*\n\nChoose a location:",
        "user_location_not_found": "Location not found.",
        "user_location_config": "✅ *Additional Config*\n\nLocation: {name}\n\nKey:\n<code>{key}</code>\n\n{sub_block}",
        "user_location_sub_block": "Subscription:\n<code>{sub}</code>",
        "user_location_sub_empty": "Subscription: not available",
        "user_location_ping": "Availability: {status}",
        "btn_main_menu_back": "🔙 Main Menu",
        "admin_menu_text": "👮‍♂️ *Admin Panel*\n\nSelect an action:",
        "btn_admin_promo_new": "➕ Create New",
        "btn_admin_promo_list": "📜 Active List",
        "btn_admin_flash": "⚡ Flash Promo",
        "btn_admin_promo_history": "👥 Usage History",
        "btn_admin_poll_new": "➕ Create Poll",
        "poll_ask_question": "Enter *poll question* (or click Cancel):",
        "poll_ask_options": "Send *poll options*, each on a new line (min 2).\n\nExample:\nYes\nNo\nMaybe",
        "poll_preview": "📊 *Poll Preview:*\n\n❓ Question: {question}\n\n🔢 Options:\n{options}\n\nSend this poll to all users?",
        "btn_send_poll": "✅ Send to All",
        "admin_server_title": "🖥 *Server Status*",
        "admin_server_nodes_title": "🌐 *Remote VPS*",
        "admin_server_node_title": "🖥 *Node Details*",
        "btn_server_nodes": "🌐 Nodes/VPS",
        "node_label": "Node",
        "health_title": "🩺 *Health Check*",
        "health_bot_db": "Bot DB",
        "health_xui_db": "X-UI DB",
        "health_access_log": "Access log",
        "health_support_bot": "Support bot",
        "health_main_bot": "Main bot",
        "health_ok": "ok",
        "health_fail": "fail",
        "health_inbound_missing": "inbound not found",
        "admin_server_live_title": "🖥 *Server Status (LIVE 🟢)*",
        "updates_title": "🧩 *Versions & Updates*",
        "xui_version_label": "🧩 *3x-ui:*",
        "xray_version_label": "🌐 *Xray:*",
        "updates_available": "✅ Local `{local}` → Update to `{remote}` available",
        "updates_uptodate": "✅ Local `{local}` (up to date)",
        "updates_local_unknown": "⚠️ Local version unknown",
        "updates_remote_unknown": "⚠️ Local `{local}` (can't check updates)",
        "btn_update_xui_xray": "⬆️ Update 3x-ui / Xray",
        "update_starting": "⬆️ Starting update...",
        "update_done": "✅ Update completed.\n\n{details}",
        "update_failed": "❌ Update failed.\n\n{details}",
        "cpu_label": "🧠 *CPU:*",
        "ram_label": "💾 *RAM:*",
        "swap_label": "💽 *SWAP:*",
        "uptime_label": "⏱ *Uptime:*",
        "disk_label": "💿 *Disk:*",
        "disk_used": "├ Used:",
        "disk_free": "├ Free:",
        "disk_total": "└ Total:",
        "traffic_speed_title": "📊 *Real-time Traffic Speed*",
        "upload_label": "⬆️ *Upload:*",
        "download_label": "⬇️ *Download:*",
        "updated_label": "🔄 Updated:",
        "live_remaining": "⏳ Remaining: {sec} sec.",
        "btn_live_monitor": "🟢 Live Monitor (30s)",
        "btn_refresh": "🔄 Refresh",
        "btn_stop": "⏹ Stop",
        "admin_prices_title": "💰 *Pricing Settings*\n\nSelect a plan to edit:",
        "price_change_prompt": "✏️ *Edit Price: {label}*\n\n Enter new price in Telegram Stars (integer):",
        "btn_cancel": "🔙 Cancel",
        "btn_change": "(Edit)",
        "stats_header": "📊 *Statistics*",
        "stats_users": "👥 *Telegram Users (Bot DB):*",
        "stats_vpn_users": "👥 *VPN Users (3x-ui):*",
        "stats_online": "⚡ *Online Users:*",
        "stats_clients": "🔌 *Total Clients:*",
        "stats_active": "✅ *Active Clients:*",
        "stats_trials": "🆓 *Trial Subs:*",
        "stats_expired_trials": "❌ *Expired Trials:*",
        "stats_revenue": "💰 *Revenue:*",
        "stats_sales": "🛒 *Sales:*",
        "btn_users_all": "👥 All",
        "btn_users_active": "🟢 Active",
        "btn_users_expiring": "⏳ Expiring Soon",
        "btn_users_online": "⚡ Online",
        "btn_users_trial": "🆓 Trials",
        "btn_users_expired": "🔴 Expired",
        "btn_cleanup_db": "🧹 Cleanup Bot DB",
        "btn_db_audit": "🔎 DB Audit / Sync",
        "btn_db_sync": "🧹 Sync & Clean",
        "btn_sync_nicks": "🔄 Sync Nicknames",
        "btn_sync_mobile_nicks": "🔄 Sync 3G/4G Nicknames",
        "db_audit_text": "🔎 *DB Audit*\n\nX-UI clients: {xui_clients}\nX-UI clients with TG ID: {xui_tg}\nX-UI clients without TG ID: {xui_no_tg}\n\nBot users (user_prefs): {bot_users}\nBot users not in X-UI: {bot_only}\nX-UI TG IDs missing in bot: {xui_only}\n\nTransactions total: {tx_total} ({tx_sum} ⭐️)\nTransactions not in X-UI: {tx_invalid} ({tx_invalid_sum} ⭐️)\n\nExamples:\nBot-only TG IDs: {bot_only_examples}\nX-UI clients without TG ID: {xui_no_tg_examples}\n\nChoose action below.",
        "db_sync_confirm_text": "⚠️ *Confirm DB Sync & Cleanup*\n\nThis will:\n- delete bot users not present in X-UI\n- delete transactions not belonging to X-UI users\n- delete traffic rows for `tg_*` emails not present in X-UI\n\nPlanned changes:\nUsers to delete: {users_deleted}\nTransactions to delete: {tx_deleted} ({tx_deleted_sum} ⭐️)\nTraffic rows to delete: {traffic_deleted}\n\nA backup will be created automatically.",
        "db_sync_done": "✅ Done.\n\nDeleted users: {users_deleted}\nDeleted transactions: {tx_deleted} ({tx_deleted_sum} ⭐️)\nDeleted traffic rows: {traffic_deleted}",
        "sync_start": "Syncing...",
        "sync_error_inbound": "❌ X-UI Inbound not found.",
        "sync_progress": "🔄 Syncing: {current}/{total}",
        "sync_complete": "✅ Sync complete!\n\nUpdated: {updated}\nFailed: {failed}\n\n⚠️ X-UI restarted to update names.",
        "sync_mobile_empty": "📭 No 3G/4G subscriptions found.",
        "users_list_title": "📋 *{title}*",
        "title_all": "All Clients",
        "title_active": "Active Clients",
        "title_expiring": "Expiring Soon (<7d)",
        "title_expired": "Expired",
        "title_online": "Online Clients",
        "title_trial": "Used Trial (All)",
        "btn_back_stats": "🔙 Back to Stats",
        "user_detail_email": "📧 Email:",
        "user_detail_tgid": "🆔 TG ID:",
        "user_detail_nick": "👤 Nickname:",
        "user_detail_enabled": "🔌 Enabled:",
        "user_detail_online": "📶 Connection:",
        "user_detail_sub": "📅 Subscription:",
        "user_detail_trial": "🆓 Trial:",
        "user_detail_expires": "⏳ Expires in:",
        "user_detail_up": "🔼 Upload:",
        "user_detail_down": "🔽 Download:",
        "user_detail_total": "📊 Total:",
        "user_detail_from": "of",
        "status_yes": "✅ Yes",
        "status_no": "❌ No",
        "status_online": "🟢 Online",
        "status_offline": "🔴 Offline",
        "trial_used_yes": "✅ Used",
        "trial_used_no": "❌ Not Used",
        "trial_unknown": "❓ Unknown",
        "hours_left": "Hours",
        "btn_reset_trial": "🔄 Reset Trial",
        "btn_rebind": "🔄 Rebind User",
        "btn_delete_user": "❌ Delete User",
        "btn_back_list": "🔙 Back to List",
        "msg_client_not_found": "❌ Client not found.",
        "msg_reset_success": "✅ Trial reset for {email}.",
        "msg_tgid_missing": "❌ Could not find User Telegram ID.",
        "rebind_title": "👤 *Rebind User*\nUUID: `{uid}`\n\nPlease select a user via the button below or send a contact.",
        "btn_select_user": "👤 Select User",
        "msg_rebind_success": "✅ *Success!*\nClient `{email}` rebound to Telegram ID `{tg_id}`.\n\n🔄 *Note:* Client email auto-updated to `{email}` for correct stats.\n\nX-UI restarted.",
        "msg_client_uuid_not_found": "❌ Client with UUID `{uid}` not found.",
        "promos_menu_title": "🎁 *Promo Code Management*\n\nSelect action:",
        "promo_list_empty": "📜 *Promo List*\n\nNo active promo codes.",
        "promo_list_title": "📜 *Active Promo Codes*\n\n",
        "promo_item_days": "⏳ Duration: {days} days",
        "promo_item_used": "👥 Used: {used} / {limit}",
        "promo_create_prompt": "🎁 *Create Promo Code*\n\nSend details in format:\n`CODE DAYS LIMIT`\n\nExample: `NEWYEAR 30 100`\n(LIMIT 0 = unlimited)",
        "promo_created": "✅ Promo `{code}` created for {days} days ({limit} uses).",
        "promo_format_error": "❌ Invalid format. Use: `CODE DAYS LIMIT`",
        "promo_delete_confirm": "❓ Are you sure you want to delete promo `{code}`?\nUsers will no longer be able to use it.",
        "promo_deleted": "✅ Promo deleted.",
        "promo_not_found": "❌ Promo not found.",
        "btn_delete": "Delete",
        "btn_yes": "Yes",
        "btn_no": "No",
        "flash_menu_title": "⚡ *Flash Promo*\n\nSelect a promo code to broadcast temporarily:",
        "btn_flash_delete_all": "🧨 Delete All Flash",
        "flash_select_prompt": "⚡ Selected Code: `{code}`\n\nEnter message lifetime in minutes (e.g., 60).\nMessage will be deleted for all users after this time.",
        "flash_broadcast_start": "⏳ Starting Flash Broadcast (ALL)...",
        "flash_msg_title": "🔥 CATCH THE PROMO CODE! 🔥",
        "flash_msg_body": "Hurry to redeem the secret code!\n\n👇 Click to reveal:\n<tg-spoiler><code>{code}</code></tg-spoiler>\n\n⏳ Expires at {time}\n(in {dur} min)",
        "flash_complete": "✅ Flash broadcast complete.\n\n📤 Sent: {sent}\n🚫 Failed: {blocked}\n⏱ Lifetime: {dur} min.",
        "flash_delete_success": "✅ Force deleted {count} messages.",
        "search_prompt": "🔍 *Search User*\n\nSend *Telegram ID* to search in database.",
        "search_error_digit": "❌ Error: ID must be digits.",
        "sales_log_empty": "📜 *Sales Log*\n\nNo sales yet.",
        "sales_log_title": "📜 *Sales Log (Last 20)*\n\n",
        "db_detail_title": "👤 *User Info (DB)*",
        "db_lang": "🌍 Language:",
        "db_reg_date": "📅 Activation Date:",
        "db_referrer": "👥 Referrer:",
        "btn_reset_trial_db": "🔄 Reset Trial (DB)",
        "btn_delete_db": "❌ Delete from DB",
        "msg_delete_db_success": "✅ User `{tg_id}` deleted from bot DB.",
        "action_cancelled": "🔙 Cancelled.",
        "broadcast_select_error": "⚠️ Please select at least one user!",
        "broadcast_menu": "📢 *Broadcast*\n\nSelect audience:",
        "btn_broadcast_all": "📢 All",
        "btn_broadcast_en": "🇮🇧 English (en)",
        "btn_broadcast_ru": "🇷🇺 Russian (ru)",
        "btn_broadcast_individual": "👥 Individual",
        "broadcast_individual_title": "📢 *Individual Broadcast*\n\nSelect users from list:",
        "btn_done_count": "✅ Done ({count})",
        "broadcast_confirm_prompt": "✅ Selected {count} recipients.\n\nNow send the message (text, photo, video, sticker) you want to broadcast.",
        "broadcast_general_prompt": "📢 *Broadcast ({target})*\n\nSend the message (text, photo, video, sticker) you want to broadcast.",
        "broadcast_start": "⏳ Broadcast started ({target})...",
        "broadcast_complete": "✅ Broadcast complete ({target}).\n\n📤 Sent: {sent}\n🚫 Failed (blocked): {blocked}",
        "btn_admin_panel": "👮‍♂️ Admin Panel",
        "btn_lang": "🌐 Language",
        "btn_back_admin": "🔙 Back to Admin",
        "logs_title": "📜 *Recent Bot Logs:*\n\n",
        "btn_clear_logs": "🧹 Clear Logs",
        "logs_cleared": "Logs cleared...",
        "logs_read_error": "Error reading logs.",
        "backup_starting": "Creating backup...",
        "backup_success": "✅ Backup created successfully in backups/ folder.",
        "backup_error": "❌ Error creating backup. Check logs.",
        "restore_menu_text": "♻️ *Restore from backup*\n\nSelect a backup set to restore. A safety backup will be created automatically.",
        "restore_no_backups": "♻️ *Restore from backup*\n\nNo backups found in backups/.",
        "restore_confirm": "⚠️ *Confirm restore*\n\nSelected: `{ts}`\n\nWill restore:\n{targets}\n\nA safety backup will be created automatically before restore.",
        "restore_preflight_title": "🔎 Backup validation:",
        "restore_in_progress": "⏳ Restore is already in progress. Please wait.",
        "restore_starting": "♻️ Restoring from backup...",
        "restore_done": "✅ Restore completed.\n\nRestored:\n{targets}\n\nSafety backup:\n{safety}",
        "restore_failed": "❌ Restore failed.\n\nError:\n{error}",
        "restore_page_text": "Page {page}/{total}",
        "btn_restore_confirm": "✅ Restore",
        "btn_restore_cancel": "🔙 Cancel",
        "btn_restart_xui": "🔄 Restart X-UI",
        "btn_restart_bot": "🔄 Restart Bot",
        "restart_starting": "🔄 Restarting X-UI...",
        "restart_done": "✅ X-UI restarted.",
        "restart_failed": "❌ Failed to restart X-UI.\n\nError:\n{error}",
        "bot_restart_starting": "🔄 Restarting bot...",
        "bot_restart_done": "✅ Bot restarted.",
        "bot_restart_failed": "❌ Failed to restart bot.\n\nError:\n{error}",
        "backup_menu_text": "💾 *Backups*\n\nCreate a new backup or restore from existing ones.",
        "btn_backup_create": "💾 Create backup",
        "upload_db_received": "📥 Backup uploaded: `{name}`\n\nHow should I restore it?",
        "upload_db_detected_xui": "Detected: X-UI database.",
        "upload_db_detected_bot": "Detected: BOT database.",
        "upload_db_detected_unknown": "Detected: unknown database type.",
        "btn_restore_as_xui": "♻️ Restore as X-UI DB",
        "btn_restore_as_bot": "♻️ Restore as BOT DB",
        "upload_restore_confirm": "⚠️ *Confirm restore*\n\nUploaded: `{name}`\n\nWill restore as: `{kind}`\n\n{check}\n\nA safety backup will be created automatically before restore.",
        "upload_restore_starting": "♻️ Restoring uploaded backup...",
        "upload_restore_done": "✅ Restore completed.\n\nRestored:\n{targets}\n\nSafety backup:\n{safety}",
        "upload_restore_failed": "❌ Restore failed.\n\nError:\n{error}",
        "upload_restore_missing": "⚠️ Uploaded file not found. Please upload again.",
        "btn_backup_delete": "🗑 Delete Backup",
        "backup_delete_confirm": "⚠️ *Confirm delete*\n\nSelected: `{ts}`\n\nWill delete:\n{targets}\n\nThis action cannot be undone.",
        "backup_delete_done": "✅ Backup deleted.\n\nDeleted:\n{targets}",
        "backup_delete_failed": "❌ Delete failed.\n\nError:\n{error}",
        "cleanup_db_done": "✅ Cleanup complete.\n\nDeleted users: {deleted}",
        "live_monitor_starting": "Starting Live Monitor...",
        "remaining_days": "⏳ Remaining: {days} days",
        "remaining_hours": "⏳ Remaining: {hours} hours",
        "error_invalid_id": "❌ Error: Invalid ID",
        "status_unbound": "Unbound",
        "sub_active_html": "✅ Your subscription is active\n\n📅 Expires: {expiry}",
        "sub_recommendation": "\n\n📋 Subscription Link:\n<code>{link}</code>",
        "sub_all_locations": "\n\n🌍 All locations ({count}):\n<code>{link}</code>",
        "sub_locations_list": "\n\n🌍 Available locations:\n{list}",
        "expiry_unlimited": "Unlimited",
        "stats_your_title": "📊 Your Statistics",
        "stats_today": "📅 Today:",
        "stats_week": "📅 This Week:",
        "stats_month": "📅 This Month:",
        "stats_total": "📦 Total:",
        "stats_expires": "⏳ Expires:",
        "unlimited_text": "♾️ Unlimited",
        "trial_expiring": "⚠️ **Your Trial ends in 24h!**\n\nDon't lose your secure connection. Subscribe now to keep access! 🚀",
        "trial_expired": "❌ **Your Trial has expired**\n\nWe hope you enjoyed the speed! 🚀\n\nGet full unlimited access starting from just 80 Star/month.\n\n👇 **Tap below to renew:**"
    },
    "ru": {
        "error_invalid_id": "❌ Ошибка: Некорректный ID",
        "status_unbound": "Не привязан",
        "sub_active_html": "✅ Ваша подписка активна\n\n📅 Истекает: {expiry}",
        "sub_recommendation": "\n\n📋 Ссылка подписки:\n<code>{link}</code>",
        "sub_all_locations": "\n\n🌍 Все локации ({count}):\n<code>{link}</code>",
        "sub_locations_list": "\n\n🌍 Доступные локации:\n{list}",
        "mobile_sub_active_html": "✅ Ваша 3G/4G подписка активна\n\n📅 Истекает: {expiry}",
        "mobile_sub_not_found": "❌ *3G/4G подписка не найдена*\n\nУ вас нет активной 3G/4G подписки. Перейдите в магазин.",
        "mobile_sub_expired": "⚠️ *3G/4G подписка истекла*\n\nОформите новый тариф для восстановления доступа.",
        "mobile_stats_title": "📊 *3G/4G статистика*\n\n⬇️ Скачано: {down}\n⬆️ Загружено: {up}\n📦 Всего: {total}",
        "expiry_unlimited": "Бессрочный",
        "stats_your_title": "📊 Ваша статистика",
        "stats_today": "📅 За сегодня:",
        "stats_week": "📅 За неделю:",
        "stats_month": "📅 За месяц:",
        "stats_total": "📦 Всего:",
        "stats_expires": "⏳ Истекает:",
        "unlimited_text": "♾️ Бессрочный",
        "live_monitor_starting": "Запуск Live мониторинга...",
        "trial_expiring": "⚠️ **Ваш пробный период истекает через 24ч!**\n\nНе теряйте доступ к защищенному соединению. Оформите подписку сейчас! 🚀",
        "trial_expired": "❌ **Ваш пробный период истек**\n\nНадеемся, вам понравилась скорость! 🚀\n\nПолучите полный безлимитный доступ всего от 80 Star/месяц.\n\n👇 **Нажмите ниже, чтобы продлить:**",
        "welcome": "Добро пожаловать в Maxi-VPN! 🛡️\n\nПожалуйста, выберите язык:",
        "main_menu": "🚀 Maxi-VPN — Твой пропуск в свободный интернет!\n\n⚡️ Высокая скорость, анонимность и доступ к любым сервисам.\n💎 Оплата в один клик через Telegram Stars.",
        "btn_buy": "💎 Купить подписку",
        "btn_mobile": "📱 3G/4G (Обход белых списков🔐)",
        "btn_config": "🚀 Мой конфиг",
        "btn_ru_bridge": "🧩 RU-Bridge",
        "btn_stats": "📊 Моя статистика",
        "btn_trial": "🆓 Пробный период",
        "btn_trial_3d": "🆓 Пробный (3 дня)",
        "btn_mobile_trial_1d": "📱 3G/4G пробный (1 день)",
        "trial_menu_title": "Выберите пробный период:",
        "mobile_trial_used": "❌ *Пробный 3G/4G уже использован*\n\nАктивация: {date}",
        "btn_ref": "👥 Рефералка",
        "btn_promo": "🎁 Промокод",
        "shop_title": "🛒 *Выберите план:*\n\nБезопасная оплата через Telegram Stars.",
        "btn_back": "🔙 Назад",
        "mobile_menu_title": "📱 *3G/4G меню*",
        "mobile_menu_desc": "🔐 **Обход белых списков у мобильных операторов**\n\nПозволяет обходить блокировку работы интернета и ваших любимых сервисов через мобильного оператора, обходя белые списки.",
        "btn_mobile_buy": "🛒 Купить 3G/4G",
        "btn_mobile_config": "📱 Мой 3G/4G конфиг",
        "btn_mobile_stats": "📶 Моя 3G/4G статистика",
        "mobile_shop_title": "🛒 *Выберите 3G/4G тариф:*\n\nБезопасная оплата через Telegram Stars.",
        "mobile_not_configured": "📱 3G/4G не настроен. Обратитесь в поддержку.",
        "btn_how_to_buy_stars": "⭐️ Как купить Звезды?",
        "how_to_buy_stars_text": "⭐️ **Как купить Telegram Stars?**\n\nTelegram Stars — это внутренняя валюта для оплаты цифровых товаров в телеграме\n\n1. **Через оффицаильного бота телеграма @PremiumBot**\nПросто запустите бота и выберите пакет звезд для покупки.\nПосле возвращайтесь и оформляйте подписку.",
        "label_1_week": "Подписка на 1 неделю",
        "label_2_weeks": "Подписка на 2 недели",
        "label_1_month": "Подписка на 1 месяц",
        "label_3_months": "Подписка на 3 месяца",
        "label_6_months": "Подписка на 6 месяцев",
        "label_ru_bridge": "RU-Bridge (1 день)",
        "label_1_year": "Подписка на 1 год",
        "label_m_1_week": "3G/4G: 1 неделя",
        "label_m_1_month": "3G/4G: 1 месяц",
        "label_m_3_months": "3G/4G: 3 месяца",
        "label_m_6_months": "3G/4G: 6 месяцев",
        "label_m_1_year": "3G/4G: 1 год",
        "invoice_title": "Maxi_VPN Подписка",
        "invoice_title_mobile": "3G/4G Подписка",
        "success_created": "✅ *Успешно!* Подписка создана.\n\n📅 Истекает: {expiry}\n\nНажмите '🚀 Мой конфиг', чтобы получить ключ.",
        "success_extended": "✅ *Успешно!* Подписка продлена.\n\n📅 Истекает: {expiry}\n\nНажмите '🚀 Мой конфиг', чтобы получить ключ.",
        "success_updated": "✅ *Успешно!* Подписка обновлена.\n\n📅 Истекает: {expiry}\n\nНажмите '🚀 Мой конфиг', чтобы получить ключ.",
        "mobile_success_created": "✅ *Успешно!* 3G/4G подписка создана.\n\n📅 Истекает: {expiry}\n\nОткройте '📱 3G/4G', чтобы получить ключ.",
        "mobile_success_extended": "✅ *Успешно!* 3G/4G подписка продлена.\n\n📅 Истекает: {expiry}\n\nОткройте '📱 3G/4G', чтобы получить ключ.",
        "mobile_success_updated": "✅ *Успешно!* 3G/4G подписка обновлена.\n\n📅 Истекает: {expiry}\n\nОткройте '📱 3G/4G', чтобы получить ключ.",
        "error_generic": "Произошла ошибка. Пожалуйста, свяжитесь с поддержкой.",
        "sub_expired": "⚠️ *Подписка истекла*\n\nВаша подписка закончилась. Пожалуйста, купите новый план для восстановления доступа.",
        "sub_active": "✅ *Ваша подписка активна*\n\n📅 Истекает: {expiry}\n\nКлюч:\n`{link}`",
        "sub_not_found": "❌ *Подписка не найдена*\n\nУ вас нет активной подписки. Пожалуйста, перейдите в магазин.",
        "ru_bridge_sub_active": "✅ RU-Bridge активен\n\n📅 Истекает: {expiry}\n\n{sub_block}",
        "ru_bridge_sub_expired": "⚠️ RU-Bridge подписка истекла\n\nОформите новый тариф для восстановления доступа.",
        "ru_bridge_sub_not_found": "❌ RU-Bridge подписка не найдена\n\nПерейдите в магазин.",
        "ru_bridge_sub_block": "Подписка:\n<code>{sub}</code>",
        "ru_bridge_sub_empty": "Подписка: недоступна",
        "ru_bridge_not_configured": "RU-Bridge не настроен. Обратитесь в поддержку.",
        "ru_bridge_success_created": "✅ RU-Bridge подписка создана.\n\n📅 Новый срок: {expiry}\n\nИспользуйте '🧩 RU-Bridge' для получения ключа.",
        "ru_bridge_success_extended": "✅ RU-Bridge подписка продлена.\n\n📅 Новый срок: {expiry}\n\nИспользуйте '🧩 RU-Bridge' для получения ключа.",
        "ru_bridge_success_updated": "✅ RU-Bridge подписка обновлена.\n\n📅 Новый срок: {expiry}\n\nИспользуйте '🧩 RU-Bridge' для получения ключа.",
        "stats_title": "📊 *Ваша статистика*\n\n⬇️ Скачано: {down:.2f} GB\n⬆️ Загружено: {up:.2f} GB\n📦 Всего: {total:.2f} GB",
        "stats_no_sub": "Статистика не найдена. Требуется подписка.",
        "expiry_warning": "⚠️ *Подписка скоро истекает!*\n\nВаша VPN подписка истечет менее чем через 24 часа.\nПожалуйста, продлите её, чтобы избежать отключения.",
        "expiry_warning_7d": "⏳ *До конца подписки 7 часов*\n\nПродлите сейчас, чтобы доступ не прерывался.\n\n👇 *Нажмите ниже, чтобы продлить:*",
        "expiry_warning_3d": "⏳ *До конца подписки 3 часа*\n\nПродлите сейчас, чтобы доступ не прерывался.\n\n👇 *Нажмите ниже, чтобы продлить:*",
        "btn_renew": "💎 Продлить сейчас",
        "btn_instructions": "📚 Инструкция по настройке",
        "btn_qrcode": "📱 QR код",
        "btn_lang": "🌐 Язык",
        "lang_sel": "Выбран язык: Русский 🇷🇺",
        "trial_used": "⚠️ *Пробный период уже использован*\n\nВы уже активировали свои 3 дня бесплатно.\nДата активации: {date}",
        "trial_activated": "🎉 *Пробный период активирован!*\n\nВам начислено 3 дня доступа.\nНажмите '🚀 Мой конфиг' для подключения.",
        "ref_title": "👥 <b>Реферальная программа</b>\n\nПриглашайте друзей и получайте бонусы!\n10% от их пополнений будет возвращаться Вам!\n\n🔗 Ваша ссылка:\n<code>{link}</code>\n\n🎁 Вы пригласили: {count} пользователей.",
        "promo_prompt": "🎁 *Активация промокода*\n\nПожалуйста, отправьте боту ваш промокод:",
        "promo_success": "✅ *Промокод активирован!* 😊\n\nДобавлено {days} дней к вашей подписке.",
        "promo_invalid": "❌ *Неверный или истекший код*",
        "promo_used": "⚠️ *Код уже использован вами*",
        "instr_menu": "📚 *Инструкция по настройке*\n\nВыберите ваше устройство:",
        "btn_android": "📱 Android (v2RayTun)",
        "btn_ios": "🍎 iOS (V2Box)",
        "btn_pc": "💻 PC (Amnezia/Hiddify)",
        "btn_all_devices": "💠 Все устройства (Happ)",
        "btn_happ_ios": "💠 Happ iOS",
        "btn_happ_android": "💠 Happ Android",
        "btn_happ_desktop": "💠 Happ Desktop",
        "btn_happ_tv": "💠 Happ TV",
        "instr_android": "📱 *Настройка Android*\n\n1. Скачайте *[v2RayTun](https://play.google.com/store/apps/details?id=com.v2raytun.android)* из Google Play.\n2. Скопируйте ваш ключ из '🚀 Мой конфиг'.\n3. Откройте v2RayTun -> нажмите 'Import' -> 'Import from Clipboard'.\n4. Нажмите кнопку подключения.",
        "instr_ios": "🍎 *Настройка iOS*\n\n1. Скачайте *[V2Box](https://apps.apple.com/app/v2box-v2ray-client/id6446814690)* из App Store.\n2. Скопируйте ваш ключ из '🚀 Мой конфиг'.\n3. Откройте V2Box, он должен автоматически предложить добавить ключ.\n4. Нажмите 'Import', выберите сервер и сдвиньте переключатель для подключения.",
        "instr_pc": "💻 *Настройка PC*\n\n1. Установите *[AmneziaVPN](https://amnezia.org/)* или *[Hiddify](https://github.com/hiddify/hiddify-next/releases)*.\n2. Скопируйте ваш ключ из '🚀 Мой конфиг'.\n3. Откройте приложение и вставьте ключ (Import from Clipboard).\n4. Подключитесь.",
        "instr_all_devices": "💠 *Настройка для всех устройств*\n\n1. Установите *[Happ](https://www.happ.su/main/ru)*.\n2. Скопируйте ваш ключ из '🚀 Мой конфиг'.\n3. Откройте Happ и импортируйте ключ из буфера.\n4. Подключитесь.",
        "instr_happ_ios": "💠 *Happ для iOS*\n\nApp Store:\n- *[Happ Proxy Utility](https://apps.apple.com/us/app/happ-proxy-utility/id6504287215)*\n- *[Happ Proxy Utility Plus](https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973)*\n\nTestFlight:\n- *[Happ TestFlight](https://testflight.apple.com/join/XMls6Ckd)*\n- *[Happ Plus TestFlight](https://testflight.apple.com/join/1bKEcMub)*\n\n1. Скопируйте ваш ключ из '🚀 Мой конфиг'.\n2. Откройте Happ и импортируйте ключ из буфера.\n3. Подключитесь.",
        "instr_happ_android": "💠 *Happ для Android*\n\nGoogle Play:\n- *[Happ](https://play.google.com/store/apps/details?id=com.happproxy)*\n\nAPK:\n- *[Happ APK](https://github.com/Happ-proxy/happ-android/releases/latest/download/Happ.apk)*\n- *[Happ Beta APK](https://github.com/Happ-proxy/happ-android/releases/latest/download/Happ_beta.apk)*\n\n1. Скопируйте ваш ключ из '🚀 Мой конфиг'.\n2. Откройте Happ и импортируйте ключ из буфера.\n3. Подключитесь.",
        "instr_happ_desktop": "💠 *Happ для Desktop*\n\nWindows:\n- *[Happ Windows](https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe)*\n\nmacOS:\n- *[Happ Proxy Utility](https://apps.apple.com/us/app/happ-proxy-utility/id6504287215)*\n- *[Happ Proxy Utility Plus](https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973)*\n- *[Happ macOS DMG](https://github.com/Happ-proxy/happ-desktop/releases/latest/download/Happ.macOS.universal.dmg)*\n\nLinux:\n- *[Happ Linux DEB](https://github.com/Happ-proxy/happ-desktop/releases/latest/download/Happ.linux.x64.deb)*\n- *[Happ Linux RPM](https://github.com/Happ-proxy/happ-desktop/releases/latest/download/Happ.linux.x64.rpm)*\n\n1. Скопируйте ваш ключ из '🚀 Мой конфиг'.\n2. Откройте Happ и импортируйте ключ из буфера.\n3. Подключитесь.",
        "instr_happ_tv": "💠 *Happ для TV*\n\nAndroid TV:\n- *[Happ TV](https://play.google.com/store/apps/details?id=com.happproxy)*\n- *[Happ TV APK](https://github.com/Happ-proxy/happ-android/releases/latest/download/Happ.apk)*\n\n1. Скопируйте ваш ключ из '🚀 Мой конфиг'.\n2. Откройте Happ и импортируйте ключ из буфера.\n3. Подключитесь.",
        "plan_1_week": "1 Неделя",
        "plan_2_weeks": "2 Недели",
        "plan_1_month": "1 Месяц",
        "plan_3_months": "3 Месяца",
        "plan_6_months": "6 Месяцев",
        "plan_ru_bridge": "RU-Bridge (1 день)",
        "plan_1_year": "1 Год",
        "plan_m_1_week": "3G/4G 1 Неделя",
        "plan_m_1_month": "3G/4G 1 Месяц",
        "plan_m_1_year": "3G/4G 1 Год",
        "plan_trial": "Пробный (3 дня)",
        "plan_manual": "Ручная",
        "plan_unlimited": "Бессрочный",
        "sub_type_unknown": "Не указан",
        "stats_sub_type": "💳 Тариф: {plan}",
        "remaining_days": "⏳ Осталось: {days} дн.",
        "remaining_hours": "⏳ Осталось: {hours} ч.",
        "rank_info_traffic": "\n🏆 Загружено данных через VPN: <code>{traffic}</code>\nВы занимаете {rank}-е место в рейтинге по трафику из {total}.",
        "traffic_info": "\n🏆 Загружено данных через VPN: <code>{traffic}</code>",
        "rank_info_sub": "\n🏆 Ваше место {rank}-е в рейтинге подписок из {total}.\n💡 Продлите подписку на больший срок, чтобы стать лидером!",
        "btn_admin_stats": "📊 Статистика",
        "btn_admin_server": "🖥 Сервер",
        "btn_admin_ru_bridge": "🧩 RU-Bridge (тест)",
        "btn_admin_health": "🩺 Проверка",
        "btn_admin_prices": "💰 Настройка цен",
        "btn_admin_promos": "🎁 Промокоды",
        "btn_admin_poll": "📊 Опросы",
        "btn_admin_broadcast": "📢 Рассылка",
        "btn_admin_sales": "📜 Журнал продаж",
        "btn_admin_backup": "💾 Бэкап",
        "btn_admin_restore": "♻️ Восстановить",
        "btn_admin_logs": "📜 Логи",
        "btn_admin_remote_panels": "🧩 Панели",
        "btn_admin_remote_locations": "🌍 Локации",
        "btn_admin_remote_nodes": "🛰 Узлы/VPS",
        "remote_panels_title": "🧩 *Удалённые панели*",
        "remote_locations_title": "🌍 *Локации*",
        "remote_nodes_title": "🛰 *Узлы/VPS*",
        "local_node_label": "🏠 Локальный VPS",
        "btn_remote_add": "➕ Добавить",
        "btn_remote_list": "📜 Список",
        "btn_remote_check": "✅ Проверить",
        "btn_remote_sync": "🔄 Синхронизировать",
        "remote_nodes_sync_title": "🔄 *Синхронизация узлов*",
        "remote_panel_prompt": "Введите данные панели:\n\n`Имя(опционально) | URL | Токен(опционально)`\n\nАвто‑имя по IP:\n`| https://panel.example.com | token123`\n\nПример:\n`EU-Panel | https://panel.example.com | token123`",
        "remote_location_prompt": "Введите данные локации:\n\n`Имя(опц) | HOST | PORT(опц) | SUB_HOST(опц) | SUB_PORT(опц) | SUB_PATH(опц)`\n\nЛибо полный режим:\n`Имя(опц) | HOST | PORT | PBK | SNI | SID | FLOW(опц) | SUB_HOST(опц) | SUB_PORT(опц) | SUB_PATH(опц)`\n\nАвто‑имя по IP:\n`| 1.2.3.4 | 443`\n\nПример:\n`Germany | de.example.com | 443`",
        "remote_node_prompt": "Введите данные узла:\n\n`Имя | HOST или HOST:PORT | SSH_USER | SSH_PASSWORD`\n`Имя | HOST | PORT | SSH_USER | SSH_PASSWORD`\n\nПример:\n`VPS-1 | 1.2.3.4:22 | root | pass`",
        "remote_panel_added": "✅ Панель добавлена.",
        "remote_location_added": "✅ Локация добавлена.",
        "remote_node_added": "✅ Узел добавлен.",
        "remote_node_added_auto": "✅ Узел добавлен. Панель и локация созданы автоматически.",
        "remote_panel_deleted": "✅ Панель удалена.",
        "remote_location_deleted": "✅ Локация удалена.",
        "remote_node_deleted": "✅ Узел удалён.",
        "remote_node_sync_ok": "✅ Узел синхронизирован.",
        "remote_node_sync_failed": "❌ Синхронизация не удалась.",
        "remote_node_sync_missing_ssh": "❌ Для узла не заданы SSH‑логин/пароль.",
        "remote_list_empty": "Список пуст.",
        "remote_check_ok": "ok",
        "remote_check_fail": "fail",
        "btn_user_locations": "🌍 Другие локации",
        "user_locations_title": "🌍 *Другие локации*\n\nВыберите локацию:",
        "user_location_not_found": "Локация не найдена.",
        "user_location_config": "✅ *Дополнительный конфиг*\n\nЛокация: {name}\n\nКлюч:\n<code>{key}</code>\n\n{sub_block}",
        "user_location_sub_block": "Подписка:\n<code>{sub}</code>",
        "user_location_sub_empty": "Подписка: недоступна",
        "user_location_ping": "Доступность: {status}",
        "btn_main_menu_back": "🔙 Главное меню",
        "btn_support": "🆘 Поддержка",
        "support_title": "🆘 *Техническая поддержка*\n\nОпишите вашу проблему одним сообщением (можно прикрепить фото).\nАдминистратор ответит вам в ближайшее время.",
        "support_sent": "✅ Сообщение отправлено в поддержку!",
        "support_reply_template": "🔔 *Ответ от поддержки:*\n\n{text}",
        "admin_support_alert": "🆘 *Новый тикет*\nПользователь: {user} (`{id}`)\n\n{text}",
        "admin_reply_hint": "↩️ Ответьте на это сообщение (Reply), чтобы написать пользователю.",
        "admin_reply_sent": "✅ Ответ отправлен пользователю.",
        "admin_menu_text": "👮‍♂️ *Админ панель*\n\nВыберите действие:",
        "btn_admin_promo_new": "➕ Создать новый",
        "btn_admin_promo_list": "📜 Список активных",
        "btn_admin_flash": "⚡ Flash Промо",
        "btn_admin_promo_history": "👥 Использования",
        "btn_admin_poll_new": "➕ Создать опрос",
        "poll_ask_question": "Введите *вопрос* для голосования (или нажмите Отмена):",
        "poll_ask_options": "Отправьте *варианты ответов*, каждый с новой строки (минимум 2).\n\nПример:\nДа\nНет\nВозможно",
        "poll_preview": "📊 *Предпросмотр опроса:*\n\n❓ Вопрос: {question}\n\n🔢 Варианты:\n{options}\n\nОтправить этот опрос всем пользователям?",
        "poll_title": "Опрос",
        "poll_total_votes": "Всего голосов",
        "poll_vote_registered": "✅ Ваш голос учтен!",
        "btn_send_poll": "✅ Отправить всем",
        "admin_server_title": "🖥 *Состояние сервера*",
        "admin_server_nodes_title": "🌐 *Удалённые VPS*",
        "admin_server_node_title": "🖥 *Параметры узла*",
        "btn_server_nodes": "🌐 Узлы/VPS",
        "node_label": "Узел",
        "health_title": "🩺 *Проверка здоровья*",
        "health_bot_db": "БД бота",
        "health_xui_db": "БД X-UI",
        "health_access_log": "Журнал access.log",
        "health_support_bot": "Support bot",
        "health_main_bot": "Основной бот",
        "health_ok": "ок",
        "health_fail": "ошибка",
        "health_inbound_missing": "inbound не найден",
        "admin_server_live_title": "🖥 *Состояние сервера (LIVE 🟢)*",
        "updates_title": "🔄 *Версии и обновления*",
        "xui_version_label": "🧩 *3x-ui:*",
        "xray_version_label": "🌐 *Xray:*",
        "updates_available": "✅ Локально `{local}` → 🆕 обновление `{remote}`",
        "updates_uptodate": "✅ Локально `{local}` (актуально)",
        "updates_local_unknown": "⚠️ Локальная версия неизвестна",
        "updates_remote_unknown": "⚠️ Локально `{local}` (не удалось проверить обновления)",
        "btn_update_xui_xray": "⬆️ Обновить 3x-ui / Xray",
        "update_starting": "⬆️ Запускаю обновление...",
        "update_done": "✅ Обновление выполнено.\n\n{details}",
        "update_failed": "❌ Обновление не удалось.\n\n{details}",
        "cpu_label": "🧠 *CPU:*",
        "ram_label": "💾 *RAM:*",
        "swap_label": "💽 *Swap:*",
        "uptime_label": "⏱ *Аптайм:*",
        "disk_label": "💿 *Disk:*",
        "disk_used": "├ Использовано:",
        "disk_free": "├ Свободно:",
        "disk_total": "└ Всего:",
        "traffic_speed_title": "📊 *Общая скорость передачи трафика в реальном времени*",
        "upload_label": "⬆️ *Отправка:*",
        "download_label": "⬇️ *Загрузка:*",
        "updated_label": "🔄 Обновлено:",
        "live_remaining": "⏳ Осталось: {sec} сек.",
        "btn_live_monitor": "🟢 Live Мониторинг (30 сек)",
        "btn_refresh": "🔄 Обновить",
        "btn_stop": "⏹ Стоп",
        "admin_prices_title": "💰 *Настройка цен*\n\nВыберите тариф для изменения стоимости:",
        "price_change_prompt": "✏️ *Изменение цены: {label}*\n\n Введите новую стоимость в Telegram Stars (целое число):",
        "btn_cancel": "🔙 Отмена",
        "btn_change": "(Изменить)",
        "stats_header": "📊 *Статистика*",
        "stats_users": "👥 *Пользователи Telegram (бот):*",
        "stats_vpn_users": "👥 *Пользователи VPN (3x-ui):*",
        "stats_online": "⚡ *Пользователи онлайн:*",
        "stats_clients": "🔌 *Всего клиентов:*",
        "stats_active": "✅ *Активные клиенты:*",
        "stats_trials": "🆓 *Пробные подписки:*",
        "stats_expired_trials": "❌ *Истекшие пробные:*",
        "stats_revenue": "💰 *Выручка:*",
        "stats_sales": "🛒 *Продажи:*",
        "btn_users_all": "👥 Все",
        "btn_users_active": "🟢 Активные",
        "btn_users_expiring": "⏳ Скоро истекают",
        "btn_users_online": "⚡ Онлайн",
        "btn_users_trial": "🆓 Пробный период",
        "btn_users_expired": "🔴 Истекшие",
        "btn_cleanup_db": "🧹 Очистить базу бота",
        "btn_db_audit": "🔎 Аудит / синхронизация БД",
        "btn_db_sync": "🧹 Согласовать и очистить",
        "btn_sync_nicks": "🔄 Обновить ники",
        "btn_sync_mobile_nicks": "🔄 Обновить ники 3G/4G",
        "db_audit_text": "🔎 *Аудит БД*\n\nКлиенты X-UI: {xui_clients}\nКлиенты X-UI с TG ID: {xui_tg}\nКлиенты X-UI без TG ID: {xui_no_tg}\n\nПользователи бота (user_prefs): {bot_users}\nПользователи бота вне X-UI: {bot_only}\nTG IDs из X-UI отсутствуют в боте: {xui_only}\n\nТранзакции всего: {tx_total} ({tx_sum} ⭐️)\nТранзакции вне X-UI: {tx_invalid} ({tx_invalid_sum} ⭐️)\n\nПримеры:\nBot-only TG IDs: {bot_only_examples}\nX-UI клиенты без TG ID: {xui_no_tg_examples}\n\nВыберите действие ниже.",
        "db_sync_confirm_text": "⚠️ *Подтвердите согласование и очистку БД*\n\nБудет выполнено:\n- удаление пользователей бота, отсутствующих в X-UI\n- удаление транзакций, не принадлежащих пользователям X-UI\n- удаление traffic-строк по email `tg_*`, которых нет в X-UI\n\nПлан изменений:\nУдалить пользователей: {users_deleted}\nУдалить транзакции: {tx_deleted} ({tx_deleted_sum} ⭐️)\nУдалить traffic-строк: {traffic_deleted}\n\nПеред очисткой автоматически будет создан бэкап.",
        "db_sync_done": "✅ Готово.\n\nУдалено пользователей: {users_deleted}\nУдалено транзакций: {tx_deleted} ({tx_deleted_sum} ⭐️)\nУдалено traffic-строк: {traffic_deleted}",
        "sync_start": "Синхронизация...",
        "sync_error_inbound": "❌ X-UI Inbound not found.",
        "sync_progress": "🔄 Синхронизация: {current}/{total}",
        "sync_complete": "✅ Синхронизация завершена!\n\nОбновлено: {updated}\nОшибок: {failed}\n\n⚠️ X-UI был перезапущен для обновления имен в панели.",
        "sync_mobile_empty": "📭 3G/4G подписок не найдено.",
        "users_list_title": "📋 *{title}*",
        "title_all": "Все клиенты",
        "title_active": "Активные клиенты",
        "title_expiring": "Скоро истекают (<7д)",
        "title_expired": "Истекшие",
        "title_online": "Онлайн клиенты",
        "title_trial": "Использовали пробный (Все)",
        "btn_back_stats": "🔙 Назад к статистике",
        "user_detail_email": "📧 Email:",
        "user_detail_tgid": "🆔 TG ID:",
        "user_detail_nick": "👤 Никнейм:",
        "user_detail_enabled": "🔌 Включен:",
        "user_detail_online": "📶 Соединение:",
        "user_detail_sub": "📅 Подписка:",
        "user_detail_trial": "🆓 Пробный период:",
        "user_detail_expires": "⏳ Истекает через:",
        "user_detail_up": "🔼 Исходящий трафик:",
        "user_detail_down": "🔽 Входящий трафик:",
        "user_detail_total": "📊 Всего:",
        "user_detail_from": "из",
        "user_detail_limit_ip": "📱 Лимит устройств:",
        "btn_edit_limit_ip": "📱 Изменить лимит устройств",
        "limit_ip_prompt": "📱 *Изменение лимита устройств*\n\nТекущий лимит: {limit}\n\nВведите новый лимит (0 = Бессрочный):",
        "limit_ip_success": "✅ Лимит устройств обновлен: {limit}",
        "limit_ip_error": "❌ Ошибка при обновлении лимита.",
        "limit_ip_invalid": "❌ Неверное число. Введите целое число.",
        "btn_ip_history": "📜 История IP",
        "ip_history_title": "📜 *История IP подключений*\n\nПользователь: `{email}`\n\n",
        "ip_history_empty": "История подключений не найдена.",
        "ip_history_entry": "{flag} `{ip}` ({country})\n🕒 {time}\n",
        "btn_suspicious": "⚠️ Мульти-IP",
        "btn_leaderboard": "🏆 Топ пользователей",
        "leaderboard_title_traffic": "🏆 *Рейтинг по трафику (Месяц)* (Стр. {page}/{total})\n\nТоп пользователей по потреблению в этом месяце:",
        "leaderboard_title_sub": "🏆 *Рейтинг подписок* (Стр. {page}/{total})\n\nТоп пользователей по длительности подписки:",
        "leaderboard_empty": "Нет данных.",
        "suspicious_title": "⚠️ *История мульти-подключений* (Стр. {page}/{total})\n\n",
        "suspicious_empty": "✅ Подозрительных активностей не обнаружено.",
        "suspicious_entry": "📧 `{email}`\n🔌 IP: {count}\n{ips}\n\n",
        "status_yes": "✅ Да",
        "status_no": "❌ Нет",
        "status_online": "🟢 Онлайн",
        "status_offline": "🔴 Офлайн",
        "trial_used_yes": "✅ Использован",
        "trial_used_no": "❌ Не использован",
        "trial_unknown": "❓ Неизвестно",
        "hours_left": "Часов",
        "sales_log_error": "❌ Ошибка при загрузке лога.",
        "btn_reset_trial": "🔄 Сбросить пробный период",
        "btn_rebind": "🔄 Перепривязать пользователя",
        "btn_delete_user": "❌ Удалить пользователя",
        "btn_back_list": "🔙 Назад к списку",
        "msg_client_not_found": "❌ Клиент не найден.",
        "msg_reset_success": "✅ Пробный период для {email} сброшен.",
        "msg_tgid_missing": "❌ Не удалось найти Telegram ID пользователя.",
        "rebind_title": "👤 *Перепривязка пользователя*\nUUID: `{uid}`\n\nПожалуйста, выберите пользователя через кнопку ниже или отправьте контакт.",
        "btn_select_user": "👤 Выбрать пользователя",
        "msg_rebind_success": "✅ *Успешно!*\nКлиент `{email}` перепривязан к Telegram ID `{tg_id}`.\n\n🔄 *Внимание:* Для корректного отображения статистики и работы подписки, бот автоматически обновил email клиента на `{email}`.\n\nX-UI перезапущен.",
        "msg_client_uuid_not_found": "❌ Клиент с UUID `{uid}` не найден.",
        "promos_menu_title": "🎁 *Управление промокодами*\n\nВыберите действие:",
        "promo_list_empty": "📜 *Список промокодов*\n\nНет активных промокодов.",
        "promo_list_title": "📜 *Активные промокоды*\n\n",
        "promo_item_days": "⏳ Срок: {days} дн.",
        "promo_item_used": "👥 Использовано: {used} / {limit}",
        "promo_create_prompt": "🎁 *Создать промокод*\n\nОтправьте детали промокода в формате:\n`CODE DAYS LIMIT`\n\nПример: `NEWYEAR 30 100`\n(LIMIT 0 = безлимит)",
        "promo_created": "✅ Промокод <code>{code}</code> создан на {days} дн. ({limit} активаций).",
        "promo_format_error": "❌ Неверный формат. Используйте: `КОД ДНИ ЛИМИТ`",
        "promo_delete_confirm": "❓ Вы уверены, что хотите удалить промокод `{code}`?\nПользователи больше не смогут его использовать.",
        "promo_deleted": "✅ Промокод удален.",
        "promo_not_found": "❌ Промокод не найден.",
        "btn_delete": "Удалить",
        "btn_yes": "Да",
        "btn_no": "Нет",
        "flash_menu_title": "⚡ *Flash Промокод*\n\nВыберите промокод, который хотите отправить во временной рассылке:",
        "btn_flash_delete_all": "🧨 Удалить все Flash",
        "flash_select_prompt": "⚡ Выбран промокод: `{code}`\n\nВведите время жизни сообщения в минутах (например: 60).\nПо истечении этого времени сообщение будет удалено у всех пользователей.",
        "flash_broadcast_start": "⏳ Запуск Flash-рассылки (ВСЕМ)...",
        "flash_msg_title": "🔥 УСПЕЙ ПОЙМАТЬ ПРОМОКОД! 🔥",
        "flash_msg_body": "Успей активировать секретный промокод!\n\n👇 Нажми, чтобы увидеть:\n<tg-spoiler><code>{code}</code></tg-spoiler>\n\n⏳ Предложение сгорит в {time}\n(через {dur} мин)",
        "flash_complete": "✅ Flash-рассылка завершена.\n\n📤 Отправлено: {sent}\n🚫 Не доставлено: {blocked}\n⏱ Время жизни: {dur} мин.",
        "flash_delete_success": "✅ Принудительно удалено {count} сообщений.",
        "search_prompt": "🔍 *Поиск пользователя*\n\nОтправьте *Telegram ID* пользователя для поиска в базе данных.",
        "search_error_digit": "❌ Ошибка: ID должен состоять из цифр.",
        "sales_log_empty": "📜 *Журнал продаж*\n\nПродаж пока нет.",
        "sales_log_title": "📜 *Журнал продаж (последние 20)*\n\n",
        "db_detail_title": "👤 *Информация о пользователе (DB)*",
        "db_lang": "🌍 Язык:",
        "db_reg_date": "📅 Дата активации:",
        "db_referrer": "👥 Реферер:",
        "btn_reset_trial_db": "🔄 Сбросить пробный период (DB)",
        "btn_delete_db": "❌ Удалить из базы",
        "msg_delete_db_success": "✅ Пользователь `{tg_id}` удален из базы бота.",
        "action_cancelled": "🔙 Отменено.",
        "broadcast_select_error": "⚠️ Выберите хотя бы одного пользователя!",
        "broadcast_menu": "📢 *Рассылка сообщений*\n\nВыберите аудиторию для рассылки:",
        "btn_broadcast_all": "📢 Всем",
        "btn_broadcast_en": "🇮🇧 Английский (en)",
        "btn_broadcast_ru": "🇷🇺 Русский (ru)",
        "btn_broadcast_individual": "👥 Индивидуально",
        "broadcast_individual_title": "📢 *Индивидуальная рассылка*\n\nВыберите пользователей из списка:",
        "btn_done_count": "✅ Готово ({count})",
        "broadcast_confirm_prompt": "✅ Выбрано {count} получателей.\n\nТеперь отправьте сообщение (текст, фото, видео, стикер), которое хотите отправить.",
        "broadcast_general_prompt": "📢 *Рассылка ({target})*\n\nОтправьте сообщение (текст, фото, видео, стикер), которое хотите отправить.",
        "broadcast_start": "⏳ Рассылка запущена ({target})...",
        "broadcast_complete": "✅ Рассылка завершена ({target}).\n\n📤 Отправлено: {sent}\n🚫 Не доставлено (бот заблокирован): {blocked}",
        "btn_admin_panel": "👮‍♂️ Админ панель",
        "btn_back_admin": "🔙 В админ панель",
        "logs_title": "📜 *Последние логи бота:*\n\n",
        "btn_clear_logs": "🧹 Очистить логи",
        "logs_cleared": "Очистка логов...",
        "logs_read_error": "Ошибка при чтении логов.",
        "backup_starting": "Создание резервной копии...",
        "backup_success": "✅ Резервная копия успешно создана в папке backups/",
        "backup_error": "❌ Ошибка при создании резервной копии. См. логи.",
        "restore_menu_text": "♻️ *Восстановление из бэкапа*\n\nВыберите набор бэкапа. Перед восстановлением автоматически будет создан страховочный бэкап.",
        "restore_no_backups": "♻️ *Восстановление из бэкапа*\n\nВ папке backups/ нет доступных бэкапов.",
        "restore_confirm": "⚠️ *Подтверждение восстановления*\n\nВыбран: `{ts}`\n\nБудет восстановлено:\n{targets}\n\nПеред восстановлением автоматически будет создан страховочный бэкап.",
        "restore_preflight_title": "🔎 Проверка бэкапа:",
        "restore_in_progress": "⏳ Восстановление уже выполняется. Подождите.",
        "restore_starting": "♻️ Восстанавливаю из бэкапа...",
        "restore_done": "✅ Восстановление завершено.\n\nВосстановлено:\n{targets}\n\nСтраховочный бэкап:\n{safety}",
        "restore_failed": "❌ Ошибка восстановления.\n\nОшибка:\n{error}",
        "restore_page_text": "Стр. {page}/{total}",
        "btn_restore_confirm": "✅ Восстановить",
        "btn_restore_cancel": "🔙 Отмена",
        "btn_restart_xui": "🔄 Перезапустить X-UI",
        "btn_restart_bot": "🔄 Перезапустить бота",
        "restart_starting": "🔄 Перезапускаю X-UI...",
        "restart_done": "✅ X-UI перезапущен.",
        "restart_failed": "❌ Не удалось перезапустить X-UI.\n\nОшибка:\n{error}",
        "bot_restart_starting": "🔄 Перезапускаю бота...",
        "bot_restart_done": "✅ Бот перезапущен.",
        "bot_restart_failed": "❌ Не удалось перезапустить бота.\n\nОшибка:\n{error}",
        "backup_menu_text": "💾 *Бэкапы*\n\nСоздайте новый бэкап или восстановите из существующих.",
        "btn_backup_create": "💾 Создать бэкап",
        "upload_db_received": "📥 Бэкап загружен: `{name}`\n\nКак восстановить этот файл?",
        "upload_db_detected_xui": "Определено: база X-UI.",
        "upload_db_detected_bot": "Определено: база BOT.",
        "upload_db_detected_unknown": "Определено: тип базы не распознан.",
        "btn_restore_as_xui": "♻️ Восстановить как X-UI DB",
        "btn_restore_as_bot": "♻️ Восстановить как BOT DB",
        "upload_restore_confirm": "⚠️ *Подтверждение восстановления*\n\nЗагружен: `{name}`\n\nБудет восстановлено как: `{kind}`\n\n{check}\n\nПеред восстановлением автоматически будет создан страховочный бэкап.",
        "upload_restore_starting": "♻️ Восстанавливаю загруженный бэкап...",
        "upload_restore_done": "✅ Восстановление завершено.\n\nВосстановлено:\n{targets}\n\nСтраховочный бэкап:\n{safety}",
        "upload_restore_failed": "❌ Ошибка восстановления.\n\nОшибка:\n{error}",
        "upload_restore_missing": "⚠️ Загруженный файл не найден. Загрузите ещё раз.",
        "btn_backup_delete": "🗑 Удалить бэкап",
        "backup_delete_confirm": "⚠️ *Подтвердите удаление*\n\nВыбран: `{ts}`\n\nБудет удалено:\n{targets}\n\nДействие необратимо.",
        "backup_delete_done": "✅ Бэкап удалён.\n\nУдалено:\n{targets}",
        "backup_delete_failed": "❌ Ошибка удаления.\n\nОшибка:\n{error}",
        "cleanup_db_done": "✅ Очистка завершена.\n\nУдалено пользователей: {deleted}"
    }
}

def init_db():
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_prefs (
            tg_id TEXT PRIMARY KEY,
            lang TEXT,
            trial_used INTEGER DEFAULT 0,
            referrer_id TEXT,
            trial_activated_at INTEGER
        )
    ''')
    # Check/Migrate columns
    try:
        cursor.execute("ALTER TABLE user_prefs ADD COLUMN trial_used INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE user_prefs ADD COLUMN referrer_id TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE user_prefs ADD COLUMN trial_activated_at INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE user_prefs ADD COLUMN username TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE user_prefs ADD COLUMN first_name TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE user_prefs ADD COLUMN last_name TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE user_prefs ADD COLUMN balance INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE user_prefs ADD COLUMN mobile_trial_used INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE user_prefs ADD COLUMN mobile_trial_activated_at INTEGER")
    except sqlite3.OperationalError:
        pass

    # Notifications Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notifications (
            tg_id TEXT,
            type TEXT,
            date INTEGER,
            PRIMARY KEY (tg_id, type)
        )
    ''')

    # Referral Bonuses Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS referral_bonuses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id TEXT,
            referred_id TEXT,
            amount INTEGER,
            type TEXT,
            date INTEGER
        )
    ''')

    # Promo tables
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            days INTEGER,
            max_uses INTEGER,
            used_count INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_promos (
            tg_id TEXT,
            code TEXT,
            used_at INTEGER,
            PRIMARY KEY (tg_id, code)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id TEXT,
            amount INTEGER,
            date INTEGER,
            plan_id TEXT
        )
    ''')
    try:
        cursor.execute("ALTER TABLE transactions ADD COLUMN telegram_payment_charge_id TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE transactions ADD COLUMN processed_at INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_charge_id "
            "ON transactions(telegram_payment_charge_id) "
            "WHERE telegram_payment_charge_id IS NOT NULL"
        )
    except sqlite3.OperationalError:
        pass

    # Traffic History Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS traffic_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT,
            date TEXT, -- YYYY-MM-DD
            up INTEGER,
            down INTEGER,
            UNIQUE(email, date)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS traffic_daily_baselines (
            email TEXT,
            date TEXT, -- YYYY-MM-DD
            up INTEGER,
            down INTEGER,
            captured_at INTEGER,
            PRIMARY KEY (email, date)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS prices (
            key TEXT PRIMARY KEY,
            amount INTEGER,
            days INTEGER
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ru_bridge_subscriptions (
            tg_id TEXT PRIMARY KEY,
            uuid TEXT,
            sub_id TEXT,
            expiry_time INTEGER,
            created_at INTEGER,
            updated_at INTEGER
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mobile_subscriptions (
            tg_id TEXT PRIMARY KEY,
            uuid TEXT,
            sub_id TEXT,
            expiry_time INTEGER,
            created_at INTEGER,
            updated_at INTEGER
        )
    ''')

    # Initialize default prices if empty
    cursor.execute("SELECT COUNT(*) FROM prices")
    if cursor.fetchone()[0] == 0:
        cursor.executemany("INSERT INTO prices (key, amount, days) VALUES (?, ?, ?)", [
            ("1_week", 40, 7),
            ("2_weeks", 60, 14),
            ("1_month", 1, 30),
            ("3_months", 3, 90),
            ("ru_bridge", 1, 1),
            ("m_1_week", 40, 7),
            ("m_2_weeks", 60, 14),
            ("m_1_month", 149, 30),
            ("m_3_months", 399, 90),
            ("6_months", 450, 180),
            ("m_6_months", 799, 180),
            ("1_year", 5, 365),
            ("m_1_year", 1399, 365),
        ])
    else:
        # Ensure new plans exist
        cursor.execute("INSERT OR IGNORE INTO prices (key, amount, days) VALUES (?, ?, ?)", ("6_months", 450, 180))
        cursor.execute("INSERT OR IGNORE INTO prices (key, amount, days) VALUES (?, ?, ?)", ("1_week", 40, 7))
        cursor.execute("INSERT OR IGNORE INTO prices (key, amount, days) VALUES (?, ?, ?)", ("2_weeks", 60, 14))
        cursor.execute("INSERT OR IGNORE INTO prices (key, amount, days) VALUES (?, ?, ?)", ("ru_bridge", 1, 1))
        cursor.execute("INSERT OR IGNORE INTO prices (key, amount, days) VALUES (?, ?, ?)", ("m_1_week", 40, 7))
        cursor.execute("INSERT OR IGNORE INTO prices (key, amount, days) VALUES (?, ?, ?)", ("m_2_weeks", 60, 14))
        cursor.execute("INSERT OR IGNORE INTO prices (key, amount, days) VALUES (?, ?, ?)", ("m_1_month", 149, 30))
        cursor.execute("INSERT OR IGNORE INTO prices (key, amount, days) VALUES (?, ?, ?)", ("m_3_months", 399, 90))
        cursor.execute("INSERT OR IGNORE INTO prices (key, amount, days) VALUES (?, ?, ?)", ("m_6_months", 799, 180))
        cursor.execute("INSERT OR IGNORE INTO prices (key, amount, days) VALUES (?, ?, ?)", ("m_1_year", 1399, 365))
        cursor.execute("UPDATE prices SET amount=?, days=? WHERE key=?", (149, 30, "m_1_month"))
        cursor.execute("UPDATE prices SET amount=?, days=? WHERE key=?", (399, 90, "m_3_months"))
        cursor.execute("UPDATE prices SET amount=?, days=? WHERE key=?", (799, 180, "m_6_months"))
        cursor.execute("UPDATE prices SET amount=?, days=? WHERE key=?", (1399, 365, "m_1_year"))
        cursor.execute(
            "UPDATE prices SET amount=?, days=? "
            "WHERE key=? AND amount=? AND days=?",
            (1, 1, "ru_bridge", 120, 30),
        )

    # Flash Messages Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS flash_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT,
            message_id INTEGER,
            delete_at INTEGER
        )
    ''')
    # Index for fast lookup
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_flash_delete ON flash_messages(delete_at)")

    # Flash Delivery Errors Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS flash_delivery_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            error_message TEXT,
            timestamp INTEGER
        )
    ''')

    # Suspicious Events Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS suspicious_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT,
            ips TEXT, -- Comma separated IPs with flags
            timestamp INTEGER, -- First detection time
            last_seen INTEGER, -- Last detection time
            count INTEGER DEFAULT 1 -- How many detection intervals
        )
    ''')

    # Polls Tables
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS polls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT,
            options TEXT, -- JSON
            created_at INTEGER,
            active INTEGER DEFAULT 1
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS poll_votes (
            poll_id INTEGER,
            tg_id TEXT,
            option_index INTEGER,
            PRIMARY KEY (poll_id, tg_id)
        )
    ''')

    # Connection Logs
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS connection_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT,
            ip TEXT,
            timestamp INTEGER,
            country_code TEXT,
            UNIQUE(email, ip)
        )
    ''')

    # Migration: Check if country_code column exists, if not add it
    try:
        cursor.execute("SELECT country_code FROM connection_logs LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE connection_logs ADD COLUMN country_code TEXT")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS remote_panels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            base_url TEXT,
            api_token TEXT,
            enabled INTEGER DEFAULT 1,
            created_at INTEGER
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS remote_locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            host TEXT,
            port INTEGER,
            public_key TEXT,
            sni TEXT,
            sid TEXT,
            flow TEXT,
            sub_host TEXT,
            sub_port INTEGER,
            sub_path TEXT,
            panel_id INTEGER,
            enabled INTEGER DEFAULT 1,
            created_at INTEGER
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS remote_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            host TEXT,
            port INTEGER,
            enabled INTEGER DEFAULT 1,
            created_at INTEGER
        )
    ''')
    cursor.execute("PRAGMA table_info(remote_nodes)")
    existing_columns = {row[1] for row in cursor.fetchall()}
    if "ssh_user" not in existing_columns:
        cursor.execute("ALTER TABLE remote_nodes ADD COLUMN ssh_user TEXT")
    if "ssh_password" not in existing_columns:
        cursor.execute("ALTER TABLE remote_nodes ADD COLUMN ssh_password TEXT")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sync_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at INTEGER
        )
    ''')

    conn.commit()
    conn.close()

def _fetch_remote_panels() -> list[dict[str, Any]]:
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, base_url, api_token, enabled, created_at FROM remote_panels ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "id": row[0],
            "name": row[1],
            "base_url": row[2],
            "api_token": row[3],
            "enabled": row[4],
            "created_at": row[5],
        }
        for row in rows
    ]

def _insert_remote_panel(name: str, base_url: str, api_token: Optional[str]) -> Optional[int]:
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO remote_panels (name, base_url, api_token, enabled, created_at) VALUES (?, ?, ?, ?, ?)",
        (name, base_url, api_token or None, 1, int(time.time())),
    )
    conn.commit()
    panel_id = cursor.lastrowid
    conn.close()
    return int(panel_id) if panel_id else None

def _get_remote_panel_id_by_base_url(base_url: str) -> Optional[int]:
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM remote_panels WHERE base_url=? ORDER BY id DESC LIMIT 1", (base_url,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return int(row[0])

def _delete_remote_panel(panel_id: int) -> None:
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM remote_panels WHERE id=?", (panel_id,))
    conn.commit()
    conn.close()

def _fetch_remote_locations() -> list[dict[str, Any]]:
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, name, host, port, public_key, sni, sid, flow, sub_host, sub_port, sub_path, panel_id, enabled "
        "FROM remote_locations ORDER BY id DESC"
    )
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "id": row[0],
            "name": row[1],
            "host": row[2],
            "port": row[3],
            "public_key": row[4],
            "sni": row[5],
            "sid": row[6],
            "flow": row[7],
            "sub_host": row[8],
            "sub_port": row[9],
            "sub_path": row[10],
            "panel_id": row[11],
            "enabled": row[12],
        }
        for row in rows
    ]

def _get_remote_location(location_id: int) -> Optional[dict[str, Any]]:
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, name, host, port, public_key, sni, sid, flow, sub_host, sub_port, sub_path, panel_id, enabled "
        "FROM remote_locations WHERE id=?",
        (location_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0],
        "name": row[1],
        "host": row[2],
        "port": row[3],
        "public_key": row[4],
        "sni": row[5],
        "sid": row[6],
        "flow": row[7],
        "sub_host": row[8],
        "sub_port": row[9],
        "sub_path": row[10],
        "panel_id": row[11],
        "enabled": row[12],
    }

def _get_remote_location_sub_settings(host: str) -> tuple[Optional[str], Optional[int], Optional[str]]:
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT sub_host, sub_port, sub_path FROM remote_locations WHERE host=? ORDER BY id DESC LIMIT 1",
        (host,),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None, None, None
    return row[0], row[1], row[2]

def _insert_remote_location(
    name: str,
    host: str,
    port: int,
    public_key: Optional[str],
    sni: Optional[str],
    sid: Optional[str],
    flow: Optional[str],
    sub_host: Optional[str],
    sub_port: Optional[int],
    sub_path: Optional[str],
    panel_id: Optional[int],
) -> None:
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO remote_locations "
        "(name, host, port, public_key, sni, sid, flow, sub_host, sub_port, sub_path, panel_id, enabled, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            name,
            host,
            port,
            public_key,
            sni,
            sid,
            flow,
            sub_host,
            sub_port,
            sub_path,
            panel_id,
            1,
            int(time.time()),
        ),
    )
    conn.commit()
    conn.close()

def _delete_remote_location(location_id: int) -> None:
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM remote_locations WHERE id=?", (location_id,))
    conn.commit()
    conn.close()

def _upsert_remote_location(
    *,
    name: str,
    host: str,
    port: int,
    public_key: Optional[str],
    sni: Optional[str],
    sid: Optional[str],
    flow: Optional[str],
    panel_id: Optional[int],
    sub_host: Optional[str] = None,
    sub_port: Optional[int] = None,
    sub_path: Optional[str] = None,
) -> int:
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM remote_locations WHERE host=? ORDER BY id DESC LIMIT 1",
        (host,),
    )
    row = cursor.fetchone()
    now_ts = int(time.time())
    if row:
        location_id = int(row[0])
        cursor.execute(
            "UPDATE remote_locations SET name=?, host=?, port=?, public_key=?, sni=?, sid=?, flow=?, "
            "sub_host=?, sub_port=?, sub_path=?, panel_id=?, enabled=1, created_at=? WHERE id=?",
            (
                name,
                host,
                int(port),
                public_key,
                sni,
                sid,
                flow,
                sub_host,
                sub_port,
                sub_path,
                panel_id,
                now_ts,
                location_id,
            ),
        )
    else:
        cursor.execute(
            "INSERT INTO remote_locations "
            "(name, host, port, public_key, sni, sid, flow, sub_host, sub_port, sub_path, panel_id, enabled, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (
                name,
                host,
                int(port),
                public_key,
                sni,
                sid,
                flow,
                sub_host,
                sub_port,
                sub_path,
                panel_id,
                now_ts,
            ),
        )
        last_id = cursor.lastrowid
        location_id = int(last_id) if last_id is not None else 0
    if location_id:
        cursor.execute(
            "DELETE FROM remote_locations WHERE host=? AND id!=?",
            (host, location_id),
        )
    conn.commit()
    conn.close()
    return location_id

def _fetch_remote_nodes() -> list[dict[str, Any]]:
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, host, port, enabled FROM remote_nodes ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "id": row[0],
            "name": row[1],
            "host": row[2],
            "port": row[3],
            "enabled": row[4],
        }
        for row in rows
    ]

def _get_remote_node(node_id: int) -> Optional[dict[str, Any]]:
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, name, host, port, ssh_user, ssh_password, enabled FROM remote_nodes WHERE id=?",
        (node_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0],
        "name": row[1],
        "host": row[2],
        "port": row[3],
        "ssh_user": row[4],
        "ssh_password": row[5],
        "enabled": row[6],
    }

def _insert_remote_node(
    name: str,
    host: str,
    port: int,
    ssh_user: Optional[str] = None,
    ssh_password: Optional[str] = None,
) -> None:
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO remote_nodes (name, host, port, ssh_user, ssh_password, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, host, port, ssh_user, ssh_password, 1, int(time.time())),
    )
    conn.commit()
    conn.close()

def _delete_remote_node(node_id: int) -> None:
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM remote_nodes WHERE id=?", (node_id,))
    conn.commit()
    conn.close()

def _get_sync_state(key: str) -> Optional[str]:
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM sync_state WHERE key=?", (key,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return str(row[0])

def _set_sync_state(key: str, value: str) -> None:
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO sync_state (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value, int(time.time())),
    )
    conn.commit()
    conn.close()

def _get_local_panel_settings() -> dict[str, Optional[str]]:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT key, value FROM settings WHERE key IN ('webPort', 'webBasePath')"
        )
        rows = cursor.fetchall()
        conn.close()
        data = {row[0]: row[1] for row in rows}
        port = data.get("webPort")
        base_path = data.get("webBasePath")
        if port:
            try:
                port = str(int(port))
            except Exception:
                port = None
        return {"port": port, "base_path": base_path}
    except Exception:
        return {"port": None, "base_path": None}

def _resolve_host_ip(host: str) -> Optional[str]:
    try:
        ipaddress.ip_address(host)
        return host
    except Exception:
        pass
    try:
        return socket.gethostbyname(host)
    except Exception:
        return None

def _geoip_country_code(ip: str) -> Optional[str]:
    try:
        requests = __import__("requests")
        resp = requests.get(f"https://ipinfo.io/{ip}/json", timeout=2)
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data.get("country")
    except Exception:
        return None

def _auto_location_name(host: str) -> str:
    ip = _resolve_host_ip(host)
    if not ip:
        return host
    cc = _geoip_country_code(ip)
    if not cc:
        return ip
    flag = get_flag_emoji(cc)
    return f"{flag} {cc}"

def _extract_host_from_url(url: str) -> str:
    if "://" not in url:
        url = f"https://{url}"
    parsed = urlparse(url)
    return parsed.hostname or url

def _split_host_port(value: str) -> tuple[str, Optional[int]]:
    host = value.strip()
    if not host:
        return "", None
    if host.startswith("[") and "]" in host:
        return host, None
    if ":" in host:
        maybe_host, maybe_port = host.rsplit(":", 1)
        if maybe_port.isdigit():
            return maybe_host, int(maybe_port)
    return host, None

def _looks_like_host(value: str) -> bool:
    if not value:
        return False
    candidate = value.strip()
    if not candidate:
        return False
    host, _ = _split_host_port(candidate)
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return "." in host or host.lower() == "localhost"

def _build_remote_panel_base_url(host: str, web_port: Optional[int], base_path: Optional[str]) -> Optional[str]:
    if not host:
        return None
    if web_port:
        base = f"https://{host}:{web_port}"
    else:
        base = f"https://{host}"
    if base_path:
        path = str(base_path).lstrip("/")
        if path:
            return f"{base}/{path}"
    return base

def _get_remote_location_id(
    host: str,
    port: int,
) -> Optional[int]:
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM remote_locations WHERE host=? AND port=? ORDER BY id DESC LIMIT 1",
        (host, port),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return int(row[0])

def _ssh_fetch_remote_xui_data(
    host: str,
    port: int,
    username: str,
    password: str,
) -> Optional[dict[str, Any]]:
    client = None
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=6,
            banner_timeout=6,
            auth_timeout=6,
            look_for_keys=False,
            allow_agent=False,
        )
        cmd = (
            "python3 - <<'PY'\n"
            "import json, sqlite3, os\n"
            "db=os.getenv('XUI_DB_PATH','/etc/x-ui/x-ui.db')\n"
            "conn=sqlite3.connect(db)\n"
            "cur=conn.cursor()\n"
            "cur.execute(\"SELECT key, value FROM settings WHERE key IN ('webPort','webBasePath','subEnable','subPort','subPath','subCertFile','webCertFile')\")\n"
            "settings={k:v for k,v in cur.fetchall()}\n"
            "cur.execute(\"SELECT port, stream_settings, protocol FROM inbounds\")\n"
            "rows=cur.fetchall()\n"
            "conn.close()\n"
            "result={\n"
            "    'web_port': settings.get('webPort'),\n"
            "    'web_base_path': settings.get('webBasePath'),\n"
            "    'sub_enable': settings.get('subEnable'),\n"
            "    'sub_port': settings.get('subPort'),\n"
            "    'sub_path': settings.get('subPath'),\n"
            "    'sub_cert': settings.get('subCertFile'),\n"
            "    'web_cert': settings.get('webCertFile'),\n"
            "}\n"
            "for port, stream_settings, protocol in rows:\n"
            "    if protocol != 'vless':\n"
            "        continue\n"
            "    try:\n"
            "        ss=json.loads(stream_settings or '{}')\n"
            "    except Exception:\n"
            "        continue\n"
            "    reality=ss.get('realitySettings') or {}\n"
            "    settings_inner=reality.get('settings') or {}\n"
            "    public_key=settings_inner.get('publicKey')\n"
            "    sni_list=reality.get('serverNames') or []\n"
            "    sid_list=reality.get('shortIds') or []\n"
            "    if public_key and sni_list and sid_list:\n"
            "        result.update({\n"
            "            'inbound_port': port,\n"
            "            'public_key': public_key,\n"
            "            'sni': sni_list[0],\n"
            "            'sid': sid_list[0],\n"
            "            'flow': settings_inner.get('flow')\n"
            "        })\n"
            "        break\n"
            "print(json.dumps(result))\n"
            "PY"
        )
        _, stdout, stderr = client.exec_command(cmd, timeout=12)
        output = stdout.read().decode("utf-8", errors="ignore").strip()
        error = stderr.read().decode("utf-8", errors="ignore").strip()
        if not output:
            if error:
                logging.warning(f"SSH sync error for {host}:{port}: {error}")
            return None
        try:
            data = json.loads(output)
        except Exception:
            logging.warning(f"SSH sync invalid json for {host}:{port}: {output}")
            return None
        if not isinstance(data, dict):
            return None
        if error:
            logging.warning(f"SSH sync stderr for {host}:{port}: {error}")
        return data
    except Exception as exc:
        logging.warning(f"SSH sync exception for {host}:{port}: {exc}")
        return None
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

def _get_master_inbound_payload() -> Optional[dict[str, Any]]:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT port, protocol, settings, stream_settings FROM inbounds WHERE id=?",
            (INBOUND_ID,),
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        port, protocol, settings, stream_settings = row
        if not port or not protocol or not settings:
            return None
        return {
            "port": int(port),
            "protocol": str(protocol),
            "settings": str(settings),
            "stream_settings": str(stream_settings or "{}"),
        }
    except Exception:
        return None

def _ssh_sync_remote_inbound(
    host: str,
    port: int,
    username: str,
    password: str,
    inbound_payload: dict[str, Any],
) -> bool:
    client = None
    try:
        master_port = int(inbound_payload.get("port") or 0)
        master_protocol = str(inbound_payload.get("protocol") or "")
        settings_raw = str(inbound_payload.get("settings") or "")
        stream_raw = str(inbound_payload.get("stream_settings") or "")
        if not master_port or not master_protocol or not settings_raw:
            return False
        settings_b64 = base64.b64encode(settings_raw.encode("utf-8")).decode("utf-8")
        stream_b64 = base64.b64encode(stream_raw.encode("utf-8")).decode("utf-8")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=8,
            banner_timeout=8,
            auth_timeout=8,
            look_for_keys=False,
            allow_agent=False,
        )
        cmd = (
            "python3 - <<'PY'\n"
            "import base64, json, sqlite3, os\n"
            "db=os.getenv('XUI_DB_PATH','/etc/x-ui/x-ui.db')\n"
            "settings_raw=base64.b64decode('" + settings_b64 + "').decode('utf-8', errors='ignore')\n"
            "stream_raw=base64.b64decode('" + stream_b64 + "').decode('utf-8', errors='ignore')\n"
            "try:\n"
            "    settings=json.loads(settings_raw)\n"
            "except Exception:\n"
            "    settings={}\n"
            "try:\n"
            "    stream=json.loads(stream_raw)\n"
            "except Exception:\n"
            "    stream={}\n"
            "conn=sqlite3.connect(db)\n"
            "cur=conn.cursor()\n"
            "cur.execute(\"SELECT id, port, protocol FROM inbounds\")\n"
            "rows=cur.fetchall()\n"
            "target_id=None\n"
            "for iid, ip, proto in rows:\n"
            "    if proto == '" + master_protocol + "' and int(ip or 0) == " + str(master_port) + ":\n"
            "        target_id=iid\n"
            "        break\n"
            "if target_id is None:\n"
            "    for iid, ip, proto in rows:\n"
            "        if proto == '" + master_protocol + "':\n"
            "            target_id=iid\n"
            "            break\n"
            "if target_id is None:\n"
            "    print(json.dumps({'ok': False, 'error': 'inbound_not_found'}))\n"
            "    conn.close()\n"
            "    raise SystemExit(0)\n"
            "cur.execute(\"UPDATE inbounds SET port=?, protocol=?, settings=?, stream_settings=? WHERE id=?\", "
            "(" + str(master_port) + ", '" + master_protocol + "', json.dumps(settings, ensure_ascii=False), "
            "json.dumps(stream, ensure_ascii=False), target_id))\n"
            "clients=settings.get('clients') or []\n"
            "for c in clients:\n"
            "    email=str(c.get('email') or '')\n"
            "    if not email:\n"
            "        continue\n"
            "    expiry=int(c.get('expiryTime') or 0)\n"
            "    enable=1 if c.get('enable') else 0\n"
            "    reset=int(c.get('reset') or 0)\n"
            "    cur.execute(\"SELECT up, down, total, all_time, last_online FROM client_traffics WHERE email=?\", (email,))\n"
            "    row=cur.fetchone()\n"
            "    if row is None:\n"
            "        try:\n"
            "            cur.execute(\"INSERT INTO client_traffics (inbound_id, enable, email, up, down, expiry_time, total, reset, all_time, last_online) VALUES (?, ?, ?, 0, 0, ?, 0, ?, 0, 0)\", (target_id, enable, email, expiry, reset))\n"
            "        except Exception:\n"
            "            try:\n"
            "                cur.execute(\"INSERT INTO client_traffics (enable, email, up, down, expiry_time) VALUES (?, ?, 0, 0, ?)\", (enable, email, expiry))\n"
            "            except Exception:\n"
            "                pass\n"
            "    else:\n"
            "        try:\n"
            "            cur.execute(\"UPDATE client_traffics SET enable=?, expiry_time=?, reset=? WHERE email=?\", (enable, expiry, reset, email))\n"
            "        except Exception:\n"
            "            try:\n"
            "                cur.execute(\"UPDATE client_traffics SET enable=?, expiry_time=? WHERE email=?\", (enable, expiry, email))\n"
            "            except Exception:\n"
            "                pass\n"
            "conn.commit()\n"
            "conn.close()\n"
            "os.system(\"systemctl restart x-ui >/dev/null 2>&1 || x-ui restart >/dev/null 2>&1 || true\")\n"
            "print(json.dumps({'ok': True, 'id': target_id}))\n"
            "PY"
        )
        _, stdout, stderr = client.exec_command(cmd, timeout=20)
        output = stdout.read().decode("utf-8", errors="ignore").strip()
        error = stderr.read().decode("utf-8", errors="ignore").strip()
        if not output:
            if error:
                logging.warning(f"SSH inbound sync error for {host}:{port}: {error}")
            return False
        try:
            data = json.loads(output)
        except Exception:
            logging.warning(f"SSH inbound sync invalid json for {host}:{port}: {output}")
            return False
        if error:
            logging.warning(f"SSH inbound sync stderr for {host}:{port}: {error}")
        return bool(data.get("ok"))
    except Exception as exc:
        logging.warning(f"SSH inbound sync exception for {host}:{port}: {exc}")
        return False
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

def _ssh_upsert_remote_inbound_client(
    host: str,
    port: int,
    username: str,
    password: str,
    inbound_id: int,
    tg_id: str,
    email: str,
    user_uuid: str,
    sub_id: str,
    expiry_ms: int,
    flow: str,
    comment: str,
) -> bool:
    client = None
    try:
        inbound_id_int = int(inbound_id or 0)
        expiry_ms_int = int(expiry_ms or 0)
        if not email or not user_uuid or not sub_id:
            return False

        email_b64 = base64.b64encode(email.encode("utf-8")).decode("utf-8")
        uuid_b64 = base64.b64encode(user_uuid.encode("utf-8")).decode("utf-8")
        sub_b64 = base64.b64encode(sub_id.encode("utf-8")).decode("utf-8")
        tg_b64 = base64.b64encode(str(tg_id).encode("utf-8")).decode("utf-8")
        flow_b64 = base64.b64encode((flow or "").encode("utf-8")).decode("utf-8")
        comment_b64 = base64.b64encode((comment or "").encode("utf-8")).decode("utf-8")

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=8,
            banner_timeout=8,
            auth_timeout=8,
            look_for_keys=False,
            allow_agent=False,
        )
        cmd = (
            "python3 - <<'PY'\n"
            "import base64, json, sqlite3, os, time\n"
            "db=os.getenv('XUI_DB_PATH','/etc/x-ui/x-ui.db')\n"
            f"inbound_id=int({inbound_id_int})\n"
            f"expiry=int({expiry_ms_int})\n"
            f"email=base64.b64decode('{email_b64}').decode('utf-8', errors='ignore')\n"
            f"user_uuid=base64.b64decode('{uuid_b64}').decode('utf-8', errors='ignore')\n"
            f"sub_id=base64.b64decode('{sub_b64}').decode('utf-8', errors='ignore')\n"
            f"tg_id=base64.b64decode('{tg_b64}').decode('utf-8', errors='ignore')\n"
            f"flow=base64.b64decode('{flow_b64}').decode('utf-8', errors='ignore')\n"
            f"comment=base64.b64decode('{comment_b64}').decode('utf-8', errors='ignore')\n"
            "conn=sqlite3.connect(db)\n"
            "cur=conn.cursor()\n"
            "target_id=None\n"
            "if inbound_id > 0:\n"
            "    cur.execute(\"SELECT id FROM inbounds WHERE id=?\", (inbound_id,))\n"
            "    row=cur.fetchone()\n"
            "    if row:\n"
            "        target_id=int(row[0])\n"
            "if target_id is None:\n"
            "    cur.execute(\"SELECT id, protocol, stream_settings FROM inbounds\")\n"
            "    rows=cur.fetchall()\n"
            "    for iid, proto, stream_settings in rows:\n"
            "        if proto != 'vless':\n"
            "            continue\n"
            "        try:\n"
            "            ss=json.loads(stream_settings or '{}')\n"
            "        except Exception:\n"
            "            continue\n"
            "        reality=ss.get('realitySettings') or {}\n"
            "        settings_inner=(reality.get('settings') or {})\n"
            "        public_key=settings_inner.get('publicKey')\n"
            "        sni_list=reality.get('serverNames') or []\n"
            "        sid_list=reality.get('shortIds') or []\n"
            "        if public_key and sni_list and sid_list:\n"
            "            target_id=int(iid)\n"
            "            break\n"
            "if target_id is None:\n"
            "    cur.execute(\"SELECT id, protocol FROM inbounds\")\n"
            "    rows=cur.fetchall()\n"
            "    for iid, proto in rows:\n"
            "        if proto == 'vless':\n"
            "            target_id=int(iid)\n"
            "            break\n"
            "if target_id is None:\n"
            "    print(json.dumps({'ok': False, 'error': 'inbound_not_found'}))\n"
            "    conn.close()\n"
            "    raise SystemExit(0)\n"
            "inbound_id=int(target_id)\n"
            "cur.execute('SELECT settings FROM inbounds WHERE id=?', (inbound_id,))\n"
            "row=cur.fetchone()\n"
            "if not row:\n"
            "    print(json.dumps({'ok': False, 'error': 'inbound_not_found'}))\n"
            "    conn.close()\n"
            "    raise SystemExit(0)\n"
            "settings_raw=row[0] or '{}'\n"
            "try:\n"
            "    settings=json.loads(settings_raw)\n"
            "except Exception:\n"
            "    settings={}\n"
            "clients=settings.get('clients') or []\n"
            "now=int(time.time()*1000)\n"
            "updated=False\n"
            "for c in clients:\n"
            "    if str(c.get('email') or '')==email or str(c.get('tgId') or '')==tg_id:\n"
            "        c['id']=user_uuid\n"
            "        c['email']=email\n"
            "        c['expiryTime']=expiry\n"
            "        c['enable']=True\n"
            "        c['subId']=sub_id\n"
            "        try:\n"
            "            c['tgId']=int(tg_id)\n"
            "        except Exception:\n"
            "            c['tgId']=tg_id\n"
            "        if flow and not c.get('flow'):\n"
            "            c['flow']=flow\n"
            "        if comment and not c.get('comment'):\n"
            "            c['comment']=comment\n"
            "        if not c.get('created_at'):\n"
            "            c['created_at']=now\n"
            "        c['updated_at']=now\n"
            "        if 'reset' not in c:\n"
            "            c['reset']=0\n"
            "        updated=True\n"
            "        break\n"
            "if not updated:\n"
            "    new_client={\n"
            "        'id': user_uuid,\n"
            "        'email': email,\n"
            "        'limitIp': 0,\n"
            "        'totalGB': 0,\n"
            "        'expiryTime': expiry,\n"
            "        'enable': True,\n"
            "        'subId': sub_id,\n"
            "        'created_at': now,\n"
            "        'updated_at': now,\n"
            "        'comment': comment,\n"
            "        'reset': 0,\n"
            "    }\n"
            "    try:\n"
            "        new_client['tgId']=int(tg_id)\n"
            "    except Exception:\n"
            "        new_client['tgId']=tg_id\n"
            "    if flow:\n"
            "        new_client['flow']=flow\n"
            "    clients.append(new_client)\n"
            "settings['clients']=clients\n"
            "cur.execute('UPDATE inbounds SET settings=? WHERE id=?', (json.dumps(settings, ensure_ascii=False), inbound_id))\n"
            "try:\n"
            "    cur.execute('UPDATE client_traffics SET enable=1, expiry_time=?, reset=0 WHERE inbound_id=? AND email=?', (expiry, inbound_id, email))\n"
            "    if cur.rowcount==0:\n"
            "        cur.execute('INSERT INTO client_traffics (inbound_id, enable, email, up, down, expiry_time, total, reset, all_time, last_online) VALUES (?, ?, ?, 0, 0, ?, 0, 0, 0, 0)', (inbound_id, 1, email, expiry))\n"
            "except Exception:\n"
            "    try:\n"
            "        cur.execute('UPDATE client_traffics SET enable=1, expiry_time=? WHERE email=?', (expiry, email))\n"
            "        if cur.rowcount==0:\n"
            "            cur.execute('INSERT INTO client_traffics (enable, email, up, down, expiry_time) VALUES (1, ?, 0, 0, ?)', (email, expiry))\n"
            "    except Exception:\n"
            "        pass\n"
            "conn.commit()\n"
            "conn.close()\n"
            "os.system('systemctl restart x-ui >/dev/null 2>&1 || x-ui restart >/dev/null 2>&1 || true')\n"
            "print(json.dumps({'ok': True, 'updated': updated}))\n"
            "PY"
        )
        _, stdout, stderr = client.exec_command(cmd, timeout=25)
        output = stdout.read().decode("utf-8", errors="ignore").strip()
        error = stderr.read().decode("utf-8", errors="ignore").strip()
        if not output:
            if error:
                logging.warning(f"SSH client upsert error for {host}:{port}: {error}")
            return False
        try:
            data = json.loads(output)
        except Exception:
            logging.warning(f"SSH client upsert invalid json for {host}:{port}: {output}")
            return False
        if error:
            logging.warning(f"SSH client upsert stderr for {host}:{port}: {error}")
        return bool(data.get("ok"))
    except Exception as exc:
        logging.warning(f"SSH client upsert exception for {host}:{port}: {exc}")
        return False
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

async def _sync_mobile_inbound_client(
    tg_id: str,
    user_uuid: str,
    sub_id: str,
    expiry_ms: int,
    comment: str = "",
) -> bool:
    if not MOBILE_SSH_HOST or not MOBILE_SSH_USER or not MOBILE_SSH_PASSWORD:
        return False
    email = _mobile_email(tg_id)
    return await asyncio.get_running_loop().run_in_executor(
        None,
        _ssh_upsert_remote_inbound_client,
        MOBILE_SSH_HOST,
        MOBILE_SSH_PORT,
        MOBILE_SSH_USER,
        MOBILE_SSH_PASSWORD,
        MOBILE_INBOUND_ID,
        str(tg_id),
        email,
        user_uuid,
        sub_id,
        int(expiry_ms),
        MOBILE_FLOW,
        comment,
    )

def _sync_remote_node_data(
    host: str,
    ssh_port: int,
    ssh_user: str,
    ssh_password: str,
    name: str,
) -> tuple[bool, bool]:
    ssh_data = _ssh_fetch_remote_xui_data(host, ssh_port, ssh_user, ssh_password)
    if not ssh_data:
        return False, False
    inbound_payload = _get_master_inbound_payload()
    inbound_synced = False
    if inbound_payload:
        inbound_synced = _ssh_sync_remote_inbound(host, ssh_port, ssh_user, ssh_password, inbound_payload)
    if not inbound_synced:
        logging.warning(f"Remote inbound sync failed for {host}:{ssh_port}")
    web_port = _safe_int(ssh_data.get("web_port"))
    web_base_path = ssh_data.get("web_base_path")
    panel_base_url: Optional[str] = _build_remote_panel_base_url(host, web_port, web_base_path)
    panel_id = None
    panel_ready = False
    if panel_base_url:
        panel_id = _get_remote_panel_id_by_base_url(panel_base_url)
        if panel_id is None:
            panel_id = _insert_remote_panel(name, panel_base_url, None)
        panel_ready = panel_id is not None
    inbound_port = _safe_int(ssh_data.get("inbound_port")) or PORT
    public_key_raw = ssh_data.get("public_key")
    sni_raw = ssh_data.get("sni")
    sid_raw = ssh_data.get("sid")
    remote_public_key = str(public_key_raw) if public_key_raw else None
    remote_sni = str(sni_raw) if sni_raw else None
    remote_sid = str(sid_raw) if sid_raw else None
    flow_raw = ssh_data.get("flow")
    remote_flow = str(flow_raw) if flow_raw else "xtls-rprx-vision"
    location_ready = False
    if inbound_port:
        location_host = name if _looks_like_host(name) else host
        location_name = _auto_location_name(location_host)
        sub_host, sub_port, sub_path = _get_remote_location_sub_settings(location_host)
        if not sub_host:
            candidate = location_host if _looks_like_host(location_host) else host
            if candidate and _looks_like_host(candidate):
                sub_host = candidate
        sub_enable = str(ssh_data.get("sub_enable") or "").lower() == "true"
        raw_sub_port = ssh_data.get("sub_port")
        raw_sub_path = str(ssh_data.get("sub_path") or "/sub/")
        raw_web_port = ssh_data.get("web_port")
        raw_web_base_path = str(ssh_data.get("web_base_path") or "/")
        if sub_host:
            if sub_enable:
                if sub_port is None:
                    sub_port = _safe_int(raw_sub_port) or 2096
                if sub_path is None:
                    sub_path = raw_sub_path
            else:
                if sub_port is None:
                    sub_port = _safe_int(raw_web_port)
                if sub_path is None:
                    base_path = raw_web_base_path or "/"
                    if base_path and not base_path.endswith("/"):
                        base_path += "/"
                    if base_path and not base_path.startswith("/"):
                        base_path = "/" + base_path
                    if raw_sub_path.startswith("/"):
                        sub_path = f"{base_path}{raw_sub_path[1:]}" if base_path else raw_sub_path
                    else:
                        sub_path = f"{base_path}{raw_sub_path}" if base_path else f"/{raw_sub_path}"
        if sub_host and not sub_path:
            sub_path = "/sub/"
        _upsert_remote_location(
            name=location_name,
            host=location_host,
            port=int(inbound_port),
            public_key=remote_public_key,
            sni=remote_sni,
            sid=remote_sid,
            flow=remote_flow,
            sub_host=sub_host,
            sub_port=sub_port,
            sub_path=sub_path,
            panel_id=panel_id,
        )
        location_ready = True
    node_ready = bool(location_ready and inbound_synced)
    return panel_ready, node_ready

def _sync_remote_nodes_locations() -> bool:
    nodes = _fetch_remote_nodes()
    if not nodes:
        return False
    any_synced = False
    for node in nodes:
        node_full = _get_remote_node(int(node["id"]))
        if not node_full:
            continue
        ssh_user = node_full.get("ssh_user")
        ssh_password = node_full.get("ssh_password")
        if not ssh_user or not ssh_password:
            continue
        host = node_full.get("host") or ""
        ssh_port = int(node_full.get("port") or 22)
        name = node_full.get("name") or host
        panel_ready, location_ready = _sync_remote_node_data(
            host,
            ssh_port,
            ssh_user,
            ssh_password,
            name,
        )
        if panel_ready or location_ready:
            any_synced = True
    return any_synced

def _get_master_inbound_hash() -> Optional[str]:
    payload = _get_master_inbound_payload()
    if not payload:
        return None
    raw = f"{payload.get('port')}|{payload.get('protocol')}|{payload.get('settings')}|{payload.get('stream_settings')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

async def _auto_sync_remote_nodes_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    if AUTO_SYNC_INTERVAL_SEC <= 0:
        return
    nodes = _fetch_remote_nodes()
    if not nodes:
        return
    inbound_hash = _get_master_inbound_hash()
    if not inbound_hash:
        return
    last_hash = _get_sync_state("master_inbound_hash")
    if inbound_hash == last_hash:
        return
    await asyncio.get_running_loop().run_in_executor(None, _sync_remote_nodes_locations)
    _set_sync_state("master_inbound_hash", inbound_hash)
    _set_sync_state("master_inbound_synced_at", str(int(time.time())))

async def _check_remote_panel(base_url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(base_url, follow_redirects=True)
            return resp.status_code < 500
    except Exception:
        return False

async def _check_tcp(host: str, port: int) -> bool:
    latency = await _check_tcp_latency(host, port)
    return latency is not None

async def _check_tcp_latency(host: str, port: int) -> Optional[int]:
    try:
        start = time.monotonic()
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3.0)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        del reader
        ms = int((time.monotonic() - start) * 1000)
        return ms
    except Exception:
        return None

def _get_user_client(tg_id: str) -> Optional[dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    settings = json.loads(row[0])
    clients = settings.get("clients", [])
    for client in clients:
        if str(client.get("tgId", "")) == tg_id or client.get("email") == f"tg_{tg_id}":
            return client
    return None

def _get_user_client_by_token(token: str) -> Optional[dict[str, Any]]:
    if not token:
        return None
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    settings = json.loads(row[0])
    clients = settings.get("clients", [])
    for client in clients:
        sub_id = str(client.get("subId") or "").strip()
        client_id = str(client.get("id") or "").strip()
        if sub_id and sub_id == token:
            return client
        if client_id and client_id == token:
            return client
    return None

def _get_spiderx_encoded() -> str:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT stream_settings FROM inbounds WHERE id=?", (INBOUND_ID,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return "%2F"
        ss = json.loads(row[0])
        reality = ss.get("realitySettings", {})
        settings_inner = reality.get("settings", {})
        spider_x = settings_inner.get("spiderX", "/")
        import urllib.parse
        return urllib.parse.quote(str(spider_x))
    except Exception:
        return "%2F"

def _build_location_vless_link(
    location: dict[str, Any],
    user_uuid: str,
    client_email: str,
) -> str:
    return _build_location_vless_link_with_settings(location, user_uuid, client_email)

def _build_location_vless_link_with_settings(
    location: dict[str, Any],
    user_uuid: str,
    client_email: str,
    base_settings: Optional[dict[str, Any]] = None,
    client_flow: Optional[str] = None,
    spx_val: Optional[str] = None,
) -> str:
    base = base_settings or {}
    host = location.get("host")
    if not host:
        return ""
    port = location.get("port") or base.get("port") or PORT
    public_key = location.get("public_key") or base.get("public_key") or PUBLIC_KEY
    sni = location.get("sni") or base.get("sni") or SNI
    sid = location.get("sid") or base.get("sid") or SID
    flow = client_flow or base.get("flow") or location.get("flow") or ""
    if not port or not public_key or not sni or not sid:
        return ""
    spx_final = spx_val or "%2F"
    flow_part = f"&flow={flow}" if flow else ""
    return (
        f"vless://{user_uuid}@{host}:{port}?"
        f"type=tcp&encryption=none&security=reality&pbk={public_key}"
        f"&fp=chrome&sni={sni}&sid={sid}&spx={spx_final}{flow_part}#{client_email}"
    )

def _location_label(location: dict[str, Any]) -> str:
    name = str(location.get("name") or "").strip()
    if name:
        return name
    host = str(location.get("host") or "").strip()
    return host or "Location"

def _build_location_sub_link(location: dict[str, Any], token: Optional[str]) -> Optional[str]:
    sub_host = location.get("sub_host")
    if not sub_host:
        if not token:
            return None
        master_link = _build_master_sub_link(token)
        if master_link:
            return master_link
        multi_url = _build_multi_sub_public_url(token)
        return multi_url if _is_https_url(multi_url) else None
    sub_port = location.get("sub_port") or 443
    sub_path = location.get("sub_path") or "/sub/"
    if not sub_path.startswith("/"):
        sub_path = f"/{sub_path}"
    return f"https://{sub_host}:{sub_port}{sub_path}"

def _ru_bridge_location() -> Optional[dict[str, Any]]:
    if not RU_BRIDGE_HOST or not RU_BRIDGE_PORT or not RU_BRIDGE_PUBLIC_KEY or not RU_BRIDGE_SNI or not RU_BRIDGE_SID:
        return None
    return {
        "host": RU_BRIDGE_HOST,
        "port": RU_BRIDGE_PORT,
        "public_key": RU_BRIDGE_PUBLIC_KEY,
        "sni": RU_BRIDGE_SNI,
        "sid": RU_BRIDGE_SID,
        "flow": RU_BRIDGE_FLOW,
        "spx": RU_BRIDGE_SPX,
        "network": RU_BRIDGE_NETWORK,
        "xhttp_path": RU_BRIDGE_XHTTP_PATH,
        "xhttp_mode": RU_BRIDGE_XHTTP_MODE,
        "sub_host": RU_BRIDGE_SUB_HOST,
        "sub_port": RU_BRIDGE_SUB_PORT,
        "sub_path": RU_BRIDGE_SUB_PATH,
    }

def _resolve_ru_bridge_inbound_id() -> Optional[int]:
    global RU_BRIDGE_INBOUND_ID
    if RU_BRIDGE_INBOUND_ID is not None:
        return RU_BRIDGE_INBOUND_ID
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, remark, tag FROM inbounds")
        rows = cursor.fetchall()
        conn.close()
        if not rows:
            return None
        def _norm(value: str) -> str:
            return (value or "").strip().lower()
        candidates = []
        if RU_BRIDGE_INBOUND_REMARK:
            candidates.append(RU_BRIDGE_INBOUND_REMARK)
        else:
            candidates.extend([
                "ru_bridge_outbound",
                "ru bridge outbound",
                "ru-bridge outbound",
                "Vless RU>NL",
            ])
        lookup: dict[str, int] = {}
        for iid, remark, tag in rows:
            if remark:
                lookup[_norm(remark)] = int(iid)
            if tag:
                lookup[_norm(tag)] = int(iid)
        for candidate in candidates:
            cid = lookup.get(_norm(candidate))
            if cid:
                RU_BRIDGE_INBOUND_ID = int(cid)
                return RU_BRIDGE_INBOUND_ID
        if RU_BRIDGE_INBOUND_REMARK:
            logging.warning("RU-Bridge inbound remark not found, inbound selection stopped")
            return None
        for iid, remark, tag in rows:
            value = _norm(remark or tag or "")
            if "ru_bridge" in value or "ru bridge" in value:
                RU_BRIDGE_INBOUND_ID = int(iid)
                return RU_BRIDGE_INBOUND_ID
    except Exception as e:
        logging.error(f"Failed to resolve RU-Bridge inbound id: {e}")
    return None

def _build_ru_bridge_sub_link(token: Optional[str]) -> Optional[str]:
    if not RU_BRIDGE_SUB_HOST or not token:
        return None
    sub_port = RU_BRIDGE_SUB_PORT or 443
    sub_path = RU_BRIDGE_SUB_PATH or "/sub/"
    if "{token}" in sub_path:
        path = sub_path.replace("{token}", token)
        if not path.startswith("/"):
            path = f"/{path}"
        return f"https://{RU_BRIDGE_SUB_HOST}:{sub_port}{path}"
    if not sub_path.startswith("/"):
        sub_path = f"/{sub_path}"
    if not sub_path.endswith("/"):
        sub_path = f"{sub_path}/"
    return f"https://{RU_BRIDGE_SUB_HOST}:{sub_port}{sub_path}{token}"

def _build_ru_bridge_vless_link(
    user_uuid: str,
    client_email: str,
    client_flow: Optional[str] = None,
) -> str:
    location = _ru_bridge_location()
    if not location:
        return ""
    host = location.get("host")
    port = location.get("port")
    public_key = location.get("public_key")
    sni = location.get("sni")
    sid = location.get("sid")
    if not host or not port or not public_key or not sni or not sid:
        return ""
    flow = client_flow or location.get("flow") or ""
    spx_val = location.get("spx") or "%2F"
    network = location.get("network") or "xhttp"
    import urllib.parse
    params = [
        f"type={urllib.parse.quote(str(network))}",
        "encryption=none",
        "security=reality",
        f"pbk={public_key}",
        "fp=chrome",
        f"sni={sni}",
        f"sid={sid}",
        f"spx={spx_val}",
    ]
    if flow:
        params.append(f"flow={flow}")
    if network == "xhttp":
        path = location.get("xhttp_path") or "/"
        mode = location.get("xhttp_mode") or "packet-up"
        params.append(f"path={urllib.parse.quote(str(path))}")
        params.append(f"mode={urllib.parse.quote(str(mode))}")
    query = "&".join(params)
    return f"vless://{user_uuid}@{host}:{port}?{query}#{client_email}"

def _build_master_sub_link(token: str) -> Optional[str]:
    if not token or not IP:
        return None
    try:
        conn_set = sqlite3.connect(DB_PATH)
        cursor_set = conn_set.cursor()
        cursor_set.execute(
            "SELECT key, value FROM settings WHERE key IN "
            "('subEnable', 'subPort', 'subPath', 'webPort', 'webBasePath', 'webCertFile', 'subCertFile')"
        )
        rows_set = cursor_set.fetchall()
        conn_set.close()
    except Exception:
        return None
    settings_map = {k: v for k, v in rows_set}
    sub_enable = settings_map.get("subEnable", "false") == "true"
    sub_port = settings_map.get("subPort", "2096")
    sub_path = settings_map.get("subPath", "/sub/")
    web_port = settings_map.get("webPort", "2053")
    web_base_path = settings_map.get("webBasePath", "/")
    web_cert = settings_map.get("webCertFile", "")
    sub_cert = settings_map.get("subCertFile", "")
    protocol = "http"
    port = web_port
    path = sub_path
    if sub_enable:
        port = sub_port
        if sub_cert:
            protocol = "https"
    else:
        base_path = web_base_path or "/"
        if base_path and not base_path.endswith("/"):
            base_path += "/"
        if not base_path.startswith("/"):
            base_path = "/" + base_path
        if sub_path.startswith("/"):
            path = base_path + sub_path[1:]
        else:
            path = base_path + sub_path
        if web_cert:
            protocol = "https"
    return f"{protocol}://{IP}:{port}{path}{token}"


def _build_remote_xui_sub_link(host: str, token: str, settings: Mapping[str, Any]) -> Optional[str]:
    if not host or not token:
        return None
    override = MOBILE_SUB_PUBLIC_URL
    if override:
        override = override.strip()
        if "{token}" in override:
            return override.replace("{token}", token)
        override = override.rstrip("/")
        return f"{override}/{token}"

    sub_enable = str(settings.get("sub_enable") or "").lower() == "true"
    sub_port = str(settings.get("sub_port") or "2096")
    sub_path = str(settings.get("sub_path") or "/sub/")
    web_port = str(settings.get("web_port") or "2053")
    web_base_path = str(settings.get("web_base_path") or "/")
    web_cert = str(settings.get("web_cert") or "")
    sub_cert = str(settings.get("sub_cert") or "")

    protocol = "http"
    port = web_port
    path = sub_path
    if sub_enable:
        port = sub_port
        if sub_cert:
            protocol = "https"
    else:
        base_path = web_base_path or "/"
        if base_path and not base_path.endswith("/"):
            base_path += "/"
        if base_path and not base_path.startswith("/"):
            base_path = "/" + base_path
        if sub_path.startswith("/"):
            path = base_path + sub_path[1:]
        else:
            path = base_path + sub_path
        if web_cert:
            protocol = "https"

    if "{token}" in path:
        full_path = path.replace("{token}", token)
    else:
        if not path.endswith("/"):
            path += "/"
        full_path = f"{path}{token}"
    if not full_path.startswith("/"):
        full_path = "/" + full_path
    return f"{protocol}://{host}:{port}{full_path}"

def _build_all_locations_subscription_payload(
    user_uuid: str,
    client_email: str,
    client_flow: str,
) -> tuple[Optional[str], int, Optional[str]]:
    links: list[str] = []
    import urllib.parse
    base_settings = {
        "port": PORT,
        "public_key": PUBLIC_KEY,
        "sni": SNI,
        "sid": SID,
    }
    spx_val = _get_spiderx_encoded()
    if IP:
        local_location = {
            "host": IP,
            "port": PORT,
            "name": _auto_location_name(IP),
        }
        link = _build_location_vless_link_with_settings(
            local_location,
            user_uuid,
            urllib.parse.quote(_location_label(local_location)),
            base_settings=base_settings,
            client_flow=client_flow,
            spx_val=spx_val,
        )
        if link:
            links.append(link)
    remote_locations = [loc for loc in _fetch_remote_locations() if loc.get("enabled")]
    for location in remote_locations:
        link = _build_location_vless_link_with_settings(
            location,
            user_uuid,
            urllib.parse.quote(_location_label(location)),
            base_settings=base_settings,
            client_flow=client_flow,
            spx_val=spx_val,
        )
        if link:
            links.append(link)
    if not links:
        return None, 0, None
    payload = "\n".join(links)
    encoded = base64.b64encode(payload.encode("utf-8")).decode("utf-8")
    return encoded, len(links), payload

def _build_all_locations_subscription(
    user_uuid: str,
    client_email: str,
    client_flow: str,
) -> tuple[Optional[str], int, Optional[str]]:
    encoded, count, payload = _build_all_locations_subscription_payload(
        user_uuid=user_uuid,
        client_email=client_email,
        client_flow=client_flow,
    )
    if not encoded:
        return None, 0, None
    return f"sub://{encoded}", count, payload

def _build_multi_sub_public_url(token: str) -> Optional[str]:
    if not token:
        return None
    base = MULTI_SUB_PUBLIC_URL
    if not base:
        if not IP:
            return None
        base = f"http://{IP}:{MULTI_SUB_PORT}"
    base = base.rstrip("/")
    return f"{base}/sub/{token}"

def _is_https_url(url: Optional[str]) -> bool:
    if not url:
        return False
    return url.startswith("https://")

def _build_multi_sub_encoded_by_token(token: str) -> Optional[str]:
    client = _get_user_client_by_token(token)
    if not client:
        return None
    expiry_ms = int(client.get("expiryTime", 0) or 0)
    now_ms = int(time.time() * 1000)
    if expiry_ms > 0 and expiry_ms <= now_ms:
        return None
    user_uuid = str(client.get("id") or "").strip()
    if not user_uuid:
        return None
    email = client.get("email") or f"tg_{client.get('tgId', '')}"
    client_flow = str(client.get("flow") or "")
    encoded, count, _payload = _build_all_locations_subscription_payload(
        user_uuid=user_uuid,
        client_email=email,
        client_flow=client_flow,
    )
    if not encoded or count <= 0:
        return None
    return encoded

class _MultiSubHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < 2 or parts[0] != "sub":
            self.send_response(404)
            self.end_headers()
            return
        token = parts[1].strip()
        encoded = _build_multi_sub_encoded_by_token(token)
        if not encoded:
            self.send_response(404)
            self.end_headers()
            return
        data = encoded.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, _format: str, *_args: Any) -> None:
        return

_MULTI_SUB_SERVER: Optional[http.server.ThreadingHTTPServer] = None
_MULTI_SUB_THREAD: Optional[threading.Thread] = None

def _start_multi_sub_server() -> None:
    global _MULTI_SUB_SERVER, _MULTI_SUB_THREAD
    if not MULTI_SUB_ENABLE:
        return
    if _MULTI_SUB_SERVER is not None:
        return
    try:
        _purge_log_file_lines("Multi-sub server started on")
        server = http.server.ThreadingHTTPServer((MULTI_SUB_HOST, MULTI_SUB_PORT), _MultiSubHandler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, name="multi-sub-server", daemon=True)
        _MULTI_SUB_SERVER = server
        _MULTI_SUB_THREAD = thread
        thread.start()
    except Exception as e:
        logging.error(f"Failed to start multi-sub server: {e}")

def _transaction_dedupe_key(
    tg_id: str,
    amount: int,
    date_ts: int,
    plan_id: str,
    charge_id: Optional[str],
) -> tuple[Any, ...]:
    if charge_id:
        return ("c", charge_id)
    return ("n", tg_id, amount, date_ts, plan_id)

def _dedupe_transactions(
    rows: Iterable[tuple[str, int, int, str, Optional[str]]],
) -> list[tuple[str, int, int, str, Optional[str]]]:
    seen: set[tuple[Any, ...]] = set()
    out: list[tuple[str, int, int, str, Optional[str]]] = []
    for tg_id, amount, date_ts, plan_id, charge_id in rows:
        key = _transaction_dedupe_key(tg_id, amount, date_ts, plan_id, charge_id)
        if key in seen:
            continue
        seen.add(key)
        out.append((tg_id, amount, date_ts, plan_id, charge_id))
    return out

def _get_sales_log_dedupe_window_sec() -> int:
    raw = os.getenv("SALES_LOG_DEDUPE_WINDOW_SEC", "600")
    try:
        value = int(raw)
    except ValueError:
        return 600
    return max(0, value)

def _get_sales_log_fuzzy_charge_dedupe_window_sec() -> int:
    raw = os.getenv("SALES_LOG_FUZZY_CHARGE_DEDUPE_WINDOW_SEC", "60")
    try:
        value = int(raw)
    except ValueError:
        return 60
    return max(0, value)

def _dedupe_sales_log_rows(
    rows: Iterable[tuple[str, int, int, str, Optional[str]]],
) -> list[tuple[str, int, int, str, Optional[str]]]:
    window_sec = _get_sales_log_dedupe_window_sec()
    fuzzy_charge_window_sec = _get_sales_log_fuzzy_charge_dedupe_window_sec()
    seen_charge_ids: set[str] = set()
    last_ts_by_key: dict[tuple[str, int], int] = {}
    last_idx_by_key: dict[tuple[str, int], int] = {}
    out: list[tuple[str, int, int, str, Optional[str]]] = []

    for tg_id, amount, date_ts, plan_id, charge_id in rows:
        key = (tg_id, amount)
        last_ts = last_ts_by_key.get(key)
        if charge_id:
            if charge_id in seen_charge_ids:
                continue
            seen_charge_ids.add(charge_id)
            active_window = fuzzy_charge_window_sec
        else:
            active_window = window_sec

        if last_ts is not None and abs(last_ts - date_ts) <= active_window:
            idx = last_idx_by_key.get(key)
            if idx is not None:
                kept_tg_id, kept_amount, kept_date_ts, kept_plan_id, kept_charge_id = out[idx]
                kept_known = bool(kept_plan_id) and kept_plan_id != "unknown"
                cur_known = bool(plan_id) and plan_id != "unknown"
                if (not kept_known) and cur_known:
                    out[idx] = (kept_tg_id, kept_amount, kept_date_ts, plan_id, kept_charge_id)
                if kept_charge_id is None and charge_id:
                    out[idx] = (kept_tg_id, kept_amount, kept_date_ts, kept_plan_id, charge_id)
            continue
        last_ts_by_key[key] = date_ts
        last_idx_by_key[key] = len(out)
        out.append((tg_id, amount, date_ts, plan_id, charge_id))

    return out

_MISSED_TX_LOG_THROTTLE: dict[str, float] = {}

def _normalize_charge_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized if normalized else None
    if isinstance(value, (int, float)):
        return str(int(value))
    normalized = str(value).strip()
    return normalized if normalized else None

def get_client_tg_id(client: Dict[str, Any]) -> Optional[str]:
    tg_id = client.get("tgId")
    if isinstance(tg_id, int):
        return str(tg_id)
    if isinstance(tg_id, str) and tg_id.isdigit():
        return tg_id
    email = str(client.get("email", "") or "")
    if email.startswith("tg_"):
        possible = email[3:].split("_", 1)[0]
        if possible.isdigit():
            return possible
    return None

def update_user_info(tg_id, username, first_name, last_name):
    try:
        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()
        # Ensure user exists first (handled by set_lang usually, but safer to upsert)
        # We use INSERT OR IGNORE then UPDATE to avoid unique constraint fail if we don't know other fields
        # Or just UPDATE if exists, else INSERT

        # Simple Upsert logic for user info
        cursor.execute("SELECT 1 FROM user_prefs WHERE tg_id=?", (str(tg_id),))
        if cursor.fetchone():
            cursor.execute("""
                UPDATE user_prefs
                SET username=?, first_name=?, last_name=?
                WHERE tg_id=?
            """, (username, first_name, last_name, str(tg_id)))
        else:
            # New user, might default lang to en
            cursor.execute("""
                INSERT INTO user_prefs (tg_id, username, first_name, last_name, lang)
                VALUES (?, ?, ?, ?, 'en')
            """, (str(tg_id), username, first_name, last_name))

        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Error updating user info: {e}")

def get_flag_emoji(country_code):
    if not country_code:
        return "🏳️"
    try:
        # Offset for Regional Indicator Symbols
        return chr(ord(country_code[0]) + 127397) + chr(ord(country_code[1]) + 127397)
    except Exception:
        return "🏳️"

def get_lang(tg_id):
    try:
        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT lang FROM user_prefs WHERE tg_id=?", (str(tg_id),))
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            return row[0]
    except Exception as e:
        logging.error(f"DB Error: {e}")
    return "ru"

def set_lang(tg_id, lang):
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    # Check if user exists
    cursor.execute("SELECT 1 FROM user_prefs WHERE tg_id=?", (str(tg_id),))
    if cursor.fetchone():
        cursor.execute("UPDATE user_prefs SET lang=? WHERE tg_id=?", (lang, str(tg_id)))
    else:
        cursor.execute("INSERT INTO user_prefs (tg_id, lang) VALUES (?, ?)", (str(tg_id), lang))
    conn.commit()
    conn.close()

def get_user_data(tg_id):
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT trial_used, referrer_id, trial_activated_at FROM user_prefs WHERE tg_id=?", (str(tg_id),))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"trial_used": row[0], "referrer_id": row[1], "trial_activated_at": row[2]}
    return {"trial_used": 0, "referrer_id": None, "trial_activated_at": None}

def set_referrer(tg_id, referrer_id):
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    # Try to insert new user with referrer
    try:
        cursor.execute("INSERT INTO user_prefs (tg_id, referrer_id) VALUES (?, ?)", (str(tg_id), str(referrer_id)))
    except sqlite3.IntegrityError:
        # User exists, update referrer ONLY if it's currently NULL or empty
        cursor.execute("UPDATE user_prefs SET referrer_id=? WHERE tg_id=? AND (referrer_id IS NULL OR referrer_id = '')", (str(referrer_id), str(tg_id)))

    conn.commit()
    conn.close()

def mark_trial_used(tg_id):
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    current_time = int(time.time())
    # Upsert: Insert if not exists, else update
    cursor.execute("""
        INSERT INTO user_prefs (tg_id, trial_used, trial_activated_at) VALUES (?, 1, ?)
        ON CONFLICT(tg_id) DO UPDATE SET trial_used=1, trial_activated_at=?
    """, (str(tg_id), current_time, current_time))
    conn.commit()
    conn.close()

def count_referrals(tg_id):
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM user_prefs WHERE referrer_id=?", (str(tg_id),))
    count = cursor.fetchone()[0]
    conn.close()
    return count

def check_promo(code, tg_id):
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()

    # Check code existence and limit
    cursor.execute("SELECT days, max_uses, used_count, code FROM promo_codes WHERE code=? COLLATE NOCASE", (code,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None, None # Invalid

    days, max_uses, used_count, actual_code = row
    if max_uses > 0 and used_count >= max_uses:
        conn.close()
        return None, None # Expired/Max used

    # Check if user used it
    cursor.execute("SELECT 1 FROM user_promos WHERE tg_id=? AND code=?", (str(tg_id), actual_code))
    if cursor.fetchone():
        conn.close()
        return "USED", actual_code

    conn.close()
    return days, actual_code

def save_support_ticket(tg_id, text):
    """Saves a new support ticket (optional, if we want history)"""
    # For now we just forward, but let's log it
    logging.info(f"Support ticket from {tg_id}: {text}")

def redeem_promo_db(code, tg_id):
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO user_promos (tg_id, code, used_at) VALUES (?, ?, ?)", (str(tg_id), code, int(time.time())))
    cursor.execute("UPDATE promo_codes SET used_count = used_count + 1 WHERE code=?", (code,))
    conn.commit()
    conn.close()

def get_prices():
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT key, amount, days FROM prices")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return PRICES # Fallback

    prices_dict = dict(PRICES)
    for r in rows:
        key = str(r[0])
        amount_raw = r[1]
        days_raw = r[2]
        try:
            amount = int(amount_raw) if amount_raw is not None else None
        except Exception:
            amount = amount_raw
        try:
            days = int(days_raw) if days_raw is not None else None
        except Exception:
            days = days_raw
        prices_dict[key] = {"amount": amount, "days": days}
    return prices_dict

def update_price(key, amount):
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE prices SET amount=? WHERE key=?", (amount, key))
    conn.commit()
    conn.close()

def get_user_rank(tg_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None, 0, 0

        settings = json.loads(row[0])
        clients = settings.get('clients', [])

        valid_clients = []
        user_expiry = None

        for c in clients:
            expiry = c.get('expiryTime', 0)
            tid = str(c.get('tgId', ''))
            sort_val = expiry if expiry > 0 else 32503680000000

            valid_clients.append({
                'tg_id': tid,
                'sort_val': sort_val
            })

            if tid == tg_id:
                user_expiry = sort_val

        if user_expiry is None:
            return None, len(valid_clients), 0

        valid_clients.sort(key=lambda x: x['sort_val'], reverse=True)

        rank = -1
        for idx, item in enumerate(valid_clients):
            if item['tg_id'] == tg_id:
                rank = idx + 1
                break

        total = len(valid_clients)
        percent_top = int((rank / total) * 100) if total > 0 else 0
        if percent_top == 0:
            percent_top = 1

        return rank, total, percent_top

    except Exception as e:
        logging.error(f"Error calculating rank: {e}")
        return None, 0, 0

def format_traffic(bytes_val):
    if bytes_val is None:
        bytes_val = 0

    # If > 1000 GB, use TB
    # 1000 GB = 1000 * 1024^3 bytes
    tb_threshold = 1000 * (1024**3)

    if bytes_val >= tb_threshold:
        val = bytes_val / (1024**4)
        return f"{val:.2f} TB"
    else:
        val = bytes_val / (1024**3)
        return f"{val:.2f} GB"

def get_monthly_traffic(email):
    """
    Get traffic for current month from traffic_history table.
    Calculates usage as (Max - Min) for the month to show delta.
    """
    try:
        now = datetime.datetime.now(TIMEZONE)
        month_prefix = now.strftime("%Y-%m")
        prev_month = (now.replace(day=1) - datetime.timedelta(days=1)).strftime("%Y-%m")

        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()

        # 1. Get Max and Min for this month (Max is effectively current snapshot)
        cursor.execute("SELECT MIN(down), MAX(down) FROM traffic_history WHERE email=? AND date LIKE ?", (email, f"{month_prefix}%"))
        row = cursor.fetchone()

        min_val = row[0] if row and row[0] is not None else 0
        max_val = row[1] if row and row[1] is not None else 0

        # 2. Get Last record of previous month (Baseline)
        cursor.execute("SELECT down FROM traffic_history WHERE email=? AND date LIKE ? ORDER BY date DESC LIMIT 1", (email, f"{prev_month}%"))
        prev_row = cursor.fetchone()

        conn.close()

        baseline = 0
        if prev_row:
            baseline = prev_row[0]
        else:
            # If no history for prev month, use first record of this month as baseline
            # This avoids counting historical traffic as "this month's usage"
            baseline = min_val

        usage = max_val - baseline
        return max(0, usage)

    except Exception as e:
        logging.error(f"Error getting monthly traffic: {e}")
        return 0

def get_user_rank_traffic(target_email):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Get all clients from client_traffics to match Panel stats
        cursor.execute("SELECT email, up, down FROM client_traffics WHERE inbound_id=?", (INBOUND_ID,))
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return None, 0, 0

        leaderboard = []
        user_traffic = 0

        for r in rows:
            email, up, down = r
            if up is None:
                up = 0
            if down is None:
                down = 0
            traffic = up + down

            leaderboard.append({
                'email': email,
                'traffic': traffic
            })

            if email == target_email:
                user_traffic = traffic

        # Sort descending
        leaderboard.sort(key=lambda x: x['traffic'], reverse=True)

        # Find rank
        rank = -1
        for idx, item in enumerate(leaderboard):
            if item['email'] == target_email:
                rank = idx + 1
                break

        total = len(leaderboard)
        return rank, total, user_traffic

    except Exception as e:
        logging.error(f"Error calculating rank: {e}")
        return None, 0, 0

def get_user_total_traffic(target_email: str) -> int:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT up, down FROM client_traffics WHERE inbound_id=? AND email=?",
            (INBOUND_ID, target_email)
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return 0
        up, down = row
        up_val = up if up is not None else 0
        down_val = down if down is not None else 0
        return up_val + down_val
    except Exception as e:
        logging.error(f"Error getting total traffic: {e}")
        return 0

def is_subscription_active(enable: bool, expiry_ms: int, current_time_ms: int) -> bool:
    return bool(enable) and (expiry_ms == 0 or expiry_ms > current_time_ms)

def get_user_rank_subscription(target_email):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None, 0, 0

        settings = json.loads(row[0])
        clients = settings.get('clients', [])

        valid_clients = []
        user_days = 0
        current_time_ms = int(time.time() * 1000)

        for c in clients:
            expiry = c.get('expiryTime', 0)
            email = c.get('email', '')

            # Unlimited (0) gets top priority
            if expiry == 0:
                days = 36500 # ~100 years
            elif expiry > current_time_ms:
                remaining_ms = expiry - current_time_ms
                days = remaining_ms / (1000 * 3600 * 24)
            else:
                # Expired or negative
                days = -1 # Treat as 0/bottom for ranking

            valid_clients.append({
                'email': email,
                'days': days
            })

            if email == target_email:
                user_days = days if days > 0 else 0

        # Sort descending
        valid_clients.sort(key=lambda x: x['days'], reverse=True)

        rank = -1
        for idx, item in enumerate(valid_clients):
            if item['email'] == target_email:
                rank = idx + 1
                break

        total = len(valid_clients)
        return rank, total, user_days

    except Exception as e:
        logging.error(f"Error calculating sub rank: {e}")
        return None, 0, 0

def t(key, lang="en"):
    return TEXTS.get(lang, TEXTS["en"]).get(key, key)

def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _infer_plan_id_from_amount(
    amount: Any,
    prices: Mapping[str, Mapping[str, Any]],
) -> Optional[str]:
    amount_int = _safe_int(amount)
    if amount_int is None:
        return None

    matches: list[str] = []
    for plan_id, pdata in prices.items():
        price_amount = _safe_int(pdata.get("amount"))
        if price_amount is None:
            continue
        if price_amount == amount_int:
            matches.append(plan_id)

    if len(matches) == 1:
        return matches[0]

    legacy_by_amount: dict[int, str] = {
        1: "1_month",
        3: "3_months",
        5: "1_year",
        450: "6_months",
    }
    return legacy_by_amount.get(amount_int)


def backfill_unknown_transaction_plan_ids() -> int:
    try:
        prices = get_prices()
    except Exception:
        prices = {}

    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id, amount FROM transactions "
            "WHERE plan_id IS NULL OR plan_id='' OR plan_id='unknown'"
        )
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return 0

    updated = 0
    for tx_id, amount in rows:
        inferred = _infer_plan_id_from_amount(amount, prices)
        if not inferred:
            continue
        cursor.execute(
            "UPDATE transactions SET plan_id=? "
            "WHERE id=? AND (plan_id IS NULL OR plan_id='' OR plan_id='unknown')",
            (inferred, tx_id),
        )
        if cursor.rowcount:
            updated += int(cursor.rowcount)

    conn.commit()
    conn.close()
    return updated


def _normalize_plan_id(plan_id: str) -> str:
    value = (plan_id or "").strip().lower()
    if value.startswith("plan_"):
        value = value[5:]
    value = value.replace("-", "_").replace(" ", "_")
    aliases = {
        "1_months": "1_month",
        "3_month": "3_months",
        "6_month": "6_months",
        "12_month": "1_year",
        "12_months": "1_year",
        "1_years": "1_year",
        "7_days": "1_week",
        "14_days": "2_weeks",
        "2_week": "2_weeks",
        "30_days": "1_month",
    }
    return aliases.get(value, value)


def _resolve_plan_label(plan_id: Optional[str], lang: str) -> str:
    if not plan_id:
        return t("plan_manual", lang)
    normalized = _normalize_plan_id(str(plan_id))
    translated = t(f"plan_{normalized}", lang)
    if translated != f"plan_{normalized}":
        return translated
    try:
        prices = get_prices()
    except Exception:
        prices = {}
    if normalized in prices:
        days = prices[normalized].get("days")
        if isinstance(days, int) and days > 0:
            return f"{days} дн." if lang == "ru" else f"{days} d"
    return t("sub_type_unknown", lang)

def format_expiry_display(expiry_ms: int, lang: str, now_ms: Optional[int] = None, unlimited_key: str = "expiry_unlimited") -> str:
    if expiry_ms == 0:
        return t(unlimited_key, lang)
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    diff_ms = expiry_ms - now_ms
    if diff_ms <= 0:
        return "0 мин." if lang == "ru" else "0 min"
    day_ms = 24 * 60 * 60 * 1000
    hour_ms = 60 * 60 * 1000
    minute_ms = 60 * 1000
    days = diff_ms // day_ms
    if days >= 365:
        expiry_dt = datetime.datetime.fromtimestamp(expiry_ms / 1000, tz=TIMEZONE)
        return expiry_dt.strftime("%d.%m.%Y %H:%M")
    if days >= 2:
        expiry_dt = datetime.datetime.fromtimestamp(expiry_ms / 1000, tz=TIMEZONE)
        return expiry_dt.strftime("%d.%m %H:%M")
    if days >= 1:
        rem_hours = (diff_ms % day_ms) // hour_ms
        rem_minutes = (diff_ms % hour_ms) // minute_ms
        return f"{days:02d};{rem_hours:02d}:{rem_minutes:02d}"
    hours = diff_ms // hour_ms
    if hours >= 1:
        rem_minutes = (diff_ms % hour_ms) // minute_ms
        return f"{hours:02d}:{rem_minutes:02d}"
    minutes = math.ceil(diff_ms / minute_ms)
    return f"{minutes} мин." if lang == "ru" else f"{minutes} min"

def _get_user_client_expiry_ms(tg_id: str) -> Optional[int]:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        settings = json.loads(row[0])
        clients = settings.get("clients", [])
        for client in clients:
            if str(client.get("tgId")) == tg_id or client.get("email") == f"tg_{tg_id}":
                expiry_raw = client.get("expiryTime", 0)
                try:
                    return int(expiry_raw)
                except Exception:
                    return 0
        return None
    except Exception as e:
        logging.error(f"Failed to read user expiry from X-UI DB: {e}")
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.from_user:
        user = update.message.from_user
        update_user_info(user.id, user.username, user.first_name, user.last_name)

    tg_id = str(update.message.from_user.id)

    # Referral check
    args = context.args
    if args and len(args) > 0:
        referrer_id = args[0]
        if referrer_id != tg_id:
            set_referrer(tg_id, referrer_id)

    # Check if user has language set
    lang = get_lang(tg_id)

    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT lang FROM user_prefs WHERE tg_id=?", (tg_id,))
    row = cursor.fetchone()
    conn.close()

    if not row or not row[0]:
        # Show language selection
        keyboard = [
            [InlineKeyboardButton("English 🇬🇧", callback_data='set_lang_en')],
            [InlineKeyboardButton("Русский 🇷🇺", callback_data='set_lang_ru')]
        ]

        # Check for welcome image
        welcome_photo_path = "welcome.jpg"
        text = "Please select your language / Пожалуйста, выберите язык:"

        if os.path.exists(welcome_photo_path):
            try:
                with open(welcome_photo_path, 'rb') as photo:
                    await update.message.reply_photo(photo=photo, caption=text, reply_markup=InlineKeyboardMarkup(keyboard))
            except Exception as e:
                 logging.error(f"Failed to send welcome photo (start): {e}")
                 await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await show_main_menu(update, context, lang)

async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    lang = query.data.split('_')[2] # set_lang_en -> en
    tg_id = str(query.from_user.id)

    set_lang(tg_id, lang)

    await query.message.delete()
    await context.bot.send_message(chat_id=tg_id, text=t("lang_sel", lang))

    # Show main menu
    await show_main_menu_query(query, context, lang)

async def change_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("English 🇬🇧", callback_data='set_lang_en')],
        [InlineKeyboardButton("Русский 🇷🇺", callback_data='set_lang_ru')]
    ]
    text = "Please select your language / Пожалуйста, выберите язык:"

    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        await query.message.delete()
        await context.bot.send_message(chat_id=query.from_user.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, lang):
    tg_id = str(update.message.from_user.id)
    keyboard = [
        [InlineKeyboardButton(t("btn_buy", lang), callback_data="shop")],
        [InlineKeyboardButton(t("btn_trial", lang), callback_data='try_trial'), InlineKeyboardButton(t("btn_promo", lang), callback_data='enter_promo')],
        [InlineKeyboardButton(t("btn_config", lang), callback_data='get_config'), InlineKeyboardButton(t("btn_stats", lang), callback_data='stats')],
        [InlineKeyboardButton(t("btn_ref", lang), callback_data='referral'), InlineKeyboardButton(t("btn_lang", lang), callback_data='change_lang')],
        [InlineKeyboardButton(t("btn_support", lang), callback_data='support_menu')]
    ]
    keyboard.insert(2, [InlineKeyboardButton(t("btn_mobile", lang), callback_data="mobile_menu")])
    if tg_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton(t("btn_admin_panel", lang), callback_data='admin_panel')])

    text = t("main_menu", lang)

    # 1. Traffic Rank (Month)
    email = f"tg_{tg_id}"
    rank, total, traffic_val = get_user_rank_traffic(email)

    # Check for legacy email (manual)
    if rank is None or rank <= 0:
         # Try finding by tg_id in clients
         conn = sqlite3.connect(DB_PATH)
         cursor = conn.cursor()
         cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
         row = cursor.fetchone()
         conn.close()
         if row:
             settings = json.loads(row[0])
             clients = settings.get('clients', [])
             for c in clients:
                 if str(c.get('tgId', '')) == tg_id:
                     email = c.get('email', '')
                     rank, total, traffic_val = get_user_rank_traffic(email)
                     break
    traffic_total = get_user_total_traffic(email)
    if rank is not None and rank > 0:
        text += t("rank_info_traffic", lang).format(rank=rank, total=total, traffic=format_traffic(traffic_total))
    else:
        text += t("traffic_info", lang).format(traffic=format_traffic(traffic_total))

    # 2. Subscription Rank
    rank_sub, total_sub, days_left = get_user_rank_subscription(email)

    # Always show rank if valid
    if rank_sub is not None and rank_sub > 0:
        text += t("rank_info_sub", lang).format(rank=rank_sub, total=total_sub)
    elif days_left > 0:
         # If has active sub but not ranked (should not happen if logic is correct, unless total=0)
         pass
    else:
         # No active sub or unlimited, maybe show encouragement
         if days_left == 0: # Unlimited or expired
             pass

    # Check for welcome image
    welcome_photo_path = "welcome.jpg"
    if os.path.exists(welcome_photo_path):
        try:
            with open(welcome_photo_path, 'rb') as photo:
                await update.message.reply_photo(photo=photo, caption=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
        except Exception as e:
             logging.error(f"Failed to send welcome photo: {e}")
             await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def show_main_menu_query(query, context, lang):
    tg_id = str(query.from_user.id)
    keyboard = [
        [InlineKeyboardButton(t("btn_buy", lang), callback_data="shop")],
        [InlineKeyboardButton(t("btn_trial", lang), callback_data='try_trial'), InlineKeyboardButton(t("btn_promo", lang), callback_data='enter_promo')],
        [InlineKeyboardButton(t("btn_config", lang), callback_data='get_config'), InlineKeyboardButton(t("btn_stats", lang), callback_data='stats')],
        [InlineKeyboardButton(t("btn_ref", lang), callback_data='referral'), InlineKeyboardButton(t("btn_lang", lang), callback_data='change_lang')],
        [InlineKeyboardButton(t("btn_support", lang), callback_data='support_menu')]
    ]
    keyboard.insert(2, [InlineKeyboardButton(t("btn_mobile", lang), callback_data="mobile_menu")])
    if tg_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton(t("btn_admin_panel", lang), callback_data='admin_panel')])

    text = t("main_menu", lang)

    # 1. Traffic Rank (Month)
    email = f"tg_{tg_id}"
    rank, total, traffic_val = get_user_rank_traffic(email)

    # Check for legacy email (manual)
    if rank is None or rank <= 0:
         # Try finding by tg_id in clients
         conn = sqlite3.connect(DB_PATH)
         cursor = conn.cursor()
         cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
         row = cursor.fetchone()
         conn.close()
         if row:
             settings = json.loads(row[0])
             clients = settings.get('clients', [])
             for c in clients:
                 if str(c.get('tgId', '')) == tg_id:
                     email = c.get('email', '')
                     rank, total, traffic_val = get_user_rank_traffic(email)
                     break
    traffic_total = get_user_total_traffic(email)
    if rank is not None and rank > 0:
        text += t("rank_info_traffic", lang).format(rank=rank, total=total, traffic=format_traffic(traffic_total))
    else:
        text += t("traffic_info", lang).format(traffic=format_traffic(traffic_total))

    # 2. Subscription Rank
    rank_sub, total_sub, days_left = get_user_rank_subscription(email)

    # Always show rank if valid
    if rank_sub is not None and rank_sub > 0:
        text += t("rank_info_sub", lang).format(rank=rank_sub, total=total_sub)
    elif days_left > 0:
         # If has active sub but not ranked (should not happen if logic is correct, unless total=0)
         pass
    else:
         # No active sub or unlimited, maybe show encouragement
         if days_left == 0: # Unlimited or expired
             pass
    # welcome_photo_path = "welcome.jpg"
    # if os.path.exists(welcome_photo_path):
    #     try:
    #         # For query, we can't easily edit text to photo.
    #         # We delete previous message and send new photo.
    #         await query.message.delete()
    #         with open(welcome_photo_path, 'rb') as photo:
    #              await context.bot.send_photo(chat_id=query.from_user.id, photo=photo, caption=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    #     except Exception as e:
    #          logging.error(f"Failed to send welcome photo (query): {e}")
    #          await context.bot.send_message(chat_id=query.from_user.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    # else:

    # Try edit first, if fail (e.g. was photo), send new
    try:
        await context.bot.edit_message_text(chat_id=query.from_user.id, message_id=query.message.message_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    except Exception:
        await query.message.delete()
        await context.bot.send_message(chat_id=query.from_user.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')



async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    current_prices = get_prices()

    keyboard = []
    order = ["1_week", "2_weeks", "1_month", "3_months", "6_months", "1_year"]

    for key in order:
        if key in current_prices:
            data = current_prices[key]
            label_key = f"label_{key}"
            label = t(label_key, lang)
            keyboard.append([InlineKeyboardButton(f"{label} - {data['amount']} ⭐️", callback_data=f'buy_{key}')])

    keyboard.append([InlineKeyboardButton(t("btn_how_to_buy_stars", lang), callback_data='how_to_buy_stars')])
    keyboard.append([InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')])

    try:
        await query.edit_message_text(
            t("shop_title", lang),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        if "Message is not modified" not in str(e):
             # Likely a photo message, delete and send new
             await query.message.delete()
             await context.bot.send_message(
                 chat_id=tg_id,
                 text=t("shop_title", lang),
                 reply_markup=InlineKeyboardMarkup(keyboard),
                 parse_mode='Markdown'
             )


async def mobile_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    if not _mobile_feature_enabled():
        try:
            await query.edit_message_text(
                _mobile_not_configured_text(tg_id, lang),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data="back_to_main")]]),
                parse_mode="Markdown",
            )
        except Exception:
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=tg_id,
                text=_mobile_not_configured_text(tg_id, lang),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data="back_to_main")]]),
                parse_mode="Markdown",
            )
        return

    keyboard = [
        [InlineKeyboardButton(t("btn_mobile_buy", lang), callback_data="mobile_shop")],
        [InlineKeyboardButton(t("btn_mobile_config", lang), callback_data="mobile_config")],
        [InlineKeyboardButton(t("btn_mobile_stats", lang), callback_data="mobile_stats")],
        [InlineKeyboardButton(t("btn_back", lang), callback_data="back_to_main")],
    ]

    try:
        await query.edit_message_text(
            f"{t('mobile_menu_title', lang)}\n\n{t('mobile_menu_desc', lang)}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
    except Exception as e:
        if "Message is not modified" not in str(e):
            await query.message.delete()
            await context.bot.send_message(
                chat_id=tg_id,
                text=f"{t('mobile_menu_title', lang)}\n\n{t('mobile_menu_desc', lang)}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )


async def mobile_shop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    if not _mobile_feature_enabled():
        await mobile_menu(update, context)
        return

    current_prices = get_prices()
    keyboard: list[list[InlineKeyboardButton]] = []
    order = ["m_1_month", "m_3_months", "m_6_months", "m_1_year"]

    for key in order:
        if key in current_prices:
            data = current_prices[key]
            label = t(f"label_{key}", lang)
            keyboard.append([InlineKeyboardButton(f"{label} - {data['amount']} ⭐️", callback_data=f"buy_{key}")])

    keyboard.append([InlineKeyboardButton(t("btn_how_to_buy_stars", lang), callback_data="how_to_buy_stars")])
    keyboard.append([InlineKeyboardButton(t("btn_back", lang), callback_data="mobile_menu")])

    try:
        await query.edit_message_text(
            t("mobile_shop_title", lang),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
    except Exception as e:
        if "Message is not modified" not in str(e):
            await query.message.delete()
            await context.bot.send_message(
                chat_id=tg_id,
                text=t("mobile_shop_title", lang),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )

async def how_to_buy_stars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    text = t("how_to_buy_stars_text", lang)

    keyboard = [
        [InlineKeyboardButton(t("btn_back", lang), callback_data='shop')]
    ]

    try:
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        if "Message is not modified" not in str(e):
             await query.message.delete()
             await context.bot.send_message(
                 chat_id=tg_id,
                 text=text,
                 reply_markup=InlineKeyboardMarkup(keyboard),
                 parse_mode='Markdown'
             )

async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    # Clear states
    context.user_data['awaiting_promo'] = False
    context.user_data['admin_action'] = None

    keyboard = [
        [InlineKeyboardButton(t("btn_buy", lang), callback_data="shop")],
        [InlineKeyboardButton(t("btn_trial", lang), callback_data='try_trial'), InlineKeyboardButton(t("btn_promo", lang), callback_data='enter_promo')],
        [InlineKeyboardButton(t("btn_config", lang), callback_data='get_config'), InlineKeyboardButton(t("btn_stats", lang), callback_data='stats')],
        [InlineKeyboardButton(t("btn_ref", lang), callback_data='referral'), InlineKeyboardButton(t("btn_lang", lang), callback_data='change_lang')]
    ]
    keyboard.insert(2, [InlineKeyboardButton(t("btn_mobile", lang), callback_data="mobile_menu")])
    if tg_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton(t("btn_admin_panel", lang), callback_data='admin_panel')])

    text = t("main_menu", lang)

    # 1. Traffic Rank (Month)
    email = f"tg_{tg_id}"
    rank, total, traffic_val = get_user_rank_traffic(email)

    # Check for legacy email (manual)
    if not rank:
         # Try finding by tg_id in clients
         conn = sqlite3.connect(DB_PATH)
         cursor = conn.cursor()
         cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
         row = cursor.fetchone()
         conn.close()
         if row:
             settings = json.loads(row[0])
             clients = settings.get('clients', [])
             for c in clients:
                 if str(c.get('tgId', '')) == tg_id:
                     email = c.get('email', '')
                     rank, total, traffic_val = get_user_rank_traffic(email)
                     break

    if rank and rank > 0:
        text += t("rank_info_traffic", lang).format(rank=rank, total=total, traffic=format_traffic(traffic_val))

    # 2. Subscription Rank
    rank_sub, total_sub, days_left = get_user_rank_subscription(email)

    # Always show rank if valid
    if rank_sub is not None and rank_sub > 0:
        text += t("rank_info_sub", lang).format(rank=rank_sub, total=total_sub)
    elif days_left > 0:
         # If has active sub but not ranked (should not happen if logic is correct, unless total=0)
         pass
    else:
         # No active sub or unlimited, maybe show encouragement
         if days_left == 0: # Unlimited or expired
             pass

    # Revert to text-only main menu
    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    except Exception as e:
        if "Message is not modified" not in str(e):
             await query.message.delete()
             await context.bot.send_message(chat_id=query.from_user.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def try_trial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    keyboard = [
        [InlineKeyboardButton(t("btn_trial_3d", lang), callback_data="try_trial_3d")],
        [InlineKeyboardButton(t("btn_mobile_trial_1d", lang), callback_data="try_trial_mobile")],
        [InlineKeyboardButton(t("btn_back", lang), callback_data="back_to_main")],
    ]
    try:
        await query.edit_message_text(
            t("trial_menu_title", lang),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
    except Exception as e:
        if "Message is not modified" not in str(e):
            await query.message.delete()
            await context.bot.send_message(
                chat_id=tg_id,
                text=t("trial_menu_title", lang),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )

async def try_trial_3d(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    user_data = get_user_data(tg_id)
    if user_data["trial_used"]:
        date_str = "Unknown"
        if user_data.get("trial_activated_at"):
            date_str = datetime.datetime.fromtimestamp(user_data["trial_activated_at"], tz=TIMEZONE).strftime("%d.%m.%Y %H:%M")

        text = t("trial_used", lang).format(date=date_str)
        try:
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data="back_to_main")]]),
                parse_mode="Markdown",
            )
        except Exception as e:
            if "Message is not modified" not in str(e):
                await query.message.delete()
                await context.bot.send_message(
                    chat_id=tg_id,
                    text=text,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data="back_to_main")]]),
                    parse_mode="Markdown",
                )
        return

    log_action(f"ACTION: User {tg_id} (@{query.from_user.username}) activated TRIAL subscription.")
    ok = await process_subscription(tg_id, 3, update, context, lang, is_callback=True)
    if ok:
        mark_trial_used(tg_id)

async def try_trial_mobile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    trial = get_mobile_trial_data(tg_id)
    if int(trial.get("mobile_trial_used") or 0) == 1:
        date_str = "Unknown"
        activated_at = trial.get("mobile_trial_activated_at")
        if activated_at:
            date_str = datetime.datetime.fromtimestamp(int(activated_at), tz=TIMEZONE).strftime("%d.%m.%Y %H:%M")
        text = t("mobile_trial_used", lang).format(date=date_str)
        try:
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data="back_to_main")]]),
                parse_mode="Markdown",
            )
        except Exception as e:
            if "Message is not modified" not in str(e):
                await query.message.delete()
                await context.bot.send_message(
                    chat_id=tg_id,
                    text=text,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data="back_to_main")]]),
                    parse_mode="Markdown",
                )
        return

    log_action(f"ACTION: User {tg_id} (@{query.from_user.username}) activated MOBILE TRIAL subscription.")
    ok = await process_mobile_subscription(tg_id, 1, update, context, lang, is_callback=True)
    if ok:
        mark_mobile_trial_used(tg_id)

async def referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    try:
        bot_username = context.bot.username
        # Fallback if username not cached
        if not bot_username:
             me = await context.bot.get_me()
             bot_username = me.username

        link = f"https://t.me/{bot_username}?start={tg_id}"
        count = count_referrals(tg_id)

        # Get balance
        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT balance FROM user_prefs WHERE tg_id=?", (tg_id,))
        row = cursor.fetchone()
        balance = row[0] if row else 0
        conn.close()

        text = t("ref_title", lang).format(link=link, count=count)
        text += f"\n\n💰 Balance: {balance} Stars" if lang == 'en' else f"\n\n💰 Баланс: {balance} Stars"

        keyboard = [
            [InlineKeyboardButton("📜 My Referrals" if lang == 'en' else "📜 Мои рефералы", callback_data='my_referrals')],
            [InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]
        ]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
    except Exception as e:
        logging.error(f"Referral error for {tg_id}: {e}")
        # Try sending without markdown if that was the issue
        try:
            # Try to delete the old message (ignore if not found)
            try:
                await query.message.delete()
            except Exception:
                pass

            # Remove HTML tags for fallback
            clean_text = text.replace('<b>', '').replace('</b>', '').replace('<code>', '').replace('</code>', '').replace('`', '')
            await context.bot.send_message(
                 chat_id=tg_id,
                 text=clean_text,
                 reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]])
            )
        except Exception as e2:
            logging.error(f"Referral fallback error: {e2}")

async def enter_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    text = t("promo_prompt", lang)
    try:
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]]),
            parse_mode='Markdown'
        )
    except Exception as e:
        if "Message is not modified" not in str(e):
             await query.message.delete()
             await context.bot.send_message(
                 chat_id=tg_id,
                 text=text,
                 reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]]),
                 parse_mode='Markdown'
             )
    context.user_data['awaiting_promo'] = True
    context.user_data['admin_action'] = None

async def my_referrals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()

    # Get total count
    cursor.execute("SELECT COUNT(*) FROM user_prefs WHERE referrer_id=?", (tg_id,))
    total_count = cursor.fetchone()[0]

    # Get last 10 referrals
    cursor.execute("SELECT tg_id, first_name, username FROM user_prefs WHERE referrer_id=? ORDER BY ROWID DESC LIMIT 10", (tg_id,))
    rows = cursor.fetchall()

    conn.close()

    title = "📜 My Referrals" if lang == 'en' else "📜 Мои рефералы"
    text = f"*{title}*\n\n"
    text += f"Total invited: {total_count}\n\n" if lang == 'en' else f"Всего приглашено: {total_count}\n\n"

    if rows:
        for r in rows:
            uid, fname, uname = r
            name = fname or uid
            if uname:
                name += f" (@{uname})"
            text += f"👤 {name}\n"
    else:
        text += "List is empty." if lang == 'en' else "Список пуст."

    keyboard = [[InlineKeyboardButton(t("btn_back", lang), callback_data='referral')]]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def show_qrcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    username = query.from_user.username or "User"

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
        row = cursor.fetchone()

        if not row:
             conn.close()
             await query.message.reply_text("Error: Inbound not found.")
             return

        settings = json.loads(row[0])
        clients = settings.get('clients', [])

        user_client = None
        for client in clients:
            if str(client.get('tgId', '')) == tg_id or client.get('email') == f"tg_{tg_id}":
                user_client = client
                break

        conn.close()

        if user_client:
            u_uuid = user_client['id']
            client_email = user_client.get('email', f"VPN_{username}")
            client_flow = user_client.get('flow', '')
            spx_val = _get_spiderx_encoded()
            vless_link = _build_location_vless_link_with_settings(
                {"host": IP or "", "port": PORT or 443},
                u_uuid,
                client_email,
                base_settings={"port": PORT, "public_key": PUBLIC_KEY, "sni": SNI, "sid": SID},
                client_flow=client_flow,
                spx_val=spx_val,
            )
            if not vless_link:
                await query.edit_message_text(
                    t("error_generic", lang),
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]]),
                )
                return
            if not vless_link:
                await query.message.reply_text(t("error_generic", lang))
                return

            # Generate QR
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(vless_link)
            qr.make(fit=True)

            img = qr.make_image(fill_color="black", back_color="white")
            bio = BytesIO()
            bio.name = 'qrcode.png'
            img.save(bio, 'PNG')
            bio.seek(0)

            await context.bot.send_photo(
                chat_id=tg_id,
                photo=bio,
                caption=f"QR Code for: <code>{client_email}</code>",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='get_config')]])
            )
        else:
            await query.message.reply_text(t("sub_not_found", lang))

    except Exception as e:
        logging.error(f"Error showing QR: {e}")
        await query.message.reply_text("Error generating QR code.")

async def backup_db(context: Optional[ContextTypes.DEFAULT_TYPE] = None):
    try:
        backup_dir = "/usr/local/x-ui/bot/backups"
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)

        timestamp = datetime.datetime.now(TIMEZONE).strftime("%Y-%m-%d_%H-%M-%S")
        created_files = []

        # Backup Bot DB
        if os.path.exists(BOT_DB_PATH):
            dest = f"{backup_dir}/bot_data_{timestamp}.db"
            shutil.copy2(BOT_DB_PATH, dest)
            created_files.append(dest)

        # Backup X-UI DB
        if os.path.exists(DB_PATH):
            dest = f"{backup_dir}/x-ui_{timestamp}.db"
            shutil.copy2(DB_PATH, dest)
            created_files.append(dest)

        keep_sets = BACKUP_KEEP_SETS if isinstance(BACKUP_KEEP_SETS, int) and BACKUP_KEEP_SETS > 0 else 0
        if keep_sets > 0:
            sets = _get_backup_sets(backup_dir)
            for set_ in sets[keep_sets:]:
                for key in ("bot_path", "xui_path"):
                    path = set_.get(key)
                    if isinstance(path, str) and os.path.isfile(path):
                        os.remove(path)
        else:
            files = sorted(
                [os.path.join(backup_dir, f) for f in os.listdir(backup_dir)],
                key=os.path.getmtime,
            )
            keep_files = BACKUP_KEEP_FILES if isinstance(BACKUP_KEEP_FILES, int) and BACKUP_KEEP_FILES > 0 else 20
            if len(files) > keep_files:
                for f in files[:-keep_files]:
                    os.remove(f)

        logging.info(f"Backup completed: {timestamp}")
        return created_files
    except Exception as e:
        logging.error(f"Backup failed: {e}")
        return []

async def send_backup_to_admin_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Scheduled job to create and send backup to admin.
    """
    files = await backup_db(context)
    if files:
        for file_path in files:
            try:
                await context.bot.send_document(
                    chat_id=ADMIN_ID,
                    document=open(file_path, 'rb'),
                    caption=f"📦 Backup: {os.path.basename(file_path)}"
                )
            except Exception as e:
                logging.error(f"Failed to send backup {file_path}: {e}")
    else:
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text="❌ Automatic Backup Failed (No files created).")
        except Exception:
            pass

async def admin_view_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                # Read first 3000 chars (latest logs)
                content = f.read(3000)
                if len(content) == 3000:
                    content += "\n...(далее обрезано)"
        else:
            content = "Log file empty or not found."

        text = t("logs_title", lang) + f"```\n{content}\n```"

        keyboard = [
            [InlineKeyboardButton(t("btn_refresh", lang), callback_data='admin_logs')],
            [InlineKeyboardButton(t("btn_clear_logs", lang), callback_data='admin_clear_logs')],
            [InlineKeyboardButton(t("btn_back_admin", lang), callback_data='admin_panel')]
        ]

        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        except Exception as e:
            if "Message is not modified" not in str(e):
                 await query.message.delete()
                 await context.bot.send_message(chat_id=query.from_user.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    except Exception as e:
        logging.error(f"Error reading logs: {e}")
        await query.message.reply_text(t("logs_read_error", lang))

async def admin_clear_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    await query.answer(t("logs_cleared", lang))

    try:
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            f.write("")

        await admin_view_logs(update, context)
    except Exception as e:
        logging.error(f"Error clearing logs: {e}")

async def admin_backup_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    if tg_id != ADMIN_ID:
        return

    lang = get_lang(tg_id)
    keyboard = [
        [InlineKeyboardButton(t("btn_backup_create", lang), callback_data="admin_create_backup")],
        [InlineKeyboardButton(t("btn_admin_restore", lang), callback_data="admin_restore_menu")],
        [InlineKeyboardButton(t("btn_back_admin", lang), callback_data="admin_panel")],
    ]
    await query.edit_message_text(
        t("backup_menu_text", lang),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def admin_create_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    await query.answer(t("backup_starting", lang))

    files = await backup_db()

    keyboard = [
        [InlineKeyboardButton(t("btn_backup_create", lang), callback_data="admin_create_backup")],
        [InlineKeyboardButton(t("btn_admin_restore", lang), callback_data="admin_restore_menu")],
        [InlineKeyboardButton(t("btn_back_admin", lang), callback_data="admin_panel")],
    ]

    if files:
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text=t("backup_success", lang),
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        # Also send files
        for file_path in files:
            try:
                await context.bot.send_document(
                    chat_id=query.from_user.id,
                    document=open(file_path, 'rb'),
                    caption=f"📦 Backup: {os.path.basename(file_path)}"
                )
            except Exception:
                pass
    else:
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text=t("backup_error", lang),
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

class BackupSet(TypedDict):
    ts: str
    bot_path: Optional[str]
    xui_path: Optional[str]
    mtime: float


def _get_backup_sets(backup_dir: str = "/usr/local/x-ui/bot/backups") -> list[BackupSet]:
    import re

    ts_re = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")
    if not os.path.isdir(backup_dir):
        return []

    by_ts: dict[str, BackupSet] = {}
    for name in os.listdir(backup_dir):
        if not name.endswith(".db"):
            continue
        full_path = os.path.join(backup_dir, name)
        if not os.path.isfile(full_path):
            continue

        if name.startswith("bot_data_"):
            ts = name.removeprefix("bot_data_").removesuffix(".db")
            kind = "bot"
        elif name.startswith("x-ui_"):
            ts = name.removeprefix("x-ui_").removesuffix(".db")
            kind = "xui"
        else:
            continue

        if not ts_re.match(ts):
            continue

        try:
            mtime = os.path.getmtime(full_path)
        except Exception:
            mtime = 0.0

        existing = by_ts.get(ts)
        if existing is None:
            by_ts[ts] = {
                "ts": ts,
                "bot_path": full_path if kind == "bot" else None,
                "xui_path": full_path if kind == "xui" else None,
                "mtime": mtime,
            }
        else:
            if kind == "bot":
                existing["bot_path"] = full_path
            else:
                existing["xui_path"] = full_path
            existing["mtime"] = max(existing["mtime"], mtime)

    return sorted(by_ts.values(), key=lambda x: x["mtime"], reverse=True)


def _format_restore_targets(set_: BackupSet) -> str:
    lines: list[str] = []
    if set_.get("bot_path"):
        lines.append(f"• BOT DB → `{BOT_DB_PATH}`")
    if set_.get("xui_path"):
        lines.append(f"• X-UI DB → `{DB_PATH}`")
    return "\n".join(lines) if lines else "—"


def _safe_backup_label(path: str) -> str:
    try:
        return os.path.basename(path)
    except Exception:
        return str(path)

def _sqlite_has_header(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            header = f.read(16)
        return header.startswith(b"SQLite format 3\x00")
    except Exception:
        return False

def _sqlite_table_names(path: str) -> set[str]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        rows = cursor.fetchall()
        names: set[str] = set()
        for row in rows:
            name = row[0] if row else None
            if isinstance(name, str):
                names.add(name)
        return names
    finally:
        conn.close()

def _detect_sqlite_db_kind(path: str) -> Optional[str]:
    if not _sqlite_has_header(path):
        return None
    try:
        tables = _sqlite_table_names(path)
    except Exception:
        return None

    xui_markers = {"inbounds", "client_traffics", "users"}
    bot_markers = {"user_prefs", "promo_codes", "transactions", "traffic_history"}

    xui_score = len(tables & xui_markers)
    bot_score = len(tables & bot_markers)

    if xui_score == 0 and bot_score == 0:
        return None
    if xui_score >= bot_score and xui_score > 0:
        return "xui"
    if bot_score > 0:
        return "bot"
    return None

def _sqlite_integrity_error(path: str) -> Optional[str]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check")
        row = cursor.fetchone()
        result = row[0] if row else None
        if isinstance(result, str) and result.lower() == "ok":
            return None
        if result is None:
            return "integrity_check: empty result"
        return str(result)
    finally:
        conn.close()

def _validate_sqlite_backup_or_raise(path: str, expected_kind: str, lang: str) -> None:
    if not os.path.exists(path):
        raise ValueError("Файл бэкапа не найден." if lang == "ru" else "Backup file not found.")
    if not _sqlite_has_header(path):
        raise ValueError("Файл не похож на SQLite DB." if lang == "ru" else "File does not look like an SQLite DB.")

    detected = _detect_sqlite_db_kind(path)
    if detected is None:
        raise ValueError(
            "Не удалось распознать тип БД. Восстановление отменено."
            if lang == "ru"
            else "Could not detect DB type. Restore cancelled."
        )
    if detected != expected_kind:
        if lang == "ru":
            raise ValueError(f"Тип БД не совпадает (ожидалось: {expected_kind}, получено: {detected}).")
        raise ValueError(f"DB type mismatch (expected: {expected_kind}, got: {detected}).")

    integrity_error = _sqlite_integrity_error(path)
    if integrity_error is not None:
        if lang == "ru":
            raise ValueError(f"Проверка целостности SQLite не прошла: {integrity_error}")
        raise ValueError(f"SQLite integrity check failed: {integrity_error}")


def _preflight_sqlite_backup(path: str, expected_kind: str, lang: str) -> tuple[bool, str]:
    label = _safe_backup_label(path)
    if not os.path.exists(path):
        msg = "файл не найден" if lang == "ru" else "file not found"
        return False, f"• `{label}`: ❌ `{msg}`"
    if not _sqlite_has_header(path):
        msg = "не похоже на SQLite" if lang == "ru" else "not an SQLite file"
        return False, f"• `{label}`: ❌ `{msg}`"

    try:
        tables = _sqlite_table_names(path)
    except Exception as e:
        return False, f"• `{label}`: ❌ `tables_read_failed` `{str(e)[:120]}`"

    xui_markers = {"inbounds", "client_traffics", "users"}
    bot_markers = {"user_prefs", "promo_codes", "transactions", "traffic_history"}
    xui_score = len(tables & xui_markers)
    bot_score = len(tables & bot_markers)
    detected: Optional[str]
    if xui_score == 0 and bot_score == 0:
        detected = None
    elif xui_score >= bot_score and xui_score > 0:
        detected = "xui"
    elif bot_score > 0:
        detected = "bot"
    else:
        detected = None

    integrity_error = None
    try:
        integrity_error = _sqlite_integrity_error(path)
    except Exception as e:
        integrity_error = str(e)[:120]

    ok = detected == expected_kind and integrity_error is None
    detected_str = detected if detected is not None else "unknown"
    integrity_str = "ok" if integrity_error is None else integrity_error
    return (
        ok,
        f"• `{label}`: "
        f"{'✅' if ok else '❌'} "
        f"{'тип' if lang == 'ru' else 'kind'}=`{detected_str}` "
        f"{'ожидалось' if lang == 'ru' else 'expected'}=`{expected_kind}` "
        f"{'таблиц' if lang == 'ru' else 'tables'}=`{len(tables)}` "
        f"integrity=`{integrity_str}`",
    )

def _parse_int(value: object | None, default: int = 0) -> int:
    if isinstance(value, int):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return int(value)
        except Exception:
            return default
    try:
        return int(str(value))
    except Exception:
        return default


async def admin_restore_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    if tg_id != ADMIN_ID:
        return

    lang = get_lang(tg_id)
    sets = _get_backup_sets()

    if not sets:
        empty_keyboard = [
            [InlineKeyboardButton(t("btn_back_admin", lang), callback_data="admin_backup_menu")],
        ]
        await query.edit_message_text(
            t("restore_no_backups", lang),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(empty_keyboard),
        )
        return

    data = query.data or ""
    page = 0
    if data.startswith("admin_restore_menu_"):
        try:
            page = int(data.removeprefix("admin_restore_menu_"))
        except Exception:
            page = 0
    else:
        page = _parse_int(context.user_data.get("restore_page"), default=0)

    page_size = 10
    total_pages = max(1, (len(sets) + page_size - 1) // page_size)
    if page < 0:
        page = 0
    if page >= total_pages:
        page = total_pages - 1
    context.user_data["restore_page"] = page

    start = page * page_size
    end = start + page_size

    keyboard: list[list[InlineKeyboardButton]] = []
    for s in sets[start:end]:
        flags: list[str] = []
        if s.get("bot_path"):
            flags.append("BOT")
        if s.get("xui_path"):
            flags.append("XUI")
        label = f"{s['ts']} ({'/'.join(flags)})"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"admin_restore_sel_{s['ts']}")])

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️", callback_data=f"admin_restore_menu_{page-1}"))
    if total_pages > 1:
        nav_row.append(InlineKeyboardButton(t("restore_page_text", lang).format(page=page + 1, total=total_pages), callback_data=f"admin_restore_menu_{page}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("➡️", callback_data=f"admin_restore_menu_{page+1}"))
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton(t("btn_restart_xui", lang), callback_data="admin_restart_xui")])
    keyboard.append([InlineKeyboardButton(t("btn_refresh", lang), callback_data=f"admin_restore_menu_{page}")])
    keyboard.append([InlineKeyboardButton(t("btn_back_admin", lang), callback_data="admin_backup_menu")])

    try:
        await query.edit_message_text(
            t("restore_menu_text", lang),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise


async def admin_restore_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    import re

    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    if tg_id != ADMIN_ID:
        return

    lang = get_lang(tg_id)
    data = query.data or ""
    ts = data.removeprefix("admin_restore_sel_")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}", ts):
        await admin_restore_menu(update, context)
        return

    selected = next((s for s in _get_backup_sets() if s["ts"] == ts), None)
    if selected is None:
        await admin_restore_menu(update, context)
        return

    targets = _format_restore_targets(selected)
    preflight_lines: list[str] = []
    preflight_ok = True
    bot_path = selected.get("bot_path")
    if isinstance(bot_path, str) and bot_path:
        ok, line = _preflight_sqlite_backup(bot_path, expected_kind="bot", lang=lang)
        preflight_ok = preflight_ok and ok
        preflight_lines.append(line)
    xui_path = selected.get("xui_path")
    if isinstance(xui_path, str) and xui_path:
        ok, line = _preflight_sqlite_backup(xui_path, expected_kind="xui", lang=lang)
        preflight_ok = preflight_ok and ok
        preflight_lines.append(line)
    preflight_text = "\n".join(preflight_lines) if preflight_lines else "—"

    page = _parse_int(context.user_data.get("restore_page"), default=0)
    keyboard: list[list[InlineKeyboardButton]] = []
    if preflight_ok:
        keyboard.append([InlineKeyboardButton(t("btn_restore_confirm", lang), callback_data=f"admin_restore_do_{ts}")])
    keyboard.append([InlineKeyboardButton(t("btn_backup_delete", lang), callback_data=f"admin_backup_del_{ts}")])
    keyboard.append([InlineKeyboardButton(t("btn_restore_cancel", lang), callback_data=f"admin_restore_menu_{page}")])

    text = (
        t("restore_confirm", lang).format(ts=ts, targets=targets)
        + "\n\n"
        + t("restore_preflight_title", lang)
        + "\n"
        + preflight_text
    )
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_backup_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    import re

    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    if tg_id != ADMIN_ID:
        return

    lang = get_lang(tg_id)
    data = query.data or ""
    ts = data.removeprefix("admin_backup_del_")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}", ts):
        await admin_restore_menu(update, context)
        return

    selected = next((s for s in _get_backup_sets() if s["ts"] == ts), None)
    if selected is None:
        await admin_restore_menu(update, context)
        return

    targets_lines: list[str] = []
    bot_path = selected["bot_path"]
    if bot_path:
        targets_lines.append(f"• `{_safe_backup_label(bot_path)}`")
    xui_path = selected["xui_path"]
    if xui_path:
        targets_lines.append(f"• `{_safe_backup_label(xui_path)}`")
    targets = "\n".join(targets_lines) if targets_lines else "—"

    keyboard = [
        [InlineKeyboardButton(t("btn_backup_delete", lang), callback_data=f"admin_backup_del_do_{ts}")],
        [InlineKeyboardButton(t("btn_restore_cancel", lang), callback_data=f"admin_restore_sel_{ts}")],
    ]
    text = t("backup_delete_confirm", lang).format(ts=ts, targets=targets)
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_backup_delete_do(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    import re

    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    if tg_id != ADMIN_ID:
        return

    lang = get_lang(tg_id)
    data = query.data or ""
    ts = data.removeprefix("admin_backup_del_do_")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}", ts):
        await admin_restore_menu(update, context)
        return

    selected = next((s for s in _get_backup_sets() if s["ts"] == ts), None)
    if selected is None:
        await admin_restore_menu(update, context)
        return

    deleted_lines: list[str] = []
    try:
        for key in ("bot_path", "xui_path"):
            path = selected.get(key)
            if isinstance(path, str) and os.path.isfile(path):
                os.remove(path)
                deleted_lines.append(f"• `{_safe_backup_label(path)}`")
        deleted = "\n".join(deleted_lines) if deleted_lines else "—"
        text = t("backup_delete_done", lang).format(targets=deleted)
        page = _parse_int(context.user_data.get("restore_page"), default=0)
        keyboard = [
            [InlineKeyboardButton(t("btn_refresh", lang), callback_data=f"admin_restore_menu_{page}")],
            [InlineKeyboardButton(t("btn_back_admin", lang), callback_data="admin_backup_menu")],
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        err = str(e)
        text = t("backup_delete_failed", lang).format(error=f"`{err[:1500]}`")
        page = _parse_int(context.user_data.get("restore_page"), default=0)
        keyboard = [
            [InlineKeyboardButton(t("btn_back_admin", lang), callback_data="admin_backup_menu")],
            [InlineKeyboardButton(t("btn_refresh", lang), callback_data=f"admin_restore_menu_{page}")],
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


def _atomic_restore_db(src: str, dest: str) -> None:
    tmp_path = f"{dest}.restore_tmp"
    shutil.copy2(src, tmp_path)
    os.replace(tmp_path, dest)


async def admin_restore_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    import re

    query = update.callback_query
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    if tg_id != ADMIN_ID:
        await query.answer()
        return
    if _RESTORE_LOCK.locked():
        await query.answer(t("restore_in_progress", lang), show_alert=True)
        return

    await query.answer(t("restore_starting", lang))

    data = query.data or ""
    ts = data.removeprefix("admin_restore_do_")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}", ts):
        await admin_restore_menu(update, context)
        return

    selected = next((s for s in _get_backup_sets() if s["ts"] == ts), None)
    if selected is None:
        await admin_restore_menu(update, context)
        return

    async with _RESTORE_LOCK:
        try:
            try:
                await query.edit_message_text(t("restore_starting", lang), parse_mode="Markdown")
            except Exception:
                pass

            bot_path = selected.get("bot_path")
            xui_path = selected.get("xui_path")
            if bot_path:
                _validate_sqlite_backup_or_raise(bot_path, expected_kind="bot", lang=lang)
            if xui_path:
                _validate_sqlite_backup_or_raise(xui_path, expected_kind="xui", lang=lang)

            safety_files = await backup_db()
            safety_label = "\n".join(f"• `{_safe_backup_label(p)}`" for p in safety_files) if safety_files else "—"

            restored_lines: list[str] = []
            if bot_path:
                _atomic_restore_db(bot_path, BOT_DB_PATH)
                restored_lines.append(f"• BOT DB ← `{_safe_backup_label(bot_path)}`")

            if xui_path:
                _atomic_restore_db(xui_path, DB_PATH)
                restored_lines.append(f"• X-UI DB ← `{_safe_backup_label(xui_path)}`")
                load_config_from_db()

            targets = "\n".join(restored_lines) if restored_lines else "—"
            text = t("restore_done", lang).format(targets=targets, safety=safety_label)
            keyboard: list[list[InlineKeyboardButton]] = []
            if xui_path:
                keyboard.append([InlineKeyboardButton(t("btn_restart_xui", lang), callback_data="admin_restart_xui")])
            if bot_path:
                keyboard.append([InlineKeyboardButton(t("btn_restart_bot", lang), callback_data="admin_restart_bot")])
            keyboard.append([InlineKeyboardButton(t("btn_back_admin", lang), callback_data="admin_backup_menu")])
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            err = str(e)
            page = _parse_int(context.user_data.get("restore_page"), default=0)
            text = t("restore_failed", lang).format(error=f"`{err[:1500]}`")
            keyboard = [
                [InlineKeyboardButton(t("btn_back_admin", lang), callback_data="admin_backup_menu")],
                [InlineKeyboardButton(t("btn_refresh", lang), callback_data=f"admin_restore_menu_{page}")],
            ]
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_restart_xui(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    if tg_id != ADMIN_ID:
        await query.answer()
        return

    await query.answer(t("restart_starting", lang))
    try:
        await _systemctl("restart", XUI_SYSTEMD_SERVICE)
        text = t("restart_done", lang)
    except Exception as e:
        text = t("restart_failed", lang).format(error=f"`{str(e)[:1500]}`")
    page = _parse_int(context.user_data.get("restore_page"), default=0)
    keyboard = [
        [InlineKeyboardButton(t("btn_back_admin", lang), callback_data="admin_backup_menu")],
        [InlineKeyboardButton(t("btn_refresh", lang), callback_data=f"admin_restore_menu_{page}")],
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_restart_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    if tg_id != ADMIN_ID:
        await query.answer()
        return

    await query.answer(t("bot_restart_starting", lang))
    try:
        await _systemctl("restart", BOT_SYSTEMD_SERVICE)
        text = t("bot_restart_done", lang)
    except Exception as e:
        text = t("bot_restart_failed", lang).format(error=f"`{str(e)[:1500]}`")
    page = _parse_int(context.user_data.get("restore_page"), default=0)
    keyboard = [
        [InlineKeyboardButton(t("btn_back_admin", lang), callback_data="admin_backup_menu")],
        [InlineKeyboardButton(t("btn_refresh", lang), callback_data=f"admin_restore_menu_{page}")],
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_upload_restore_prepare(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str) -> None:
    query = update.callback_query
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    if tg_id != ADMIN_ID:
        await query.answer()
        return
    if _RESTORE_LOCK.locked():
        await query.answer(t("restore_in_progress", lang), show_alert=True)
        return

    pending_path = context.user_data.get("pending_upload_path")
    if not isinstance(pending_path, str) or not os.path.exists(pending_path):
        await query.answer(t("upload_restore_missing", lang), show_alert=True)
        await admin_restore_menu(update, context)
        return

    if kind not in ("xui", "bot"):
        await query.answer(t("upload_restore_failed", lang).format(error="`unknown_kind`"), show_alert=True)
        return

    await query.answer()
    ok, check_line = _preflight_sqlite_backup(pending_path, expected_kind=kind, lang=lang)
    text = t("upload_restore_confirm", lang).format(
        name=_safe_backup_label(pending_path),
        kind=kind,
        check=check_line,
    )

    keyboard: list[list[InlineKeyboardButton]] = []
    if ok:
        keyboard.append([InlineKeyboardButton(t("btn_restore_confirm", lang), callback_data=f"admin_upload_restore_do_{kind}")])
    keyboard.append([InlineKeyboardButton(t("btn_restore_cancel", lang), callback_data="admin_backup_menu")])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_restore_uploaded_as(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str) -> None:
    query = update.callback_query
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    if tg_id != ADMIN_ID:
        await query.answer()
        return
    if _RESTORE_LOCK.locked():
        await query.answer(t("restore_in_progress", lang), show_alert=True)
        return

    pending_path = context.user_data.get("pending_upload_path")
    pending_ts = context.user_data.get("pending_upload_ts")
    if not isinstance(pending_path, str) or not isinstance(pending_ts, str) or not os.path.exists(pending_path):
        await query.answer(t("upload_restore_missing", lang), show_alert=True)
        await admin_restore_menu(update, context)
        return

    await query.answer(t("upload_restore_starting", lang))
    async with _RESTORE_LOCK:
        try:
            try:
                await query.edit_message_text(t("upload_restore_starting", lang), parse_mode="Markdown")
            except Exception:
                pass

            if kind in ("xui", "bot"):
                _validate_sqlite_backup_or_raise(pending_path, expected_kind=kind, lang=lang)

            safety_files = await backup_db()
            safety_label = "\n".join(f"• `{_safe_backup_label(p)}`" for p in safety_files) if safety_files else "—"

            backup_dir = "/usr/local/x-ui/bot/backups"
            os.makedirs(backup_dir, exist_ok=True)

            restored_lines: list[str] = []
            if kind == "xui":
                stored_name = f"x-ui_{pending_ts}.db"
                stored_path = os.path.join(backup_dir, stored_name)
                shutil.copy2(pending_path, stored_path)
                _atomic_restore_db(stored_path, DB_PATH)
                load_config_from_db()
                restored_lines.append(f"• X-UI DB ← `{_safe_backup_label(stored_path)}`")
            elif kind == "bot":
                stored_name = f"bot_data_{pending_ts}.db"
                stored_path = os.path.join(backup_dir, stored_name)
                shutil.copy2(pending_path, stored_path)
                _atomic_restore_db(stored_path, BOT_DB_PATH)
                restored_lines.append(f"• BOT DB ← `{_safe_backup_label(stored_path)}`")
            else:
                raise ValueError("Unknown restore kind")

            targets = "\n".join(restored_lines) if restored_lines else "—"
            text = t("upload_restore_done", lang).format(targets=targets, safety=safety_label)
            keyboard: list[list[InlineKeyboardButton]] = []
            if kind == "xui":
                keyboard.append([InlineKeyboardButton(t("btn_restart_xui", lang), callback_data="admin_restart_xui")])
            if kind == "bot":
                keyboard.append([InlineKeyboardButton(t("btn_restart_bot", lang), callback_data="admin_restart_bot")])
            keyboard.append([InlineKeyboardButton(t("btn_back_admin", lang), callback_data="admin_backup_menu")])
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            err = str(e)
            text = t("upload_restore_failed", lang).format(error=f"`{err[:1500]}`")
            keyboard = [
                [InlineKeyboardButton(t("btn_back_admin", lang), callback_data="admin_backup_menu")],
                [InlineKeyboardButton(t("btn_refresh", lang), callback_data="admin_restore_menu_0")],
            ]
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_restore_uploaded_as_xui(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await admin_upload_restore_prepare(update, context, kind="xui")


async def admin_restore_uploaded_as_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await admin_upload_restore_prepare(update, context, kind="bot")


async def admin_restore_uploaded_do_xui(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await admin_restore_uploaded_as(update, context, kind="xui")


async def admin_restore_uploaded_do_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await admin_restore_uploaded_as(update, context, kind="bot")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        tg_id = str(query.from_user.id)
    else:
        tg_id = str(update.message.from_user.id)

    if tg_id != ADMIN_ID:
        return

    lang = get_lang(tg_id)

    buttons = [
        InlineKeyboardButton(t("btn_admin_stats", lang), callback_data='admin_stats'),
        InlineKeyboardButton(t("btn_admin_server", lang), callback_data='admin_server'),
        InlineKeyboardButton(t("btn_admin_ru_bridge", lang), callback_data='ru_bridge_config'),
        InlineKeyboardButton(t("btn_admin_prices", lang), callback_data='admin_prices'),
        InlineKeyboardButton(t("btn_admin_promos", lang), callback_data='admin_promos_menu'),
        InlineKeyboardButton(t("btn_suspicious", lang), callback_data='admin_suspicious'),
        InlineKeyboardButton(t("btn_leaderboard", lang), callback_data='admin_leaderboard'),
        InlineKeyboardButton(t("btn_admin_poll", lang), callback_data='admin_poll_menu'),
        InlineKeyboardButton(t("btn_admin_broadcast", lang), callback_data='admin_broadcast'),
        InlineKeyboardButton(t("btn_admin_sales", lang), callback_data='admin_sales_log'),
        InlineKeyboardButton(t("btn_admin_remote_panels", lang), callback_data='admin_remote_panels'),
        InlineKeyboardButton(t("btn_admin_remote_locations", lang), callback_data='admin_remote_locations'),
        InlineKeyboardButton(t("btn_admin_remote_nodes", lang), callback_data='admin_remote_nodes'),
        InlineKeyboardButton(t("btn_admin_backup", lang), callback_data='admin_backup_menu'),
        InlineKeyboardButton(t("btn_admin_logs", lang), callback_data='admin_logs'),
    ]
    keyboard = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    keyboard.append([InlineKeyboardButton(t("btn_main_menu_back", lang), callback_data='back_to_main')])

    text = t("admin_menu_text", lang)

    # We use edit_message_text if callback, reply if command
    if query:
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        except Exception as e:
            if "Message is not modified" not in str(e):
                 await query.message.delete()
                 await context.bot.send_message(chat_id=tg_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_remote_panels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        tg_id = str(query.from_user.id)
    else:
        tg_id = str(update.message.from_user.id)

    if tg_id != ADMIN_ID:
        return

    lang = get_lang(tg_id)
    keyboard = [
        [InlineKeyboardButton(t("btn_remote_add", lang), callback_data="admin_remote_panels_add")],
        [InlineKeyboardButton(t("btn_remote_list", lang), callback_data="admin_remote_panels_list")],
        [InlineKeyboardButton(t("btn_remote_check", lang), callback_data="admin_remote_panels_check")],
        [InlineKeyboardButton(t("btn_back_admin", lang), callback_data="admin_panel")],
    ]
    text = t("remote_panels_title", lang)

    if query:
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        except Exception as e:
            if "Message is not modified" not in str(e):
                await query.message.delete()
                await context.bot.send_message(chat_id=tg_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_remote_panels_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    if tg_id != ADMIN_ID:
        return
    lang = get_lang(tg_id)
    context.user_data["admin_action"] = "awaiting_remote_panel"
    await query.edit_message_text(t("remote_panel_prompt", lang), parse_mode="Markdown")

async def admin_remote_panels_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    if tg_id != ADMIN_ID:
        return
    lang = get_lang(tg_id)
    panels = _fetch_remote_panels()
    if not panels:
        await asyncio.get_running_loop().run_in_executor(None, _sync_remote_nodes_locations)
        panels = _fetch_remote_panels()
    if not panels:
        text = t("remote_list_empty", lang)
    else:
        lines = []
        for idx, panel in enumerate(panels, start=1):
            name = _escape_markdown(panel["name"] or "")
            base_url = _escape_markdown(panel["base_url"] or "")
            lines.append(f"{idx}. {name} | {base_url}")
        text = f"{t('remote_panels_title', lang)}\n\n" + "\n".join(lines)
    keyboard = [
        [InlineKeyboardButton(f"🗑 {t('btn_delete', lang)} {panel['name']}", callback_data=f"admin_remote_panels_del_{panel['id']}")]
        for panel in panels
    ]
    keyboard.append([InlineKeyboardButton(t("btn_back", lang), callback_data="admin_remote_panels")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_remote_panels_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    if tg_id != ADMIN_ID:
        return
    lang = get_lang(tg_id)
    panels = _fetch_remote_panels()
    if not panels:
        text = t("remote_list_empty", lang)
    else:
        lines = []
        for idx, panel in enumerate(panels, start=1):
            ok = await _check_remote_panel(panel["base_url"])
            status = t("remote_check_ok", lang) if ok else t("remote_check_fail", lang)
            name = _escape_markdown(panel["name"] or "")
            lines.append(f"{idx}. {name} — {status}")
        text = f"{t('remote_panels_title', lang)}\n\n" + "\n".join(lines)
    keyboard = [[InlineKeyboardButton(t("btn_back", lang), callback_data="admin_remote_panels")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_remote_panels_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    if tg_id != ADMIN_ID:
        return
    lang = get_lang(tg_id)
    try:
        panel_id = int(query.data.split("_")[-1])
    except Exception:
        panel_id = 0
    if panel_id:
        _delete_remote_panel(panel_id)
    await query.edit_message_text(t("remote_panel_deleted", lang), parse_mode="Markdown")
    await admin_remote_panels(update, context)

async def admin_remote_locations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        tg_id = str(query.from_user.id)
    else:
        tg_id = str(update.message.from_user.id)
    if tg_id != ADMIN_ID:
        return
    lang = get_lang(tg_id)
    keyboard = [
        [InlineKeyboardButton(t("btn_remote_add", lang), callback_data="admin_remote_locations_add")],
        [InlineKeyboardButton(t("btn_remote_list", lang), callback_data="admin_remote_locations_list")],
        [InlineKeyboardButton(t("btn_remote_check", lang), callback_data="admin_remote_locations_check")],
        [InlineKeyboardButton(t("btn_back_admin", lang), callback_data="admin_panel")],
    ]
    text = t("remote_locations_title", lang)
    if query:
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        except Exception as e:
            if "Message is not modified" not in str(e):
                await query.message.delete()
                await context.bot.send_message(chat_id=tg_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_remote_locations_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    if tg_id != ADMIN_ID:
        return
    lang = get_lang(tg_id)
    context.user_data["admin_action"] = "awaiting_remote_location"
    await query.edit_message_text(t("remote_location_prompt", lang), parse_mode="Markdown")

async def admin_remote_locations_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    if tg_id != ADMIN_ID:
        return
    lang = get_lang(tg_id)
    locations = _fetch_remote_locations()
    if not locations:
        await asyncio.get_running_loop().run_in_executor(None, _sync_remote_nodes_locations)
        locations = _fetch_remote_locations()
    if not locations:
        text = t("remote_list_empty", lang)
    else:
        lines = []
        for idx, location in enumerate(locations, start=1):
            name = _escape_markdown(location["name"] or "")
            host = _escape_markdown(location["host"] or "")
            port = location["port"] or 0
            lines.append(f"{idx}. {name} | {host}:{port}")
        text = f"{t('remote_locations_title', lang)}\n\n" + "\n".join(lines)
    keyboard = [
        [InlineKeyboardButton(f"🗑 {t('btn_delete', lang)} {location['name']}", callback_data=f"admin_remote_locations_del_{location['id']}")]
        for location in locations
    ]
    keyboard.append([InlineKeyboardButton(t("btn_back", lang), callback_data="admin_remote_locations")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_remote_locations_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    if tg_id != ADMIN_ID:
        return
    lang = get_lang(tg_id)
    locations = _fetch_remote_locations()
    if not locations:
        text = t("remote_list_empty", lang)
    else:
        lines = []
        for idx, location in enumerate(locations, start=1):
            host = location["host"] or ""
            port = location["port"] or 0
            latency = await _check_tcp_latency(host, int(port))
            status = t("remote_check_ok", lang) if latency is not None else t("remote_check_fail", lang)
            latency_label = f"{status} ({latency}ms)" if latency is not None else status
            name = _escape_markdown(location["name"] or "")
            lines.append(f"{idx}. {name} — {latency_label}")
        text = f"{t('remote_locations_title', lang)}\n\n" + "\n".join(lines)
    keyboard = [[InlineKeyboardButton(t("btn_back", lang), callback_data="admin_remote_locations")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_remote_locations_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    if tg_id != ADMIN_ID:
        return
    lang = get_lang(tg_id)
    try:
        location_id = int(query.data.split("_")[-1])
    except Exception:
        location_id = 0
    if location_id:
        _delete_remote_location(location_id)
    await query.edit_message_text(t("remote_location_deleted", lang), parse_mode="Markdown")
    await admin_remote_locations(update, context)

async def admin_remote_nodes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        tg_id = str(query.from_user.id)
    else:
        tg_id = str(update.message.from_user.id)
    if tg_id != ADMIN_ID:
        return
    lang = get_lang(tg_id)
    keyboard = [
        [InlineKeyboardButton(t("btn_remote_add", lang), callback_data="admin_remote_nodes_add")],
        [InlineKeyboardButton(t("btn_remote_list", lang), callback_data="admin_remote_nodes_list")],
        [InlineKeyboardButton(t("btn_remote_check", lang), callback_data="admin_remote_nodes_check")],
        [InlineKeyboardButton(t("btn_remote_sync", lang), callback_data="admin_remote_nodes_sync_menu")],
        [InlineKeyboardButton(t("btn_back_admin", lang), callback_data="admin_panel")],
    ]
    text = t("remote_nodes_title", lang)
    if query:
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        except Exception as e:
            if "Message is not modified" not in str(e):
                await query.message.delete()
                await context.bot.send_message(chat_id=tg_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_remote_nodes_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    if tg_id != ADMIN_ID:
        return
    lang = get_lang(tg_id)
    context.user_data["admin_action"] = "awaiting_remote_node"
    await query.edit_message_text(t("remote_node_prompt", lang), parse_mode="Markdown")

async def admin_remote_nodes_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    if tg_id != ADMIN_ID:
        return
    lang = get_lang(tg_id)
    nodes = _fetch_remote_nodes()
    lines = []
    local_settings = _get_local_panel_settings()
    local_port = local_settings.get("port") or "22"
    if IP:
        local_name = _escape_markdown(t("local_node_label", lang))
        local_host = _escape_markdown(IP)
        local_location = _escape_markdown(_auto_location_name(IP))
        lines.append(f"1. {local_name} | {local_host}:{local_port} | {local_location}")
    start_idx = len(lines) + 1
    for idx, node in enumerate(nodes, start=start_idx):
        name = _escape_markdown(node["name"] or "")
        host = _escape_markdown(node["host"] or "")
        port = node["port"] or 0
        location = _escape_markdown(_auto_location_name(node["host"] or ""))
        lines.append(f"{idx}. {name} | {host}:{port} | {location}")
    if not lines:
        text = t("remote_list_empty", lang)
    else:
        text = f"{t('remote_nodes_title', lang)}\n\n" + "\n".join(lines)
    keyboard = [
        [InlineKeyboardButton(f"🗑 {t('btn_delete', lang)} {node['name']}", callback_data=f"admin_remote_nodes_del_{node['id']}")]
        for node in nodes
    ]
    keyboard.append([InlineKeyboardButton(t("btn_back", lang), callback_data="admin_remote_nodes")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_remote_nodes_sync_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    if tg_id != ADMIN_ID:
        return
    lang = get_lang(tg_id)
    nodes = _fetch_remote_nodes()
    if not nodes:
        text = t("remote_list_empty", lang)
        keyboard = [[InlineKeyboardButton(t("btn_back", lang), callback_data="admin_remote_nodes")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return
    text = t("remote_nodes_sync_title", lang)
    keyboard = [
        [InlineKeyboardButton(f"🔄 {node['name']}", callback_data=f"admin_remote_nodes_sync_{node['id']}")]
        for node in nodes
    ]
    keyboard.append([InlineKeyboardButton(t("btn_back", lang), callback_data="admin_remote_nodes")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_remote_nodes_sync_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    if tg_id != ADMIN_ID:
        return
    lang = get_lang(tg_id)
    try:
        node_id = int(query.data.split("_")[-1])
    except Exception:
        node_id = 0
    node = _get_remote_node(node_id) if node_id else None
    if not node:
        await query.edit_message_text(t("error_generic", lang), parse_mode="Markdown")
        return
    ssh_user = node.get("ssh_user")
    ssh_password = node.get("ssh_password")
    if not ssh_user or not ssh_password:
        await query.edit_message_text(t("remote_node_sync_missing_ssh", lang), parse_mode="Markdown")
        return
    host = node.get("host") or ""
    ssh_port = int(node.get("port") or 22)
    panel_ready, location_ready = await asyncio.get_running_loop().run_in_executor(
        None, _sync_remote_node_data, host, ssh_port, ssh_user, ssh_password, node.get("name") or host
    )
    if panel_ready or location_ready:
        await query.edit_message_text(t("remote_node_sync_ok", lang), parse_mode="Markdown")
    else:
        await query.edit_message_text(t("remote_node_sync_failed", lang), parse_mode="Markdown")
    await admin_remote_nodes(update, context)

async def admin_remote_nodes_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    if tg_id != ADMIN_ID:
        return
    lang = get_lang(tg_id)
    nodes = _fetch_remote_nodes()
    lines = []
    local_settings = _get_local_panel_settings()
    local_port = local_settings.get("port") or "22"
    if IP:
        latency = await _check_tcp_latency(IP, int(local_port))
        status = t("remote_check_ok", lang) if latency is not None else t("remote_check_fail", lang)
        latency_label = f"{status} ({latency}ms)" if latency is not None else status
        local_name = _escape_markdown(t("local_node_label", lang))
        lines.append(f"1. {local_name} — {latency_label}")
    start_idx = len(lines) + 1
    for idx, node in enumerate(nodes, start=start_idx):
        host = node["host"] or ""
        port = node["port"] or 22
        latency = await _check_tcp_latency(host, int(port))
        status = t("remote_check_ok", lang) if latency is not None else t("remote_check_fail", lang)
        latency_label = f"{status} ({latency}ms)" if latency is not None else status
        name = _escape_markdown(node["name"] or "")
        lines.append(f"{idx}. {name} — {latency_label}")
    if not lines:
        text = t("remote_list_empty", lang)
    else:
        text = f"{t('remote_nodes_title', lang)}\n\n" + "\n".join(lines)
    keyboard = [[InlineKeyboardButton(t("btn_back", lang), callback_data="admin_remote_nodes")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_remote_nodes_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    if tg_id != ADMIN_ID:
        return
    lang = get_lang(tg_id)
    try:
        node_id = int(query.data.split("_")[-1])
    except Exception:
        node_id = 0
    if node_id:
        _delete_remote_node(node_id)
    await query.edit_message_text(t("remote_node_deleted", lang), parse_mode="Markdown")
    await admin_remote_nodes(update, context)

async def user_locations_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    locations = [loc for loc in _fetch_remote_locations() if loc.get("enabled")]
    if not locations:
        await asyncio.get_running_loop().run_in_executor(None, _sync_remote_nodes_locations)
        locations = [loc for loc in _fetch_remote_locations() if loc.get("enabled")]
    if not locations:
        text = t("remote_list_empty", lang)
        keyboard = [[InlineKeyboardButton(t("btn_back", lang), callback_data="get_config")]]
    else:
        text = t("user_locations_title", lang)
        keyboard = [
            [InlineKeyboardButton(loc["name"], callback_data=f"user_location_{loc['id']}")]
            for loc in locations
        ]
        keyboard.append([InlineKeyboardButton(t("btn_back", lang), callback_data="get_config")])
    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    except Exception as e:
        if "Message is not modified" not in str(e):
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=tg_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )

async def user_location_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    try:
        location_id = int(query.data.split("_")[-1])
    except Exception:
        location_id = 0
    location = _get_remote_location(location_id) if location_id else None
    if not location or not location.get("enabled"):
        await query.edit_message_text(t("user_location_not_found", lang), parse_mode="Markdown")
        return
    user_client = _get_user_client(tg_id)
    if not user_client:
        await query.edit_message_text(t("sub_not_found", lang), parse_mode="Markdown")
        return
    user_uuid = user_client.get("id")
    client_email = user_client.get("email") or f"tg_{tg_id}"
    client_flow = user_client.get("flow", "")
    if not user_uuid:
        await query.edit_message_text(t("sub_not_found", lang), parse_mode="Markdown")
        return
    spx_val = _get_spiderx_encoded()
    vless_link = _build_location_vless_link_with_settings(
        location,
        user_uuid,
        client_email,
        base_settings={"port": PORT, "public_key": PUBLIC_KEY, "sni": SNI, "sid": SID},
        client_flow=client_flow,
        spx_val=spx_val,
    )
    if not vless_link:
        await query.edit_message_text(t("error_generic", lang), parse_mode="Markdown")
        return
    sub_token = str(user_client.get("subId") or user_uuid).strip()
    sub_link = _build_location_sub_link(location, sub_token)
    if sub_link:
        sub_block = t("user_location_sub_block", lang).format(sub=html.escape(sub_link))
    else:
        sub_block = t("user_location_sub_empty", lang)
    ping_status = t("remote_check_fail", lang)
    latency = await _check_tcp_latency(str(location.get("host") or ""), int(location.get("port") or 0))
    if latency is not None:
        ping_status = f"{t('remote_check_ok', lang)} ({latency}ms)"
    ping_block = t("user_location_ping", lang).format(status=html.escape(ping_status))
    sub_block = f"{sub_block}\n\n{ping_block}"
    text = t("user_location_config", lang).format(
        name=html.escape(str(location["name"])),
        key=html.escape(vless_link),
        sub_block=sub_block,
    )
    keyboard = [[InlineKeyboardButton(t("btn_back", lang), callback_data="user_locations")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

def get_net_io_counters():
    try:
        with open('/proc/net/dev', 'r') as f:
            lines = f.readlines()

        rx_total = 0
        tx_total = 0

        for line in lines[2:]:
            if ':' in line:
                data = line.split(':')[1].split()
                if len(data) >= 9:
                    rx_total += int(data[0])
                    tx_total += int(data[8])
        return rx_total, tx_total
    except Exception:
        return 0, 0

async def get_system_stats():
    # Network (Start)
    rx1, tx1 = get_net_io_counters()

    # CPU
    try:
        with open('/proc/stat', 'r') as f:
            line = f.readline()
            parts = line.split()
            total_1 = sum(int(x) for x in parts[1:])
            idle_1 = int(parts[4])

        await asyncio.sleep(1.0) # Wait 1 sec for better accuracy (Async)

        with open('/proc/stat', 'r') as f:
            line = f.readline()
            parts = line.split()
            total_2 = sum(int(x) for x in parts[1:])
            idle_2 = int(parts[4])

        diff_total = total_2 - total_1
        diff_idle = idle_2 - idle_1
        cpu_usage = (1 - diff_idle / diff_total) * 100
    except Exception:
        cpu_usage = 0

    # Network (End)
    rx2, tx2 = get_net_io_counters()

    # Speed in Bytes per second (since we slept 1s)
    # If sleep was 0.5, we would multiply by 2.
    # We changed sleep to 1.0 for easier calc and better sample.
    rx_speed = rx2 - rx1
    tx_speed = tx2 - tx1

    # RAM
    try:
        mem_info = {}
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                parts = line.split(':')
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = int(parts[1].split()[0]) # kB
                    mem_info[key] = val

        total_ram = mem_info.get('MemTotal', 0)
        avail_ram = mem_info.get('MemAvailable', 0)
        used_ram = total_ram - avail_ram
        ram_usage = (used_ram / total_ram) * 100 if total_ram > 0 else 0
        ram_total_gb = total_ram / (1024 * 1024)
        ram_used_gb = used_ram / (1024 * 1024)

        total_swap = mem_info.get('SwapTotal', 0)
        free_swap = mem_info.get('SwapFree', 0)
        used_swap = total_swap - free_swap
        swap_usage = (used_swap / total_swap) * 100 if total_swap > 0 else 0
        swap_total_gb = total_swap / (1024 * 1024)
        swap_used_gb = used_swap / (1024 * 1024)
    except Exception:
        ram_usage = 0
        ram_total_gb = 0
        ram_used_gb = 0
        swap_usage = 0
        swap_total_gb = 0
        swap_used_gb = 0

    try:
        with open('/proc/uptime', 'r') as f:
            uptime_str = f.readline().strip().split()[0]
            uptime_sec = int(float(uptime_str))
    except Exception:
        uptime_sec = 0

    # Disk
    try:
        disk = shutil.disk_usage('/')
        disk_total_gb = disk.total / (1024**3)
        disk_used_gb = disk.used / (1024**3)
        disk_free_gb = disk.free / (1024**3)
        disk_usage = (disk.used / disk.total) * 100
    except Exception:
        disk_usage = 0
        disk_total_gb = 0
        disk_used_gb = 0
        disk_free_gb = 0

    return {
        'cpu': cpu_usage,
        'ram_usage': ram_usage,
        'ram_total': ram_total_gb,
        'ram_used': ram_used_gb,
        'swap_usage': swap_usage,
        'swap_total': swap_total_gb,
        'swap_used': swap_used_gb,
        'disk_usage': disk_usage,
        'disk_total': disk_total_gb,
        'disk_used': disk_used_gb,
        'disk_free': disk_free_gb,
        'rx_speed': rx_speed,
        'tx_speed': tx_speed,
        'uptime_sec': uptime_sec,
    }

async def admin_server(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Stop any running live monitor
    context.user_data['live_monitoring_active'] = False

    query = update.callback_query

    # If called from "Live" button, we might loop.
    # But usually we separate the loop handler.
    # Let's check if this is a refresh or initial load.

    try:
        await query.answer("Обновление данных...")
    except Exception:
        pass # Ignore if already answered

    stats = await get_system_stats()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    local_xui_v, local_xray_v = await asyncio.gather(_get_local_xui_version(), _get_local_xray_version())
    remote_xui_v, remote_xray_v = await asyncio.gather(
        _github_latest_version("MHSanaei", "3x-ui"),
        _github_latest_version("XTLS", "Xray-core"),
    )

    tx_speed_str = format_bytes(stats['tx_speed']) + "/s"
    rx_speed_str = format_bytes(stats['rx_speed']) + "/s"
    uptime_str = format_uptime(int(stats.get('uptime_sec', 0)))

    text = f"{t('admin_server_title', lang)}\n\n" \
           f"{t('updates_title', lang)}\n" \
           f"{t('xui_version_label', lang)} {_format_update_status(local_xui_v, remote_xui_v, lang)}\n" \
           f"{t('xray_version_label', lang)} {_format_update_status(local_xray_v, remote_xray_v, lang)}\n\n" \
           f"{t('cpu_label', lang)} {stats['cpu']:.1f}%\n" \
           f"{t('ram_label', lang)} {stats['ram_usage']:.1f}% ({stats['ram_used']:.2f} / {stats['ram_total']:.2f} GB)\n" \
           f"{t('swap_label', lang)} {stats['swap_usage']:.1f}% ({stats['swap_used']:.2f} / {stats['swap_total']:.2f} GB)\n" \
           f"{t('disk_label', lang)} {stats['disk_usage']:.1f}%\n" \
           f"{t('disk_used', lang)} {stats['disk_used']:.2f} GB\n" \
           f"{t('disk_free', lang)} {stats['disk_free']:.2f} GB\n" \
           f"{t('disk_total', lang)} {stats['disk_total']:.2f} GB\n\n" \
           f"{t('uptime_label', lang)} {uptime_str}\n\n" \
           f"{t('traffic_speed_title', lang)}\n" \
           f"{t('upload_label', lang)}\n{tx_speed_str}\n" \
           f"{t('download_label', lang)}\n{rx_speed_str}\n\n" \
           f"{t('updated_label', lang)} {datetime.datetime.now(TIMEZONE).strftime('%H:%M:%S')}"

    keyboard = [
        [InlineKeyboardButton(t("btn_live_monitor", lang), callback_data='admin_server_live')],
        [InlineKeyboardButton(t("btn_admin_health", lang), callback_data='admin_health')],
        [InlineKeyboardButton(t("btn_update_xui_xray", lang), callback_data='admin_update_xui_xray')],
        [InlineKeyboardButton(t("btn_server_nodes", lang), callback_data='admin_server_nodes')],
        [InlineKeyboardButton(t("btn_refresh", lang), callback_data='admin_server')],
        [InlineKeyboardButton(t("btn_back_admin", lang), callback_data='admin_panel')]
    ]

    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    except Exception as e:
        # If message content is same (Telegram API error), we just ignore or answer
        if "Message is not modified" not in str(e):
             await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_server_nodes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    if tg_id != ADMIN_ID:
        return
    lang = get_lang(tg_id)
    nodes = _fetch_remote_nodes()
    if not nodes:
        text = t("remote_list_empty", lang)
        keyboard = [[InlineKeyboardButton(t("btn_back", lang), callback_data="admin_server")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return
    lines = []
    for idx, node in enumerate(nodes, start=1):
        name = _escape_markdown(node["name"] or "")
        host = _escape_markdown(node["host"] or "")
        port = node["port"] or 22
        location = _escape_markdown(_auto_location_name(node["host"] or ""))
        lines.append(f"{idx}. {name} | {host}:{port} | {location}")
    text = f"{t('admin_server_nodes_title', lang)}\n\n" + "\n".join(lines)
    keyboard = [
        [InlineKeyboardButton(f"🔍 {node['name']}", callback_data=f"admin_server_node_{node['id']}")]
        for node in nodes
    ]
    keyboard.append([InlineKeyboardButton(t("btn_back", lang), callback_data="admin_server")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_server_node_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    if tg_id != ADMIN_ID:
        return
    lang = get_lang(tg_id)
    try:
        node_id = int(query.data.split("_")[-1])
    except Exception:
        node_id = 0
    node = _get_remote_node(node_id) if node_id else None
    if not node:
        await query.edit_message_text(t("error_generic", lang), parse_mode="Markdown")
        return
    host = node.get("host") or ""
    ssh_port = int(node.get("port") or 22)
    ssh_user = node.get("ssh_user")
    ssh_password = node.get("ssh_password")
    if not ssh_user or not ssh_password:
        keyboard = [[InlineKeyboardButton(t("btn_back", lang), callback_data="admin_server_nodes")]]
        await query.edit_message_text(t("remote_node_sync_missing_ssh", lang), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return
    ssh_data = await asyncio.get_running_loop().run_in_executor(
        None, _ssh_fetch_remote_xui_data, host, ssh_port, ssh_user, ssh_password
    )
    if not ssh_data:
        keyboard = [[InlineKeyboardButton(t("btn_back", lang), callback_data="admin_server_nodes")]]
        await query.edit_message_text(t("remote_node_sync_failed", lang), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return
    web_port = _safe_int(ssh_data.get("web_port"))
    web_base_path = ssh_data.get("web_base_path")
    panel_base_url = _build_remote_panel_base_url(host, web_port, web_base_path)
    inbound_port = _safe_int(ssh_data.get("inbound_port"))
    public_key_raw = ssh_data.get("public_key")
    sni_raw = ssh_data.get("sni")
    sid_raw = ssh_data.get("sid")
    flow_raw = ssh_data.get("flow")
    public_key = str(public_key_raw) if public_key_raw else "—"
    sni = str(sni_raw) if sni_raw else "—"
    sid = str(sid_raw) if sid_raw else "—"
    flow = str(flow_raw) if flow_raw else "xtls-rprx-vision"
    panel_line = panel_base_url or "—"
    inbound_line = str(inbound_port) if inbound_port else "—"
    text = (
        f"{t('admin_server_node_title', lang)}\n\n"
        f"• {t('node_label', lang)}: {_escape_markdown(node.get('name') or '')}\n"
        f"• HOST: {_escape_markdown(host)}:{ssh_port}\n"
        f"• Panel: {_escape_markdown(panel_line)}\n"
        f"• Inbound: {_escape_markdown(inbound_line)}\n"
        f"• PBK: {_escape_markdown(public_key)}\n"
        f"• SNI: {_escape_markdown(sni)}\n"
        f"• SID: {_escape_markdown(sid)}\n"
        f"• FLOW: {_escape_markdown(flow)}"
    )
    keyboard = [
        [InlineKeyboardButton(t("btn_remote_sync", lang), callback_data=f"admin_remote_nodes_sync_{node['id']}")],
        [InlineKeyboardButton(t("btn_back", lang), callback_data="admin_server_nodes")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

def _health_check_bot_db() -> tuple[bool, str]:
    try:
        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM user_prefs")
        count = cursor.fetchone()[0]
        conn.close()
        return True, str(count)
    except Exception as e:
        return False, str(e)


def _health_check_xui_db() -> tuple[bool, str]:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM inbounds WHERE id=?", (INBOUND_ID,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return True, ""
        return False, "inbound_missing"
    except Exception as e:
        return False, str(e)


async def admin_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
        tg_id = str(query.from_user.id)
    else:
        tg_id = str(update.message.from_user.id)

    if tg_id != ADMIN_ID:
        return

    lang = get_lang(tg_id)

    bot_ok, bot_detail = _health_check_bot_db()
    xui_ok, xui_detail = _health_check_xui_db()
    access_log_ok = os.path.exists(ACCESS_LOG_PATH)

    application = getattr(context, "application", None)
    support_bot = None
    if application is not None:
        bot_data = getattr(application, "bot_data", None)
        if isinstance(bot_data, dict):
            support_bot = bot_data.get("support_bot")

    support_bot_ok = support_bot is not None
    main_bot_ok = getattr(context, "bot", None) is not None

    def _line(ok: bool, label: str, detail: str = "") -> str:
        status = t("health_ok", lang) if ok else t("health_fail", lang)
        suffix = f"{detail}" if detail else ""
        return f"{'✅' if ok else '❌'} {label} — {status}{suffix}"

    bot_detail_text = f" ({bot_detail})" if bot_ok else f": {bot_detail}"
    if xui_ok:
        xui_detail_text = ""
    else:
        xui_detail_text = f": {t('health_inbound_missing', lang)}" if xui_detail == "inbound_missing" else f": {xui_detail}"

    text = "\n".join([
        t("health_title", lang),
        "",
        _line(bot_ok, t("health_bot_db", lang), bot_detail_text),
        _line(xui_ok, t("health_xui_db", lang), xui_detail_text),
        _line(access_log_ok, t("health_access_log", lang)),
        _line(support_bot_ok, t("health_support_bot", lang)),
        _line(main_bot_ok, t("health_main_bot", lang)),
    ])

    keyboard = [
        [InlineKeyboardButton(t("btn_refresh", lang), callback_data='admin_health')],
        [InlineKeyboardButton(t("btn_back_admin", lang), callback_data='admin_panel')]
    ]

    if query:
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_update_xui_xray(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data['live_monitoring_active'] = False
    query = update.callback_query
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    if tg_id != ADMIN_ID:
        await query.answer()
        return

    await query.answer(t("update_starting", lang))
    local_xui_v, local_xray_v = await asyncio.gather(_get_local_xui_version(), _get_local_xray_version())
    remote_xui_v, remote_xray_v = await asyncio.gather(
        _github_latest_version("MHSanaei", "3x-ui"),
        _github_latest_version("XTLS", "Xray-core"),
    )

    need_xui_update = remote_xui_v is not None and (
        local_xui_v is None or _version_tuple(local_xui_v) < _version_tuple(remote_xui_v)
    )
    need_xray_update = remote_xray_v is not None and (
        local_xray_v is None or _version_tuple(local_xray_v) < _version_tuple(remote_xray_v)
    )

    status_lines: list[str] = []
    restart_needed = False

    if need_xui_update:
        xui_rc, xui_out = await _cmd_status("x-ui", "update")
        xui_details = (xui_out or "").strip()[:1200] if xui_out else ""
        if not xui_details:
            xui_details = "—"
        xui_details = f"```{xui_details}```"
        if xui_rc == 0:
            status_lines.append(t("update_done", lang).format(details=xui_details))
            restart_needed = True
        else:
            status_lines.append(t("update_failed", lang).format(details=xui_details))
    else:
        status_lines.append(
            f"{t('xui_version_label', lang)} {_format_update_status(local_xui_v, remote_xui_v, lang)}"
        )

    if need_xray_update:
        xray_ok, xray_details = await _update_xray_binary()
        if xray_ok:
            status_lines.append(f"{t('xray_version_label', lang)} ✅ {xray_details}")
            restart_needed = True
        else:
            status_lines.append(f"{t('xray_version_label', lang)} ❌ {xray_details}")
    else:
        status_lines.append(
            f"{t('xray_version_label', lang)} {_format_update_status(local_xray_v, remote_xray_v, lang)}"
        )

    if restart_needed:
        try:
            await _systemctl("restart", XUI_SYSTEMD_SERVICE)
        except Exception:
            pass

    local_xui_after, local_xray_after = await asyncio.gather(_get_local_xui_version(), _get_local_xray_version())
    status_lines.append(
        "\n".join(
            [
                f"{t('xui_version_label', lang)} `{local_xui_after or '—'}`",
                f"{t('xray_version_label', lang)} `{local_xray_after or '—'}`",
            ]
        )
    )

    text = "\n\n".join(status_lines).strip()

    keyboard = [
        [InlineKeyboardButton(t("btn_refresh", lang), callback_data="admin_server")],
        [InlineKeyboardButton(t("btn_back_admin", lang), callback_data="admin_panel")],
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_server_live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    await query.answer(t("live_monitor_starting", lang))

    context.user_data['live_monitoring_active'] = True

    # Run in background task to not block updates
    asyncio.create_task(run_live_monitor(update, context))

async def run_live_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    effective_user = update.effective_user
    if not effective_user:
        return
    tg_id = str(effective_user.id)
    lang = get_lang(tg_id)

    # Run for 30 iterations * ~1 seconds = 30 seconds
    for i in range(30):
        # Check if stopped
        if not context.user_data.get('live_monitoring_active', False):
            break

        try:
            stats = await get_system_stats() # Takes ~1 second

            # Re-check after sleep
            if not context.user_data.get('live_monitoring_active', False):
                break

            tx_speed_str = format_bytes(stats['tx_speed']) + "/s"
            rx_speed_str = format_bytes(stats['rx_speed']) + "/s"
            uptime_str = format_uptime(int(stats.get('uptime_sec', 0)))

            text = f"{t('admin_server_live_title', lang)}\n\n" \
                   f"{t('cpu_label', lang)} {stats['cpu']:.1f}%\n" \
                   f"{t('ram_label', lang)} {stats['ram_usage']:.1f}% ({stats['ram_used']:.2f} / {stats['ram_total']:.2f} GB)\n" \
                   f"{t('swap_label', lang)} {stats['swap_usage']:.1f}% ({stats['swap_used']:.2f} / {stats['swap_total']:.2f} GB)\n" \
                   f"{t('disk_label', lang)} {stats['disk_usage']:.1f}%\n" \
                   f"{t('disk_used', lang)} {stats['disk_used']:.2f} GB\n" \
                   f"{t('disk_free', lang)} {stats['disk_free']:.2f} GB\n" \
                   f"{t('disk_total', lang)} {stats['disk_total']:.2f} GB\n\n" \
                   f"{t('uptime_label', lang)} {uptime_str}\n\n" \
                   f"{t('traffic_speed_title', lang)}\n" \
                   f"{t('upload_label', lang)}\n{tx_speed_str}\n" \
                   f"{t('download_label', lang)}\n{rx_speed_str}\n\n" \
                   f"{t('updated_label', lang)} {datetime.datetime.now(TIMEZONE).strftime('%H:%M:%S')}\n" \
                   f"{t('live_remaining', lang).format(sec=30 - (i*1))}"

            keyboard = [
                [InlineKeyboardButton(t("btn_stop", lang), callback_data='admin_server')],
                [InlineKeyboardButton(t("btn_back_admin", lang), callback_data='admin_panel')]
            ]

            # Use bot.edit_message_text because we are in background task
            # query might be stale, but message_id/chat_id are same
            effective_chat = update.effective_chat
            effective_message = update.effective_message
            if not effective_chat or not effective_message:
                return
            chat_id = effective_chat.id
            message_id = effective_message.message_id

            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )

        except Exception as e:
            # If message deleted or other error, stop loop
            if "Message is not modified" not in str(e):
                logging.error(f"Live monitor error: {e}")
                break
            pass

    # After loop finishes naturally (not stopped by flag), revert to static view
    if context.user_data.get('live_monitoring_active', False):
         context.user_data['live_monitoring_active'] = False
         try:
             await admin_server(update, context)
         except Exception:
             pass

async def admin_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    current_prices = get_prices()

    keyboard = []
    order = ["1_week", "2_weeks", "1_month", "ru_bridge", "3_months", "6_months", "1_year"]

    for key in order:
        if key in current_prices:
            amount = current_prices[key]['amount']
            label = t(f"plan_{key}", lang)
            keyboard.append([InlineKeyboardButton(f"{label}: {amount} ⭐️ {t('btn_change', lang)}", callback_data=f'admin_edit_price_{key}')])

    keyboard.append([InlineKeyboardButton(t("btn_back_admin", lang), callback_data='admin_panel')])

    await query.edit_message_text(
        t("admin_prices_title", lang),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def admin_edit_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    key = query.data.split('_', 3)[3] # admin_edit_price_KEY

    context.user_data['edit_price_key'] = key
    context.user_data['admin_action'] = 'awaiting_price_amount'

    labels = {
        "1_week": t("plan_1_week", lang),
        "2_weeks": t("plan_2_weeks", lang),
        "1_month": t("plan_1_month", lang),
        "3_months": t("plan_3_months", lang),
        "6_months": t("plan_6_months", lang),
        "1_year": t("plan_1_year", lang),
        "ru_bridge": t("plan_ru_bridge", lang)
    }

    await query.edit_message_text(
        t("price_edit_prompt", lang).format(label=labels.get(key, key)),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_cancel", lang), callback_data='admin_prices')]]),
        parse_mode='Markdown'
    )

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM user_prefs")
    total_users = cursor.fetchone()[0]

    # Get trial users and paid users
    cursor.execute("SELECT tg_id FROM user_prefs WHERE trial_used=1")
    trial_users = set(row[0] for row in cursor.fetchall())

    cursor.execute("SELECT DISTINCT tg_id FROM transactions")
    paid_users = set(row[0] for row in cursor.fetchall())

    # Pure trial users are those who used trial but never paid
    pure_trial_users = trial_users - paid_users

    conn.close()

    # Active subs
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
    row = cursor.fetchone()

    # Online users count (last 10 seconds for real-time accuracy)
    current_time_ms = int(time.time() * 1000)
    threshold = current_time_ms - (10 * 1000)
    cursor.execute("SELECT COUNT(DISTINCT email) FROM client_traffics WHERE inbound_id=? AND last_online > ?", (INBOUND_ID, threshold))
    online_users = cursor.fetchone()[0]

    conn.close()

    active_subs = 0
    total_clients = 0
    vpn_users = set()

    active_trials = 0
    expired_trials = 0

    if row:
        settings = json.loads(row[0])
        clients = settings.get('clients', [])
        total_clients = len(clients)

        for client in clients:
            expiry = client.get('expiryTime', 0)
            enable = client.get('enable', False)
            client_tg_id = get_client_tg_id(client)
            if client_tg_id is not None:
                vpn_users.add(client_tg_id)

            # Count overall active
            if enable:
                if expiry == 0 or expiry > current_time_ms:
                    active_subs += 1

            # Count trial stats
            if client_tg_id in pure_trial_users:
                if enable and (expiry == 0 or expiry > current_time_ms):
                    active_trials += 1
                elif enable and expiry > 0 and expiry < current_time_ms:
                    expired_trials += 1
                # If disabled, maybe expired or banned. Let's count as expired if expiry < now
                elif not enable and expiry > 0 and expiry < current_time_ms:
                     expired_trials += 1
                # If just disabled but time left?
                # Let's simplify: if in pure_trial_users and not active -> expired (roughly)
                # Better:
                # Active Trial = enable=True AND expiry > now
                # Expired Trial = expiry < now (regardless of enable)

                # Re-eval for trials:
                # if expiry > 0 and expiry < current_time_ms: expired_trials += 1

    vpn_users_count = len(vpn_users)

    total_revenue = 0
    total_sales = 0
    try:
        conn_bot2 = sqlite3.connect(BOT_DB_PATH)
        cursor_bot2 = conn_bot2.cursor()
        if vpn_users:
            vpn_list = sorted(vpn_users)
            placeholders = ",".join(["?"] * len(vpn_list))
            try:
                cursor_bot2.execute(
                    f"SELECT tg_id, amount, date, plan_id, telegram_payment_charge_id "
                    f"FROM transactions WHERE tg_id IN ({placeholders}) AND tg_id != '369456269'",
                    tuple(vpn_list),
                )
            except sqlite3.OperationalError:
                cursor_bot2.execute(
                    f"SELECT tg_id, amount, date, plan_id, NULL "
                    f"FROM transactions WHERE tg_id IN ({placeholders}) AND tg_id != '369456269'",
                    tuple(vpn_list),
                )
        else:
            try:
                cursor_bot2.execute(
                    "SELECT tg_id, amount, date, plan_id, telegram_payment_charge_id "
                    "FROM transactions WHERE tg_id != '369456269'"
                )
            except sqlite3.OperationalError:
                cursor_bot2.execute(
                    "SELECT tg_id, amount, date, plan_id, NULL FROM transactions WHERE tg_id != '369456269'"
                )
        tx_rows = cursor_bot2.fetchall()
        conn_bot2.close()

        unique_rows = _dedupe_sales_log_rows(tx_rows)
        total_sales = len(unique_rows)
        total_revenue = sum(r[1] for r in unique_rows)
    except Exception as e:
        logging.error(f"Failed to compute sales stats: {e}")

    text = f"{t('stats_header', lang)}\n\n" \
           f"{t('stats_users', lang)} {total_users}\n" \
           f"{t('stats_vpn_users', lang)} {vpn_users_count}\n" \
           f"{t('stats_online', lang)} {online_users}\n" \
           f"{t('stats_clients', lang)} {total_clients}\n" \
           f"{t('stats_active', lang)} {active_subs}\n" \
           f"{t('stats_trials', lang)} {active_trials}\n" \
           f"{t('stats_expired_trials', lang)} {expired_trials}\n" \
           f"{t('stats_revenue', lang)} {total_revenue} ⭐️\n" \
           f"{t('stats_sales', lang)} {total_sales}\n"

    keyboard = [
        [
            InlineKeyboardButton(t("btn_users_all", lang), callback_data='admin_users_all_0'),
            InlineKeyboardButton(t("btn_users_active", lang), callback_data='admin_users_active_0'),
            InlineKeyboardButton(t("btn_users_expiring", lang), callback_data='admin_users_expiring_0')
        ],
        [
            InlineKeyboardButton(t("btn_users_online", lang), callback_data='admin_users_online_0'),
            InlineKeyboardButton(t("btn_users_trial", lang), callback_data='admin_users_trial_0'),
            InlineKeyboardButton(t("btn_users_expired", lang), callback_data='admin_users_expired_0')
        ],
        [InlineKeyboardButton(t("btn_cleanup_db", lang), callback_data='admin_cleanup_db')],
        [InlineKeyboardButton(t("btn_db_audit", lang), callback_data='admin_db_audit')],
        [InlineKeyboardButton(t("btn_sync_nicks", lang), callback_data='admin_sync_nicks')],
        [InlineKeyboardButton(t("btn_sync_mobile_nicks", lang), callback_data='admin_sync_mobile_nicks')],
        [InlineKeyboardButton(t("btn_back_admin", lang), callback_data='admin_panel')]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_sync_nicknames(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    await query.answer(t("sync_start", lang), show_alert=False)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        await query.message.reply_text(t("sync_error_inbound", lang))
        return

    settings = json.loads(row[0])
    clients = settings.get('clients', [])

    updated_count = 0
    failed_count = 0
    total = len(clients)

    progress_msg = await context.bot.send_message(chat_id=query.from_user.id, text=t("sync_progress", lang).format(current=0, total=total))

    changed = False

    for i, client in enumerate(clients):
        tg_id = str(client.get('tgId', ''))

        # Fallback: Extract tg_id from email if tgId field is empty
        if not tg_id or not tg_id.isdigit():
            email = client.get('email', '')
            if email.startswith('tg_'):
                possible_id = email[3:]
                # Check if it has name part (tg_123_name) -> not supported by current new logic but possible old logic
                # Actually new logic is just tg_ID.
                # Let's try to split by _ and take first part
                parts = possible_id.split('_')
                if parts[0].isdigit():
                    tg_id = parts[0]
                    # Update the client object with the recovered tgId for future consistency
                    client['tgId'] = int(tg_id)

        if tg_id and tg_id.isdigit():
            user_nick = ""
            uname = None
            fname = None
            lname = None

            # Try to get info from Telegram
            try:
                chat = await context.bot.get_chat(tg_id)
                uname = chat.username
                fname = chat.first_name
                lname = chat.last_name
                # Update Bot DB
                update_user_info(tg_id, uname, fname, lname)
            except Exception as e:
                logging.warning(f"Sync: Failed to fetch chat {tg_id} from API: {e}")
                # Fallback to local DB
                try:
                    conn_bot = sqlite3.connect(BOT_DB_PATH)
                    cursor_bot = conn_bot.cursor()
                    cursor_bot.execute("SELECT username, first_name, last_name FROM user_prefs WHERE tg_id=?", (tg_id,))
                    row_u = cursor_bot.fetchone()
                    conn_bot.close()
                    if row_u:
                        uname = row_u[0]
                        fname = row_u[1]
                        lname = row_u[2]
                        logging.info(f"Sync: Found cached info for {tg_id}: {uname} {fname}")
                except Exception:
                    pass

            # Construct nickname
            if uname:
                user_nick = f"@{uname}"
            elif fname:
                user_nick = fname
                if lname:
                    user_nick += f" {lname}"

            try:
                # 3. Update X-UI Comment (nickname)
                if user_nick:
                    # Check existing keys
                    old_comment = client.get('comment', '')
                    old_remark = client.get('_comment', '')
                    old_u_remark = client.get('remark', '')

                    if not old_comment and not old_remark and not old_u_remark:
                         client['comment'] = user_nick
                         client['_comment'] = user_nick

                         # Ensure tgId is set correctly
                         if 'tgId' not in client or not client['tgId']:
                             client['tgId'] = int(tg_id)

                         clients[i] = client
                         changed = True
                         updated_count += 1
                         logging.info(f"Sync: Updated comment for {tg_id} -> {user_nick}")
                else:
                    logging.warning(f"Sync: No nickname found for {tg_id}")

                # Also force update tgId if it was missing/mismatched
                current_tgid = client.get('tgId')
                if str(current_tgid) != str(tg_id):
                    client['tgId'] = int(tg_id)
                    clients[i] = client
                    changed = True
                    logging.info(f"Sync: Restored tgId for {tg_id}")

            except Exception as e:
                logging.error(f"Sync: Critical error processing client {tg_id}: {e}")
                failed_count += 1

        # Update progress
        if (i + 1) % 2 == 0 or (i + 1) == total:
            try:
                await progress_msg.edit_text(t("sync_progress", lang).format(current=i+1, total=total))
            except Exception:
                pass

        await asyncio.sleep(0.05)

    if changed:
        # Save X-UI settings
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        new_settings = json.dumps(settings, indent=2)
        cursor.execute("UPDATE inbounds SET settings=? WHERE id=?", (new_settings, INBOUND_ID))
        conn.commit()
        conn.close()
        # Restart X-UI
        # subprocess.run(["systemctl", "restart", "x-ui"])
        proc = await asyncio.create_subprocess_exec("systemctl", "restart", "x-ui")
        await proc.wait()

    try:
        await progress_msg.edit_text(t("sync_complete", lang).format(updated=updated_count, failed=failed_count))
    except Exception:
        pass

    # Return to stats
    await admin_stats(update, context)

async def admin_sync_mobile_nicknames(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    tg_id = str(query.from_user.id)
    if tg_id != ADMIN_ID:
        return
    lang = get_lang(tg_id)
    await query.answer(t("sync_start", lang), show_alert=False)

    if not _mobile_feature_enabled():
        try:
            await context.bot.send_message(chat_id=tg_id, text=_mobile_not_configured_text(tg_id, lang), parse_mode="Markdown")
        except Exception:
            pass
        await admin_stats(update, context)
        return

    try:
        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT tg_id, uuid, sub_id, expiry_time FROM mobile_subscriptions")
        rows = cursor.fetchall()
        conn.close()
    except Exception:
        rows = []

    total = len(rows)
    if total == 0:
        try:
            await context.bot.send_message(chat_id=tg_id, text=t("sync_mobile_empty", lang))
        except Exception:
            pass
        await admin_stats(update, context)
        return

    updated_count = 0
    failed_count = 0
    progress_msg = await context.bot.send_message(
        chat_id=tg_id,
        text=t("sync_progress", lang).format(current=0, total=total),
    )

    for i, row in enumerate(rows):
        sub_tg_id, user_uuid, sub_id, expiry_time = row
        sub_tg_id_str = str(sub_tg_id)

        uname = None
        fname = None
        lname = None
        try:
            chat = await context.bot.get_chat(sub_tg_id_str)
            uname = chat.username
            fname = chat.first_name
            lname = chat.last_name
            update_user_info(sub_tg_id_str, uname, fname, lname)
        except Exception:
            try:
                conn_bot = sqlite3.connect(BOT_DB_PATH)
                cursor_bot = conn_bot.cursor()
                cursor_bot.execute(
                    "SELECT username, first_name, last_name FROM user_prefs WHERE tg_id=?",
                    (sub_tg_id_str,),
                )
                row_u = cursor_bot.fetchone()
                conn_bot.close()
                if row_u:
                    uname = row_u[0]
                    fname = row_u[1]
                    lname = row_u[2]
            except Exception:
                pass

        user_nick = ""
        if uname:
            user_nick = f"@{uname}"
        elif fname:
            user_nick = fname
            if lname:
                user_nick += f" {lname}"
        if not user_nick:
            user_nick = f"tg_{sub_tg_id_str}"

        try:
            ok = await _sync_mobile_inbound_client(
                tg_id=sub_tg_id_str,
                user_uuid=str(user_uuid),
                sub_id=str(sub_id),
                expiry_ms=int(expiry_time or 0),
                comment=user_nick,
            )
            if ok:
                updated_count += 1
            else:
                failed_count += 1
        except Exception:
            failed_count += 1

        if (i + 1) % 2 == 0 or (i + 1) == total:
            try:
                await progress_msg.edit_text(t("sync_progress", lang).format(current=i + 1, total=total))
            except Exception:
                pass

        await asyncio.sleep(0.05)

    try:
        await progress_msg.edit_text(t("sync_complete", lang).format(updated=updated_count, failed=failed_count))
    except Exception:
        pass

    await admin_stats(update, context)

async def admin_users_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    # format: admin_users_{filter}_{page}
    parts = query.data.split('_')
    # parts[0]=admin, [1]=users, [2]=filter, [3]=page
    if len(parts) == 4:
        filter_type = parts[2]
        try:
            page = int(parts[3])
        except Exception:
            page = 0
    else:
        # fallback
        filter_type = 'all'
        try:
            page = int(parts[-1])
        except Exception:
            page = 0

    ITEMS_PER_PAGE = 10
    current_time_ms = int(time.time() * 1000)

    # Special handling for 'trial' filter: source from DB + X-UI
    display_items: list[dict[str, Any]] = []

    if filter_type == 'trial':
        # 1. Fetch all trial users from BOT DB
        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT tg_id FROM user_prefs WHERE trial_used=1")
        trial_rows = cursor.fetchall() # [(tg_id,), ...]
        try:
            cursor.execute("SELECT tg_id, username, first_name, last_name FROM user_prefs WHERE trial_used=1")
            user_prefs_rows = cursor.fetchall()
        except Exception:
            user_prefs_rows = []
        conn.close()

        user_info_map: dict[str, dict[str, Any]] = {}
        for row_user in user_prefs_rows:
            tid, uname, fname, lname = row_user
            user_info_map[str(tid)] = {
                "username": uname,
                "first_name": fname,
                "last_name": lname,
            }

        # 2. Fetch X-UI clients for mapping
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
        row = cursor.fetchone()
        conn.close()

        expiry_map = {}
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT email, expiry_time FROM client_traffics WHERE inbound_id=?", (INBOUND_ID,))
            rows = cursor.fetchall()
            conn.close()
            for r in rows:
                expiry_map[r[0]] = r[1]
        except Exception:
            expiry_map = {}

        xui_clients_map = {}
        if row:
            settings = json.loads(row[0])
            for c in settings.get('clients', []):
                tid = str(c.get('tgId', ''))
                if tid:
                    xui_clients_map[tid] = c

        for r in trial_rows:
            trial_tg_id = str(r[0])
            client = xui_clients_map.get(trial_tg_id)

            if client:
                # Exists in X-UI
                email = client.get('email', 'Unknown')
                enable_val = client.get('enable', False)
                expiry_val = expiry_map.get(email, client.get('expiryTime', 0))
                is_active = is_subscription_active(enable_val, expiry_val, current_time_ms)
                status = "🟢" if is_active else "🔴"
                uid = client.get('id')
                label = f"{status} {email}"
                if trial_tg_id in user_info_map:
                    uinfo = user_info_map[trial_tg_id]
                    if uinfo.get("username"):
                        label = f"{label} (@{uinfo['username']})"
                    elif uinfo.get("first_name"):
                        name = str(uinfo["first_name"])
                        if uinfo.get("last_name"):
                            name += f" {uinfo['last_name']}"
                        label = f"{label} ({name})"
                display_items.append({
                    'label': label,
                    'callback': f"admin_u_{uid}",
                    'sort_key': (0 if not is_active else 1, email.lower()),
                    'tg_id': trial_tg_id
                })
            else:
                # Deleted from X-UI
                label = f"❌ {trial_tg_id} (Del)"
                if trial_tg_id in user_info_map:
                    uinfo = user_info_map[trial_tg_id]
                    if uinfo.get("username"):
                        label = f"{label} (@{uinfo['username']})"
                    elif uinfo.get("first_name"):
                        name = str(uinfo["first_name"])
                        if uinfo.get("last_name"):
                            name += f" {uinfo['last_name']}"
                        label = f"{label} ({name})"
                display_items.append({
                    'label': label,
                    'callback': f"admin_db_detail_{trial_tg_id}",
                    'sort_key': (2, trial_tg_id),
                    'tg_id': trial_tg_id
                })

        display_items.sort(key=lambda x: x['sort_key'])

    else:
        # Standard X-UI filters
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            await query.edit_message_text(t("sync_error_inbound", lang), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back_admin", lang), callback_data='admin_stats')]]))
            return

        settings = json.loads(row[0])
        clients = settings.get('clients', [])

        # Pre-fetch user details (username/name) from DB for ALL clients to avoid N+1 queries later
        # We can fetch all user_prefs and map by tg_id
        conn_bot = sqlite3.connect(BOT_DB_PATH)
        cursor_bot = conn_bot.cursor()
        try:
            cursor_bot.execute("SELECT tg_id, username, first_name, last_name FROM user_prefs")
            user_prefs_rows = cursor_bot.fetchall()
        except Exception:
            user_prefs_rows = []
        conn_bot.close()

        user_info_map = {} # tg_id -> {username, first_name, last_name}
        for r in user_prefs_rows:
            tid, uname, fname, lname = r
            user_info_map[str(tid)] = {
                'username': uname,
                'first_name': fname,
                'last_name': lname
            }

        expiry_map = {}
        try:
            conn_stats = sqlite3.connect(DB_PATH)
            cursor_stats = conn_stats.cursor()
            cursor_stats.execute("SELECT email, expiry_time FROM client_traffics WHERE inbound_id=?", (INBOUND_ID,))
            rows_stats = cursor_stats.fetchall()
            conn_stats.close()
            for r in rows_stats:
                expiry_map[r[0]] = r[1]
        except Exception:
            expiry_map = {}

        # Filtering
        filtered_clients = []
        current_time = current_time_ms

        # Pre-fetch online emails if needed
        online_emails = set()
        if filter_type == 'online':
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            # Get clients active in last 10 seconds
            threshold = current_time - (10 * 1000)
            cursor.execute("SELECT email FROM client_traffics WHERE last_online > ?", (threshold,))
            rows = cursor.fetchall()
            conn.close()
            for r in rows:
                online_emails.add(r[0])

        for c in clients:
            expiry = expiry_map.get(c.get('email', ''), c.get('expiryTime', 0))
            enable = c.get('enable', False)

            if filter_type == 'all':
                filtered_clients.append(c)
            elif filter_type == 'active':
                if enable and (expiry == 0 or expiry > current_time):
                    filtered_clients.append(c)
            elif filter_type == 'expiring':
                days_7_ms = 7 * 24 * 3600 * 1000
                if enable and expiry > current_time and expiry < (current_time + days_7_ms):
                    filtered_clients.append(c)
            elif filter_type == 'expired':
                if expiry > 0 and expiry < current_time:
                    filtered_clients.append(c)
            elif filter_type == 'online':
                if c.get('email') in online_emails:
                    filtered_clients.append(c)

        # Sort and map to display items
        def client_sort_key(c):
            email_val = c.get('email', '').lower()
            if filter_type == 'all':
                expiry_val = expiry_map.get(c.get('email', ''), c.get('expiryTime', 0))
                enable_val = c.get('enable', False)
                is_active = is_subscription_active(enable_val, expiry_val, current_time_ms)
                return (0 if not is_active else 1, email_val)
            return email_val

        filtered_clients.sort(key=client_sort_key)

        for c in filtered_clients:
            enable_val = c.get('enable', False)
            expiry_val = expiry_map.get(c.get('email', ''), c.get('expiryTime', 0))
            is_active = is_subscription_active(enable_val, expiry_val, current_time_ms)
            status = "🟢" if is_active else "🔴"
            email = c.get('email', 'Unknown')
            uid = c.get('id')
            tg_id = str(c.get('tgId', ''))

            label = f"{status} {email}"

            # Enrich label with name if available
            if tg_id in user_info_map:
                uinfo = user_info_map[tg_id]
                if uinfo['username']:
                    label = f"{label} (@{uinfo['username']})"
                elif uinfo['first_name']:
                    name = uinfo['first_name']
                    if uinfo['last_name']:
                        name += f" {uinfo['last_name']}"
                    label = f"{label} ({name})"

            display_items.append({
                'label': label,
                'callback': f"admin_u_{uid}",
                'tg_id': tg_id
            })

    # Pagination
    total_items = len(display_items)
    total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    if total_pages == 0:
        total_pages = 1

    if page >= total_pages:
        page = total_pages - 1

    start = page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    current_items = display_items[start:end]

    keyboard = []
    for item in current_items:
        # If still no name (not in DB), try dynamic fetch (fallback, slower but works for fresh start)
        # But wait, we are in a loop for 10 items.
        # If user interacts with bot, it will be in DB.
        # If user never interacted (manual add), we can't get name anyway except get_chat.
        # Let's keep the get_chat fallback for the current page only if DB failed.

        label = item['label']
        # Check if label already enriched (contains @ or ())
        if "(@" not in label and "(" not in label:
            tg_id_str = str(item.get('tg_id', ''))
            if tg_id_str and tg_id_str.isdigit():
                try:
                    chat = await context.bot.get_chat(tg_id_str)
                    # Also save to DB for next time!
                    uname = chat.username
                    fname = chat.first_name
                    lname = chat.last_name
                    update_user_info(tg_id_str, uname, fname, lname)

                    if uname:
                        label = f"{label} (@{uname})"
                    elif fname:
                        name = fname
                        if lname:
                            name += f" {lname}"
                        label = f"{label} ({name})"
                except Exception:
                    pass

        keyboard.append([InlineKeyboardButton(label, callback_data=item['callback'])])

    # Navigation
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️", callback_data=f'admin_users_{filter_type}_{page-1}'))

    filter_icons = {'all': '👥', 'active': '🟢', 'expiring': '⏳', 'expired': '🔴', 'online': '⚡', 'trial': '🆓'}
    nav_row.append(InlineKeyboardButton(f"{filter_icons.get(filter_type, '')} {page+1}/{total_pages}", callback_data='noop'))

    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("➡️", callback_data=f'admin_users_{filter_type}_{page+1}'))

    keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton(t("btn_back_stats", lang), callback_data='admin_stats')])

    title_map = {
        'all': t("title_all", lang),
        'active': t("title_active", lang),
        'expiring': t("title_expiring", lang),
        'expired': t("title_expired", lang),
        'online': t("title_online", lang),
        'trial': t("title_trial", lang)
    }
    await query.edit_message_text(t("users_list_title", lang).format(title=title_map.get(filter_type, 'Clients')), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    # format: admin_leaderboard_{sort_type}_{page}
    # sort_type: traffic (default), sub
    parts = query.data.split('_')
    # parts: admin, leaderboard, [sort_type], [page]

    sort_type = 'traffic'
    page = 0

    if len(parts) >= 3:
        # Check if parts[2] is sort type or page
        if parts[2] in ['traffic', 'sub']:
            sort_type = parts[2]
            if len(parts) >= 4:
                try:
                    page = int(parts[3])
                except Exception:
                    pass
        else:
            # Legacy format or just page
            try:
                page = int(parts[2])
            except Exception:
                pass

    ITEMS_PER_PAGE = 10

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        return

    settings = json.loads(row[0])
    clients = settings.get('clients', [])
    conn.close()

    leaderboard = []

    # Prepare data based on sort type
    current_time_ms = int(time.time() * 1000)

    # Prepare data based on sort type
    current_time_ms = int(time.time() * 1000)

    # Pre-fetch traffic and expiry stats from DB (client_traffics)
    # We fetch this for BOTH sort types to ensure data is fresh
    db_stats_map = {}

    conn_stats = sqlite3.connect(DB_PATH)
    cursor_stats = conn_stats.cursor()
    try:
        # Fetch up, down, expiry_time
        cursor_stats.execute("SELECT email, up, down, expiry_time FROM client_traffics WHERE inbound_id=?", (INBOUND_ID,))
        rows = cursor_stats.fetchall()
        for r in rows:
            if r[0]:
                up = r[1] or 0
                down = r[2] or 0
                expiry = r[3] or 0
                db_stats_map[r[0]] = {
                    'traffic': up + down,
                    'expiry': expiry
                }
    except Exception as e:
        logging.error(f"Error fetching client_traffics: {e}")
    finally:
        conn_stats.close()

    for c in clients:
        email = c.get('email', '')
        uid = c.get('id')
        enable = c.get('enable')
        comment = c.get('comment') or c.get('_comment') or c.get('remark') or email

        item = {
            'email': email,
            'label': comment,
            'uid': uid,
            'enable': enable,
            'sort_val': 0,
            'display_val': ""
        }

        # Get DB stats
        db_stats = db_stats_map.get(email)
        expiry_db = db_stats.get('expiry') if db_stats is not None else None
        expiry_json = c.get('expiryTime', 0)
        expiry_effective = expiry_json if expiry_db is None else expiry_db
        is_active = is_subscription_active(enable, expiry_effective, current_time_ms)

        if sort_type == 'traffic':
            if db_stats is not None:
                traffic = db_stats.get('traffic', 0)
            else:
                traffic = (c.get('up', 0) or 0) + (c.get('down', 0) or 0)

            item['sort_val'] = traffic
            item['display_val'] = format_traffic(traffic)
        elif sort_type == 'sub':
            # Compare expiry from JSON and DB
            expiry_db = expiry_db

            # Use max expiry (assuming extension increases time)
            # Use 0 (unlimited) if EITHER is 0?
            # No, if one is 0 (unlimited) and other is timestamp, 0 usually wins (unlimited).
            # But wait, 0 means unlimited. Timestamp means limited.
            # If I changed from limited to unlimited, 0 is newer.
            # If I changed from unlimited to limited, timestamp is newer.
            # We can't know which is newer without `updated_at` (which might be in JSON).
            # Let's assume JSON (inbounds) is the "Config" (Intention) and DB (client_traffics) is "State".
            # Usually X-UI syncs Config -> State.
            # So JSON should be preferred for Expiry?
            # But user says it's outdated. That implies JSON is OLDER than reality?
            # If JSON is older, then DB must be newer.
            # So let's try to trust DB if it differs?
            # However, 0 vs timestamp is tricky.
            # Let's just use max() for now, but handle 0 carefully.
            # If expiry_json == 0, use 0. (Config says unlimited).
            # If expiry_db == 0, use 0. (DB says unlimited).

            if expiry_db is None:
                expiry = expiry_json
            elif expiry_json == 0 or expiry_db == 0:
                expiry = 0
            else:
                expiry = max(expiry_json, expiry_db)

            if expiry == 0:
                item['sort_val'] = 36500 * 24 * 3600 * 1000 # Put unlimited at top (large number)
                item['display_val'] = "♾️"
            elif expiry > current_time_ms:
                remaining_ms = expiry - current_time_ms
                days = remaining_ms / (1000 * 3600 * 24)
                item['sort_val'] = days
                item['display_val'] = f"{int(days)}d"
            else:
                item['sort_val'] = -1
                item['display_val'] = "Expired"

        item['is_active'] = is_active
        leaderboard.append(item)

    # Sort: inactive first, then by value desc
    leaderboard.sort(key=lambda x: (0 if not x.get('is_active') else 1, -x['sort_val']))

    total_items = len(leaderboard)
    total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    if total_pages == 0:
        total_pages = 1

    if page >= total_pages:
        page = total_pages - 1
    if page < 0:
        page = 0

    start = page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    current_items = leaderboard[start:end]

    title_key = "leaderboard_title_traffic" if sort_type == 'traffic' else "leaderboard_title_sub"
    text = t(title_key, lang).format(page=page+1, total=total_pages)
    if not current_items:
        text += t("leaderboard_empty", lang)

    # Keyboard construction
    keyboard = []

    # Toggle Button
    toggle_sort = 'sub' if sort_type == 'traffic' else 'traffic'
    toggle_label = "🔄 Sort by Subscription" if sort_type == 'traffic' else "🔄 Sort by Traffic"
    keyboard.append([InlineKeyboardButton(toggle_label, callback_data=f'admin_leaderboard_{toggle_sort}_0')])

    for i, item in enumerate(current_items):
        rank = start + i + 1
        status = "🟢" if item.get('is_active') else "🔴"
        label_text = item['label']
        # Truncate label
        if len(label_text) > 20:
            label_text = label_text[:17] + "..."

        btn_label = f"#{rank} {status} {label_text} ({item['display_val']})"
        keyboard.append([InlineKeyboardButton(btn_label, callback_data=f"admin_u_{item['uid']}")])

    # Navigation
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️", callback_data=f'admin_leaderboard_{sort_type}_{page-1}'))

    nav_row.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data='noop'))

    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("➡️", callback_data=f'admin_leaderboard_{sort_type}_{page+1}'))

    keyboard.append(nav_row)
    keyboard.append([InlineKeyboardButton(t("btn_back_admin", lang), callback_data='admin_panel')])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_reset_trial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    uid = query.data.split('_', 3)[3] # admin_reset_trial_UID

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return

    settings = json.loads(row[0])
    clients = settings.get('clients', [])
    client = next((c for c in clients if c.get('id') == uid), None)

    if client and client.get('tgId'):
        tg_id = str(client.get('tgId'))
        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("UPDATE user_prefs SET trial_used=0 WHERE tg_id=?", (tg_id,))
        conn.commit()
        conn.close()

        await context.bot.send_message(chat_id=query.from_user.id, text=t("msg_reset_success", lang).format(email=client.get('email')))

        # Refresh details
        await admin_user_detail(update, context)
    else:
         await context.bot.send_message(chat_id=query.from_user.id, text=t("msg_tgid_missing", lang))

async def admin_user_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    uid = query.data.split('_', 2)[2]

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        return

    settings = json.loads(row[0])
    clients = settings.get('clients', [])

    client = next((c for c in clients if c.get('id') == uid), None)
    if not client:
        conn.close()
        await query.edit_message_text(t("msg_client_not_found", lang), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back_list", lang), callback_data='admin_users_0')]]))
        return

    email = client.get('email', 'Unknown')

    # Get stats from client_traffics
    cursor.execute("SELECT up, down, last_online, expiry_time FROM client_traffics WHERE email=?", (email,))
    traffic_row = cursor.fetchone()
    conn.close()

    # Default values from settings
    up = client.get('up', 0)
    down = client.get('down', 0)
    enable_val = client.get('enable', False)
    expiry_ms = client.get('expiryTime', 0)
    total_limit = client.get('total', 0)
    limit_ip = client.get('limitIp', 0)
    last_online = 0

    if traffic_row:
        if traffic_row[0] is not None:
            up = traffic_row[0]
        if traffic_row[1] is not None:
            down = traffic_row[1]
        if traffic_row[2] is not None:
            last_online = traffic_row[2]
        if traffic_row[3] is not None:
            expiry_ms = traffic_row[3]

    # Calculations
    up_gb = up / (1024**3)
    down_gb = down / (1024**3)
    total_used_gb = up_gb + down_gb

    limit_str = f"{total_limit / (1024**3):.2f} GB" if total_limit > 0 else f"♾️ {t('plan_unlimited', lang)}"
    limit_ip_str = str(limit_ip) if limit_ip > 0 else "♾️"

    current_time_ms = int(time.time() * 1000)

    # Online status (10 seconds threshold)
    is_online = (current_time_ms - last_online) < 10 * 1000 if last_online > 0 else False
    online_status = t("status_online", lang) if is_online else t("status_offline", lang)

    # Active status
    is_enabled_str = t("status_yes", lang) if enable_val else t("status_no", lang)

    # Subscription status
    is_sub_active = (expiry_ms == 0) or (expiry_ms > current_time_ms)
    sub_active_str = t("status_yes", lang) if is_sub_active else t("status_no", lang)

    # Rank
    rank, total_users, _ = get_user_rank_traffic(email)
    rank_str = f"#{rank} / {total_users}" if rank else "?"

    expiry_display = format_expiry_display(expiry_ms, lang, current_time_ms, "expiry_unlimited")

    current_time_str = datetime.datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")

    # Check trial status
    trial_status_str = f"❓ {t('trial_unknown', lang)}"
    show_reset_trial = False

    if client.get('tgId'):
        tg_id_val = str(client.get('tgId'))

        # Try to get Username
        username = t("trial_unknown", lang) # Not found
        try:
            # Check DB first
            conn = sqlite3.connect(BOT_DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT username, first_name, last_name, trial_used FROM user_prefs WHERE tg_id=?", (tg_id_val,))
            row = cursor.fetchone()
            conn.close()

            db_uname = None
            db_fname = None
            db_lname = None
            trial_status_str = t("trial_used_no", lang)
            show_reset_trial = False

            if row:
                db_uname = row[0]
                db_fname = row[1]
                db_lname = row[2]
                if row[3]:
                    trial_status_str = t("trial_used_yes", lang)
                    show_reset_trial = True
            else:
                trial_status_str = f"{t('trial_used_no', lang)} (No DB)"

            # Use DB info if available
            if db_uname:
                username = f"@{db_uname}"
            elif db_fname:
                username = db_fname
                if db_lname:
                    username += f" {db_lname}"
            else:
                # Try fetch if not in DB
                chat = await context.bot.get_chat(tg_id_val)
                if chat.username:
                    username = f"@{chat.username}"
                    # Update DB
                    update_user_info(tg_id_val, chat.username, chat.first_name, chat.last_name)
                elif chat.first_name:
                    username = chat.first_name
                    if chat.last_name:
                        username += f" {chat.last_name}"
                    update_user_info(tg_id_val, None, chat.first_name, chat.last_name)
        except Exception:
            # logging.error(f"Failed to resolve username for {tg_id_val}: {e}")
            pass

    else:
        tg_id_val = t("status_unbound", lang)
        username = "-"
        trial_status_str = f"❓ {t('trial_unknown', lang)}"

    text = f"""{t('user_detail_email', lang)} {email}
🏆 Rank: {rank_str}
{t('user_detail_tgid', lang)} {tg_id_val}
{t('user_detail_nick', lang)} {username}
{t('user_detail_enabled', lang)} {is_enabled_str}
{t('user_detail_online', lang)} {online_status}
{t('user_detail_sub', lang)} {sub_active_str}
{t('user_detail_limit_ip', lang)} {limit_ip_str}
{t('user_detail_trial', lang)} {trial_status_str}
{t('user_detail_expires', lang)} {expiry_display}
{t('user_detail_up', lang)} ↑{up_gb:.2f}GB
{t('user_detail_down', lang)} ↓{down_gb:.2f}GB
{t('user_detail_total', lang)} ↑↓{total_used_gb:.2f}GB {t('user_detail_from', lang)} {limit_str}

{t('updated_label', lang)} {current_time_str}"""

    keyboard = []
    if show_reset_trial:
        keyboard.append([InlineKeyboardButton(t("btn_reset_trial", lang), callback_data=f'admin_reset_trial_{uid}')])

    keyboard.append([InlineKeyboardButton(t("btn_edit_limit_ip", lang), callback_data=f'admin_edit_limit_ip_{uid}')])
    keyboard.append([InlineKeyboardButton(t("btn_ip_history", lang), callback_data=f'admin_ip_history_{uid}')])
    keyboard.append([InlineKeyboardButton(t("btn_rebind", lang), callback_data=f'admin_rebind_{uid}')])
    keyboard.append([InlineKeyboardButton(t("btn_delete_user", lang), callback_data=f'admin_del_client_ask_{uid}')])
    keyboard.append([InlineKeyboardButton(t("btn_back_list", lang), callback_data='admin_users_0')])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_edit_limit_ip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    uid = query.data.split('_')[4] # admin_edit_limit_ip_UUID

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return

    settings = json.loads(row[0])
    clients = settings.get('clients', [])
    client = next((c for c in clients if c.get('id') == uid), None)

    if not client:
        return

    current_limit = client.get('limitIp', 0)

    context.user_data['edit_limit_ip_uid'] = uid
    context.user_data['admin_action'] = 'awaiting_limit_ip'

    await query.edit_message_text(
        t("limit_ip_prompt", lang).format(limit=current_limit),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_cancel", lang), callback_data=f'admin_u_{uid}')]]),
        parse_mode='Markdown'
    )

async def admin_ip_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    uid = query.data.split('_', 3)[3] # admin_ip_history_UUID

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return

    settings = json.loads(row[0])
    clients = settings.get('clients', [])
    client = next((c for c in clients if c.get('id') == uid), None)

    if not client:
        return

    email = client.get('email')

    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT ip, timestamp, country_code FROM connection_logs WHERE email=? ORDER BY timestamp DESC LIMIT 20", (email,))
    rows = cursor.fetchall()
    conn.close()

    text = t("ip_history_title", lang).format(email=email)

    if not rows:
        text += t("ip_history_empty", lang)
    else:
        for row in rows:
            ip, ts, cc = row
            time_str = datetime.datetime.fromtimestamp(ts, tz=TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
            flag = get_flag_emoji(cc)
            country = cc if cc else "Unknown"
            text += t("ip_history_entry", lang).format(flag=flag, ip=ip, country=country, time=time_str)

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back_list", lang), callback_data=f'admin_u_{uid}')]]),
        parse_mode='Markdown'
    )

async def admin_suspicious_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    # admin_suspicious_PAGE
    parts = query.data.split('_')
    page = 0
    if len(parts) >= 3:
        try:
            page = int(parts[2])
        except Exception:
            page = 0

    ITEMS_PER_PAGE = 20
    offset = page * ITEMS_PER_PAGE
    now_ts = int(time.time())
    since_ts = now_ts - SUSPICIOUS_EVENTS_LOOKBACK_SEC

    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()

    # Get total count
    cursor.execute("SELECT COUNT(*) FROM suspicious_events WHERE last_seen > ?", (since_ts,))
    total_items = cursor.fetchone()[0]
    total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    if total_pages == 0:
        total_pages = 1

    if page >= total_pages:
        page = total_pages - 1
        offset = page * ITEMS_PER_PAGE

    # Get items
    cursor.execute("""
        SELECT email, ips, last_seen, count
        FROM suspicious_events
        WHERE last_seen > ?
        ORDER BY last_seen DESC
        LIMIT ? OFFSET ?
    """, (since_ts, ITEMS_PER_PAGE, offset))
    rows = cursor.fetchall()
    conn.close()

    text = t("suspicious_title", lang).format(page=page+1, total=total_pages)
    lookback_hours = max(1, int(SUSPICIOUS_EVENTS_LOOKBACK_SEC // 3600))
    if lang == "ru":
        text += f"Период: последние {lookback_hours} ч\n\n"
    else:
        text += f"Period: last {lookback_hours}h\n\n"

    if not rows:
        text += t("suspicious_empty", lang)
    else:
        # Fetch client comments map (email -> comment)
        client_map = {}
        try:
            conn_xui = sqlite3.connect(DB_PATH)
            cursor_xui = conn_xui.cursor()
            cursor_xui.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
            row_xui = cursor_xui.fetchone()
            conn_xui.close()

            if row_xui:
                settings = json.loads(row_xui[0])
                clients = settings.get('clients', [])
                for c in clients:
                    email = c.get('email', '')
                    comment = c.get('comment', '') or c.get('_comment', '') or c.get('remark', '')
                    if email and comment:
                        client_map[email] = comment
        except Exception as e:
            logging.error(f"Error fetching client comments for suspicious: {e}")

        for row in rows:
            email, ip_str, last_seen, count = row
            time_str = datetime.datetime.fromtimestamp(last_seen, tz=TIMEZONE).strftime("%Y-%m-%d %H:%M")

            # Format IPs: try to ensure flags are present if string already has them, otherwise just display
            # The background task stores formatted string "🇺🇸 1.2.3.4, 🇩🇪 5.6.7.8"

            # Get name from map
            user_name = client_map.get(email, "")
            user_info_str = ""
            if user_name:
                user_info_str = f"👤 {user_name}\n"

            # Text entry
            text += f"📧 `{email}`\n{user_info_str}⏱ {time_str} | 🔢 {count} x\n🔌 {ip_str}\n\n"

    # Pagination
    keyboard = []
    nav_row = []

    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️", callback_data=f'admin_suspicious_{page-1}'))

    nav_row.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data='noop'))

    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("➡️", callback_data=f'admin_suspicious_{page+1}'))

    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton(t("btn_back_admin", lang), callback_data='admin_panel')])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def admin_rebind_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    # Expected format: admin_rebind_UUID
    try:
        # Split by 'admin_rebind_' and take the rest
        # data: admin_rebind_123-456
        uid = query.data[len("admin_rebind_"):]
    except IndexError:
        await query.message.reply_text(t("error_invalid_id", lang))
        return

    context.user_data['rebind_uid'] = uid
    context.user_data['admin_action'] = 'awaiting_rebind_contact'

    keyboard = [
        [KeyboardButton(t("btn_select_user", lang), request_users=KeyboardButtonRequestUsers(request_id=1, user_is_bot=False, max_quantity=1))],
        [KeyboardButton(t("btn_cancel", lang))]
    ]

    # We need to send a new message for reply keyboard, or delete previous and send new
    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text=t("rebind_title", lang).format(uid=uid),
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode='Markdown'
    )

async def admin_promos_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    keyboard = [
        [InlineKeyboardButton(t("btn_admin_promo_new", lang), callback_data='admin_new_promo')],
        [InlineKeyboardButton(t("btn_admin_promo_list", lang), callback_data='admin_promo_list')],
        [InlineKeyboardButton(t("btn_admin_flash", lang), callback_data='admin_flash_menu')],
        [InlineKeyboardButton(t("btn_admin_promo_history", lang), callback_data='admin_promo_uses_0')],
        [InlineKeyboardButton(t("btn_back_admin", lang), callback_data='admin_panel')]
    ]

    await query.edit_message_text(
        t("promos_menu_title", lang),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def admin_promo_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    # Fetch active promos: max_uses=0 (unlimited) OR used_count < max_uses
    # Also we don't track expiry date of the promo itself yet, only days it gives.
    cursor.execute("SELECT code, days, max_uses, used_count FROM promo_codes WHERE max_uses <= 0 OR used_count < max_uses")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await query.edit_message_text(
            t("promo_list_empty", lang),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='admin_promos_menu')]]),
            parse_mode='Markdown'
        )
        return

    text = t("promo_list_title", lang)
    keyboard = []

    for r in rows:
        code, days, max_uses, used_count = r
        limit_str = "♾️" if max_uses <= 0 else f"{max_uses}"
        text += f"🏷 `{code}`\n{t('promo_item_days', lang).format(days=days)}\n{t('promo_item_used', lang).format(used=used_count, limit=limit_str)}\n\n"
        # Add delete button for each promo
        keyboard.append([InlineKeyboardButton(f"🗑 {t('btn_delete', lang)} {code}", callback_data=f'admin_revoke_code_menu_{code}')])

    # Split if too long (simple check)
    if len(text) > 4000:
        text = text[:4000] + "\n...(обрезано)"

    keyboard.append([InlineKeyboardButton(t("btn_back", lang), callback_data='admin_promos_menu')])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def admin_revoke_promo_code_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    code = query.data[len("admin_revoke_code_menu_"):]

    text = t("promo_delete_confirm", lang).format(code=code)

    keyboard = [
        [InlineKeyboardButton(t("btn_yes", lang), callback_data=f'admin_revoke_code_act_{code}')],
        [InlineKeyboardButton(t("btn_no", lang), callback_data='admin_promo_list')]
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_revoke_promo_code_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    code = query.data[len("admin_revoke_code_act_"):]

    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM promo_codes WHERE code=?", (code,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted > 0:
        await query.answer(t("promo_deleted", lang), show_alert=True)
    else:
        await query.answer(t("promo_not_found", lang), show_alert=True)

    # Refresh list
    await admin_promo_list(update, context)

async def admin_promo_uses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        page = int(query.data.split('_')[3])
    except Exception:
        page = 0

    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()

    # Get distinct users who used promos, ordered by most recent use
    cursor.execute("""
        SELECT DISTINCT tg_id
        FROM user_promos
        ORDER BY used_at DESC
    """)
    all_users = [row[0] for row in cursor.fetchall()]

    users_per_page = 10
    total_pages = math.ceil(len(all_users) / users_per_page)
    if total_pages == 0:
        total_pages = 1

    if page >= total_pages:
        page = total_pages - 1
    if page < 0:
        page = 0

    start = page * users_per_page
    end = start + users_per_page
    current_users_ids = all_users[start:end]

    keyboard = []

    for uid in current_users_ids:
        # Get user info
        cursor.execute("SELECT first_name, username FROM user_prefs WHERE tg_id=?", (uid,))
        u_row = cursor.fetchone()
        name = uid
        if u_row:
            f_name = u_row[0] or ""
            u_name = f"@{u_row[1]}" if u_row[1] else ""
            display = f"{f_name} {u_name}".strip()
            if display:
                name = display

        # Truncate name if too long
        if len(name) > 30:
            name = name[:27] + "..."

        # Get count of promos
        cursor.execute("SELECT COUNT(*) FROM user_promos WHERE tg_id=?", (uid,))
        count = cursor.fetchone()[0]

        label = f"{name} ({count} шт.)"
        keyboard.append([InlineKeyboardButton(label, callback_data=f'admin_promo_u_{uid}')])

    conn.close()

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️", callback_data=f'admin_promo_uses_{page-1}'))

    nav_row.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data='noop'))

    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("➡️", callback_data=f'admin_promo_uses_{page+1}'))

    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='admin_promos_menu')])

    await query.edit_message_text(
        "👥 *Пользователи промокодов*\n\nВыберите пользователя для просмотра деталей:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def admin_promo_user_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        tg_id = query.data.split('_')[3]
    except Exception:
        return

    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()

    # Get user info
    cursor.execute("SELECT first_name, username FROM user_prefs WHERE tg_id=?", (tg_id,))
    u_row = cursor.fetchone()
    name = tg_id
    if u_row:
        f_name = u_row[0] or ""
        u_name = f"@{u_row[1]}" if u_row[1] else ""
        display = f"{f_name} {u_name}".strip()
        if display:
            name = display

    # Get promos
    cursor.execute("""
        SELECT u.code, u.used_at, p.days
        FROM user_promos u
        LEFT JOIN promo_codes p ON u.code = p.code
        WHERE u.tg_id=?
        ORDER BY u.used_at DESC
    """, (tg_id,))
    rows = cursor.fetchall()
    conn.close()

    # Use HTML for safety with names
    safe_name = html.escape(name)
    text = f"👤 Промокоды пользователя\n{safe_name}\n<code>{tg_id}</code>\n\n"

    if not rows:
        text += "Нет использованных промокодов."
    else:
        for row in rows:
            code, used_at, days = row
            date_str = datetime.datetime.fromtimestamp(used_at, tz=TIMEZONE).strftime("%d.%m.%Y %H:%M")
            days_str = f"{days} дн." if days else "N/A"
            safe_code = html.escape(code)

            # Check expiration
            is_expired = False
            if days:
                expire_ts = used_at + (days * 24 * 3600)
                if expire_ts < time.time():
                    is_expired = True

            icon = "❌" if is_expired else "✅"
            text += f"{icon} 🏷 <code>{safe_code}</code>\n⏳ {days_str} | 📅 {date_str}\n\n"

    keyboard = []
    if rows:
        keyboard.append([InlineKeyboardButton("🗑 Аннулировать промокод", callback_data=f'admin_revoke_user_menu_{tg_id}')])

    keyboard.append([InlineKeyboardButton("🔙 К списку", callback_data='admin_promo_uses_0')])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

async def admin_revoke_user_promo_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = query.data.split('_')[4]

    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT u.code, p.days
        FROM user_promos u
        LEFT JOIN promo_codes p ON u.code = p.code
        WHERE u.tg_id=?
    """, (tg_id,))
    rows = cursor.fetchall()
    conn.close()

    keyboard = []
    for row in rows:
        code, days = row
        keyboard.append([InlineKeyboardButton(f"{code} (-{days} дн.)", callback_data=f'admin_revoke_user_conf_{tg_id}_{code}')])

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f'admin_promo_u_{tg_id}')])

    await query.edit_message_text("🗑 *Аннулирование промокода*\n\nВыберите промокод для отмены (срок подписки уменьшится):", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_revoke_user_promo_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split('_')
    tg_id = parts[4]
    code = parts[5]

    # Get days
    days = 0
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT days FROM promo_codes WHERE code=?", (code,))
    row = cursor.fetchone()
    if row:
        days = row[0]
    conn.close()

    keyboard = [
        [InlineKeyboardButton("✅ Да, аннулировать", callback_data=f'admin_revoke_user_act_{tg_id}_{code}')],
        [InlineKeyboardButton("❌ Отмена", callback_data=f'admin_revoke_user_menu_{tg_id}')]
    ]

    await query.edit_message_text(f"⚠️ Вы уверены, что хотите аннулировать промокод `{code}` для пользователя `{tg_id}`?\n\nСрок подписки уменьшится на {days} дней.", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_revoke_user_promo_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split('_')
    tg_id = parts[4]
    code = parts[5]

    # 1. Get days and delete from DB
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()

    # Get days first
    cursor.execute("SELECT days FROM promo_codes WHERE code=?", (code,))
    row = cursor.fetchone()
    days = row[0] if row else 0

    # Delete from user_promos
    cursor.execute("DELETE FROM user_promos WHERE tg_id=? AND code=?", (tg_id, code))

    # Decrement used_count
    cursor.execute("UPDATE promo_codes SET used_count = MAX(0, used_count - 1) WHERE code=?", (code,))

    conn.commit()
    conn.close()

    # 2. Update Subscription (-days)
    if days > 0:
        await process_subscription(tg_id, -days, update, context, get_lang(tg_id), is_callback=True)

    await query.edit_message_text(f"✅ Промокод `{code}` аннулирован.\nСрок подписки уменьшен на {days} дн.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К пользователю", callback_data=f'admin_promo_u_{tg_id}')]]), parse_mode='Markdown')

async def admin_new_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "🎁 *Создать промокод*\n\nОтправьте детали промокода в формате:\n`CODE DAYS LIMIT`\n\nПример: `NEWYEAR 30 100`\n(LIMIT 0 = безлимит)",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data='admin_promos_menu')]]),
        parse_mode='Markdown'
    )
    context.user_data['admin_action'] = 'awaiting_promo_data'

async def admin_flash_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    # Get active promos
    cursor.execute("SELECT code, days, max_uses, used_count FROM promo_codes WHERE max_uses <= 0 OR used_count < max_uses")
    rows = cursor.fetchall()
    conn.close()

    keyboard = []
    for r in rows:
        code, days, max_uses, used_count = r
        remaining = "♾️"
        if max_uses > 0:
            remaining = max_uses - used_count

        keyboard.append([InlineKeyboardButton(f"{code} ({days} дн. | ост: {remaining})", callback_data=f'admin_flash_sel_{code}')])

    keyboard.append([InlineKeyboardButton("🧨 Удалить все Flash", callback_data='admin_flash_delete_all')])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='admin_promos_menu')])

    await query.edit_message_text(
        "⚡ *Flash Промокод*\n\nВыберите промокод, который хотите отправить во временной рассылке:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

def _flash_delete_is_permanent_error(err: Exception) -> bool:
    if isinstance(err, Forbidden):
        return True
    if isinstance(err, BadRequest):
        msg = str(err).lower()
        return (
            "chat not found" in msg
            or "message to delete not found" in msg
            or "message can't be deleted" in msg
            or "message cannot be deleted" in msg
        )
    return False

async def admin_flash_delete_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Удаление...")

    try:
        conn = sqlite3.connect(BOT_DB_PATH, timeout=10)
        cursor = conn.cursor()
        cursor.execute("SELECT id, chat_id, message_id FROM flash_messages")
        rows = cursor.fetchall()

        deleted_count = 0
        failed_count = 0
        for row in rows:
            db_id, chat_id, msg_id = row
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                deleted_count += 1
            except Exception as e:
                if _flash_delete_is_permanent_error(e):
                    failed_count += 1
                elif isinstance(e, (TimedOut, NetworkError)):
                    failed_count += 1
                else:
                    failed_count += 1

        cursor.execute("DELETE FROM flash_messages")
        conn.commit()
        conn.close()

        await query.message.reply_text(
            f"✅ Принудительно удалено: {deleted_count}\n"
            f"⚠️ Не удалось удалить: {failed_count}"
        )
        # Return to menu
        await admin_flash_menu(update, context)

    except Exception as e:
        logging.error(f"Error in delete all flash: {e}")
        await query.message.reply_text("❌ Ошибка при удалении.")

async def admin_flash_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    code = query.data.split('_')[3]
    context.user_data['flash_code'] = code
    context.user_data['admin_action'] = 'awaiting_flash_duration'

    await query.edit_message_text(
        f"⚡ Выбран промокод: `{code}`\n\nВведите время жизни сообщения в минутах (например: 60).\nПо истечении этого времени сообщение будет удалено у всех пользователей.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data='admin_flash_menu')]]),
        parse_mode='Markdown'
    )

async def admin_flash_errors(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT user_id, error_message, timestamp FROM flash_delivery_errors ORDER BY timestamp DESC")
        raw_rows = cursor.fetchall()
    except sqlite3.OperationalError:
        conn.close()
        await query.edit_message_text(
            "📉 *Недоставленные сообщения:*\n\nСписок пуст.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_flash_menu")]]),
        )
        return

    rows: list[tuple[str, str]] = []
    seen_users: set[str] = set()
    for uid, err, _ts in raw_rows:
        uid_str = str(uid)
        if uid_str in seen_users:
            continue
        seen_users.add(uid_str)
        rows.append((uid_str, str(err)))

    user_map: dict[str, tuple[Optional[str], Optional[str]]] = {}
    try:
        cursor.execute("SELECT tg_id, first_name, username FROM user_prefs")
        user_rows = cursor.fetchall()
        user_map = {str(u[0]): (u[1], u[2]) for u in user_rows}
    except sqlite3.OperationalError:
        try:
            cursor.execute("SELECT tg_id, username FROM user_prefs")
            user_rows2 = cursor.fetchall()
            user_map = {str(u[0]): (None, u[1]) for u in user_rows2}
        except sqlite3.OperationalError:
            user_map = {}
    finally:
        conn.close()

    text = "📉 *Недоставленные сообщения:*\n\n"
    if not rows:
        text += "Список пуст."
    else:
        for r in rows:
            uid, err = r
            name_info = user_map.get(uid)
            safe_uid = _escape_markdown(uid)
            name_str = f"`{safe_uid}`"
            if name_info:
                fn = _escape_markdown(name_info[0] or "")
                un = _escape_markdown(f"@{name_info[1]}") if name_info[1] else ""
                name_str = f"{fn} {un} (`{safe_uid}`)".strip()

            # Simplify error
            err_clean = str(err)
            if "Forbidden" in err_clean:
                err_clean = "Бот заблокирован"
            elif "chat not found" in err_clean.lower():
                err_clean = "Чат не найден"
            err_clean = _escape_markdown(err_clean)

            text += f"👤 {name_str}\n❌ {err_clean}\n\n"

    # Split if too long
    if len(text) > 4000:
        text = text[:4000] + "\n...(обрезано)"

    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data='admin_flash_menu')]]))

async def cleanup_flash_messages(context: ContextTypes.DEFAULT_TYPE):
    try:
        current_ts = int(time.time())
        conn = sqlite3.connect(BOT_DB_PATH, timeout=10)
        cursor = conn.cursor()

        cursor.execute("SELECT id, chat_id, message_id FROM flash_messages WHERE delete_at <= ?", (current_ts,))
        rows = cursor.fetchall()

        if not rows:
            conn.close()
            return

        deleted_count = 0
        kept_count = 0
        for row in rows:
            db_id, chat_id, msg_id = row
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                cursor.execute("DELETE FROM flash_messages WHERE id=?", (db_id,))
                deleted_count += 1
            except Exception as e:
                if _flash_delete_is_permanent_error(e):
                    cursor.execute("DELETE FROM flash_messages WHERE id=?", (db_id,))
                    deleted_count += 1
                else:
                    kept_count += 1

        conn.commit()
        conn.close()
        if deleted_count > 0:
            if kept_count > 0:
                logging.info(f"Cleaned up {deleted_count} flash messages (kept {kept_count} for retry).")
            else:
                logging.info(f"Cleaned up {deleted_count} flash messages.")

    except Exception as e:
        logging.error(f"Error in cleanup_flash_messages: {e}")

async def admin_search_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "🔍 *Поиск пользователя*\n\nОтправьте *Telegram ID* пользователя для поиска в базе данных.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data='admin_panel')]]),
        parse_mode='Markdown'
    )
    context.user_data['admin_action'] = 'awaiting_search_user'


async def admin_sales_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    try:
        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT tg_id, amount, date, plan_id, telegram_payment_charge_id "
                "FROM transactions WHERE tg_id != '369456269' ORDER BY date DESC LIMIT 100"
            )
        except sqlite3.OperationalError:
            cursor.execute(
                "SELECT tg_id, amount, date, plan_id, NULL "
                "FROM transactions WHERE tg_id != '369456269' ORDER BY date DESC LIMIT 100"
            )
        rows = _dedupe_sales_log_rows(cursor.fetchall())[:20]
        conn.close()

        if not rows:
            await query.edit_message_text(
                t("sales_log_empty", lang),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back_admin", lang), callback_data='admin_panel')]]),
                parse_mode='Markdown'
            )
            return

        # Fetch client comments map (tg_id -> comment)
        client_map = {}
        try:
            conn_xui = sqlite3.connect(DB_PATH)
            cursor_xui = conn_xui.cursor()
            cursor_xui.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
            row_xui = cursor_xui.fetchone()
            conn_xui.close()

            if row_xui:
                settings = json.loads(row_xui[0])
                clients = settings.get('clients', [])
                for c in clients:
                    cid = str(c.get('tgId', ''))
                    comment = c.get('comment', '') or c.get('_comment', '') or c.get('remark', '')
                    if cid and comment:
                        client_map[cid] = comment
        except Exception as e:
            logging.error(f"Error fetching client comments: {e}")

        text = t("sales_log_title", lang)

        for row in rows:
            tg_id_tx, amount, date_ts, plan_id, _charge_id = row
            date_str = datetime.datetime.fromtimestamp(date_ts, tz=TIMEZONE).strftime("%d.%m %H:%M")

            # Localize plan name
            plan_display = TEXTS[lang].get(f"plan_{plan_id}", plan_id)

            # Get name from map
            user_name = client_map.get(tg_id_tx, "Unknown")
            # If name is unknown, try to find in user_prefs?
            # (Optional, but user specifically asked for comment cells)

            text += f"📅 `{date_str}` | 🆔 `{tg_id_tx}`\n👤 {user_name}\n💳 {plan_display} | 💰 {amount} XTR\n\n"

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back_admin", lang), callback_data='admin_panel')]]),
            parse_mode='Markdown'
        )
    except Exception as e:
        logging.error(f"Error in sales log: {e}")
        await query.edit_message_text(t("sales_log_error", lang), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back_admin", lang), callback_data='admin_panel')]]))

async def admin_user_db_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, tg_id):
    user_data = get_user_data(tg_id)
    lang = get_lang(tg_id)

    trial_status = "❌ Не использован"
    trial_date = ""
    if user_data['trial_used']:
        trial_status = "✅ Использован"
        if user_data.get('trial_activated_at'):
            trial_date = datetime.datetime.fromtimestamp(user_data['trial_activated_at'], tz=TIMEZONE).strftime("%d.%m.%Y %H:%M")

    text = f"""👤 *Информация о пользователе (DB)*

🆔 TG ID: `{tg_id}`
🌍 Язык: {lang}
🆓 Пробный период: {trial_status}
📅 Дата активации: {trial_date}
👥 Реферер: {user_data.get('referrer_id') or 'Нет'}
"""
    keyboard = []
    if user_data['trial_used']:
        keyboard.append([InlineKeyboardButton("🔄 Сбросить пробный период (DB)", callback_data=f'admin_rt_db_{tg_id}')])

    keyboard.append([InlineKeyboardButton("❌ Удалить из базы", callback_data=f'admin_del_db_{tg_id}')])
    keyboard.append([InlineKeyboardButton("🔙 В админ панель", callback_data='admin_panel')])

    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        except Exception:
            # Fallback if message not modified or other error
            pass
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_db_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        tg_id = query.data.split('_')[3] # admin_db_detail_TGID
        await admin_user_db_detail(update, context, tg_id)
    except Exception:
        pass

async def admin_reset_trial_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # admin_rt_db_TGID
    try:
        tg_id = query.data.split('_')[3]
    except Exception:
        return

    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE user_prefs SET trial_used=0, trial_activated_at=NULL WHERE tg_id=?", (tg_id,))
    conn.commit()
    conn.close()

    await query.edit_message_text(f"✅ Пробный период для `{tg_id}` сброшен.", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 В админ панель", callback_data='admin_panel')]]))

async def admin_delete_user_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # admin_del_db_TGID
    try:
        tg_id = query.data.split('_')[3]
    except Exception:
        return

    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_prefs WHERE tg_id=?", (tg_id,))
    cursor.execute("DELETE FROM user_promos WHERE tg_id=?", (tg_id,))
    conn.commit()
    conn.close()

    await query.edit_message_text(f"✅ Пользователь `{tg_id}` удален из базы бота.", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 В админ панель", callback_data='admin_panel')]]))

async def admin_cleanup_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = get_lang(str(query.from_user.id))

    vpn_tg_ids: set[str] = set()
    try:
        conn_xui = sqlite3.connect(DB_PATH)
        cursor_xui = conn_xui.cursor()
        cursor_xui.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
        row = cursor_xui.fetchone()
        conn_xui.close()
        if row:
            settings = json.loads(row[0])
            for client in settings.get("clients", []):
                client_tg_id = get_client_tg_id(client)
                if client_tg_id is not None:
                    vpn_tg_ids.add(client_tg_id)
    except Exception:
        vpn_tg_ids = set()

    conn_bot = sqlite3.connect(BOT_DB_PATH)
    cursor_bot = conn_bot.cursor()
    cursor_bot.execute("SELECT DISTINCT tg_id FROM transactions")
    tx_users = {str(r[0]) for r in cursor_bot.fetchall()}

    try:
        cursor_bot.execute(
            """
            SELECT tg_id
            FROM user_prefs
            WHERE (username IS NULL OR username = '')
              AND (first_name IS NULL OR first_name = '')
              AND (last_name IS NULL OR last_name = '')
              AND (trial_used IS NULL OR trial_used = 0)
              AND trial_activated_at IS NULL
            """
        )
        candidates = [str(r[0]) for r in cursor_bot.fetchall()]
    except Exception:
        candidates = []

    delete_ids = [tg for tg in candidates if tg not in vpn_tg_ids and tg not in tx_users]
    if delete_ids:
        cursor_bot.executemany("DELETE FROM user_prefs WHERE tg_id=?", [(tg,) for tg in delete_ids])
        cursor_bot.executemany("DELETE FROM user_promos WHERE tg_id=?", [(tg,) for tg in delete_ids])
        cursor_bot.executemany("DELETE FROM notifications WHERE tg_id=?", [(tg,) for tg in delete_ids])
        cursor_bot.executemany("DELETE FROM referral_bonuses WHERE referrer_id=? OR referred_id=?", [(tg, tg) for tg in delete_ids])
        conn_bot.commit()

    conn_bot.close()

    await query.edit_message_text(
        t("cleanup_db_done", lang).format(deleted=len(delete_ids)),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back_stats", lang), callback_data="admin_stats")]]),
        parse_mode="Markdown",
    )

async def admin_db_audit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = get_lang(str(query.from_user.id))

    vpn_tg_ids: set[str] = set()
    xui_no_tg_emails: list[str] = []
    xui_clients_total = 0
    try:
        conn_xui = sqlite3.connect(DB_PATH)
        cursor_xui = conn_xui.cursor()
        cursor_xui.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
        row = cursor_xui.fetchone()
        conn_xui.close()
        if row and row[0]:
            settings = json.loads(row[0])
            clients = settings.get("clients", []) or []
            xui_clients_total = len(clients)
            for client in clients:
                tid = get_client_tg_id(client)
                if tid is not None:
                    vpn_tg_ids.add(tid)
                else:
                    email = str(client.get("email", "") or "")
                    if email:
                        xui_no_tg_emails.append(email)
    except Exception:
        vpn_tg_ids = set()

    conn_bot = sqlite3.connect(BOT_DB_PATH)
    cursor_bot = conn_bot.cursor()
    cursor_bot.execute("SELECT tg_id FROM user_prefs")
    bot_users = {str(r[0]) for r in cursor_bot.fetchall() if r and r[0] is not None}

    cursor_bot.execute("SELECT tg_id, amount FROM transactions")
    tx_rows = [(str(r[0]), int(r[1] or 0)) for r in cursor_bot.fetchall() if r and r[0] is not None]
    conn_bot.close()

    bot_only = sorted(bot_users - vpn_tg_ids)
    xui_only = sorted(vpn_tg_ids - bot_users)

    tx_total = len(tx_rows)
    tx_sum = sum(a for _, a in tx_rows)
    tx_invalid_rows = [(tg, a) for tg, a in tx_rows if tg not in vpn_tg_ids]
    tx_invalid = len(tx_invalid_rows)
    tx_invalid_sum = sum(a for _, a in tx_invalid_rows)

    bot_only_examples_raw = ", ".join(bot_only[:20]) if bot_only else "—"
    xui_no_tg_examples_raw = ", ".join(xui_no_tg_emails[:10]) if xui_no_tg_emails else "—"
    bot_only_examples = _escape_markdown(bot_only_examples_raw)
    xui_no_tg_examples = _escape_markdown(xui_no_tg_examples_raw)

    text = t("db_audit_text", lang).format(
        xui_clients=xui_clients_total,
        xui_tg=len(vpn_tg_ids),
        xui_no_tg=len(xui_no_tg_emails),
        bot_users=len(bot_users),
        bot_only=len(bot_only),
        xui_only=len(xui_only),
        tx_total=tx_total,
        tx_sum=tx_sum,
        tx_invalid=tx_invalid,
        tx_invalid_sum=tx_invalid_sum,
        bot_only_examples=bot_only_examples,
        xui_no_tg_examples=xui_no_tg_examples,
    )

    keyboard = [
        [InlineKeyboardButton(t("btn_db_sync", lang), callback_data="admin_db_sync_confirm")],
        [InlineKeyboardButton(t("btn_back_stats", lang), callback_data="admin_stats")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

def _db_sync_plan() -> tuple[int, int, int, int]:
    vpn_tg_ids: set[str] = set()
    try:
        conn_xui = sqlite3.connect(DB_PATH)
        cursor_xui = conn_xui.cursor()
        cursor_xui.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
        row = cursor_xui.fetchone()
        conn_xui.close()
        if row and row[0]:
            settings = json.loads(row[0])
            for client in settings.get("clients", []) or []:
                tid = get_client_tg_id(client)
                if tid is not None:
                    vpn_tg_ids.add(tid)
    except Exception:
        vpn_tg_ids = set()

    protected_ids = set()
    if ADMIN_ID:
        protected_ids.add(str(ADMIN_ID))
    protected_tx_ids = {"369456269"} | protected_ids

    conn_bot = sqlite3.connect(BOT_DB_PATH)
    cursor_bot = conn_bot.cursor()

    cursor_bot.execute("SELECT tg_id FROM user_prefs")
    bot_users = {str(r[0]) for r in cursor_bot.fetchall() if r and r[0] is not None}
    delete_user_ids = [tg for tg in bot_users if tg not in vpn_tg_ids and tg not in protected_ids]
    users_deleted = len(delete_user_ids)

    cursor_bot.execute("SELECT id, tg_id, amount FROM transactions")
    tx_rows = [(int(r[0]), str(r[1]), int(r[2] or 0)) for r in cursor_bot.fetchall() if r and r[0] is not None]
    invalid_tx = [(tx_id, amount) for tx_id, tg, amount in tx_rows if tg not in vpn_tg_ids and tg not in protected_tx_ids]
    tx_deleted = len(invalid_tx)
    tx_deleted_sum = sum(amount for _, amount in invalid_tx)

    def email_tid(email: str) -> Optional[str]:
        if not email.startswith("tg_"):
            return None
        possible = email[3:].split("_", 1)[0]
        return possible if possible.isdigit() else None

    traffic_deleted = 0
    cursor_bot.execute("SELECT email FROM traffic_history WHERE email LIKE 'tg_%'")
    traffic_deleted += sum(
        1
        for (email,) in cursor_bot.fetchall()
        if (tid := email_tid(str(email or ""))) is not None and tid not in vpn_tg_ids
    )
    cursor_bot.execute("SELECT email FROM traffic_daily_baselines WHERE email LIKE 'tg_%'")
    traffic_deleted += sum(
        1
        for (email,) in cursor_bot.fetchall()
        if (tid := email_tid(str(email or ""))) is not None and tid not in vpn_tg_ids
    )

    conn_bot.close()
    return users_deleted, tx_deleted, tx_deleted_sum, traffic_deleted

async def admin_db_sync_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = get_lang(str(query.from_user.id))

    users_deleted, tx_deleted, tx_deleted_sum, traffic_deleted = _db_sync_plan()

    text = t("db_sync_confirm_text", lang).format(
        users_deleted=users_deleted,
        tx_deleted=tx_deleted,
        tx_deleted_sum=tx_deleted_sum,
        traffic_deleted=traffic_deleted,
    )
    keyboard = [
        [
            InlineKeyboardButton(t("btn_yes", lang), callback_data="admin_db_sync_all"),
            InlineKeyboardButton(t("btn_no", lang), callback_data="admin_db_audit"),
        ],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_db_sync_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = get_lang(str(query.from_user.id))

    try:
        await backup_db()
    except Exception:
        pass

    vpn_tg_ids: set[str] = set()
    try:
        conn_xui = sqlite3.connect(DB_PATH)
        cursor_xui = conn_xui.cursor()
        cursor_xui.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
        row = cursor_xui.fetchone()
        conn_xui.close()
        if row and row[0]:
            settings = json.loads(row[0])
            for client in settings.get("clients", []) or []:
                tid = get_client_tg_id(client)
                if tid is not None:
                    vpn_tg_ids.add(tid)
    except Exception:
        vpn_tg_ids = set()

    protected_ids = set()
    if ADMIN_ID:
        protected_ids.add(str(ADMIN_ID))
    protected_tx_ids = {"369456269"} | protected_ids

    conn_bot = sqlite3.connect(BOT_DB_PATH)
    cursor_bot = conn_bot.cursor()

    cursor_bot.execute("SELECT id, tg_id, amount FROM transactions")
    tx_rows = [(int(r[0]), str(r[1]), int(r[2] or 0)) for r in cursor_bot.fetchall() if r and r[0] is not None]
    invalid_tx_ids = [tx_id for tx_id, tg, _ in tx_rows if tg not in vpn_tg_ids and tg not in protected_tx_ids]
    tx_deleted_sum = sum(amount for tx_id, tg, amount in tx_rows if tg not in vpn_tg_ids and tg not in protected_tx_ids)
    if invalid_tx_ids:
        placeholders = ",".join(["?"] * len(invalid_tx_ids))
        cursor_bot.execute(f"DELETE FROM transactions WHERE id IN ({placeholders})", tuple(invalid_tx_ids))
        tx_deleted = int(cursor_bot.rowcount or 0)
    else:
        tx_deleted = 0

    cursor_bot.execute("SELECT tg_id FROM user_prefs")
    bot_users = {str(r[0]) for r in cursor_bot.fetchall() if r and r[0] is not None}
    delete_user_ids = sorted([tg for tg in bot_users if tg not in vpn_tg_ids and tg not in protected_ids])
    if delete_user_ids:
        placeholders = ",".join(["?"] * len(delete_user_ids))
        cursor_bot.execute(f"DELETE FROM user_prefs WHERE tg_id IN ({placeholders})", tuple(delete_user_ids))
        users_deleted = int(cursor_bot.rowcount or 0)
        cursor_bot.execute(f"DELETE FROM user_promos WHERE tg_id IN ({placeholders})", tuple(delete_user_ids))
        cursor_bot.execute(f"DELETE FROM notifications WHERE tg_id IN ({placeholders})", tuple(delete_user_ids))
        cursor_bot.execute(f"DELETE FROM poll_votes WHERE tg_id IN ({placeholders})", tuple(delete_user_ids))
        cursor_bot.execute(
            f"DELETE FROM referral_bonuses WHERE referrer_id IN ({placeholders}) OR referred_id IN ({placeholders})",
            tuple(delete_user_ids) + tuple(delete_user_ids),
        )
    else:
        users_deleted = 0

    def email_tid(email: str) -> Optional[str]:
        if not email.startswith("tg_"):
            return None
        possible = email[3:].split("_", 1)[0]
        return possible if possible.isdigit() else None

    traffic_deleted = 0
    cursor_bot.execute("SELECT DISTINCT email FROM traffic_history WHERE email LIKE 'tg_%'")
    delete_emails = []
    for (email,) in cursor_bot.fetchall():
        email_str = str(email or "")
        tid = email_tid(email_str)
        if tid is not None and tid not in vpn_tg_ids:
            delete_emails.append(email_str)
    if delete_emails:
        placeholders = ",".join(["?"] * len(delete_emails))
        cursor_bot.execute(f"DELETE FROM traffic_history WHERE email IN ({placeholders})", tuple(delete_emails))
        traffic_deleted += int(cursor_bot.rowcount or 0)
        cursor_bot.execute(f"DELETE FROM traffic_daily_baselines WHERE email IN ({placeholders})", tuple(delete_emails))
        traffic_deleted += int(cursor_bot.rowcount or 0)

    conn_bot.commit()
    conn_bot.close()

    await query.edit_message_text(
        t("db_sync_done", lang).format(
            users_deleted=users_deleted,
            tx_deleted=tx_deleted,
            tx_deleted_sum=tx_deleted_sum,
            traffic_deleted=traffic_deleted,
        ),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back_stats", lang), callback_data="admin_stats")]]),
        parse_mode="Markdown",
    )

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    keyboard = [
        [InlineKeyboardButton(t("btn_broadcast_all", lang), callback_data='admin_broadcast_all')],
        [InlineKeyboardButton(t("btn_broadcast_en", lang), callback_data='admin_broadcast_en')],
        [InlineKeyboardButton(t("btn_broadcast_ru", lang), callback_data='admin_broadcast_ru')],
        [InlineKeyboardButton(t("btn_broadcast_individual", lang), callback_data='admin_broadcast_individual')],
        [InlineKeyboardButton(t("btn_cancel", lang), callback_data='admin_panel')]
    ]

    await query.edit_message_text(
        t("broadcast_menu", lang),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

def get_users_pagination_keyboard(users, selected_ids, page, lang='ru', users_per_page=10):
    total_pages = math.ceil(len(users) / users_per_page)
    if total_pages == 0:
        total_pages = 1

    start = page * users_per_page
    end = start + users_per_page
    current_users = users[start:end]

    keyboard = []
    for u in current_users:
        uid = str(u[0])
        first_name = u[1] or ""
        username = f" (@{u[2]})" if u[2] else ""
        # Truncate name if too long
        name_display = (first_name + username).strip() or f"ID: {uid}"
        if len(name_display) > 30:
            name_display = name_display[:27] + "..."

        icon = "✅" if uid in selected_ids else "☑️"
        label = f"{icon} {name_display}"

        keyboard.append([InlineKeyboardButton(label, callback_data=f'admin_broadcast_toggle_{uid}_{page}')])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️", callback_data=f'admin_broadcast_page_{page-1}'))

    nav_row.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data='noop'))

    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("➡️", callback_data=f'admin_broadcast_page_{page+1}'))

    keyboard.append(nav_row)

    confirm_text = t("btn_done_count", lang).format(count=len(selected_ids))
    keyboard.append([InlineKeyboardButton(confirm_text, callback_data='admin_broadcast_confirm')])
    keyboard.append([InlineKeyboardButton(t("btn_cancel", lang), callback_data='admin_panel')])

    return InlineKeyboardMarkup(keyboard)

async def admin_broadcast_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    parts = query.data.split('_')
    # Format: admin_broadcast_ACTION_PARAM...
    # actions: all, en, ru, individual, toggle, page, confirm
    action = parts[2]

    if action == 'individual':
        await query.answer()
        context.user_data['broadcast_selected_ids'] = []
        context.user_data['broadcast_target'] = 'individual'

        users_by_id: dict[str, tuple[str, str, str]] = {}
        try:
            conn = sqlite3.connect(BOT_DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT tg_id, first_name, username FROM user_prefs")
            for tg, first_name, username in cursor.fetchall():
                tg_str = str(tg)
                users_by_id[tg_str] = (tg_str, first_name or "", username or "")
            conn.close()
        except Exception:
            users_by_id = {}

        try:
            conn_xui = sqlite3.connect(DB_PATH)
            cursor_xui = conn_xui.cursor()
            cursor_xui.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
            row = cursor_xui.fetchone()
            conn_xui.close()

            if row:
                settings = json.loads(row[0])
                clients = settings.get("clients", [])
                for client in clients:
                    client_tg_id = get_client_tg_id(client)
                    if client_tg_id is None:
                        continue
                    if client_tg_id not in users_by_id:
                        users_by_id[client_tg_id] = (client_tg_id, "", "")
        except Exception:
            pass

        users = list(users_by_id.values())

        keyboard = get_users_pagination_keyboard(users, [], 0, lang)
        await query.edit_message_text(
            t("broadcast_individual_title", lang),
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        return

    if action == 'toggle':
        uid = parts[3]
        page = int(parts[4])
        selected = context.user_data.get('broadcast_selected_ids', [])

        if uid in selected:
            selected.remove(uid)
        else:
            selected.append(uid)

        context.user_data['broadcast_selected_ids'] = selected

        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT tg_id, first_name, username FROM user_prefs")
        users = cursor.fetchall()
        conn.close()

        keyboard = get_users_pagination_keyboard(users, selected, page, lang)
        try:
            await query.edit_message_reply_markup(reply_markup=keyboard)
        except Exception:
            pass
        await query.answer()
        return

    if action == 'page':
        page = int(parts[3])
        selected = context.user_data.get('broadcast_selected_ids', [])

        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT tg_id, first_name, username FROM user_prefs")
        users = cursor.fetchall()
        conn.close()

        keyboard = get_users_pagination_keyboard(users, selected, page, lang)
        try:
            await query.edit_message_reply_markup(reply_markup=keyboard)
        except Exception:
            pass
        await query.answer()
        return

    if action == 'confirm':
        selected = context.user_data.get('broadcast_selected_ids', [])
        if not selected:
            await query.answer(t("broadcast_select_error", lang), show_alert=True)
            return

        await query.answer()
        context.user_data['broadcast_users'] = selected
        context.user_data['broadcast_target'] = 'individual'

        await query.edit_message_text(
            t("broadcast_confirm_prompt", lang).format(count=len(selected)),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_cancel", lang), callback_data='admin_panel')]]),
            parse_mode='Markdown'
        )
        context.user_data['admin_action'] = 'awaiting_broadcast'
        return

    # Fallback for all/en/ru
    await query.answer()
    target = action
    context.user_data['broadcast_target'] = target

    target_name = t("btn_broadcast_all", lang)
    if target == 'en':
        target_name = t("btn_broadcast_en", lang)
    if target == 'ru':
        target_name = t("btn_broadcast_ru", lang)

    await query.edit_message_text(
        t("broadcast_general_prompt", lang).format(target=target_name),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_cancel", lang), callback_data='admin_panel')]]),
        parse_mode='Markdown'
    )
    context.user_data['admin_action'] = 'awaiting_broadcast'

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.from_user:
        user = update.message.from_user
        update_user_info(user.id, user.username, user.first_name, user.last_name)

    tg_id = str(update.message.from_user.id)
    lang = get_lang(tg_id)
    document = getattr(update.message, "document", None)
    if tg_id == ADMIN_ID and document is not None:
        file_name = getattr(document, "file_name", None) or "backup.db"
        if str(file_name).lower().endswith(".db"):
            try:
                import re

                backup_dir = "/usr/local/x-ui/bot/backups"
                os.makedirs(backup_dir, exist_ok=True)

                ts = datetime.datetime.now(TIMEZONE).strftime("%Y-%m-%d_%H-%M-%S")
                base_name = os.path.basename(str(file_name))
                safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", base_name)[:120]
                local_path = os.path.join(backup_dir, f"upload_{ts}_{safe_name}")

                file = await context.bot.get_file(document.file_id)
                await file.download_to_drive(custom_path=local_path)

                context.user_data["pending_upload_path"] = local_path
                context.user_data["pending_upload_ts"] = ts
                detected_kind = _detect_sqlite_db_kind(local_path)
                context.user_data["pending_upload_detected_kind"] = detected_kind

                detected_text = t("upload_db_detected_unknown", lang)
                if detected_kind == "xui":
                    detected_text = t("upload_db_detected_xui", lang)
                elif detected_kind == "bot":
                    detected_text = t("upload_db_detected_bot", lang)

                xui_button = InlineKeyboardButton(t("btn_restore_as_xui", lang), callback_data="admin_upload_restore_xui")
                bot_button = InlineKeyboardButton(t("btn_restore_as_bot", lang), callback_data="admin_upload_restore_bot")
                if detected_kind == "bot":
                    first_row = [bot_button]
                    second_row = [xui_button]
                else:
                    first_row = [xui_button]
                    second_row = [bot_button]
                keyboard = [first_row, second_row, [InlineKeyboardButton(t("btn_back_admin", lang), callback_data="admin_panel")]]

                await update.message.reply_text(
                    f"{t('upload_db_received', lang).format(name=_safe_backup_label(local_path))}\n\n{detected_text}",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            except Exception as e:
                await update.message.reply_text(t("upload_restore_failed", lang).format(error=str(e)[:1500]), parse_mode="Markdown")
            return

    text = update.message.text
    action = context.user_data.get('admin_action')

    # Admin actions
    if tg_id == ADMIN_ID:

        # Handle Cancel Button for Rebind
        if text == t("btn_cancel", lang) and action == 'awaiting_rebind_contact':
            context.user_data['admin_action'] = None
            context.user_data['rebind_uid'] = None
            await update.message.reply_text(t("action_cancelled", lang), reply_markup=ReplyKeyboardRemove())
            # Show admin panel again
            await admin_panel(update, context)
            return

        # Handle User Shared (Rebind)
        if action == 'awaiting_rebind_contact' and (update.message.users_shared or update.message.contact):
            uid = context.user_data.get('rebind_uid')
            if not uid:
                 await update.message.reply_text("❌ Ошибка: ID пользователя не найден.", reply_markup=ReplyKeyboardRemove())
                 context.user_data['admin_action'] = None
                 return

            target_tg_id = None
            if update.message.users_shared:
                target_tg_id = str(update.message.users_shared.users[0].user_id)
            elif update.message.contact:
                target_tg_id = str(update.message.contact.user_id)

            if not target_tg_id:
                await update.message.reply_text("❌ Не удалось получить ID пользователя.", reply_markup=ReplyKeyboardRemove())
                return

            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
            row = cursor.fetchone()

            if not row:
                await update.message.reply_text("❌ Входящее соединение не найдено.", reply_markup=ReplyKeyboardRemove())
                conn.close()
                return

            settings = json.loads(row[0])
            clients = settings.get('clients', [])

            found = False
            client_email = ""
            old_email = ""

            for client in clients:
                if client.get('id') == uid:
                    old_email = client.get('email')
                    client['tgId'] = int(target_tg_id) if target_tg_id.isdigit() else target_tg_id
                    client['email'] = f"tg_{target_tg_id}" # Update email to match standard format
                    client['updated_at'] = int(time.time() * 1000)
                    client_email = client.get('email')
                    found = True
                    break

            if found:
                # Need to update client_traffics as well because email changed
                # We rename old email to new email in client_traffics table
                try:
                    if old_email and client_email and old_email != client_email:
                         # Check if record exists for old email
                         conn.execute("UPDATE client_traffics SET email=? WHERE email=?", (client_email, old_email))
                         # Also update traffic_history if we want to preserve history
                         conn_bot = sqlite3.connect(BOT_DB_PATH)
                         conn_bot.execute("UPDATE traffic_history SET email=? WHERE email=?", (client_email, old_email))
                         conn_bot.commit()
                         conn_bot.close()

                         # Force update current traffic from client dict to client_traffics table
                         # Because X-UI might overwrite it with 0 if we just changed email?
                         # Or maybe client dict has the correct current values 'up' and 'down'.
                         current_up = client.get('up', 0)
                         current_down = client.get('down', 0)
                         if current_up > 0 or current_down > 0:
                             conn.execute("UPDATE client_traffics SET up=?, down=? WHERE email=?", (current_up, current_down, client_email))

                except Exception as e:
                     logging.error(f"Error migrating stats: {e}")

                new_settings = json.dumps(settings, indent=2)
                cursor.execute("UPDATE inbounds SET settings=? WHERE id=?", (new_settings, INBOUND_ID))
                conn.commit()
                conn.close()

                # Restart X-UI
                await _systemctl("restart", "x-ui")

                await update.message.reply_text(f"✅ *Успешно!*\nКлиент `{client_email}` перепривязан к Telegram ID `{target_tg_id}`.\n\n🔄 *Внимание:* Для корректного отображения статистики и работы подписки, бот автоматически обновил email клиента на `{client_email}`.\n\nX-UI перезапущен.", parse_mode='Markdown', reply_markup=ReplyKeyboardRemove())

                # Show admin user detail again
                keyboard = [
                    [InlineKeyboardButton("🔄 Перепривязать пользователя", callback_data=f'admin_rebind_{uid}')],
                    [InlineKeyboardButton("🔙 Назад к списку", callback_data='admin_users_0')]
                ]
                await update.message.reply_text(f"👤 Клиент: {client_email}", reply_markup=InlineKeyboardMarkup(keyboard))

                context.user_data['admin_action'] = None
                context.user_data['rebind_uid'] = None
            else:
                conn.close()
                await update.message.reply_text(f"❌ Клиент с UUID `{uid}` не найден.", reply_markup=ReplyKeyboardRemove())
            return

        if action == 'awaiting_promo_data':
            if not text:
                return
            try:
                parts = text.split()
                if len(parts) != 3:
                    raise ValueError
                code, days, limit = parts[0].upper(), int(parts[1]), int(parts[2]) # Force uppercase

                conn = sqlite3.connect(BOT_DB_PATH)
                cursor = conn.cursor()
                cursor.execute("INSERT OR REPLACE INTO promo_codes (code, days, max_uses) VALUES (?, ?, ?)", (code, days, limit))
                conn.commit()
                conn.close()

                await update.message.reply_text(f"✅ Промокод `{code}` создан на {days} дн. ({limit} активаций).")
                # Show menu again
                keyboard = [
                    [InlineKeyboardButton("➕ Создать новый", callback_data='admin_new_promo')],
                    [InlineKeyboardButton("📜 Список активных", callback_data='admin_promo_list')],
                    [InlineKeyboardButton("👥 Использования", callback_data='admin_promo_uses_0')],
                    [InlineKeyboardButton("🔙 Назад", callback_data='admin_panel')]
                ]
                await update.message.reply_text("🎁 *Управление промокодами*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
                context.user_data['admin_action'] = None
            except Exception:
                await update.message.reply_text("❌ Неверный формат. Используйте: `КОД ДНИ ЛИМИТ`")
            return

        if action == "awaiting_remote_panel":
            if not text:
                return
            try:
                parts = [p.strip() for p in text.split("|")]
                if len(parts) < 1:
                    raise ValueError
                name = ""
                base_url = ""
                api_token = None
                if len(parts) == 1:
                    base_url = parts[0]
                elif parts[0].startswith("http"):
                    base_url = parts[0]
                    api_token = parts[1] if len(parts) > 1 and parts[1] else None
                else:
                    name = parts[0]
                    base_url = parts[1] if len(parts) > 1 else ""
                    api_token = parts[2] if len(parts) > 2 and parts[2] else None
                if not base_url:
                    raise ValueError
                if not name or name.lower() in ("-", "auto"):
                    host = _extract_host_from_url(base_url)
                    name = _auto_location_name(host)
                _insert_remote_panel(name, base_url, api_token)
                await update.message.reply_text(t("remote_panel_added", lang))
                context.user_data["admin_action"] = None
                await admin_remote_panels(update, context)
            except Exception:
                await update.message.reply_text(t("error_generic", lang))
            return

        if action == "awaiting_remote_location":
            if not text:
                return
            try:
                parts = [p.strip() for p in text.split("|")]
                if not parts:
                    raise ValueError
                name = ""
                offset = 0
                if len(parts) > 1 and parts[0] and parts[0].lower() not in ("-", "auto") and not _looks_like_host(parts[0]):
                    name = parts[0]
                    offset = 1
                remaining = parts[offset:]
                if not remaining or not remaining[0]:
                    raise ValueError
                host = remaining[0]
                rest = remaining[1:]
                port: Optional[int] = None
                if rest and rest[0]:
                    if rest[0].isdigit():
                        port = int(rest[0])
                        rest = rest[1:]
                if port is None:
                    port = PORT or 443
                public_key = None
                sni = None
                sid = None
                flow = None
                sub_host = None
                sub_port = None
                sub_path = None
                if rest:
                    if len(rest) == 1 or (len(rest) >= 2 and rest[1].isdigit()):
                        sub_host = rest[0] or None
                        if len(rest) > 1 and rest[1]:
                            sub_port = int(rest[1])
                        if len(rest) > 2 and rest[2]:
                            sub_path = rest[2]
                    elif len(rest) >= 3:
                        public_key = rest[0] or None
                        sni = rest[1] or None
                        sid = rest[2] or None
                        flow = rest[3] if len(rest) > 3 and rest[3] else None
                        sub_host = rest[4] if len(rest) > 4 and rest[4] else None
                        sub_port = int(rest[5]) if len(rest) > 5 and rest[5] else None
                        sub_path = rest[6] if len(rest) > 6 and rest[6] else None
                if not name or name.lower() in ("-", "auto"):
                    name = _auto_location_name(host)
                _insert_remote_location(
                    name=name,
                    host=host,
                    port=port,
                    public_key=public_key,
                    sni=sni,
                    sid=sid,
                    flow=flow,
                    sub_host=sub_host,
                    sub_port=sub_port,
                    sub_path=sub_path,
                    panel_id=None,
                )
                await update.message.reply_text(t("remote_location_added", lang))
                context.user_data["admin_action"] = None
                await admin_remote_locations(update, context)
            except Exception:
                await update.message.reply_text(t("error_generic", lang))
            return

        if action == "awaiting_remote_node":
            if not text:
                return
            try:
                parts = [p.strip() for p in text.split("|")]
                if len(parts) < 4:
                    raise ValueError
                name = parts[0]
                host_value = parts[1]
                ssh_port: Optional[int] = None
                ssh_user: Optional[str] = None
                ssh_password: Optional[str] = None
                if len(parts) == 4:
                    ssh_user = parts[2]
                    ssh_password = parts[3]
                else:
                    ssh_port = int(parts[2]) if parts[2] else None
                    ssh_user = parts[3] if len(parts) > 3 else None
                    ssh_password = parts[4] if len(parts) > 4 else None
                host, parsed_port = _split_host_port(host_value)
                if not host:
                    raise ValueError
                if ssh_port is None:
                    ssh_port = parsed_port or 22
                if not ssh_user or not ssh_password:
                    raise ValueError
                if not name or name.lower() in ("-", "auto"):
                    name = _auto_location_name(host)
                _insert_remote_node(name, host, int(ssh_port), ssh_user, ssh_password)
                panel_ready, location_ready = await asyncio.get_running_loop().run_in_executor(
                    None, _sync_remote_node_data, host, int(ssh_port), ssh_user, ssh_password, name
                )
                if panel_ready or location_ready:
                    await update.message.reply_text(t("remote_node_added_auto", lang))
                else:
                    await update.message.reply_text(t("remote_node_added", lang))
                context.user_data["admin_action"] = None
                await admin_remote_nodes(update, context)
            except Exception:
                await update.message.reply_text(t("error_generic", lang))
            return



        elif action == 'awaiting_price_amount':
            try:
                if not text:
                    raise ValueError
                amount = int(text)
                if amount <= 0:
                    raise ValueError

                key = context.user_data.get('edit_price_key')
                if key:
                    update_price(key, amount)
                    await update.message.reply_text(f"✅ Цена обновлена: {amount} ⭐️")
                    # Return to prices menu
                    # We can't edit the previous message easily without query, so send new menu
                    # Or just done.

                    # Let's show the menu again
                    current_prices = get_prices()
                    keyboard = []
                    order = ["1_week", "2_weeks", "1_month", "ru_bridge", "3_months", "6_months", "1_year"]
                    labels = {
                        "1_week": "1 Неделя",
                        "2_weeks": "2 Недели",
                        "1_month": "1 Месяц",
                        "ru_bridge": "RU-Bridge (1 месяц)",
                        "3_months": "3 Месяца",
                        "6_months": "6 Месяцев",
                        "1_year": "1 Год"
                    }
                    for k in order:
                        if k in current_prices:
                            amt = current_prices[k]['amount']
                            keyboard.append([InlineKeyboardButton(f"{labels[k]}: {amt} ⭐️ (Изменить)", callback_data=f'admin_edit_price_{k}')])
                    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='admin_panel')])

                    await update.message.reply_text("💰 **Настройка цен**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

                context.user_data['admin_action'] = None
                context.user_data['edit_price_key'] = None
            except Exception:
                await update.message.reply_text("❌ Ошибка. Введите целое положительное число.")
            return

        elif action == 'awaiting_flash_duration':
            if not text:
                return
            try:
                duration = int(text)
                if duration <= 0:
                    raise ValueError

                flash_code = context.user_data.get('flash_code')
                if not flash_code:
                    await update.message.reply_text("❌ Промокод не найден. Попробуйте еще раз.")
                    return
                code_str = str(flash_code)

                # Start broadcasting
                status_msg = await update.message.reply_text("⏳ Запуск Flash-рассылки (ВСЕМ)...")

                # Fetch all users
                conn = sqlite3.connect(BOT_DB_PATH)
                cursor = conn.cursor()

                # Clear previous flash errors
                cursor.execute("DELETE FROM flash_delivery_errors")
                conn.commit()

                users = []
                # Sync X-UI
                try:
                    conn_xui = sqlite3.connect(DB_PATH)
                    cursor_xui = conn_xui.cursor()
                    cursor_xui.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
                    row = cursor_xui.fetchone()
                    conn_xui.close()
                    if row:
                        settings = json.loads(row[0])
                        clients = settings.get('clients', [])
                        for client in clients:
                            tid = client.get('tgId')
                            if tid:
                                users.append((str(tid),))
                except Exception:
                    pass

                cursor.execute("SELECT tg_id FROM user_prefs")
                bot_users = cursor.fetchall()

                # Merge
                user_ids = set([u[0] for u in users])
                for u in bot_users:
                    if u[0] not in user_ids:
                        users.append(u)
                        user_ids.add(u[0])

                conn.close()

                sent = 0
                blocked = 0
                delete_at = int(time.time()) + (duration * 60)

                # Format end time
                end_time_str = datetime.datetime.fromtimestamp(delete_at, tz=TIMEZONE).strftime("%H:%M")

                # Make code copyable by clicking on it inside spoiler (using monospaced font)
                msg_text = f"🔥 УСПЕЙ ПОЙМАТЬ ПРОМОКОД! 🔥\n\nУспей активировать секретный промокод!\n\n👇 Нажми, чтобы увидеть:\n<tg-spoiler><code>{code_str}</code></tg-spoiler>\n\n⏳ Предложение сгорит в {end_time_str}\n(через {duration} мин)"

                conn = sqlite3.connect(BOT_DB_PATH)
                cursor = conn.cursor()

                for user_row in users:
                    user_id = user_row[0]
                    # Skip sender if needed, but let's send to all for test

                    try:
                        sent_msg = await context.bot.send_message(chat_id=user_id, text=msg_text, parse_mode='HTML')
                        sent += 1

                        # Save for deletion
                        cursor.execute("INSERT INTO flash_messages (chat_id, message_id, delete_at) VALUES (?, ?, ?)",
                                       (str(user_id), sent_msg.message_id, delete_at))

                        await asyncio.sleep(0.05)
                    except Exception as e:
                         if "Forbidden" in str(e) or "blocked" in str(e):
                             blocked += 1

                         # Log delivery error
                         try:
                             cursor.execute("INSERT INTO flash_delivery_errors (user_id, error_message, timestamp) VALUES (?, ?, ?)",
                                            (str(user_id), str(e), int(time.time())))
                         except Exception:
                             pass

                         pass

                conn.commit()
                conn.close()

                result_text = f"✅ Flash-рассылка завершена.\n\n📤 Отправлено: {sent}\n🚫 Не доставлено: {blocked}\n⏱ Время жизни: {duration} мин."
                keyboard = []
                if blocked > 0:
                    keyboard.append([InlineKeyboardButton("📉 Показать недоставленные", callback_data='admin_flash_errors')])

                await status_msg.edit_text(result_text, reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None)

                context.user_data['admin_action'] = None
                context.user_data['flash_code'] = None

            except Exception as e:
                logging.error(f"Flash broadcast error: {e}")
                await update.message.reply_text("❌ Ошибка. Введите число минут.")
            return

        elif action == 'awaiting_broadcast_users_input':
            if not text:
                return
            clean_text = text.replace(',', ' ').strip()
            ids = clean_text.split()
            valid_ids = []
            for uid in ids:
                if uid.isdigit() or (uid.startswith('-') and uid[1:].isdigit()):
                    valid_ids.append(uid)

            if not valid_ids:
                await update.message.reply_text("❌ Не найдено корректных ID. Попробуйте еще раз или нажмите Отмена.")
                return

            context.user_data['broadcast_users'] = valid_ids
            context.user_data['admin_action'] = 'awaiting_broadcast'

            await update.message.reply_text(
                f"✅ Принято {len(valid_ids)} получателей.\n\nТеперь отправьте сообщение (текст, фото, видео, стикер), которое хотите отправить.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data='admin_panel')]])
            )
            return

        elif action == 'awaiting_broadcast':
            # Use copy_message to support all content types (text, photo, video, sticker, etc.)
            msg_id = update.message.message_id
            chat_id_from = update.message.chat_id
            target = context.user_data.get('broadcast_target', 'all')

            conn = sqlite3.connect(BOT_DB_PATH)
            cursor = conn.cursor()

            users = []

            if target == 'all':
                # Sync all active users from X-UI DB first
                try:
                    conn_xui = sqlite3.connect(DB_PATH)
                    cursor_xui = conn_xui.cursor()
                    cursor_xui.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
                    row = cursor_xui.fetchone()
                    conn_xui.close()

                    if row:
                        settings = json.loads(row[0])
                        clients = settings.get('clients', [])
                        for client in clients:
                            tg_id = client.get('tgId')
                            if tg_id:
                                users.append((str(tg_id),))
                except Exception as e:
                     logging.error(f"Error getting X-UI users for broadcast: {e}")

                # Also get users from bot DB who might not be active in X-UI anymore but are in bot
                cursor.execute("SELECT tg_id FROM user_prefs")
                bot_users = cursor.fetchall()

                # Merge lists, unique IDs
                user_ids = set([u[0] for u in users])
                for u in bot_users:
                    if u[0] not in user_ids:
                        users.append(u)
                        user_ids.add(u[0])

            elif target == 'individual':
                user_ids = context.user_data.get('broadcast_users', [])
                users = [(uid,) for uid in user_ids]
            else:
                cursor.execute("SELECT tg_id FROM user_prefs WHERE lang=?", (target,))
                users = cursor.fetchall()

            conn.close()

            sent = 0
            blocked = 0

            target_name = "ВСЕМ"
            if target == 'en':
                target_name = "English (en)"
            if target == 'ru':
                target_name = "Русский (ru)"
            if target == 'individual':
                target_name = f"Индивидуально: {len(users)}"

            status_msg = await update.message.reply_text(f"⏳ Рассылка запущена ({target_name})...")

            for user_row in users:
                user_id = str(user_row[0])
                # Skip sending to self (admin) if desired, or keep it for verification
                if str(user_id) == str(tg_id):
                    # We can skip the sender to avoid double notification, or just let it be
                    pass

                try:
                    await context.bot.copy_message(chat_id=user_id, from_chat_id=chat_id_from, message_id=msg_id)
                    sent += 1
                    await asyncio.sleep(0.05) # Rate limit protection
                except Exception as e:
                    if "Forbidden" in str(e) or "blocked" in str(e):
                        blocked += 1
                    pass

            await status_msg.edit_text(f"✅ Рассылка завершена ({target_name}).\n\n📤 Отправлено: {sent}\n🚫 Не доставлено (бот заблокирован): {blocked}")
            context.user_data['admin_action'] = None
            context.user_data['broadcast_target'] = None
            return

        elif action == 'awaiting_search_user':
            if not text:
                return
            target_id = text.strip()
            # Simple validation
            if not target_id.isdigit():
                await update.message.reply_text("❌ Ошибка: ID должен состоять из цифр.")
                return

            await admin_user_db_detail(update, context, target_id)
            context.user_data['admin_action'] = None
            return

        elif action == 'awaiting_limit_ip':
            if not text:
                return
            uid = context.user_data.get('edit_limit_ip_uid')
            try:
                new_limit = int(text.strip())
                if new_limit < 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text(t("limit_ip_invalid", lang))
                return

            # Update X-UI DB
            try:
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
                row = cursor.fetchone()

                if row:
                    settings = json.loads(row[0])
                    clients = settings.get('clients', [])

                    found = False
                    for client in clients:
                        if client.get('id') == uid:
                            client['limitIp'] = new_limit
                            found = True
                            break

                    if found:
                        new_settings = json.dumps(settings)
                        cursor.execute("UPDATE inbounds SET settings=? WHERE id=?", (new_settings, INBOUND_ID))
                        conn.commit()

                        # Restart X-UI
                        await _systemctl("restart", "x-ui")

                        await update.message.reply_text(t("limit_ip_success", lang).format(limit=new_limit if new_limit > 0 else "Unlimited"))
                    else:
                        await update.message.reply_text(t("msg_client_not_found", lang))
                else:
                    await update.message.reply_text(t("sync_error_inbound", lang))

                conn.close()

            except Exception as e:
                logging.error(f"Error updating limitIp: {e}")
                await update.message.reply_text(t("limit_ip_error", lang))

            context.user_data['admin_action'] = None
            # Return to user detail
            # We can't easily trigger the callback handler from here without mocking, so user has to navigate back manually or we send a link/button
            await admin_user_detail(update, context) # This might fail if update.callback_query is missing, but we can try adapting admin_user_detail or just sending a fresh message
            return

        elif action == 'awaiting_poll_question':
            if not text:
                return
            context.user_data['poll_question'] = text.strip()
            context.user_data['admin_action'] = 'awaiting_poll_options'

            await update.message.reply_text(t("poll_ask_options", lang))
            return

        elif action == 'awaiting_poll_options':
            if not text:
                return
            options = [opt.strip() for opt in text.split('\n') if opt.strip()]

            if len(options) < 2:
                await update.message.reply_text("❌ Ошибка: Должно быть минимум 2 варианта ответа.")
                return

            if len(options) > 10:
                await update.message.reply_text("❌ Ошибка: Максимум 10 вариантов ответа.")
                return

            context.user_data['poll_options'] = options
            question = context.user_data.get('poll_question')
            if not question:
                await update.message.reply_text("❌ Ошибка: Вопрос опроса не найден.")
                context.user_data['admin_action'] = None
                return

            # Preview by sending poll to admin
            await context.bot.send_poll(
                chat_id=tg_id,
                question=question,
                options=options,
                is_anonymous=True,
                allows_multiple_answers=False
            )

            keyboard = [
                [InlineKeyboardButton(t("btn_send_poll", lang), callback_data='admin_poll_send')],
                [InlineKeyboardButton("🔙 Отмена", callback_data='admin_poll_menu')]
            ]

            await update.message.reply_text(
                t("poll_preview", lang).format(question=question, options="\n".join(f"{i+1}. {o}" for i, o in enumerate(options))),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            context.user_data['admin_action'] = None
            return

    # --- Support Logic ---
    if action == 'awaiting_support_message':
        if not text and not update.message.photo:
            return

        # Forward to admin via Support Bot
        user = update.message.from_user
        user_display = f"@{user.username}" if user.username else user.first_name

        text_content = text or "[Photo]"

        alert_text = t("admin_support_alert", "ru").format(user=user_display, id=tg_id, text=text_content)

        # Access Support Bot
        support_bot = context.bot_data.get('support_bot')

        # Logging for debug
        logging.info(f"Support Message from {tg_id}. Bot data keys: {list(context.bot_data.keys())}")

        if support_bot:
            try:
                logging.info(f"Attempting to send support message to ADMIN_ID: {ADMIN_ID} via Support Bot")
                if update.message.photo:
                    await support_bot.send_photo(chat_id=ADMIN_ID, photo=update.message.photo[-1].file_id, caption=alert_text, parse_mode='Markdown')
                else:
                    await support_bot.send_message(chat_id=ADMIN_ID, text=alert_text, parse_mode='Markdown')

                # Send hint to admin
                await support_bot.send_message(chat_id=ADMIN_ID, text=t("admin_reply_hint", "ru"))

                await update.message.reply_text(t("support_sent", lang))
                save_support_ticket(tg_id, text)
            except Exception as e:
                logging.error(f"Failed to forward support message via support bot: {e}")
                # Fallback to main bot if support bot fails (e.g. admin didn't start it)
                try:
                    logging.info("Fallback: Sending via Main Bot")
                    if update.message.photo:
                         await context.bot.send_photo(chat_id=ADMIN_ID, photo=update.message.photo[-1].file_id, caption=alert_text, parse_mode='Markdown')
                    else:
                         await context.bot.send_message(chat_id=ADMIN_ID, text=alert_text, parse_mode='Markdown')
                    await update.message.reply_text(t("support_sent", lang))
                except Exception as ex:
                    logging.error(f"Fallback failed too: {ex}")
                    await update.message.reply_text(t("error_generic", lang))
        else:
             # Fallback to main bot if support bot not linked (should not happen)
             logging.error("Support bot not found in context! Falling back to Main Bot.")
             try:
                 if update.message.photo:
                     await context.bot.send_photo(chat_id=ADMIN_ID, photo=update.message.photo[-1].file_id, caption=alert_text, parse_mode='Markdown')
                 else:
                     await context.bot.send_message(chat_id=ADMIN_ID, text=alert_text, parse_mode='Markdown')
                 await update.message.reply_text(t("support_sent", lang))
             except Exception as ex:
                logging.error(f"Main bot fallback failed: {ex}")
                await update.message.reply_text(t("error_generic", lang))

        context.user_data['admin_action'] = None
        return

    # --- Admin Reply Logic (Legacy/Main Bot fallback) ---
    if tg_id == ADMIN_ID and update.message.reply_to_message:
        # Check if replying to a forwarded message or our alert
        # We need to extract the original user ID from the alert text
        # Alert format: ... User: @name (`123456789`) ...

        reply_text = update.message.reply_to_message.caption or update.message.reply_to_message.text
        if not reply_text:
            return

        import re
        # Look for (`123456789`) pattern
        match = re.search(r'\(`(\d+)`\)', reply_text)
        if match:
            target_user_id = match.group(1)

            try:
                # Send anonymous reply
                target_lang = get_lang(target_user_id)
                reply_body = t("support_reply_template", target_lang).format(text=text)

                await context.bot.send_message(chat_id=target_user_id, text=reply_body, parse_mode='Markdown')
                await update.message.reply_text(t("admin_reply_sent", "ru"))

            except Exception as e:
                await update.message.reply_text(f"❌ Failed to send reply: {e}")
        return

    if action == 'awaiting_search_user':
        if not text:
            return
        tg_id_search = text.strip()

        if not tg_id_search.isdigit():
            await update.message.reply_text(t("search_error_digit", lang))
            return

        context.user_data['admin_action'] = None
        await admin_user_db_detail(update, context, tg_id_search)
        return

    if context.user_data.get('awaiting_promo'):
        if not text:
            return
        tg_id = str(update.message.from_user.id)
        lang = get_lang(tg_id)
        code = text.strip()

        # Check promo with case insensitivity handled by DB
        days, actual_code = check_promo(code, tg_id)

        if days == "USED":
             await update.message.reply_text(t("promo_used", lang))
        elif days is None:
             await update.message.reply_text(t("promo_invalid", lang))
        else:
             username = update.message.from_user.username or update.message.from_user.first_name
             log_action(f"ACTION: User {tg_id} (@{username}) redeemed promo code: {actual_code} ({days} days).")
             redeem_promo_db(actual_code, tg_id)

             await process_subscription(tg_id, days, update, context, lang)

            # Celebration animation
             msg = await update.message.reply_text("🎆")
             await asyncio.sleep(0.5)
             await msg.edit_text("🎆 🎇")
             await asyncio.sleep(0.5)
             await msg.edit_text("🎆 🎇 ✨")
             await asyncio.sleep(0.5)
             # Replace animation with the detailed success message
             await msg.edit_text(t("promo_success", lang).format(days=days), parse_mode='Markdown')

        context.user_data['awaiting_promo'] = False
        return

async def initiate_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    plan_key = query.data.split('_', 1)[1]
    current_prices = get_prices()

    if plan_key not in current_prices:
        return

    plan = current_prices[plan_key]

    chat_id = query.message.chat_id
    title = t("invoice_title_mobile", lang) if str(plan_key).startswith("m_") else t("invoice_title", lang)
    description = t(f"label_{plan_key}", lang)
    payload = plan_key
    currency = "XTR"
    price = plan['amount']
    prices = [LabeledPrice(description, price)]

    await context.bot.send_invoice(
        chat_id=chat_id,
        title=title,
        description=description,
        payload=payload,
        provider_token="",
        currency=currency,
        prices=prices
    )

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    current_prices = get_prices()
    if query.invoice_payload not in current_prices:
        await query.answer(ok=False, error_message="Invalid plan selected.")
    else:
        await query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # CRITICAL: Record payment IMMEDIATELY to prevent loss in case of crash later
    try:
        payment = update.message.successful_payment
        payload = payment.invoice_payload
        tg_id = str(update.message.from_user.id)
        charge_id = _normalize_charge_id(getattr(payment, "telegram_payment_charge_id", None))

        # 1. Immediate DB Insert (Fail-safe)
        inserted_tx_id: Optional[int] = None
        try:
            conn = sqlite3.connect(BOT_DB_PATH)
            cursor = conn.cursor()
            # Determine plan amount safely
            amount = payment.total_amount
            now_ts = int(time.time())
            msg_date = getattr(update.message, "date", None)
            date_ts = int(msg_date.timestamp()) if isinstance(msg_date, datetime.datetime) else now_ts
            if charge_id:
                try:
                    cursor.execute(
                        "SELECT 1 FROM transactions WHERE telegram_payment_charge_id=? LIMIT 1",
                        (charge_id,),
                    )
                    existing = cursor.fetchone()
                    if existing:
                        conn.close()
                        log_action(f"INFO: Duplicate successful_payment ignored for {tg_id} (charge_id: {charge_id})")
                        return
                    cursor.execute(
                        "INSERT OR IGNORE INTO transactions (tg_id, amount, date, plan_id, telegram_payment_charge_id, processed_at) "
                        "VALUES (?, ?, ?, ?, ?, NULL)",
                        (tg_id, amount, date_ts, payload, charge_id),
                    )
                    inserted_tx_id = cursor.lastrowid if cursor.lastrowid else None
                except sqlite3.OperationalError as e:
                    if "no such column" in str(e):
                        cursor.execute(
                            "INSERT INTO transactions (tg_id, amount, date, plan_id) VALUES (?, ?, ?, ?)",
                            (tg_id, amount, date_ts, payload),
                        )
                        inserted_tx_id = cursor.lastrowid if cursor.lastrowid else None
                        charge_id = None
                    else:
                        raise
            else:
                try:
                    cursor.execute(
                        "SELECT 1 FROM transactions "
                        "WHERE tg_id=? AND amount=? AND plan_id=? AND date BETWEEN ? AND ? "
                        "LIMIT 1",
                        (tg_id, amount, payload, date_ts - 600, date_ts + 600),
                    )
                    if cursor.fetchone():
                        conn.close()
                        log_action(f"INFO: Duplicate successful_payment ignored for {tg_id} (no charge_id)")
                        return
                except sqlite3.OperationalError:
                    pass

                try:
                    cursor.execute(
                        "INSERT INTO transactions (tg_id, amount, date, plan_id, processed_at) "
                        "VALUES (?, ?, ?, ?, NULL)",
                        (tg_id, amount, date_ts, payload),
                    )
                except sqlite3.OperationalError:
                    cursor.execute(
                        "INSERT INTO transactions (tg_id, amount, date, plan_id) VALUES (?, ?, ?, ?)",
                        (tg_id, amount, date_ts, payload),
                    )
                inserted_tx_id = cursor.lastrowid if cursor.lastrowid else None
            conn.commit()
            conn.close()
            log_action(f"SUCCESS: Transaction recorded for {tg_id} (Amount: {amount})")
        except Exception as db_e:
            log_action(f"CRITICAL DB ERROR: Failed to save transaction for {tg_id}: {db_e}")
            # Even if DB fails, we try to proceed, but this is bad.

        current_prices = get_prices()
        plan = current_prices.get(payload)

        if not plan:
            log_action(f"ERROR: Plan not found for payload: {payload}. User {tg_id} paid {payment.total_amount}.")
            try:
                await context.bot.send_message(chat_id=ADMIN_ID, text=f"⚠️ ERROR: Unknown Plan Paid!\nUser: {tg_id}\nPayload: {payload}\nAmount: {payment.total_amount}")
            except Exception:
                pass
            # Try to recover based on amount if possible, or return
            # But we already saved tx, so admin can check.
            return

        lang = get_lang(tg_id)
        days_to_add = plan['days']

        log_action(f"ACTION: User {tg_id} (@{update.message.from_user.username}) purchased subscription: {payload} ({plan['amount']} XTR).")

        # Celebration animation for Payment
        msg = await update.message.reply_text("🎆")
        await asyncio.sleep(1.0)
        await msg.edit_text("🎆 🎇")
        await asyncio.sleep(0.75)
        await msg.edit_text("🎆 🎇 ✨")
        await asyncio.sleep(0.5)
        await msg.edit_text("🎉 ОПЛАТА ПРОШЛА УСПЕШНО! 🎉")

        # Notify Admin
        try:
            admin_lang = get_lang(ADMIN_ID)
            buyer_username = update.message.from_user.username or "NoUsername"
            plan_name = t(f"plan_{payload}", admin_lang)
            now_ms = int(time.time() * 1000)
            old_expiry_ms = _get_mobile_subscription_expiry_ms(tg_id) if str(payload).startswith("m_") else _get_user_client_expiry_ms(tg_id)
            ms_to_add = days_to_add * 24 * 60 * 60 * 1000

            if admin_lang == "ru":
                title = "💰 *Оплата подписки*"
                type_new = "Новая подписка"
                type_renew = "Продление"
                type_reactivate = "Возобновление"
                label_type = "🧾 Тип"
                label_user = "👤 Пользователь"
                label_plan = "💳 Тариф"
                label_amount = "💸 Сумма"
                label_added = "➕ Добавлено"
                unit_days = "дн."
                label_before = "⏳ Было"
                label_after = "✅ Стало"
                label_charge = "🧷 Charge"
                fallback_expiry = "—"
            else:
                title = "💰 *Subscription payment*"
                type_new = "New subscription"
                type_renew = "Renewal"
                type_reactivate = "Reactivation"
                label_type = "🧾 Type"
                label_user = "👤 User"
                label_plan = "💳 Plan"
                label_amount = "💸 Amount"
                label_added = "➕ Added"
                unit_days = "days"
                label_before = "⏳ Before"
                label_after = "✅ After"
                label_charge = "🧷 Charge"
                fallback_expiry = "—"

            if old_expiry_ms is None:
                sale_type = type_new
                new_expiry_ms = now_ms + ms_to_add
            elif old_expiry_ms == 0:
                sale_type = type_renew
                new_expiry_ms = 0
            elif old_expiry_ms < now_ms:
                sale_type = type_reactivate
                new_expiry_ms = now_ms + ms_to_add
            else:
                sale_type = type_renew
                new_expiry_ms = old_expiry_ms + ms_to_add

            old_expiry_disp = fallback_expiry if old_expiry_ms is None else format_expiry_display(old_expiry_ms, admin_lang, now_ms=now_ms)
            new_expiry_disp = format_expiry_display(new_expiry_ms, admin_lang, now_ms=now_ms)
            safe_buyer_username = _escape_markdown(buyer_username)
            safe_plan_name = _escape_markdown(plan_name)
            safe_old_expiry = _escape_markdown(old_expiry_disp)
            safe_new_expiry = _escape_markdown(new_expiry_disp)

            admin_msg = (
                f"{title}\n\n"
                f"{label_type}: *{sale_type}*\n"
                f"{label_user}: @{safe_buyer_username} (`{tg_id}`)\n"
                f"{label_plan}: {safe_plan_name}\n"
                f"{label_amount}: {payment.total_amount} Stars\n"
                f"{label_added}: {days_to_add} {unit_days}\n"
                f"{label_before}: {safe_old_expiry}\n"
                f"{label_after}: {safe_new_expiry}"
            )
            if charge_id:
                admin_msg += f"\n{label_charge}: `{charge_id}`"

            # Send via Support Bot first, then fallback to Main Bot
            support_bot = context.bot_data.get('support_bot') if isinstance(context.bot_data, dict) else None
            sent = False
            if support_bot:
                try:
                    await support_bot.send_message(chat_id=ADMIN_ID, text=admin_msg, parse_mode='Markdown')
                    sent = True
                except Exception as e:
                    logging.error(f"Failed to send sales notification via support bot: {e}")

            if not sent:
                await context.bot.send_message(chat_id=ADMIN_ID, text=admin_msg, parse_mode='Markdown')

        except Exception as e:
            logging.error(f"Failed to notify admin: {e}")

        processed_ok = False
        if payload == "ru_bridge":
            processed_ok = await process_ru_bridge_subscription(tg_id, days_to_add, update, context, lang)
        elif str(payload).startswith("m_"):
            processed_ok = await process_mobile_subscription(tg_id, days_to_add, update, context, lang)
        else:
            processed_ok = await process_subscription(tg_id, days_to_add, update, context, lang)
        if processed_ok and charge_id:
            try:
                conn = sqlite3.connect(BOT_DB_PATH)
                conn.execute(
                    "UPDATE transactions SET processed_at=? "
                    "WHERE telegram_payment_charge_id=? AND processed_at IS NULL",
                    (int(time.time()), charge_id),
                )
                conn.commit()
                conn.close()
            except Exception as e:
                logging.error(f"Failed to mark transaction as processed (charge_id: {charge_id}): {e}")
        if processed_ok and inserted_tx_id and not charge_id:
            try:
                conn = sqlite3.connect(BOT_DB_PATH)
                conn.execute(
                    "UPDATE transactions SET processed_at=? WHERE id=? AND processed_at IS NULL",
                    (int(time.time()), inserted_tx_id),
                )
                conn.commit()
                conn.close()
            except Exception as e:
                logging.error(f"Failed to mark transaction as processed (id: {inserted_tx_id}): {e}")

        # Check Referral Bonus (7 days for referrer)
        try:
            conn = sqlite3.connect(BOT_DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT referrer_id FROM user_prefs WHERE tg_id=?", (tg_id,))
            row = cursor.fetchone()
            conn.close()

            if row and row[0]:
                referrer_id = row[0]
                # Grant bonus days to referrer
                await add_days_to_user(referrer_id, REF_BONUS_DAYS, context)

                # Notify referrer
                ref_lang = get_lang(referrer_id)
                msg_text = f"🎉 **Referral Bonus!**\n\nUser you invited has purchased a subscription.\nYou received +{REF_BONUS_DAYS} days!"
                if ref_lang == 'ru':
                    msg_text = f"🎉 **Реферальный бонус!**\n\nПриглашенный вами пользователь купил подписку.\nВам начислено +{REF_BONUS_DAYS} дней!"

                try:
                    await context.bot.send_message(chat_id=referrer_id, text=msg_text, parse_mode='Markdown')
                except Exception:
                    pass # User might have blocked bot

            # 10% Cashback Logic
            cashback_amount = int(payment.total_amount * 0.10)
            if cashback_amount > 0 and row and row[0]:
                referrer_id = row[0]
                conn = sqlite3.connect(BOT_DB_PATH)
                cursor = conn.cursor()
                cursor.execute("UPDATE user_prefs SET balance = balance + ? WHERE tg_id=?", (cashback_amount, referrer_id))
                cursor.execute("INSERT INTO referral_bonuses (referrer_id, referred_id, amount, type, date) VALUES (?, ?, ?, 'cashback', ?)",
                               (referrer_id, tg_id, cashback_amount, int(time.time())))
                conn.commit()
                conn.close()

                # Notify referrer about cashback
                cb_lang = get_lang(referrer_id)
                cb_text = f"💰 **Cashback!**\n\n+ {cashback_amount} Stars (10%) from referral purchase!"
                if cb_lang == 'ru':
                    cb_text = f"💰 **Кэшбэк!**\n\n+ {cashback_amount} Stars (10%) от покупки реферала!"

                try:
                    await context.bot.send_message(chat_id=referrer_id, text=cb_text, parse_mode='Markdown')
                except Exception:
                    pass

        except Exception as e:
            logging.error(f"Error checking referral bonus: {e}")

    except Exception as e:
        log_action(f"CRITICAL ERROR in successful_payment: {e}")
        _record_payment_error()
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=f"⚠️ CRITICAL PAYMENT ERROR: {e}")
        except Exception:
            pass

async def add_days_to_user(tg_id, days_to_add, context):
    # Simplified version of process_subscription for background tasks
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        return

    settings = json.loads(row[0])
    clients = settings.get('clients', [])

    user_client = None
    client_index = -1

    for idx, client in enumerate(clients):
        if str(client.get('tgId')) == str(tg_id) or client.get('email') == f"tg_{tg_id}":
            user_client = client
            client_index = idx
            break

    current_time_ms = int(time.time() * 1000)
    ms_to_add = days_to_add * 24 * 60 * 60 * 1000

    if user_client:
        current_expiry = user_client.get('expiryTime', 0)

        if current_expiry == 0:
            new_expiry = 0
        elif current_expiry < current_time_ms:
            new_expiry = current_time_ms + ms_to_add
        else:
            new_expiry = current_expiry + ms_to_add

        user_client['expiryTime'] = new_expiry
        user_client['enable'] = True
        user_client['updated_at'] = current_time_ms
        clients[client_index] = user_client
        email = user_client.get('email') or f"tg_{tg_id}"
        if email:
            try:
                cursor.execute("UPDATE client_traffics SET expiry_time=?, enable=1 WHERE email=?", (new_expiry, email))
                if cursor.rowcount == 0:
                    cursor.execute("""
                        INSERT INTO client_traffics (inbound_id, enable, email, up, down, expiry_time, total, reset, all_time, last_online)
                        VALUES (?, ?, ?, 0, 0, ?, 0, 0, 0, 0)
                    """, (INBOUND_ID, 1, email, new_expiry))
            except Exception as e:
                logging.error(f"Error updating client_traffics in add_days_to_user: {e}")
    else:
        # Create new if not exists (rare for referral bonus but possible)
        u_uuid = str(uuid.uuid4())
        new_expiry = current_time_ms + ms_to_add
        new_client = {
            "id": u_uuid,
            "email": f"tg_{tg_id}",
            "limitIp": 0,
            "totalGB": 0,
            "expiryTime": new_expiry,
            "enable": True,
            "tgId": int(tg_id) if tg_id.isdigit() else tg_id,
            "subId": str(uuid.uuid4()).replace('-', '')[:16],
            "flow": "xtls-rprx-vision",
            "created_at": current_time_ms,
            "updated_at": current_time_ms,
            "comment": "Referral Bonus",
            "reset": 0
        }
        clients.append(new_client)

        # Also add to client_traffics
        cursor.execute("""
            INSERT INTO client_traffics (inbound_id, enable, email, up, down, expiry_time, total, reset, all_time, last_online)
            VALUES (?, ?, ?, 0, 0, ?, 0, 0, 0, 0)
        """, (INBOUND_ID, 1, f"tg_{tg_id}", new_expiry))

    # Stop X-UI to prevent overwrite
    await _systemctl("stop", "x-ui")

    cursor.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(settings), INBOUND_ID))
    conn.commit()
    conn.close()

    await _systemctl("start", "x-ui")

def _fetch_ru_bridge_subscription(tg_id: str) -> Optional[dict[str, Any]]:
    try:
        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT uuid, sub_id, expiry_time FROM ru_bridge_subscriptions WHERE tg_id=?",
            (tg_id,),
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return {
            "tg_id": tg_id,
            "uuid": row[0],
            "sub_id": row[1],
            "expiry_time": row[2],
        }
    except Exception as e:
        logging.error(f"Failed to fetch RU-Bridge subscription: {e}")
        return None

def _ru_bridge_email(tg_id: str) -> str:
    return f"ru_bridge_{tg_id}"

def _upsert_ru_bridge_subscription(tg_id: str, days_to_add: int) -> Optional[dict[str, Any]]:
    try:
        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT uuid, sub_id, expiry_time FROM ru_bridge_subscriptions WHERE tg_id=?",
            (tg_id,),
        )
        row = cursor.fetchone()
        current_time_ms = int(time.time() * 1000)
        ms_to_add = days_to_add * 24 * 60 * 60 * 1000
        if row:
            user_uuid, sub_id, current_expiry = row
            current_expiry = int(current_expiry or 0)
            if current_expiry == 0:
                new_expiry = 0
            elif current_expiry < current_time_ms:
                new_expiry = current_time_ms + ms_to_add
            else:
                new_expiry = current_expiry + ms_to_add
            cursor.execute(
                "UPDATE ru_bridge_subscriptions SET expiry_time=?, updated_at=? WHERE tg_id=?",
                (new_expiry, current_time_ms, tg_id),
            )
            created = False
        else:
            user_uuid = str(uuid.uuid4())
            sub_id = str(uuid.uuid4()).replace("-", "")[:16]
            new_expiry = current_time_ms + ms_to_add
            cursor.execute(
                "INSERT INTO ru_bridge_subscriptions (tg_id, uuid, sub_id, expiry_time, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (tg_id, user_uuid, sub_id, new_expiry, current_time_ms, current_time_ms),
            )
            created = True
        conn.commit()
        conn.close()
        return {
            "tg_id": tg_id,
            "uuid": user_uuid,
            "sub_id": sub_id,
            "expiry_time": new_expiry,
            "created": created,
        }
    except Exception as e:
        logging.error(f"Failed to upsert RU-Bridge subscription: {e}")
        return None

def get_mobile_trial_data(tg_id: str) -> dict[str, Any]:
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT mobile_trial_used, mobile_trial_activated_at FROM user_prefs WHERE tg_id=?",
            (str(tg_id),),
        )
        row = cursor.fetchone()
        if row:
            return {"mobile_trial_used": int(row[0] or 0), "mobile_trial_activated_at": row[1]}
        return {"mobile_trial_used": 0, "mobile_trial_activated_at": None}
    finally:
        conn.close()

def mark_mobile_trial_used(tg_id: str) -> None:
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    try:
        current_time = int(time.time())
        cursor.execute(
            """
            INSERT INTO user_prefs (tg_id, mobile_trial_used, mobile_trial_activated_at)
            VALUES (?, 1, ?)
            ON CONFLICT(tg_id) DO UPDATE SET mobile_trial_used=1, mobile_trial_activated_at=?
            """,
            (str(tg_id), current_time, current_time),
        )
        conn.commit()
    finally:
        conn.close()

def _fetch_mobile_subscription(tg_id: str) -> Optional[dict[str, Any]]:
    try:
        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT uuid, sub_id, expiry_time FROM mobile_subscriptions WHERE tg_id=?",
            (tg_id,),
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return {
            "tg_id": tg_id,
            "uuid": row[0],
            "sub_id": row[1],
            "expiry_time": row[2],
        }
    except Exception as e:
        logging.error(f"Failed to fetch mobile subscription: {e}")
        return None


def _get_mobile_subscription_expiry_ms(tg_id: str) -> Optional[int]:
    sub = _fetch_mobile_subscription(tg_id)
    if not sub:
        return None
    try:
        return int(sub.get("expiry_time") or 0)
    except Exception:
        return None


def _mobile_email(tg_id: str) -> str:
    return f"mobile_{tg_id}"

def _upsert_mobile_subscription(tg_id: str, days_to_add: int) -> Optional[dict[str, Any]]:
    try:
        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT uuid, sub_id, expiry_time FROM mobile_subscriptions WHERE tg_id=?",
            (tg_id,),
        )
        row = cursor.fetchone()
        current_time_ms = int(time.time() * 1000)
        ms_to_add = days_to_add * 24 * 60 * 60 * 1000
        if row:
            user_uuid, sub_id, current_expiry = row
            current_expiry = int(current_expiry or 0)
            if current_expiry == 0:
                new_expiry = 0
            elif current_expiry < current_time_ms:
                new_expiry = current_time_ms + ms_to_add
            else:
                new_expiry = current_expiry + ms_to_add
            cursor.execute(
                "UPDATE mobile_subscriptions SET expiry_time=?, updated_at=? WHERE tg_id=?",
                (new_expiry, current_time_ms, tg_id),
            )
            created = False
        else:
            user_uuid = str(uuid.uuid4())
            sub_id = str(uuid.uuid4()).replace("-", "")[:16]
            new_expiry = current_time_ms + ms_to_add
            cursor.execute(
                "INSERT INTO mobile_subscriptions (tg_id, uuid, sub_id, expiry_time, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (tg_id, user_uuid, sub_id, new_expiry, current_time_ms, current_time_ms),
            )
            created = True
        conn.commit()
        conn.close()
        return {
            "tg_id": tg_id,
            "uuid": user_uuid,
            "sub_id": sub_id,
            "expiry_time": new_expiry,
            "created": created,
        }
    except Exception as e:
        logging.error(f"Failed to upsert mobile subscription: {e}")
        return None

async def _sync_ru_bridge_inbound_client(tg_id: str, user_uuid: str, sub_id: str, expiry_ms: int) -> bool:
    inbound_id = _resolve_ru_bridge_inbound_id()
    if inbound_id is None:
        return False
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT settings FROM inbounds WHERE id=?", (inbound_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return False
        settings_raw = row[0] or "{}"
        try:
            settings = json.loads(settings_raw)
        except Exception:
            settings = {}
        clients = settings.get("clients", [])
        email = _ru_bridge_email(tg_id)
        user_client = None
        client_index = -1
        for idx, client in enumerate(clients):
            if str(client.get("tgId")) == str(tg_id) or client.get("email") == email:
                user_client = client
                client_index = idx
                break
        current_time_ms = int(time.time() * 1000)
        flow_value = RU_BRIDGE_FLOW or ""
        if user_client:
            user_client["id"] = user_uuid
            user_client["email"] = email
            user_client["expiryTime"] = expiry_ms
            user_client["enable"] = True
            user_client["subId"] = sub_id
            user_client["tgId"] = int(tg_id) if tg_id.isdigit() else tg_id
            if flow_value and not user_client.get("flow"):
                user_client["flow"] = flow_value
            user_client["updated_at"] = current_time_ms
            if not user_client.get("created_at"):
                user_client["created_at"] = current_time_ms
            clients[client_index] = user_client
        else:
            new_client = {
                "id": user_uuid,
                "email": email,
                "limitIp": 0,
                "totalGB": 0,
                "expiryTime": expiry_ms,
                "enable": True,
                "tgId": int(tg_id) if tg_id.isdigit() else tg_id,
                "subId": sub_id,
                "created_at": current_time_ms,
                "updated_at": current_time_ms,
                "comment": "RU-Bridge",
                "reset": 0,
            }
            if flow_value:
                new_client["flow"] = flow_value
            clients.append(new_client)
        settings["clients"] = clients
        if email:
            try:
                cursor.execute(
                    "UPDATE client_traffics SET expiry_time=?, enable=1 WHERE inbound_id=? AND email=?",
                    (expiry_ms, inbound_id, email),
                )
                if cursor.rowcount == 0:
                    cursor.execute(
                        "INSERT INTO client_traffics (inbound_id, enable, email, up, down, expiry_time, total, reset, all_time, last_online) "
                        "VALUES (?, ?, ?, 0, 0, ?, 0, 0, 0, 0)",
                        (inbound_id, 1, email, expiry_ms),
                    )
            except Exception as e:
                logging.error(f"Error updating client_traffics for RU-Bridge: {e}")
        await _systemctl("stop", XUI_SYSTEMD_SERVICE)
        cursor.execute(
            "UPDATE inbounds SET settings=? WHERE id=?",
            (json.dumps(settings), inbound_id),
        )
        conn.commit()
        conn.close()
        await _systemctl("start", XUI_SYSTEMD_SERVICE)
        return True
    except Exception as e:
        logging.error(f"Failed to sync RU-Bridge inbound client: {e}")
        return False

async def _add_days_ru_bridge(tg_id: str, days_to_add: int) -> Optional[int]:
    data = _upsert_ru_bridge_subscription(tg_id, days_to_add)
    if not data:
        return None
    synced = await _sync_ru_bridge_inbound_client(
        tg_id=tg_id,
        user_uuid=str(data["uuid"]),
        sub_id=str(data["sub_id"]),
        expiry_ms=int(data["expiry_time"]),
    )
    if not synced:
        return None
    return int(data["expiry_time"])

async def process_ru_bridge_subscription(tg_id, days_to_add, update, context, lang, is_callback=False) -> bool:
    if not _ru_bridge_location():
        try:
            if is_callback:
                await update.callback_query.edit_message_text(
                    t("ru_bridge_not_configured", lang),
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]]),
                )
            else:
                await update.message.reply_text(
                    t("ru_bridge_not_configured", lang),
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]]),
                )
        except Exception:
            pass
        return False
    if _resolve_ru_bridge_inbound_id() is None:
        try:
            if is_callback:
                await update.callback_query.edit_message_text(
                    t("ru_bridge_not_configured", lang),
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]]),
                )
            else:
                await update.message.reply_text(
                    t("ru_bridge_not_configured", lang),
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]]),
                )
        except Exception:
            pass
        return False
    try:
        data = _upsert_ru_bridge_subscription(tg_id, days_to_add)
        if not data:
            raise RuntimeError("ru_bridge_db_failed")
        synced = await _sync_ru_bridge_inbound_client(
            tg_id=tg_id,
            user_uuid=str(data["uuid"]),
            sub_id=str(data["sub_id"]),
            expiry_ms=int(data["expiry_time"]),
        )
        if not synced:
            raise RuntimeError("ru_bridge_inbound_failed")
        msg_key = "ru_bridge_success_extended" if days_to_add > 0 else "ru_bridge_success_updated"
        if data.get("created"):
            msg_key = "ru_bridge_success_created"
        expiry_date = format_expiry_display(int(data["expiry_time"]), lang)
        text = t(msg_key, lang).format(expiry=expiry_date)
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(t("btn_ru_bridge", lang), callback_data='ru_bridge_config')],
            [InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')],
        ])
        if is_callback:
            try:
                await update.callback_query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)
            except Exception:
                await update.callback_query.message.delete()
                await context.bot.send_message(chat_id=tg_id, text=text, parse_mode='Markdown', reply_markup=reply_markup)
        else:
            await update.message.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)
        return True
    except Exception as e:
        logging.error(f"Error processing RU-Bridge subscription: {e}")
        if is_callback:
            try:
                await update.callback_query.edit_message_text(t("error_generic", lang))
            except Exception:
                pass
        else:
            await update.message.reply_text(t("error_generic", lang))
        return False


async def process_mobile_subscription(tg_id, days_to_add, update, context, lang, is_callback=False) -> bool:
    if not _mobile_feature_enabled():
        try:
            if is_callback:
                await update.callback_query.edit_message_text(
                    _mobile_not_configured_text(str(tg_id), lang),
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data="back_to_main")]]),
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(
                    _mobile_not_configured_text(str(tg_id), lang),
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data="back_to_main")]]),
                    parse_mode="Markdown",
                )
        except Exception:
            pass
        return False
    try:
        data = _upsert_mobile_subscription(str(tg_id), int(days_to_add))
        if not data:
            raise RuntimeError("mobile_db_failed")
        user_nick = ""
        user = None
        if update.callback_query:
            user = update.callback_query.from_user
        elif update.message:
            user = update.message.from_user
        if user:
            if user.username:
                user_nick = f"@{user.username}"
            elif user.first_name:
                user_nick = user.first_name
                if user.last_name:
                    user_nick += f" {user.last_name}"
        if not user_nick:
            user_nick = f"tg_{tg_id}"
        synced = await _sync_mobile_inbound_client(
            tg_id=str(tg_id),
            user_uuid=str(data["uuid"]),
            sub_id=str(data["sub_id"]),
            expiry_ms=int(data["expiry_time"]),
            comment=user_nick,
        )
        if not synced:
            raise RuntimeError("mobile_inbound_failed")
        msg_key = "mobile_success_extended" if int(days_to_add) > 0 else "mobile_success_updated"
        if data.get("created"):
            msg_key = "mobile_success_created"
        expiry_date = format_expiry_display(int(data["expiry_time"]), lang)
        text = t(msg_key, lang).format(expiry=expiry_date)
        reply_markup = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(t("btn_mobile_config", lang), callback_data="mobile_config")],
                [InlineKeyboardButton(t("btn_back", lang), callback_data="back_to_main")],
            ]
        )
        if is_callback:
            try:
                await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)
            except Exception:
                await update.callback_query.message.delete()
                await context.bot.send_message(chat_id=tg_id, text=text, parse_mode="Markdown", reply_markup=reply_markup)
        else:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)
        return True
    except Exception as e:
        logging.error(f"Error processing mobile subscription: {e}")
        if is_callback:
            try:
                await update.callback_query.edit_message_text(t("error_generic", lang))
            except Exception:
                pass
        else:
            await update.message.reply_text(t("error_generic", lang))
        return False

async def ru_bridge_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    if tg_id != ADMIN_ID:
        return
    lang = get_lang(tg_id)
    if not _ru_bridge_location():
        try:
            await query.edit_message_text(
                t("ru_bridge_not_configured", lang),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]]),
            )
        except Exception:
            await query.message.delete()
            await context.bot.send_message(
                chat_id=tg_id,
                text=t("ru_bridge_not_configured", lang),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]]),
            )
        return
    sub = _fetch_ru_bridge_subscription(tg_id)
    if not sub:
        try:
            await query.edit_message_text(
                t("ru_bridge_sub_not_found", lang),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(t("btn_buy", lang), callback_data='shop')],
                    [InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')],
                ]),
                parse_mode='Markdown',
            )
        except Exception:
            await query.message.delete()
            await context.bot.send_message(
                chat_id=tg_id,
                text=t("ru_bridge_sub_not_found", lang),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(t("btn_buy", lang), callback_data='shop')],
                    [InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')],
                ]),
                parse_mode='Markdown',
            )
        return
    expiry_ms = int(sub.get("expiry_time") or 0)
    current_ms = int(time.time() * 1000)
    if expiry_ms > 0 and expiry_ms < current_ms:
        try:
            await query.edit_message_text(
                t("ru_bridge_sub_expired", lang),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(t("btn_buy", lang), callback_data='shop')],
                    [InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')],
                ]),
                parse_mode='Markdown',
            )
        except Exception:
            await query.message.delete()
            await context.bot.send_message(
                chat_id=tg_id,
                text=t("ru_bridge_sub_expired", lang),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(t("btn_buy", lang), callback_data='shop')],
                    [InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')],
                ]),
                parse_mode='Markdown',
            )
        return
    user_uuid = str(sub.get("uuid") or "")
    if not user_uuid:
        await query.edit_message_text(t("error_generic", lang))
        return
    sub_id = str(sub.get("sub_id") or "").strip()
    sub_link = _build_ru_bridge_sub_link(sub_id)
    sub_block = t("ru_bridge_sub_block", lang).format(sub=html.escape(sub_link)) if sub_link else t("ru_bridge_sub_empty", lang)
    expiry_str = format_expiry_display(expiry_ms, lang)
    msg_text = t("ru_bridge_sub_active", lang).format(
        expiry=expiry_str,
        sub_block=sub_block,
    )
    try:
        await query.edit_message_text(
            msg_text,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(t("btn_instructions", lang), callback_data='instructions')],
                [InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')],
            ]),
        )
    except Exception:
        await query.message.delete()
        await context.bot.send_message(
            chat_id=tg_id,
            text=msg_text,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(t("btn_instructions", lang), callback_data='instructions')],
                [InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')],
            ]),
        )


def _ssh_fetch_remote_client_traffic(
    host: str,
    port: int,
    username: str,
    password: str,
    email: str,
) -> Optional[dict[str, Any]]:
    client = None
    try:
        email_b64 = base64.b64encode(email.encode("utf-8")).decode("utf-8")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=6,
            banner_timeout=6,
            auth_timeout=6,
            look_for_keys=False,
            allow_agent=False,
        )
        cmd = (
            "python3 - <<'PY'\n"
            "import base64, json, sqlite3, os\n"
            "db=os.getenv('XUI_DB_PATH','/etc/x-ui/x-ui.db')\n"
            "email=base64.b64decode('" + email_b64 + "').decode('utf-8', errors='ignore')\n"
            "conn=sqlite3.connect(db)\n"
            "cur=conn.cursor()\n"
            "cur.execute('SELECT up, down, expiry_time, last_online FROM client_traffics WHERE email=? LIMIT 1', (email,))\n"
            "row=cur.fetchone()\n"
            "conn.close()\n"
            "if row:\n"
            "    up, down, expiry_time, last_online = row\n"
            "    print(json.dumps({'ok': True, 'up': up or 0, 'down': down or 0, 'expiry_time': expiry_time or 0, 'last_online': last_online or 0}))\n"
            "else:\n"
            "    print(json.dumps({'ok': False}))\n"
            "PY"
        )
        _, stdout, stderr = client.exec_command(cmd, timeout=12)
        output = stdout.read().decode("utf-8", errors="ignore").strip()
        error = stderr.read().decode("utf-8", errors="ignore").strip()
        if not output:
            if error:
                logging.warning(f"SSH traffic fetch error for {host}:{port}: {error}")
            return None
        try:
            data = json.loads(output)
        except Exception:
            logging.warning(f"SSH traffic fetch invalid json for {host}:{port}: {output}")
            return None
        if not isinstance(data, dict):
            return None
        if error:
            logging.warning(f"SSH traffic fetch stderr for {host}:{port}: {error}")
        return data
    except Exception as exc:
        logging.warning(f"SSH traffic fetch exception for {host}:{port}: {exc}")
        return None
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


async def _fetch_mobile_remote_xui_data() -> Optional[dict[str, Any]]:
    if not _mobile_feature_enabled():
        return None
    return await asyncio.get_running_loop().run_in_executor(
        None,
        _ssh_fetch_remote_xui_data,
        MOBILE_SSH_HOST,
        MOBILE_SSH_PORT,
        MOBILE_SSH_USER,
        MOBILE_SSH_PASSWORD,
    )


async def _fetch_mobile_remote_client_traffic(email: str) -> Optional[dict[str, Any]]:
    if not _mobile_feature_enabled():
        return None
    return await asyncio.get_running_loop().run_in_executor(
        None,
        _ssh_fetch_remote_client_traffic,
        MOBILE_SSH_HOST,
        MOBILE_SSH_PORT,
        MOBILE_SSH_USER,
        MOBILE_SSH_PASSWORD,
        email,
    )


async def mobile_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    if not _mobile_feature_enabled():
        try:
            await query.edit_message_text(
                _mobile_not_configured_text(tg_id, lang),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data="back_to_main")]]),
                parse_mode="Markdown",
            )
        except Exception:
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=tg_id,
                text=_mobile_not_configured_text(tg_id, lang),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data="back_to_main")]]),
                parse_mode="Markdown",
            )
        return

    sub = _fetch_mobile_subscription(tg_id)
    if not sub:
        try:
            await query.edit_message_text(
                t("mobile_sub_not_found", lang),
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton(t("btn_mobile_buy", lang), callback_data="mobile_shop")],
                        [InlineKeyboardButton(t("btn_back", lang), callback_data="mobile_menu")],
                    ]
                ),
                parse_mode="Markdown",
            )
        except Exception:
            await query.message.delete()
            await context.bot.send_message(
                chat_id=tg_id,
                text=t("mobile_sub_not_found", lang),
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton(t("btn_mobile_buy", lang), callback_data="mobile_shop")],
                        [InlineKeyboardButton(t("btn_back", lang), callback_data="mobile_menu")],
                    ]
                ),
                parse_mode="Markdown",
            )
        return

    expiry_ms = int(sub.get("expiry_time") or 0)
    current_ms = int(time.time() * 1000)
    if expiry_ms > 0 and expiry_ms < current_ms:
        try:
            await query.edit_message_text(
                t("mobile_sub_expired", lang),
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton(t("btn_mobile_buy", lang), callback_data="mobile_shop")],
                        [InlineKeyboardButton(t("btn_back", lang), callback_data="mobile_menu")],
                    ]
                ),
                parse_mode="Markdown",
            )
        except Exception:
            await query.message.delete()
            await context.bot.send_message(
                chat_id=tg_id,
                text=t("mobile_sub_expired", lang),
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton(t("btn_mobile_buy", lang), callback_data="mobile_shop")],
                        [InlineKeyboardButton(t("btn_back", lang), callback_data="mobile_menu")],
                    ]
                ),
                parse_mode="Markdown",
            )
        return

    sub_token = str(sub.get("sub_id") or "").strip()
    if not sub_token:
        await query.edit_message_text(t("error_generic", lang))
        return

    remote = await _fetch_mobile_remote_xui_data()
    if not remote:
        await query.edit_message_text(t("error_generic", lang))
        return

    sub_link = _build_remote_xui_sub_link(MOBILE_SSH_HOST, sub_token, remote)
    if not sub_link:
        await query.edit_message_text(t("error_generic", lang))
        return

    expiry_str = format_expiry_display(expiry_ms, lang)
    msg_text = t("mobile_sub_active_html", lang).format(expiry=expiry_str)
    msg_text += f"\n\n<code>{html.escape(sub_link)}</code>"

    try:
        await query.edit_message_text(
            msg_text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton(t("btn_qrcode", lang), callback_data="mobile_show_qrcode")],
                    [InlineKeyboardButton(t("btn_back", lang), callback_data="mobile_menu")],
                ]
            ),
        )
    except Exception:
        await query.message.delete()
        await context.bot.send_message(
            chat_id=tg_id,
            text=msg_text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton(t("btn_qrcode", lang), callback_data="mobile_show_qrcode")],
                    [InlineKeyboardButton(t("btn_back", lang), callback_data="mobile_menu")],
                ]
            ),
        )


async def show_mobile_qrcode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    if not _mobile_feature_enabled():
        await mobile_menu(update, context)
        return

    sub = _fetch_mobile_subscription(tg_id)
    if not sub:
        await mobile_config(update, context)
        return

    expiry_ms = int(sub.get("expiry_time") or 0)
    current_ms = int(time.time() * 1000)
    if expiry_ms > 0 and expiry_ms < current_ms:
        await mobile_config(update, context)
        return
    sub_token = str(sub.get("sub_id") or "").strip()
    if not sub_token:
        await query.edit_message_text(t("error_generic", lang))
        return

    remote = await _fetch_mobile_remote_xui_data()
    if not remote:
        await query.edit_message_text(t("error_generic", lang))
        return
    sub_link = _build_remote_xui_sub_link(MOBILE_SSH_HOST, sub_token, remote)
    if not sub_link:
        await query.edit_message_text(t("error_generic", lang))
        return

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(sub_link)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    bio = BytesIO()
    bio.name = "qrcode.png"
    img.save(bio, "PNG")
    bio.seek(0)

    client_email = _mobile_email(tg_id)
    await context.bot.send_photo(
        chat_id=tg_id,
        photo=bio,
        caption=f"Subscription QR for: <code>{html.escape(client_email)}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data="mobile_config")]]),
    )


async def mobile_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    if not _mobile_feature_enabled():
        await mobile_menu(update, context)
        return

    sub = _fetch_mobile_subscription(tg_id)
    if not sub:
        text = t("mobile_sub_not_found", lang)
        try:
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data="mobile_menu")]]),
                parse_mode="Markdown",
            )
        except Exception:
            await query.message.delete()
            await context.bot.send_message(
                chat_id=tg_id,
                text=text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data="mobile_menu")]]),
                parse_mode="Markdown",
            )
        return

    email = _mobile_email(tg_id)
    traffic = await _fetch_mobile_remote_client_traffic(email)
    if not traffic or not traffic.get("ok"):
        text = "Нет данных по трафику 3G/4G." if lang == "ru" else "No 3G/4G traffic data found."
        try:
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data="mobile_menu")]]),
            )
        except Exception:
            await query.message.delete()
            await context.bot.send_message(
                chat_id=tg_id,
                text=text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data="mobile_menu")]]),
            )
        return

    up = int(traffic.get("up") or 0)
    down = int(traffic.get("down") or 0)
    total = up + down

    title = "📶 *Моя статистика 3G/4G*" if lang == "ru" else "📶 *My 3G/4G Stats*"
    text = (
        f"{title}\n\n"
        f"⬇️ Download: {down / (1024 ** 3):.2f} GB\n"
        f"⬆️ Upload: {up / (1024 ** 3):.2f} GB\n"
        f"📦 Total: {total / (1024 ** 3):.2f} GB"
    )

    try:
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data="mobile_menu")]]),
        )
    except Exception:
        await query.message.delete()
        await context.bot.send_message(
            chat_id=tg_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data="mobile_menu")]]),
        )


async def process_subscription(tg_id, days_to_add, update, context, lang, is_callback=False) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
        row = cursor.fetchone()

        if not row:
            if is_callback:
                try:
                    await update.callback_query.edit_message_text("Error: Inbound not found.")
                except Exception as e:
                    if "Message is not modified" not in str(e):
                         await update.callback_query.message.delete()
                         await context.bot.send_message(chat_id=tg_id, text="Error: Inbound not found.")
            else:
                await update.message.reply_text("Error: Inbound not found.")
            conn.close()
            return False

        settings = json.loads(row[0])
        clients = settings.get('clients', [])

        user_client = None
        client_index = -1

        for idx, client in enumerate(clients):
            if str(client.get('tgId')) == tg_id or client.get('email') == f"tg_{tg_id}":
                user_client = client
                client_index = idx
                break

        current_time_ms = int(time.time() * 1000)
        ms_to_add = days_to_add * 24 * 60 * 60 * 1000

        if user_client:
            current_expiry = user_client.get('expiryTime', 0)

            # Ensure email is updated if nickname is available
            # Check if email is in old format tg_ID or just different
            # We can't easily fetch nickname here without API call, which is slow.
            # But if we have it in DB, we can use it.
            # However, to avoid complexity, we can just respect the existing email
            # UNLESS we are creating a NEW one.
            # If updating existing, we keep email unless Admin syncs it.

            if current_expiry == 0:
                new_expiry = 0 # Remain unlimited
            elif current_expiry < current_time_ms:
                new_expiry = current_time_ms + ms_to_add
            else:
                new_expiry = current_expiry + ms_to_add

            # Update comment with latest nickname if available (User Request: auto-update comment on any sub action)
            try:
                user = None
                if update.callback_query:
                    user = update.callback_query.from_user
                elif update.message:
                    user = update.message.from_user

                if user:
                    user_nick = ""
                    if user.username:
                        user_nick = f"@{user.username}"
                    elif user.first_name:
                        user_nick = user.first_name
                        if user.last_name:
                            user_nick += f" {user.last_name}"

                    if user_nick:
                        # Only write if comment is empty, or user wants force update?
                        # User said: "в дальнейшем при любых подписках... сразу туда заполнять данные в эти комментарии"
                        # Implicitly means we should ensure it's set.
                        # And: "Если в комментарии уже есть чтото, то пропускаем перезапись."

                        old_comment = user_client.get('comment', '')
                        if not old_comment:
                            user_client['comment'] = user_nick
            except Exception:
                pass

            user_client['expiryTime'] = new_expiry
            user_client['enable'] = True
            user_client['updated_at'] = current_time_ms
            clients[client_index] = user_client

            # IMPORTANT: Assign updated clients list back to settings (was missing for update case)
            settings['clients'] = clients

            msg_key = "success_extended"
            if days_to_add < 0:
                msg_key = "success_updated"

            # Special case: If unlimited, we might want to tell user "You have unlimited, no changes made"
            # but usually extending unlimited is just ... unlimited.
            if current_expiry == 0:
                 # If unlimited, we don't change expiry, but we might want to re-enable if disabled
                 pass
        else:
            u_uuid = str(uuid.uuid4())
            new_expiry = current_time_ms + ms_to_add

            # Try to get nickname for new client
            uname_val = ""
            try:
                # Check DB first
                conn_db = sqlite3.connect(BOT_DB_PATH)
                cursor_db = conn_db.cursor()
                cursor_db.execute("SELECT username, first_name, last_name FROM user_prefs WHERE tg_id=?", (tg_id,))
                row_db = cursor_db.fetchone()
                conn_db.close()

                if row_db:
                    if row_db[0]:
                        uname_val = f"@{row_db[0]}"
                    elif row_db[1]:
                        uname_val = row_db[1]
                        if row_db[2]:
                            uname_val += f" {row_db[2]}"
                else:
                    # Fetch
                    chat = await context.bot.get_chat(tg_id)
                    if chat.username:
                        uname_val = f"@{chat.username}"
                    elif chat.first_name:
                        uname_val = chat.first_name
                        if chat.last_name:
                            uname_val += f" {chat.last_name}"
            except Exception:
                pass

            if not uname_val:
                uname_val = "User"

            # Use simple tg_ID for email, put nickname in comment
            new_email = f"tg_{tg_id}"

            new_client = {
                "id": u_uuid,
                "email": new_email,
                "limitIp": 0,
                "totalGB": 0,
                "expiryTime": new_expiry,
                "enable": True,
                "tgId": int(tg_id) if tg_id.isdigit() else tg_id,
                "subId": str(uuid.uuid4()).replace('-', '')[:16],
                "flow": "xtls-rprx-vision",
                "created_at": current_time_ms,
                "updated_at": current_time_ms,
                "comment": uname_val, # Use full nickname
                "reset": 0
            }
            clients.append(new_client)
            settings['clients'] = clients
            msg_key = "success_created"

            # Insert into client_traffics
            cursor.execute("""
                INSERT INTO client_traffics (inbound_id, enable, email, up, down, expiry_time, total, reset, all_time, last_online)
                VALUES (?, ?, ?, 0, 0, ?, 0, 0, 0, 0)
            """, (INBOUND_ID, 1, new_email, new_expiry))

        # Also update client_traffics with new expiry
        if user_client:
             email = user_client.get('email')
             if email:
                 try:
                     conn.execute("UPDATE client_traffics SET expiry_time=?, enable=1 WHERE email=?", (new_expiry, email))
                 except Exception as e:
                     logging.error(f"Error updating client_traffics for existing user: {e}")

        # Stop X-UI to prevent overwrite
        await _systemctl("stop", "x-ui")

        cursor.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(settings), INBOUND_ID))
        conn.commit()
        conn.close()

        await _systemctl("start", "x-ui")

        expiry_date = format_expiry_display(new_expiry, lang)

        text = t(msg_key, lang).format(expiry=expiry_date)

        keyboard = [
            [InlineKeyboardButton(t("btn_config", lang), callback_data='get_config')],
            [InlineKeyboardButton(t("btn_instructions", lang), callback_data='instructions'),
             InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if is_callback:
            # If called from callback (Trial), we edit message
             try:
                 await update.callback_query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)
             except Exception as e:
                 if "Message is not modified" not in str(e):
                      await update.callback_query.message.delete()
                      await context.bot.send_message(chat_id=tg_id, text=text, parse_mode='Markdown', reply_markup=reply_markup)
        else:
             await update.message.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)

        return True
    except Exception as e:
        logging.error(f"Error processing subscription: {e}")
        if is_callback:
             try:
                 await update.callback_query.edit_message_text(t("error_generic", lang))
             except Exception as ex:
                 if "Message is not modified" not in str(ex):
                      await update.callback_query.message.delete()
                      await context.bot.send_message(chat_id=tg_id, text=t("error_generic", lang))
        else:
             await update.message.reply_text(t("error_generic", lang))
        return False

async def get_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    username = query.from_user.username or "User"

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
        row = cursor.fetchone()

        if not row:
            try:
                await query.edit_message_text(
                    "Error: Inbound not found.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]])
                )
            except Exception as e:
                if "Message is not modified" not in str(e):
                     await query.message.delete()
                     await context.bot.send_message(
                         chat_id=tg_id,
                         text="Error: Inbound not found.",
                         reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]])
                     )
            conn.close()
            return

        settings = json.loads(row[0])
        clients = settings.get('clients', [])

        user_client = None
        for client in clients:
            if str(client.get('tgId', '')) == tg_id or client.get('email') == f"tg_{tg_id}":
                user_client = client
                break

        conn.close()

        if user_client:
            expiry_ms = user_client.get('expiryTime', 0)
            current_ms = int(time.time() * 1000)

            if expiry_ms > 0 and expiry_ms < current_ms:
                 try:
                     await query.edit_message_text(
                         t("sub_expired", lang),
                         reply_markup=InlineKeyboardMarkup([
                             [InlineKeyboardButton(t("btn_buy", lang), callback_data='shop')],
                             [InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]
                         ]),
                         parse_mode='Markdown'
                     )
                 except Exception:
                     await query.message.delete()
                     await context.bot.send_message(
                         chat_id=tg_id,
                         text=t("sub_expired", lang),
                         reply_markup=InlineKeyboardMarkup([
                             [InlineKeyboardButton(t("btn_buy", lang), callback_data='shop')],
                             [InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]
                         ]),
                         parse_mode='Markdown'
                     )
                 return

            u_uuid = user_client['id']
            client_email = user_client.get('email', f"VPN_{username}")
            client_flow = user_client.get('flow', '')

            sub_id = user_client.get('subId')
            sub_token = str(sub_id).strip() if sub_id else str(u_uuid).strip()
            sub_link = _build_master_sub_link(sub_token) if sub_token else None

            expiry_str = format_expiry_display(expiry_ms, lang)
            msg_text = t("sub_active_html", lang).format(expiry=expiry_str)

            all_sub_link, all_sub_count, all_sub_payload = _build_all_locations_subscription(
                user_uuid=u_uuid,
                client_email=client_email,
                client_flow=client_flow,
            )
            multi_sub_url = _build_multi_sub_public_url(sub_token) if MULTI_SUB_ENABLE and all_sub_count > 1 else None
            primary_sub_link = multi_sub_url or sub_link
            if primary_sub_link:
                msg_text += t("sub_recommendation", lang).format(link=html.escape(primary_sub_link))
            location_items: list[str] = []
            if IP:
                location_items.append(_auto_location_name(IP))
            for loc in [loc for loc in _fetch_remote_locations() if loc.get("enabled")]:
                location_items.append(_location_label(loc))
            unique_items = []
            for item in location_items:
                if item and item not in unique_items:
                    unique_items.append(item)
            if unique_items:
                locations_text = "\n".join(f"• {html.escape(item)}" for item in unique_items)
                block = t("sub_locations_list", lang).format(list=locations_text)
                if len(msg_text) + len(block) <= 3500:
                    msg_text += block

            try:
                await query.edit_message_text(
                    msg_text,
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton(t("btn_instructions", lang), callback_data='instructions')],
                        [InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]
                    ])
                )
            except Exception:
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await context.bot.send_message(
                    chat_id=tg_id,
                    text=msg_text,
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton(t("btn_instructions", lang), callback_data='instructions')],
                        [InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]
                    ])
                )
            if (not multi_sub_url) and all_sub_payload and all_sub_count > 0:
                if all_sub_count > 1 or len(all_sub_payload) > 1500:
                    bio = BytesIO(all_sub_payload.encode("utf-8"))
                    bio.name = "all_locations.txt"
                    await context.bot.send_document(chat_id=tg_id, document=bio)
        else:
            try:
                await query.edit_message_text(
                    t("sub_not_found", lang),
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton(t("btn_buy", lang), callback_data='shop')],
                        [InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]
                    ]),
                    parse_mode='Markdown'
                )
            except Exception:
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await context.bot.send_message(
                    chat_id=tg_id,
                    text=t("sub_not_found", lang),
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton(t("btn_buy", lang), callback_data='shop')],
                        [InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]
                    ]),
                    parse_mode='Markdown'
                )

    except Exception as e:
        logging.error(f"Error: {e}")
        try:
            await query.edit_message_text(
                t("error_generic", lang),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]])
            )
        except Exception:
             try:
                 await query.message.delete()
             except Exception:
                 pass
             await context.bot.send_message(
                 chat_id=tg_id,
                 text=t("error_generic", lang),
                 reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]])
             )

def format_bytes(size):
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"


def format_uptime(uptime_sec: int) -> str:
    try:
        sec = max(int(uptime_sec), 0)
    except Exception:
        sec = 0
    days, rem = divmod(sec, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    email = f"tg_{tg_id}"

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Get current traffic
        cursor.execute("SELECT up, down, expiry_time FROM client_traffics WHERE email=?", (email,))
        row = cursor.fetchone()

        current_up = 0
        current_down = 0
        expiry_time = 0
        found = False

        if row:
            current_up, current_down, expiry_time = row
            found = True
        else:
            # Fallback to inbounds if no traffic yet
            cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
            row_inbound = cursor.fetchone()
            if row_inbound:
                settings = json.loads(row_inbound[0])
                clients = settings.get('clients', [])

                # Search by tg_id (as integer) or email
                user_client = None
                for c in clients:
                     # Check tgId as integer or string
                     if str(c.get('tgId', '')) == tg_id:
                         user_client = c
                         # Update email to match found client
                         email = c.get('email')
                         break
                     elif c.get('email') == email:
                         user_client = c
                         break

                if user_client:
                    # Try to get fresh stats from client_traffics using the found email
                    found_email = user_client.get('email')

                    # Also try to update remark if it's empty (proactive update)
                    try:
                        # Check remark
                        # Note: We are in a read-only transaction here maybe? No, we can write.
                        # But we are inside `stats` handler, we should be careful.
                        # However, user requested: "в дальнейшем при любых подписках... сразу туда заполнять данные"
                        # This block is for existing users viewing stats.
                        # Let's do it in 'process_subscription' instead for new subs.
                        # Here we just read.
                        pass
                    except Exception:
                        pass

                    if found_email:
                        cursor.execute("SELECT up, down FROM client_traffics WHERE email=?", (found_email,))
                        row_fresh = cursor.fetchone()
                        if row_fresh:
                            current_up, current_down = row_fresh
                        else:
                            current_up = user_client.get('up', 0)
                            current_down = user_client.get('down', 0)
                    else:
                        current_up = user_client.get('up', 0)
                        current_down = user_client.get('down', 0)

                    expiry_time = user_client.get('expiryTime', 0)
                    found = True

        conn.close()

        if not found:
         text = t("stats_no_sub", lang)
         try:
             await query.edit_message_text(
                 text,
                 parse_mode='Markdown',
                 reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]])
             )
         except Exception as e:
             if "Message is not modified" not in str(e):
                  await query.message.delete()
                  await context.bot.send_message(
                      chat_id=tg_id,
                      text=text,
                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]]),
                      parse_mode='Markdown'
                  )
         return

        current_total = current_up + current_down

        # Get history for periods
        conn_bot = sqlite3.connect(BOT_DB_PATH)
        cursor_bot = conn_bot.cursor()

        # Determine Plan
        sub_plan = t("plan_manual", lang)

        if expiry_time == 0:
            sub_plan = t("plan_unlimited", lang)
        else:
            cursor_bot.execute(
                "SELECT id, plan_id, amount FROM transactions WHERE tg_id=? "
                "ORDER BY date DESC LIMIT 1",
                (tg_id,),
            )
            last_tx = cursor_bot.fetchone()
            if last_tx:
                last_tx_id, last_plan_id, last_amount = last_tx
                normalized_last_plan = _normalize_plan_id(str(last_plan_id)) if last_plan_id else ""
                if not normalized_last_plan or normalized_last_plan == "unknown":
                    try:
                        prices = get_prices()
                    except Exception:
                        prices = {}
                    inferred = _infer_plan_id_from_amount(last_amount, prices)
                    if inferred:
                        sub_plan = _resolve_plan_label(inferred, lang)
                        try:
                            cursor_bot.execute(
                                "UPDATE transactions SET plan_id=? "
                                "WHERE id=? AND (plan_id IS NULL OR plan_id='' OR plan_id='unknown')",
                                (inferred, last_tx_id),
                            )
                            conn_bot.commit()
                        except Exception:
                            pass
                    else:
                        sub_plan = t("sub_type_unknown", lang)
                else:
                    sub_plan = _resolve_plan_label(str(last_plan_id), lang)
            else:
                cursor_bot.execute("SELECT trial_used FROM user_prefs WHERE tg_id=?", (tg_id,))
                pref = cursor_bot.fetchone()
                if pref and pref[0]:
                    sub_plan = t("plan_trial", lang)

        now = datetime.datetime.now(TIMEZONE)
        today_str = now.strftime("%Y-%m-%d")

        cursor_bot.execute(
            """
            INSERT OR IGNORE INTO traffic_daily_baselines (email, date, up, down, captured_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (email, today_str, current_up, current_down, int(time.time())),
        )

        cursor_bot.execute(
            "SELECT up, down FROM traffic_daily_baselines WHERE email=? AND date=?",
            (email, today_str),
        )
        day_row = cursor_bot.fetchone()
        baseline_day_up = day_row[0] if day_row else current_up
        baseline_day_down = day_row[1] if day_row else current_down
        day_up = max(0, current_up - baseline_day_up)
        day_down = max(0, current_down - baseline_day_down)

        week_start = (now - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        cursor_bot.execute(
            "SELECT up, down FROM traffic_daily_baselines WHERE email=? AND date=?",
            (email, week_start),
        )
        week_row = cursor_bot.fetchone()
        if not week_row:
            cursor_bot.execute(
                "SELECT up, down FROM traffic_daily_baselines WHERE email=? AND date >= ? ORDER BY date ASC LIMIT 1",
                (email, week_start),
            )
            week_row = cursor_bot.fetchone()
        baseline_week_up = week_row[0] if week_row else current_up
        baseline_week_down = week_row[1] if week_row else current_down
        week_up = max(0, current_up - baseline_week_up)
        week_down = max(0, current_down - baseline_week_down)

        month_start = (now - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
        cursor_bot.execute(
            "SELECT up, down FROM traffic_daily_baselines WHERE email=? AND date >= ? ORDER BY date ASC LIMIT 1",
            (email, month_start),
        )
        month_row = cursor_bot.fetchone()
        baseline_month_up = month_row[0] if month_row else current_up
        baseline_month_down = month_row[1] if month_row else current_down
        month_up = max(0, current_up - baseline_month_up)
        month_down = max(0, current_down - baseline_month_down)

        conn_bot.close()

        expiry_str = format_expiry_display(expiry_time, lang, unlimited_key="unlimited_text")
        sub_plan_safe = _escape_markdown(str(sub_plan or "—"))

        text = f"""{t("stats_your_title", lang)}

{t("stats_sub_type", lang).format(plan=sub_plan_safe)}

{t("stats_today", lang)}
⬇️ {format_bytes(day_down)}  ⬆️ {format_bytes(day_up)}

{t("stats_week", lang)}
⬇️ {format_bytes(week_down)}  ⬆️ {format_bytes(week_up)}

{t("stats_month", lang)}
⬇️ {format_bytes(month_down)}  ⬆️ {format_bytes(month_up)}

{t("stats_total", lang)}
⬇️ {format_bytes(current_down)}  ⬆️ {format_bytes(current_up)}
∑ {format_bytes(current_total)}

{t("stats_expires", lang)} {_escape_markdown(expiry_str)}"""

        try:
            await query.edit_message_text(
                text,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]])
            )
        except Exception as e:
            if "Message is not modified" not in str(e):
                 await query.message.delete()
                 await context.bot.send_message(
                     chat_id=tg_id,
                     text=text,
                     reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]]),
                     parse_mode='Markdown'
                 )

    except Exception as e:
        logging.error(e)
        try:
            await query.edit_message_text(
                t("error_generic", lang),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]])
            )
        except Exception as ex:
            if "Message is not modified" not in str(ex):
                 await query.message.delete()
                 await context.bot.send_message(
                     chat_id=tg_id,
                     text=t("error_generic", lang),
                     reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]])
                 )

async def instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    keyboard = [
        [InlineKeyboardButton(t("btn_android", lang), callback_data='instr_android')],
        [InlineKeyboardButton(t("btn_ios", lang), callback_data='instr_ios')],
        [InlineKeyboardButton(t("btn_pc", lang), callback_data='instr_pc')],
        [InlineKeyboardButton(t("btn_happ_ios", lang), callback_data='instr_happ_ios')],
        [InlineKeyboardButton(t("btn_happ_android", lang), callback_data='instr_happ_android')],
        [InlineKeyboardButton(t("btn_happ_desktop", lang), callback_data='instr_happ_desktop')],
        [InlineKeyboardButton(t("btn_happ_tv", lang), callback_data='instr_happ_tv')],
        [InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]
    ]

    try:
        await query.edit_message_text(
            t("instr_menu", lang),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        if "Message is not modified" not in str(e):
             await query.message.delete()
             await context.bot.send_message(
                 chat_id=tg_id,
                 text=t("instr_menu", lang),
                 reply_markup=InlineKeyboardMarkup(keyboard),
                 parse_mode='Markdown'
             )

async def show_instruction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    platform_key = query.data.replace("instr_", "", 1)
    text = t(f"instr_{platform_key}", lang)

    keyboard = [[InlineKeyboardButton(t("btn_back", lang), callback_data='instructions')]]

    try:
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        if "Message is not modified" not in str(e):
             await query.message.delete()
             await context.bot.send_message(
                 chat_id=tg_id,
                 text=text,
                 reply_markup=InlineKeyboardMarkup(keyboard),
                 parse_mode='Markdown'
             )

async def log_traffic_stats(context: ContextTypes.DEFAULT_TYPE):
    try:
        today = datetime.datetime.now(TIMEZONE).strftime("%Y-%m-%d")

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT email, up, down FROM client_traffics WHERE inbound_id=?", (INBOUND_ID,))
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return

        conn_bot = sqlite3.connect(BOT_DB_PATH)
        cursor_bot = conn_bot.cursor()

        for r in rows:
            email, up, down = r
            # We store the CURRENT TOTAL up/down for that day.
            # When calculating daily usage, we need delta.
            # Actually, X-UI stores total accumulation.
            # So to get usage for a specific day, we need to know what was the total at the beginning of the day.
            # But here we just snapshot the current state.
            # Wait, if we snapshot every hour, we just overwrite for today.
            # Yes, 'INSERT OR REPLACE' or UPDATE.

            cursor_bot.execute("""
                INSERT INTO traffic_history (email, date, up, down)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(email, date) DO UPDATE SET up=excluded.up, down=excluded.down
            """, (email, today, up, down))

            cursor_bot.execute(
                """
                INSERT OR IGNORE INTO traffic_daily_baselines (email, date, up, down, captured_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (email, today, up, down, int(time.time())),
            )

        conn_bot.commit()
        conn_bot.close()

    except Exception as e:
        logging.error(f"Error logging traffic: {e}")


async def send_daily_report_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ADMIN_ID:
        return
    if _DAILY_REPORT_ENABLED <= 0:
        return

    now_dt = datetime.datetime.now(TIMEZONE)
    end_dt = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    start_dt = end_dt - datetime.timedelta(days=1)
    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())
    start_ms = start_ts * 1000
    end_ms = end_ts * 1000
    now_ts = int(now_dt.timestamp())
    now_ms = int(time.time() * 1000)

    revenue = 0
    tx_count = 0
    buyers: set[str] = set()
    renew_tx = 0
    new_buyers = 0
    renewed_recent: set[str] = set()

    try:
        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT tg_id, amount, date FROM transactions WHERE date>=? AND date<?",
            (start_ts, end_ts),
        )
        rows = cursor.fetchall()

        admin_str = str(ADMIN_ID)
        for tg_id, amount, _date in rows:
            tg_id_str = str(tg_id)
            if tg_id_str == admin_str:
                continue
            buyers.add(tg_id_str)
            if amount is not None:
                revenue += int(amount)
            tx_count += 1

        if buyers:
            placeholders = ",".join(["?"] * len(buyers))
            params = tuple(sorted(buyers))
            cursor.execute(
                f"SELECT tg_id, MIN(date) FROM transactions WHERE tg_id IN ({placeholders}) GROUP BY tg_id",
                params,
            )
            for tg_id, min_date in cursor.fetchall():
                try:
                    if min_date is not None and int(min_date) >= start_ts and int(min_date) < end_ts:
                        new_buyers += 1
                except Exception:
                    continue

            cursor.execute(
                "SELECT COUNT(*) FROM transactions t "
                "WHERE t.date>=? AND t.date<? AND t.tg_id != ? "
                "AND EXISTS(SELECT 1 FROM transactions t2 WHERE t2.tg_id=t.tg_id AND t2.date<? AND t2.tg_id != ?)",
                (start_ts, end_ts, admin_str, start_ts, admin_str),
            )
            row = cursor.fetchone()
            renew_tx = int(row[0]) if row else 0

        cursor.execute(
            "SELECT DISTINCT tg_id FROM transactions WHERE date>=? AND date<?",
            (start_ts, now_ts),
        )
        for (tg_id,) in cursor.fetchall():
            tg_id_str = str(tg_id)
            if tg_id_str != str(ADMIN_ID):
                renewed_recent.add(tg_id_str)

        conn.close()
    except Exception as e:
        logging.error(f"Daily report tx query failed: {e}")

    expired_yesterday: set[str] = set()
    try:
        conn_xui = sqlite3.connect(DB_PATH)
        cursor_xui = conn_xui.cursor()
        cursor_xui.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
        row = cursor_xui.fetchone()
        conn_xui.close()

        if row:
            settings = json.loads(row[0])
            clients = settings.get('clients', [])
            for client in clients:
                tg_id = client.get('tgId', None)
                if tg_id is None:
                    continue
                tg_id_str = str(tg_id)
                if not tg_id_str.isdigit():
                    continue
                expiry = client.get('expiryTime', 0)
                try:
                    expiry_ms = int(expiry)
                except Exception:
                    continue
                if expiry_ms <= 0:
                    continue
                if start_ms <= expiry_ms < end_ms:
                    if expiry_ms < now_ms:
                        expired_yesterday.add(tg_id_str)
    except Exception as e:
        logging.error(f"Daily report churn scan failed: {e}")

    churn = 0
    for tg_id_str in expired_yesterday:
        if tg_id_str not in renewed_recent:
            churn += 1

    renew_buyers = max(len(buyers) - new_buyers, 0)

    admin_lang = get_lang(ADMIN_ID)
    date_label = start_dt.strftime("%Y-%m-%d")
    if admin_lang == "ru":
        msg = (
            f"📈 *Ежедневный отчёт ({date_label})*\n\n"
            f"💰 Выручка: *{revenue}* ⭐️\n"
            f"🛒 Платежей: *{tx_count}*\n"
            f"👤 Покупателей: *{len(buyers)}* (новых: *{new_buyers}*, продления: *{renew_buyers}*)\n"
            f"🔁 Продлений (платежей): *{renew_tx}*\n"
            f"📉 Отток (истекли и не продлили): *{churn}* из *{len(expired_yesterday)}*\n"
            f"🕒 Сформировано: {now_dt.strftime('%Y-%m-%d %H:%M:%S')}"
        )
    else:
        msg = (
            f"📈 *Daily report ({date_label})*\n\n"
            f"💰 Revenue: *{revenue}* ⭐️\n"
            f"🛒 Payments: *{tx_count}*\n"
            f"👤 Buyers: *{len(buyers)}* (new: *{new_buyers}*, renewals: *{renew_buyers}*)\n"
            f"🔁 Renewal payments: *{renew_tx}*\n"
            f"📉 Churn (expired & not renewed): *{churn}* of *{len(expired_yesterday)}*\n"
            f"🕒 Generated: {now_dt.strftime('%Y-%m-%d %H:%M:%S')}"
        )

    await _send_admin_message(context, msg)

async def check_expiring_subscriptions(context: ContextTypes.DEFAULT_TYPE):
    try:
        logging.info("Checking for expiring subscriptions...")
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
        row = cursor.fetchone()

        if not row:
            conn.close()
            return

        settings = json.loads(row[0])
        clients = settings.get('clients', [])

        current_time = time.time() * 1000
        one_hour_ms = 60 * 60 * 1000
        one_day_ms = 24 * one_hour_ms

        # Connect to Bot DB for notifications
        conn_bot = sqlite3.connect(BOT_DB_PATH)
        cursor_bot = conn_bot.cursor()

        for client in clients:
            expiry_time = client.get('expiryTime', 0)
            tg_id = str(client.get('tgId', ''))

            if expiry_time > 0 and tg_id and tg_id.isdigit():
                time_left = expiry_time - current_time

                # Check if it's a Trial User (heuristic: trial_used=1 in prefs)
                is_trial = False
                cursor_bot.execute("SELECT trial_used FROM user_prefs WHERE tg_id=?", (tg_id,))
                pref = cursor_bot.fetchone()
                if pref and pref[0]:
                    # Check if they have any paid transactions
                    cursor_bot.execute("SELECT 1 FROM transactions WHERE tg_id=?", (tg_id,))
                    if not cursor_bot.fetchone():
                        is_trial = True

                if is_trial:
                    reminders = [
                        ("expiry_warning_24h", "trial_expiring", one_day_ms, 0),
                    ]
                else:
                    reminders = [
                        ("expiry_warning_7d", "expiry_warning_7d", 7 * one_hour_ms, 6 * one_hour_ms),
                        ("expiry_warning_3d", "expiry_warning_3d", 3 * one_hour_ms, 2 * one_hour_ms),
                        ("expiry_warning_24h", "expiry_warning", one_day_ms, 0),
                    ]

                for notif_type, msg_key, upper, lower in reminders:
                    if lower < time_left <= upper:
                        cursor_bot.execute("SELECT date FROM notifications WHERE tg_id=? AND type=?", (tg_id, notif_type))
                        notif = cursor_bot.fetchone()

                        should_send = True
                        if notif and (time.time() - notif[0]) < 86400:
                            should_send = False

                        if should_send:
                            try:
                                user_lang = get_lang(tg_id)
                                await context.bot.send_message(
                                    chat_id=tg_id,
                                    text=t(msg_key, user_lang),
                                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_renew", user_lang), callback_data='shop')]]),
                                    parse_mode='Markdown'
                                )
                                cursor_bot.execute("INSERT OR REPLACE INTO notifications (tg_id, type, date) VALUES (?, ?, ?)",
                                                   (tg_id, notif_type, int(time.time())))
                                conn_bot.commit()
                                logging.info(f"Sent expiry warning to {tg_id} ({notif_type})")
                            except Exception as ex:
                                logging.warning(f"Failed to send warning to {tg_id}: {ex}")
                        break

        conn_bot.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error in check_expiring_subscriptions: {e}")

async def check_expired_trials(context: ContextTypes.DEFAULT_TYPE):
    """
    Check for users whose trial expired recently and encourage them to buy.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
        row = cursor.fetchone()

        if not row:
            conn.close()
            return

        settings = json.loads(row[0])
        clients = settings.get('clients', [])

        current_time = time.time() * 1000

        conn_bot = sqlite3.connect(BOT_DB_PATH)
        cursor_bot = conn_bot.cursor()

        for client in clients:
            expiry_time = client.get('expiryTime', 0)
            tg_id = str(client.get('tgId', ''))

            if expiry_time > 0 and tg_id and tg_id.isdigit():
                # Check if expired
                if expiry_time < current_time:
                    # Check if expired RECENTLY (e.g., within last 24 hours)
                    # We don't want to spam old users.
                    # Expiry time is in ms.
                    ms_since_expiry = current_time - expiry_time
                    hours_since_expiry = ms_since_expiry / (1000 * 3600)

                    if 0 < hours_since_expiry < 48: # Window of 48h after expiry
                        # Check if Trial User
                        cursor_bot.execute("SELECT trial_used FROM user_prefs WHERE tg_id=?", (tg_id,))
                        pref = cursor_bot.fetchone()

                        if pref and pref[0]: # Has used trial
                            # Check if PAID user (don't annoy paid users who expired)
                            cursor_bot.execute("SELECT 1 FROM transactions WHERE tg_id=?", (tg_id,))
                            if not cursor_bot.fetchone():
                                # Pure Trial user who expired recently.

                                # Check if already notified
                                cursor_bot.execute("SELECT date FROM notifications WHERE tg_id=? AND type='trial_expired_followup'", (tg_id,))
                                if not cursor_bot.fetchone():
                                    try:
                                        user_lang = get_lang(tg_id)
                                        await context.bot.send_message(
                                            chat_id=tg_id,
                                            text=t("trial_expired", user_lang),
                                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_buy", user_lang), callback_data='shop')]]),
                                            parse_mode='Markdown'
                                        )
                                        cursor_bot.execute("INSERT OR REPLACE INTO notifications (tg_id, type, date) VALUES (?, ?, ?)",
                                                           (tg_id, 'trial_expired_followup', int(time.time())))
                                        conn_bot.commit()
                                        logging.info(f"Sent trial expired followup to {tg_id}")
                                    except Exception as ex:
                                        logging.warning(f"Failed to send trial followup to {tg_id}: {ex}")

        conn_bot.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error in check_expired_trials: {e}")

async def watch_access_log(app):
    """
    Background task to monitor access.log and record unique connections.
    """
    import re
    # Updated regex to handle microseconds and 'from' keyword
    # Example: 2026/01/19 13:11:31.193164 from 31.29.179.60:43924 accepted tcp:d0.mradx.net:443 [inbound-17343 >> direct] email: tg_824606348
    log_pattern = re.compile(r'^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)? from (?:tcp:|udp:)?(\d{1,3}(?:\.\d{1,3}){3}):\d+ accepted .*?email:\s*(\S+)')

    if not os.path.exists(ACCESS_LOG_PATH):
        logging.warning(f"Access log not found at {ACCESS_LOG_PATH}")
        return

    logging.info(f"Starting to watch access log at {ACCESS_LOG_PATH}")

    try:
        # Open file and seek to end
        file = open(ACCESS_LOG_PATH, 'r', encoding='utf-8')
        file.seek(0, os.SEEK_END)

        while True:
            line = file.readline()
            if not line:
                await asyncio.sleep(1)
                continue

            match = log_pattern.search(line)
            if match:
                ip = match.group(1)
                email = match.group(2)
                timestamp = int(time.time())

                # Store in DB
                try:
                    country_code = None
                    try:
                        def _check_ip():
                            c = sqlite3.connect(BOT_DB_PATH)
                            cur = c.cursor()
                            cur.execute("SELECT country_code FROM connection_logs WHERE ip=?", (ip,))
                            res = cur.fetchone()
                            c.close()
                            return res[0] if res else None

                        cached_cc = await asyncio.get_running_loop().run_in_executor(None, _check_ip)

                        if cached_cc:
                            country_code = cached_cc
                        else:
                             def _fetch_country_code():
                                 requests = __import__("requests")
                                 resp = requests.get(f"https://ipinfo.io/{ip}/json", timeout=2)
                                 if resp.status_code == 200:
                                     data = resp.json()
                                     return data.get("country")
                                 return None

                             country_code = await asyncio.get_running_loop().run_in_executor(None, _fetch_country_code)

                    except Exception as ex:
                        logging.warning(f"GeoIP failed for {ip}: {ex}")

                    def _update_db():
                        conn = sqlite3.connect(BOT_DB_PATH)
                        cursor = conn.cursor()
                        cursor.execute("""
                            INSERT INTO connection_logs (email, ip, timestamp, country_code)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(email, ip) DO UPDATE SET timestamp=excluded.timestamp, country_code=coalesce(excluded.country_code, connection_logs.country_code)
                        """, (email, ip, timestamp, country_code))
                        conn.commit()
                        conn.close()

                    await asyncio.get_running_loop().run_in_executor(None, _update_db)

                except Exception as e:
                    logging.error(f"Error updating connection logs: {e}")

    except Exception as e:
        logging.error(f"Error in watch_access_log: {e}")

async def post_init(application):
    # Set bot commands
    await application.bot.set_my_commands([
        ("start", "Start the bot / Запустить бота"),
        ("shop", "Buy Subscription / Купить подписку"),
        ("stats", "My Stats / Моя статистика"),
        ("get_config", "My Config / Мой конфиг")
    ])

    # Set description for Russian
    description_ru = """🚀 Maxi_VPN — быстрый и защищённый VPN в Telegram
🔐 Современный протокол VLESS + Reality — максимальная анонимность, обход блокировок и стабильное соединение без лишних настроек.

⚡ Преимущества:
• Высокая скорость и низкие задержки
• Без логов и рекламы
• Устойчив к блокировкам
• iOS / Android / Windows / macOS
• Мгновенная активация после оплаты
• Управление подпиской прямо в боте

🎁 Гибкие тарифы и удобная оплата
👉 Нажми «Старт» и подключись за 1 минуту 🔥"""

    try:
        await application.bot.set_my_description(description_ru, language_code='ru')
        await application.bot.set_my_short_description("Maxi_VPN — быстрый и защищённый VPN", language_code='ru')
    except Exception as e:
        logging.error(f"Failed to set description: {e}")

    # Start log watcher
    asyncio.create_task(watch_access_log(application))

async def admin_delete_client_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # admin_del_client_ask_UUID
    try:
        uid = query.data.split('_', 4)[4]
    except IndexError:
        return

    keyboard = [
        [InlineKeyboardButton("✅ Да, удалить", callback_data=f'admin_del_client_confirm_{uid}')],
        [InlineKeyboardButton("❌ Нет, отмена", callback_data=f'admin_u_{uid}')]
    ]

    await query.edit_message_text(
        f"⚠️ **Вы уверены, что хотите удалить этого пользователя из X-UI?**\nUUID: `{uid}`\n\nЭто действие необратимо!",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def admin_delete_client_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # admin_del_client_confirm_UUID
    try:
        uid = query.data.split('_', 4)[4]
    except IndexError:
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        await query.edit_message_text("❌ Входящее соединение не найдено.")
        return

    settings = json.loads(row[0])
    clients = settings.get('clients', [])

    # Find email for cleanup
    email = None
    for c in clients:
        if c.get('id') == uid:
            email = c.get('email')
            break

    # Filter out the client
    initial_len = len(clients)
    clients = [c for c in clients if c.get('id') != uid]

    if len(clients) == initial_len:
        conn.close()
        await query.edit_message_text("❌ Клиент не найден или уже удален.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К списку", callback_data='admin_users_0')]]))
        return

    # Save back
    settings['clients'] = clients
    new_settings = json.dumps(settings, indent=2)
    cursor.execute("UPDATE inbounds SET settings=? WHERE id=?", (new_settings, INBOUND_ID))

    # Clean up client_traffics if email found
    if email:
        try:
             cursor.execute("DELETE FROM client_traffics WHERE email=?", (email,))
        except sqlite3.Error:
            pass

    conn.commit()
    conn.close()

    # Restart X-UI
    await _systemctl("restart", "x-ui")

    await query.edit_message_text(
        "✅ Клиент успешно удален из X-UI.\nX-UI перезапущен.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К списку", callback_data='admin_users_0')]])
    )

async def admin_poll_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    keyboard = [
        [InlineKeyboardButton(t("btn_admin_poll_new", lang), callback_data='admin_poll_new')],
        [InlineKeyboardButton("🔙 В админ панель", callback_data='admin_panel')]
    ]

    text = "📊 *Меню опросов*\n\nСоздавайте и рассылайте опросы всем пользователям."

    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    except Exception as e:
        if "Message is not modified" not in str(e):
             await query.message.delete()
             await context.bot.send_message(chat_id=tg_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_poll_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    context.user_data['admin_action'] = 'awaiting_poll_question'

    keyboard = [[InlineKeyboardButton("🔙 Отмена", callback_data='admin_poll_menu')]]

    await query.edit_message_text(
        t("poll_ask_question", lang),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

def generate_poll_message(poll_id, lang):
    try:
        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()

        # Get Poll
        cursor.execute("SELECT question, options, active FROM polls WHERE id=?", (poll_id,))
        row = cursor.fetchone()

        if not row:
            conn.close()
            return None, None

        question, options_json, active = row
        options = json.loads(options_json)

        # Get Votes
        cursor.execute("SELECT option_index, COUNT(*) FROM poll_votes WHERE poll_id=? GROUP BY option_index", (poll_id,))
        vote_counts = {row[0]: row[1] for row in cursor.fetchall()}

        conn.close()

        total_votes = sum(vote_counts.values())

        text = f"📊 *{t('poll_title', lang)}*\n\n{question}\n\n"

        for idx, option in enumerate(options):
            count = vote_counts.get(idx, 0)
            percent = (count / total_votes * 100) if total_votes > 0 else 0

            # Progress Bar (10 chars)
            filled = int(percent // 10)
            empty = 10 - filled
            bar = "▓" * filled + "░" * empty

            text += f"{option}\n{bar} {int(percent)}% ({count})\n\n"

        text += f"👥 {t('poll_total_votes', lang)}: {total_votes}"

        keyboard = []
        if active:
            for idx, option in enumerate(options):
                keyboard.append([InlineKeyboardButton(option, callback_data=f'poll_vote_{poll_id}_{idx}')])

        # Add Refresh Button
        keyboard.append([InlineKeyboardButton("🔄 " + t('btn_refresh', lang), callback_data=f'poll_refresh_{poll_id}')])

        return text, InlineKeyboardMarkup(keyboard)
    except Exception as e:
        logging.error(f"Error generating poll message: {e}")
        return None, None

async def handle_poll_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    parts = query.data.split('_')
    # poll_vote_POLLID_IDX
    poll_id = int(parts[2])
    option_idx = int(parts[3])

    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()

    # Save vote (upsert)
    cursor.execute("INSERT OR REPLACE INTO poll_votes (poll_id, tg_id, option_index) VALUES (?, ?, ?)", (poll_id, tg_id, option_idx))
    conn.commit()
    conn.close()

    text, reply_markup = generate_poll_message(poll_id, lang)

    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    except Exception:
        pass # Message not modified

    await query.answer(t("poll_vote_registered", lang))

async def handle_poll_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    parts = query.data.split('_')
    # poll_refresh_POLLID
    poll_id = int(parts[2])

    text, reply_markup = generate_poll_message(poll_id, lang)

    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    except Exception:
        pass

    await query.answer()

async def admin_poll_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    question = context.user_data.get('poll_question')
    options = context.user_data.get('poll_options')

    if not question or not options:
        await query.edit_message_text("❌ Ошибка: Опрос не найден. Создайте его заново.")
        return

    # Create Poll in DB
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO polls (question, options, created_at) VALUES (?, ?, ?)", (question, json.dumps(options), int(time.time())))
    poll_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # Get all users
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT tg_id FROM user_prefs")
    users = cursor.fetchall()
    conn.close()

    # Also sync from X-UI
    xui_users = []
    try:
        conn_xui = sqlite3.connect(DB_PATH)
        cursor_xui = conn_xui.cursor()
        cursor_xui.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
        row = cursor_xui.fetchone()
        conn_xui.close()
        if row:
            settings = json.loads(row[0])
            clients = settings.get('clients', [])
            for client in clients:
                cid = client.get('tgId')
                if cid:
                    xui_users.append(str(cid))
    except Exception:
        pass

    all_users = set([u[0] for u in users] + xui_users)

    sent = 0
    blocked = 0

    status_msg = await query.edit_message_text(f"⏳ Рассылка опроса запущена ({len(all_users)} пользователей)...")
    status_message = status_msg if not isinstance(status_msg, bool) else None

    # Pre-generate messages
    msg_ru, markup_ru = generate_poll_message(poll_id, 'ru')
    msg_en, markup_en = generate_poll_message(poll_id, 'en')

    # Map user langs
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT tg_id, lang FROM user_prefs")
    user_langs = {row[0]: row[1] for row in cursor.fetchall()}
    conn.close()

    for user_id in all_users:
        try:
            u_lang = user_langs.get(user_id, 'ru')
            text = msg_en if u_lang == 'en' else msg_ru
            markup = markup_en if u_lang == 'en' else markup_ru

            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=markup,
                parse_mode='Markdown'
            )
            sent += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            if "Forbidden" in str(e) or "blocked" in str(e):
                blocked += 1
            pass

    if status_message:
        await status_message.edit_text(
            f"✅ Рассылка опроса завершена.\n\n📤 Отправлено: {sent}\n🚫 Не доставлено: {blocked}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back_admin", lang), callback_data='admin_poll_menu')]])
        )
    else:
        await context.bot.send_message(
            chat_id=tg_id,
            text=f"✅ Рассылка опроса завершена.\n\n📤 Отправлено: {sent}\n🚫 Не доставлено: {blocked}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back_admin", lang), callback_data='admin_poll_menu')]])
        )

async def support_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)

    try:
        await query.edit_message_text(
            t("support_title", lang),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]]),
            parse_mode='Markdown'
        )
    except Exception:
        # If message cannot be edited (e.g. it has a photo), delete and send new
        await query.message.delete()
        await context.bot.send_message(
            chat_id=tg_id,
            text=t("support_title", lang),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]]),
            parse_mode='Markdown'
        )

    context.user_data['admin_action'] = 'awaiting_support_message'

async def detect_suspicious_activity(context: ContextTypes.DEFAULT_TYPE):
    """
    Background task to analyze logs and store suspicious events (Multi-IP).
    Runs every 5 minutes. Analyzes last 10 minutes.
    """
    try:
        # Analyze last 10 minutes (600 seconds)
        # We look for SIMULTANEOUS usage in the same minute
        now = int(time.time())
        threshold = now - 600

        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()

        # Get logs
        cursor.execute("""
            SELECT email, ip, timestamp, country_code
            FROM connection_logs
            WHERE timestamp > ?
            ORDER BY timestamp ASC
        """, (threshold,))
        rows = cursor.fetchall()

        if not rows:
            conn.close()
            return

        # Analysis Logic (Sliding Window - 60 seconds)
        # We look for overlapping usage within a 60-second window
        user_logs: dict[str, list[LogEntry]] = {}
        for row in rows:
            email, ip, ts, cc = row
            if not email or not ip or ts is None:
                continue
            email_str = str(email)
            ip_str = str(ip)
            ts_int = int(ts)
            cc_val = str(cc) if cc is not None else None
            if email_str not in user_logs:
                user_logs[email_str] = []
            user_logs[email_str].append({'ip': ip_str, 'ts': ts_int, 'cc': cc_val})

        suspicious_users: list[SuspiciousUser] = []
        window = 60  # 60 seconds

        for email, logs in user_logs.items():
            logs.sort(key=lambda x: x['ts'])

            detected_ips: set[tuple[str, Optional[str]]] = set()
            start = 0
            current_window_ips: dict[str, int] = {}
            has_suspicious = False
            intensity_score = 0

            for end in range(len(logs)):
                current_log = logs[end]
                ip = current_log['ip']
                current_window_ips[ip] = current_window_ips.get(ip, 0) + 1

                while logs[end]['ts'] - logs[start]['ts'] > window:
                    remove_ip = logs[start]['ip']
                    current_window_ips[remove_ip] -= 1
                    if current_window_ips[remove_ip] == 0:
                        del current_window_ips[remove_ip]
                    start += 1

                if len(current_window_ips) > 1:
                    has_suspicious = True
                    intensity_score += 1
                    for ip_key in current_window_ips:
                        # Find cc for this IP from current logs
                        # (Optimized: we could cache this, but searching small list is fine)
                        cc = next((log_entry['cc'] for log_entry in logs if log_entry['ip'] == ip_key), None)
                        detected_ips.add((ip_key, cc))

            if has_suspicious:
                # Use intensity_score as 'minutes' count equivalent
                # To prevent crazy high numbers, we can cap it or scale it.
                # But 'count' in DB is just an integer. Let's use 1 per detection cycle
                # plus a fraction of intensity to show severity?
                # Or just use 1 to keep it simple and consistent with "events".
                # Actually, let's use a minimum of 1.

                suspicious_users.append({
                    'email': email,
                    'ips': detected_ips,
                    'minutes': max(1, intensity_score // 5) # Heuristic: 5 suspicious logs ~ 1 "unit" of suspicion
                })

        # Save to DB
        current_time = int(time.time())

        for user in suspicious_users:
            email = user['email']
            # Format IPs string
            ip_lines = []
            for ip, cc in user['ips']:
                flag = get_flag_emoji(cc)
                ip_lines.append(f"{flag} {ip}")
            ip_str = ", ".join(ip_lines)

            # Check if event exists for this user recently (e.g. last 30 mins) to avoid spamming DB
            # If exists, update 'last_seen' and increment 'count'
            # If IPs changed, maybe create new? Let's just update for simplicity.

            recent_threshold = current_time - 1800 # 30 mins

            cursor.execute("SELECT id, count, ips FROM suspicious_events WHERE email=? AND last_seen > ?", (email, recent_threshold))
            existing = cursor.fetchone()

            if existing:
                # Update
                eid, count, old_ips = existing
                # Merge IPs if new ones appeared
                # Simple logic: overwrite with latest detected set (or merge strings, but that's messy)
                # Let's overwrite IPs with the current detected set as it's the latest state.
                # Or better: merge unique IPs.

                # We can't easily parse old_ips back to set without regex.
                # Let's just update last_seen and count.
                cursor.execute("UPDATE suspicious_events SET last_seen=?, count=count+?, ips=? WHERE id=?", (current_time, user['minutes'], ip_str, eid))
            else:
                # Insert New
                cursor.execute("INSERT INTO suspicious_events (email, ips, timestamp, last_seen, count) VALUES (?, ?, ?, ?, ?)",
                               (email, ip_str, current_time, current_time, user['minutes']))

        conn.commit()
        conn.close()

    except Exception as e:
        logging.error(f"Error in detect_suspicious_activity: {e}")

def register_handlers(application):
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CallbackQueryHandler(set_language, pattern='^set_lang_'))
    application.add_handler(CallbackQueryHandler(change_lang, pattern='^change_lang$'))
    application.add_handler(CallbackQueryHandler(shop, pattern='^shop$'))
    application.add_handler(CallbackQueryHandler(mobile_menu, pattern='^mobile_menu$'))
    application.add_handler(CallbackQueryHandler(mobile_shop, pattern='^mobile_shop$'))
    application.add_handler(CallbackQueryHandler(how_to_buy_stars, pattern='^how_to_buy_stars$'))
    application.add_handler(CallbackQueryHandler(back_to_main, pattern='^back_to_main$'))
    application.add_handler(CallbackQueryHandler(initiate_payment, pattern='^buy_'))
    application.add_handler(CallbackQueryHandler(get_config, pattern='^get_config$'))
    application.add_handler(CallbackQueryHandler(mobile_config, pattern='^mobile_config$'))
    application.add_handler(CallbackQueryHandler(mobile_stats, pattern='^mobile_stats$'))
    application.add_handler(CallbackQueryHandler(show_mobile_qrcode, pattern='^mobile_show_qrcode$'))
    application.add_handler(CallbackQueryHandler(ru_bridge_config, pattern='^ru_bridge_config$'))
    application.add_handler(CallbackQueryHandler(user_locations_menu, pattern='^user_locations$'))
    application.add_handler(CallbackQueryHandler(user_location_select, pattern='^user_location_'))
    application.add_handler(CallbackQueryHandler(stats, pattern='^stats$'))
    application.add_handler(CallbackQueryHandler(try_trial, pattern='^try_trial$'))
    application.add_handler(CallbackQueryHandler(try_trial_3d, pattern='^try_trial_3d$'))
    application.add_handler(CallbackQueryHandler(try_trial_mobile, pattern='^try_trial_mobile$'))
    application.add_handler(CallbackQueryHandler(enter_promo, pattern='^enter_promo$'))
    application.add_handler(CallbackQueryHandler(referral, pattern='^referral$'))
    application.add_handler(CallbackQueryHandler(my_referrals, pattern='^my_referrals$'))
    application.add_handler(CallbackQueryHandler(show_qrcode, pattern='^show_qrcode$'))
    application.add_handler(CallbackQueryHandler(instructions, pattern='^instructions$'))
    application.add_handler(CallbackQueryHandler(show_instruction, pattern='^instr_'))

    application.add_handler(CommandHandler('admin', admin_panel))
    application.add_handler(CommandHandler('health', admin_health))
    application.add_handler(CallbackQueryHandler(admin_panel, pattern='^admin_panel$'))
    application.add_handler(CallbackQueryHandler(admin_remote_panels, pattern='^admin_remote_panels$'))
    application.add_handler(CallbackQueryHandler(admin_remote_panels_add, pattern='^admin_remote_panels_add$'))
    application.add_handler(CallbackQueryHandler(admin_remote_panels_list, pattern='^admin_remote_panels_list$'))
    application.add_handler(CallbackQueryHandler(admin_remote_panels_check, pattern='^admin_remote_panels_check$'))
    application.add_handler(CallbackQueryHandler(admin_remote_panels_delete, pattern='^admin_remote_panels_del_'))
    application.add_handler(CallbackQueryHandler(admin_remote_locations, pattern='^admin_remote_locations$'))
    application.add_handler(CallbackQueryHandler(admin_remote_locations_add, pattern='^admin_remote_locations_add$'))
    application.add_handler(CallbackQueryHandler(admin_remote_locations_list, pattern='^admin_remote_locations_list$'))
    application.add_handler(CallbackQueryHandler(admin_remote_locations_check, pattern='^admin_remote_locations_check$'))
    application.add_handler(CallbackQueryHandler(admin_remote_locations_delete, pattern='^admin_remote_locations_del_'))
    application.add_handler(CallbackQueryHandler(admin_remote_nodes, pattern='^admin_remote_nodes$'))
    application.add_handler(CallbackQueryHandler(admin_remote_nodes_add, pattern='^admin_remote_nodes_add$'))
    application.add_handler(CallbackQueryHandler(admin_remote_nodes_list, pattern='^admin_remote_nodes_list$'))
    application.add_handler(CallbackQueryHandler(admin_remote_nodes_check, pattern='^admin_remote_nodes_check$'))
    application.add_handler(CallbackQueryHandler(admin_remote_nodes_sync_menu, pattern='^admin_remote_nodes_sync_menu$'))
    application.add_handler(CallbackQueryHandler(admin_remote_nodes_sync_action, pattern='^admin_remote_nodes_sync_'))
    application.add_handler(CallbackQueryHandler(admin_remote_nodes_delete, pattern='^admin_remote_nodes_del_'))
    application.add_handler(CallbackQueryHandler(admin_health, pattern='^admin_health$'))
    application.add_handler(CallbackQueryHandler(admin_stats, pattern='^admin_stats$'))
    application.add_handler(CallbackQueryHandler(admin_cleanup_db, pattern='^admin_cleanup_db$'))
    application.add_handler(CallbackQueryHandler(admin_db_audit, pattern='^admin_db_audit$'))
    application.add_handler(CallbackQueryHandler(admin_db_sync_confirm, pattern='^admin_db_sync_confirm$'))
    application.add_handler(CallbackQueryHandler(admin_db_sync_all, pattern='^admin_db_sync_all$'))
    application.add_handler(CallbackQueryHandler(admin_sync_nicknames, pattern='^admin_sync_nicks$'))
    application.add_handler(CallbackQueryHandler(admin_sync_mobile_nicknames, pattern='^admin_sync_mobile_nicks$'))
    application.add_handler(CallbackQueryHandler(admin_server, pattern='^admin_server$'))
    application.add_handler(CallbackQueryHandler(admin_server_live, pattern='^admin_server_live$'))
    application.add_handler(CallbackQueryHandler(admin_server_nodes, pattern='^admin_server_nodes$'))
    application.add_handler(CallbackQueryHandler(admin_server_node_detail, pattern='^admin_server_node_'))
    application.add_handler(CallbackQueryHandler(admin_update_xui_xray, pattern='^admin_update_xui_xray$'))
    application.add_handler(CallbackQueryHandler(admin_rebind_user, pattern='^admin_rebind_'))
    application.add_handler(CallbackQueryHandler(admin_users_list, pattern='^admin_users_'))
    application.add_handler(CallbackQueryHandler(admin_user_detail, pattern='^admin_u_'))
    application.add_handler(CallbackQueryHandler(admin_reset_trial, pattern='^admin_reset_trial_'))
    application.add_handler(CallbackQueryHandler(admin_prices, pattern='^admin_prices$'))
    application.add_handler(CallbackQueryHandler(admin_edit_price, pattern='^admin_edit_price_'))
    application.add_handler(CallbackQueryHandler(admin_new_promo, pattern='^admin_new_promo$'))
    application.add_handler(CallbackQueryHandler(admin_promos_menu, pattern='^admin_promos_menu$'))
    application.add_handler(CallbackQueryHandler(admin_promo_list, pattern='^admin_promo_list$'))
    application.add_handler(CallbackQueryHandler(admin_promo_uses, pattern='^admin_promo_uses_'))
    application.add_handler(CallbackQueryHandler(admin_promo_user_detail, pattern='^admin_promo_u_'))
    application.add_handler(CallbackQueryHandler(admin_revoke_promo_code_menu, pattern='^admin_revoke_code_menu_'))
    application.add_handler(CallbackQueryHandler(admin_revoke_promo_code_action, pattern='^admin_revoke_code_act_'))
    application.add_handler(CallbackQueryHandler(admin_revoke_user_promo_menu, pattern='^admin_revoke_user_menu_'))
    application.add_handler(CallbackQueryHandler(admin_revoke_user_promo_confirm, pattern='^admin_revoke_user_conf_'))
    application.add_handler(CallbackQueryHandler(admin_revoke_user_promo_action, pattern='^admin_revoke_user_act_'))
    application.add_handler(CallbackQueryHandler(admin_broadcast, pattern='^admin_broadcast$'))
    application.add_handler(CallbackQueryHandler(admin_broadcast_target, pattern='^admin_broadcast_(all|en|ru|individual|toggle|page|confirm).*'))
    application.add_handler(CallbackQueryHandler(admin_poll_menu, pattern='^admin_poll_menu$'))
    application.add_handler(CallbackQueryHandler(admin_poll_new, pattern='^admin_poll_new$'))
    application.add_handler(CallbackQueryHandler(admin_poll_send, pattern='^admin_poll_send$'))
    application.add_handler(CallbackQueryHandler(handle_poll_vote, pattern='^poll_vote_'))
    application.add_handler(CallbackQueryHandler(handle_poll_refresh, pattern='^poll_refresh_'))
    application.add_handler(CallbackQueryHandler(admin_sales_log, pattern='^admin_sales_log$'))
    application.add_handler(CallbackQueryHandler(admin_backup_menu, pattern='^admin_backup_menu$'))
    application.add_handler(CallbackQueryHandler(admin_create_backup, pattern='^admin_create_backup$'))
    application.add_handler(CallbackQueryHandler(admin_restore_menu, pattern=r'^admin_restore_menu(_\d+)?$'))
    application.add_handler(CallbackQueryHandler(admin_restore_select, pattern='^admin_restore_sel_'))
    application.add_handler(CallbackQueryHandler(admin_restore_confirm, pattern='^admin_restore_do_'))
    application.add_handler(CallbackQueryHandler(admin_restart_xui, pattern='^admin_restart_xui$'))
    application.add_handler(CallbackQueryHandler(admin_restart_bot, pattern='^admin_restart_bot$'))
    application.add_handler(CallbackQueryHandler(admin_backup_delete_do, pattern='^admin_backup_del_do_'))
    application.add_handler(CallbackQueryHandler(admin_backup_delete_confirm, pattern='^admin_backup_del_'))
    application.add_handler(CallbackQueryHandler(admin_restore_uploaded_as_xui, pattern='^admin_upload_restore_xui$'))
    application.add_handler(CallbackQueryHandler(admin_restore_uploaded_as_bot, pattern='^admin_upload_restore_bot$'))
    application.add_handler(CallbackQueryHandler(admin_restore_uploaded_do_xui, pattern='^admin_upload_restore_do_xui$'))
    application.add_handler(CallbackQueryHandler(admin_restore_uploaded_do_bot, pattern='^admin_upload_restore_do_bot$'))
    application.add_handler(CallbackQueryHandler(admin_view_logs, pattern='^admin_logs$'))
    application.add_handler(CallbackQueryHandler(admin_clear_logs, pattern='^admin_clear_logs$'))

    application.add_handler(CallbackQueryHandler(admin_search_user, pattern='^admin_search_user$'))
    application.add_handler(CallbackQueryHandler(admin_db_detail_callback, pattern='^admin_db_detail_'))
    application.add_handler(CallbackQueryHandler(admin_reset_trial_db, pattern='^admin_rt_db_'))
    application.add_handler(CallbackQueryHandler(admin_delete_user_db, pattern='^admin_del_db_'))
    application.add_handler(CallbackQueryHandler(admin_delete_client_ask, pattern='^admin_del_client_ask_'))
    application.add_handler(CallbackQueryHandler(admin_delete_client_confirm, pattern='^admin_del_client_confirm_'))
    application.add_handler(CallbackQueryHandler(admin_edit_limit_ip, pattern='^admin_edit_limit_ip_'))
    application.add_handler(CallbackQueryHandler(admin_ip_history, pattern='^admin_ip_history_'))
    application.add_handler(CallbackQueryHandler(admin_suspicious_users, pattern='^admin_suspicious.*'))
    application.add_handler(CallbackQueryHandler(admin_leaderboard, pattern='^admin_leaderboard'))

    application.add_handler(CallbackQueryHandler(admin_flash_menu, pattern='^admin_flash_menu$'))
    application.add_handler(CallbackQueryHandler(admin_flash_select, pattern='^admin_flash_sel_'))
    application.add_handler(CallbackQueryHandler(admin_flash_delete_all, pattern='^admin_flash_delete_all$'))
    application.add_handler(CallbackQueryHandler(admin_flash_errors, pattern='^admin_flash_errors$'))

    application.add_handler(CallbackQueryHandler(support_menu, pattern='^support_menu$'))

    application.add_handler(MessageHandler(~filters.COMMAND & ~filters.SUCCESSFUL_PAYMENT, handle_message))

    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

SUPPORT_BOT_TOKEN = os.getenv("SUPPORT_BOT_TOKEN")

_ERROR_NOTIFY_LAST: dict[str, float] = {}
_ERROR_NOTIFY_SUPPRESSED: dict[str, int] = {}
_ERROR_NOTIFY_INTERVAL_SEC = int(os.getenv("ERROR_NOTIFY_INTERVAL_SEC", "60"))
_ERROR_DIGEST_INTERVAL_SEC = int(os.getenv("ERROR_DIGEST_INTERVAL_SEC", "86400"))
_ERROR_DIGEST_FIRST_SEC = int(os.getenv("ERROR_DIGEST_FIRST_SEC", "3600"))
_ERROR_DIGEST_TOP = int(os.getenv("ERROR_DIGEST_TOP", "10"))

_MONITOR_ENABLED = int(os.getenv("MONITOR_ENABLED", "1"))
_MONITOR_INTERVAL_SEC = int(os.getenv("MONITOR_INTERVAL_SEC", "60"))
_MONITOR_ALERT_COOLDOWN_SEC = int(os.getenv("MONITOR_ALERT_COOLDOWN_SEC", "900"))

_ONLINE_SPIKE_ABS = int(os.getenv("ONLINE_SPIKE_ABS", "50"))
_ONLINE_SPIKE_FACTOR = float(os.getenv("ONLINE_SPIKE_FACTOR", "3.0"))
_ONLINE_EMA_ALPHA = float(os.getenv("ONLINE_EMA_ALPHA", "0.2"))

_TRAFFIC_SPIKE_BPS = int(os.getenv("TRAFFIC_SPIKE_BPS", str(50 * 1024 * 1024)))
_TRAFFIC_SPIKE_FACTOR = float(os.getenv("TRAFFIC_SPIKE_FACTOR", "4.0"))
_TRAFFIC_SPIKE_MIN_BPS = int(os.getenv("TRAFFIC_SPIKE_MIN_BPS", str(5 * 1024 * 1024)))
_TRAFFIC_EMA_ALPHA = float(os.getenv("TRAFFIC_EMA_ALPHA", "0.2"))

_PAYMENT_ERROR_THRESHOLD = int(os.getenv("PAYMENT_ERROR_THRESHOLD", "3"))
_PAYMENT_ERROR_WINDOW_SEC = int(os.getenv("PAYMENT_ERROR_WINDOW_SEC", "900"))

_DAILY_REPORT_ENABLED = int(os.getenv("DAILY_REPORT_ENABLED", "1"))
_DAILY_REPORT_FIRST_SEC = int(os.getenv("DAILY_REPORT_FIRST_SEC", "21600"))

class ErrorDigestEntry(TypedDict):
    count: int
    first_ts: float
    last_ts: float
    last_summary: str
    err_head: str


_ERROR_DIGEST: dict[str, ErrorDigestEntry] = {}
_ERROR_DIGEST_SINCE_TS = time.time()

_MONITOR_LAST_ALERT_TS: dict[str, float] = {}
_MONITOR_NET_LAST: tuple[int, int, float] | None = None
_MONITOR_TRAFFIC_EMA_BPS: float | None = None
_MONITOR_ONLINE_EMA: float | None = None

_MONITOR_PAYMENT_ERRORS: deque[float] = deque(maxlen=5000)


def _record_admin_delivery_error(error: Exception, text: str) -> None:
    try:
        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()
        message = f"{str(error)} | {text[:500]}"
        cursor.execute(
            "INSERT INTO flash_delivery_errors (user_id, error_message, timestamp) VALUES (?, ?, ?)",
            ("admin_notify", message, int(time.time()))
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


async def _send_admin_message(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    if not ADMIN_ID:
        return

    application = getattr(context, "application", None)
    support_bot = None
    if application is not None:
        bot_data = getattr(application, "bot_data", None)
        if isinstance(bot_data, dict):
            support_bot = bot_data.get("support_bot")

    async def _try_send(bot) -> tuple[bool, Exception | None]:
        if not bot:
            return False, None
        try:
            await bot.send_message(chat_id=ADMIN_ID, text=text, parse_mode='Markdown')
            return True, None
        except BadRequest:
            try:
                await bot.send_message(chat_id=ADMIN_ID, text=text)
                return True, None
            except Exception as e:
                return False, e
        except Exception as e:
            return False, e

    ok, err = await _try_send(support_bot)
    if ok:
        return

    ok, err = await _try_send(context.bot)
    if ok:
        return

    if err is None:
        err = RuntimeError("admin notify failed: no bot available")

    logging.error(f"Failed to send admin message: {err}")
    log_action(f"ERROR: Failed to send admin message: {err}")
    _record_admin_delivery_error(err, text)


def _monitor_can_alert(key: str, now: float) -> bool:
    last = _MONITOR_LAST_ALERT_TS.get(key)
    if last is None:
        return True
    return (now - last) >= max(_MONITOR_ALERT_COOLDOWN_SEC, 0)


def _monitor_mark_alert(key: str, now: float) -> None:
    _MONITOR_LAST_ALERT_TS[key] = now


def _record_payment_error(ts: float | None = None) -> None:
    _MONITOR_PAYMENT_ERRORS.append(float(time.time() if ts is None else ts))


def _get_online_users_count() -> int:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        current_time_ms = int(time.time() * 1000)
        threshold = current_time_ms - (10 * 1000)
        cursor.execute(
            "SELECT COUNT(DISTINCT email) FROM client_traffics WHERE inbound_id=? AND last_online > ?",
            (INBOUND_ID, threshold),
        )
        row = cursor.fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


async def monitor_thresholds_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    global _MONITOR_NET_LAST
    global _MONITOR_TRAFFIC_EMA_BPS
    global _MONITOR_ONLINE_EMA

    if not ADMIN_ID:
        return
    if _MONITOR_ENABLED <= 0:
        return

    now = time.time()

    online = _get_online_users_count()
    if _MONITOR_ONLINE_EMA is None:
        _MONITOR_ONLINE_EMA = float(online)
    else:
        alpha = max(0.0, min(_ONLINE_EMA_ALPHA, 1.0))
        _MONITOR_ONLINE_EMA = (alpha * float(online)) + ((1.0 - alpha) * _MONITOR_ONLINE_EMA)

    traffic_bps = 0.0
    try:
        rx, tx = get_net_io_counters()
        if _MONITOR_NET_LAST is not None:
            last_rx, last_tx, last_ts = _MONITOR_NET_LAST
            dt = now - last_ts
            if dt > 0:
                traffic_bps = float((rx - last_rx) + (tx - last_tx)) / dt
        _MONITOR_NET_LAST = (int(rx), int(tx), now)
    except Exception:
        pass

    if _MONITOR_TRAFFIC_EMA_BPS is None:
        _MONITOR_TRAFFIC_EMA_BPS = float(traffic_bps)
    else:
        alpha = max(0.0, min(_TRAFFIC_EMA_ALPHA, 1.0))
        _MONITOR_TRAFFIC_EMA_BPS = (alpha * float(traffic_bps)) + ((1.0 - alpha) * _MONITOR_TRAFFIC_EMA_BPS)

    if _ONLINE_SPIKE_ABS > 0 and _ONLINE_SPIKE_FACTOR > 0 and _MONITOR_ONLINE_EMA is not None:
        if (
            online >= _ONLINE_SPIKE_ABS
            and float(online) >= (_MONITOR_ONLINE_EMA * _ONLINE_SPIKE_FACTOR)
            and _monitor_can_alert("online_spike", now)
        ):
            admin_lang = get_lang(ADMIN_ID)
            if admin_lang == "ru":
                msg = (
                    "🚨 *Алерт: всплеск онлайна*\n\n"
                    f"Онлайн сейчас: *{online}*\n"
                    f"База (EMA): *{_MONITOR_ONLINE_EMA:.1f}*\n"
                    f"Порог: *{_ONLINE_SPIKE_ABS}* и ×{_ONLINE_SPIKE_FACTOR}\n"
                    f"Время: {datetime.datetime.now(TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')}"
                )
            else:
                msg = (
                    "🚨 *Alert: online spike*\n\n"
                    f"Online now: *{online}*\n"
                    f"Baseline (EMA): *{_MONITOR_ONLINE_EMA:.1f}*\n"
                    f"Threshold: *{_ONLINE_SPIKE_ABS}* and ×{_ONLINE_SPIKE_FACTOR}\n"
                    f"Time: {datetime.datetime.now(TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')}"
                )
            _monitor_mark_alert("online_spike", now)
            await _send_admin_message(context, msg)

    if _TRAFFIC_SPIKE_BPS > 0 and _monitor_can_alert("traffic_spike", now):
        over_abs = traffic_bps >= float(_TRAFFIC_SPIKE_BPS)
        over_factor = False
        if _TRAFFIC_SPIKE_FACTOR > 0 and _MONITOR_TRAFFIC_EMA_BPS is not None:
            if traffic_bps >= float(_TRAFFIC_SPIKE_MIN_BPS) and traffic_bps >= (_MONITOR_TRAFFIC_EMA_BPS * _TRAFFIC_SPIKE_FACTOR):
                over_factor = True
        if over_abs or over_factor:
            admin_lang = get_lang(ADMIN_ID)
            if admin_lang == "ru":
                msg = (
                    "🚨 *Алерт: аномальный трафик*\n\n"
                    f"Скорость: *{format_bytes(float(traffic_bps))}/s*\n"
                    f"База (EMA): *{format_bytes(float(_MONITOR_TRAFFIC_EMA_BPS or 0.0))}/s*\n"
                    f"Порог: *{format_bytes(float(_TRAFFIC_SPIKE_BPS))}/s* или ×{_TRAFFIC_SPIKE_FACTOR}\n"
                    f"Время: {datetime.datetime.now(TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')}"
                )
            else:
                msg = (
                    "🚨 *Alert: abnormal traffic*\n\n"
                    f"Speed: *{format_bytes(float(traffic_bps))}/s*\n"
                    f"Baseline (EMA): *{format_bytes(float(_MONITOR_TRAFFIC_EMA_BPS or 0.0))}/s*\n"
                    f"Threshold: *{format_bytes(float(_TRAFFIC_SPIKE_BPS))}/s* or ×{_TRAFFIC_SPIKE_FACTOR}\n"
                    f"Time: {datetime.datetime.now(TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')}"
                )
            _monitor_mark_alert("traffic_spike", now)
            await _send_admin_message(context, msg)

    if _PAYMENT_ERROR_THRESHOLD > 0 and _PAYMENT_ERROR_WINDOW_SEC > 0:
        try:
            cutoff = now - float(_PAYMENT_ERROR_WINDOW_SEC)
            while _MONITOR_PAYMENT_ERRORS and float(_MONITOR_PAYMENT_ERRORS[0]) < cutoff:
                _MONITOR_PAYMENT_ERRORS.popleft()
            err_count = len(_MONITOR_PAYMENT_ERRORS)
        except Exception:
            err_count = 0

        if err_count >= _PAYMENT_ERROR_THRESHOLD and _monitor_can_alert("payment_errors", now):
            admin_lang = get_lang(ADMIN_ID)
            window_min = int(_PAYMENT_ERROR_WINDOW_SEC / 60)
            if admin_lang == "ru":
                msg = (
                    "🚨 *Алерт: массовые ошибки оплаты*\n\n"
                    f"Ошибок за последние {window_min} мин: *{err_count}*\n"
                    f"Порог: *{_PAYMENT_ERROR_THRESHOLD}*\n"
                    f"Время: {datetime.datetime.now(TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')}"
                )
            else:
                msg = (
                    "🚨 *Alert: mass payment errors*\n\n"
                    f"Errors in last {window_min} min: *{err_count}*\n"
                    f"Threshold: *{_PAYMENT_ERROR_THRESHOLD}*\n"
                    f"Time: {datetime.datetime.now(TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')}"
                )
            _monitor_mark_alert("payment_errors", now)
            try:
                _MONITOR_PAYMENT_ERRORS.clear()
            except Exception:
                pass
            await _send_admin_message(context, msg)

def _fmt_dt(ts: float) -> str:
    try:
        return datetime.datetime.fromtimestamp(ts, tz=TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(int(ts))


async def send_error_digest_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    global _ERROR_DIGEST_SINCE_TS

    if not ADMIN_ID:
        return
    if _ERROR_DIGEST_INTERVAL_SEC <= 0:
        return
    if not _ERROR_DIGEST:
        _ERROR_DIGEST_SINCE_TS = time.time()
        return

    items = list(_ERROR_DIGEST.items())
    items.sort(key=lambda kv: kv[1]["count"], reverse=True)
    top_n = _ERROR_DIGEST_TOP if _ERROR_DIGEST_TOP > 0 else 10

    now = time.time()
    header = f"BOT ERROR DIGEST since {_fmt_dt(_ERROR_DIGEST_SINCE_TS)}"
    lines: list[str] = [header]
    for sig, data in items[:top_n]:
        count = data["count"]
        last_ts = data["last_ts"]
        last_summary = data["last_summary"][:200]
        err_head = data["err_head"][:200]
        lines.append(f"- {count}x {sig[:200]}")
        if err_head:
            lines.append(f"  head: {err_head}")
        if last_summary:
            lines.append(f"  last: {last_summary}")
        if last_ts > 0:
            lines.append(f"  at: {_fmt_dt(last_ts)}")

    payload = "\n".join(lines)[:3900]
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=payload)
    except Exception as e:
        logging.error(f"Failed to send error digest: {e}")
        return

    _ERROR_DIGEST.clear()
    _ERROR_DIGEST_SINCE_TS = now


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    import traceback

    try:
        err = getattr(context, "error", None)
        err_text = "".join(traceback.format_exception(type(err), err, err.__traceback__)) if err else "Unknown error"
        logging.error(err_text)
        if "successful_payment" in err_text or "precheckout_callback" in err_text:
            _record_payment_error()

        now = time.time()
        sig = f"{type(err).__name__}:{str(err)}" if err else "UnknownError"
        summary_parts: list[str] = []
        if isinstance(update, Update):
            user = update.effective_user
            chat = update.effective_chat
            if user is not None:
                summary_parts.append(f"user_id={user.id}")
                if user.username:
                    summary_parts.append(f"username=@{user.username}")
            if chat is not None:
                summary_parts.append(f"chat_id={chat.id}")
            if update.callback_query is not None and update.callback_query.data:
                summary_parts.append(f"callback={update.callback_query.data[:200]}")
            if update.message is not None and update.message.text:
                summary_parts.append(f"text={update.message.text[:200]}")

        summary = " ".join(summary_parts) if summary_parts else "update=unknown"
        try:
            entry = _ERROR_DIGEST.get(sig)
            if entry is None:
                _ERROR_DIGEST[sig] = {
                    "count": 1,
                    "first_ts": now,
                    "last_ts": now,
                    "last_summary": summary,
                    "err_head": err_text.splitlines()[0] if err_text else "",
                }
            else:
                entry["count"] = entry["count"] + 1
                entry["last_ts"] = now
                entry["last_summary"] = summary
                if not entry["err_head"]:
                    entry["err_head"] = err_text.splitlines()[0] if err_text else ""
            if len(_ERROR_DIGEST) > 1000:
                _ERROR_DIGEST.clear()
                _ERROR_DIGEST_SINCE_TS = now
        except Exception:
            pass

        last = _ERROR_NOTIFY_LAST.get(sig)
        if last is not None and now - last < _ERROR_NOTIFY_INTERVAL_SEC:
            _ERROR_NOTIFY_SUPPRESSED[sig] = int(_ERROR_NOTIFY_SUPPRESSED.get(sig, 0)) + 1
            if len(_ERROR_NOTIFY_LAST) > 500:
                _ERROR_NOTIFY_LAST.clear()
                _ERROR_NOTIFY_SUPPRESSED.clear()
            return
        _ERROR_NOTIFY_LAST[sig] = now
        suppressed = int(_ERROR_NOTIFY_SUPPRESSED.pop(sig, 0))

        suppressed_line = ""
        if suppressed > 0:
            suppressed_line = f"\n\nsuppressed={suppressed} window={_ERROR_NOTIFY_INTERVAL_SEC}s"
        payload = f"BOT ERROR\n{summary}\n\n{err_text}{suppressed_line}"
        payload = payload[:3900]

        if not ADMIN_ID:
            return
        try:
            application = getattr(context, "application", None)
            if application is None:
                return
            await application.bot.send_message(chat_id=ADMIN_ID, text=payload)
        except Exception as send_ex:
            logging.error(f"Failed to notify admin about error: {send_ex}")
    except Exception as ex:
        logging.error(f"global_error_handler failed: {ex}")

async def admin_bot_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler for /start command in Support Bot.
    Shows status and info for Admin.
    """
    if ADMIN_ID_INT is None or update.message.chat_id != ADMIN_ID_INT:
        await update.message.reply_text("⛔ Доступ запрещен. Этот бот только для администратора.")
        return

    text = (
        "🤖 *Панель Поддержки (Admin Side)*\n\n"
        "✅ Бот активен и готов пересылать сообщения.\n"
        "📩 Все тикеты от пользователей будут приходить сюда.\n\n"
        "ℹ️ *Как отвечать:*\n"
        "Просто сделайте **Reply (Ответить)** на сообщение пользователя, чтобы отправить ему ответ.\n\n"
        f"🆔 Ваш Admin ID: `{ADMIN_ID}`"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

async def admin_bot_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles replies from Admin in the Admin Bot.
    Should forward text to User via Main Bot.
    """
    if ADMIN_ID_INT is None or update.message.chat_id != ADMIN_ID_INT:
        return

    # Check if replying to an alert
    if update.message.reply_to_message:
        reply_text = update.message.reply_to_message.caption or update.message.reply_to_message.text or ""
        import re
        # Look for (`123456789`) pattern
        match = re.search(r'\(`(\d+)`\)', reply_text)
        if match:
            target_user_id = match.group(1)
            text_to_send = update.message.text or ""

            # If photo
            photo = update.message.photo[-1].file_id if update.message.photo else None

            if not text_to_send and not photo:
                return

            try:
                # Use Main Bot instance to send message
                # We need access to main_application.bot
                main_bot = context.bot_data.get('main_bot')
                if main_bot:
                    target_lang = get_lang(target_user_id)

                    if text_to_send:
                        reply_body = t("support_reply_template", target_lang).format(text=text_to_send)
                        await main_bot.send_message(chat_id=target_user_id, text=reply_body, parse_mode='Markdown')

                    if photo:
                        caption = t("support_reply_template", target_lang).format(text="") if not text_to_send else None
                        await main_bot.send_photo(chat_id=target_user_id, photo=photo, caption=caption, parse_mode='Markdown')

                    await update.message.reply_text(t("admin_reply_sent", "ru"))
                else:
                    await update.message.reply_text("❌ Error: Main bot not linked.")

            except Exception as e:
                await update.message.reply_text(f"❌ Failed to send reply: {e}")

async def check_missed_transactions(context: ContextTypes.DEFAULT_TYPE):
    """
    Background task to check for missing Star transactions (every minute).
    Recovers payments that were successful in Telegram but missing in local DB.
    """
    try:
        # 1. Fetch recent Star Transactions from Telegram
        # We fetch last 20 to be efficient.
        try:
            # Try using the wrapper method if available (v20+)
            result = await context.bot.get_star_transactions(limit=20)
            # In PTB v21+, returns StarTransactions object which has .transactions list
            if hasattr(result, 'transactions'):
                txs = list(result.transactions)
            else:
                txs = list(result)
        except Exception:
             # Fallback: Try raw API if wrapper fails or method not found
             return

        if not txs:
            return

        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()

        current_prices = get_prices()

        for tx in txs:
            # Filter for incoming payments (source is User)
            if not tx.source:
                continue

            # source might be User object or Chat object, or just ID if using some library versions
            # In python-telegram-bot v20+, StarTransaction.source is TransactionPartnerUser or similar.

            tg_id = None
            if hasattr(tx.source, 'user'):
                # TransactionPartnerUser(user=User(...))
                tg_id = str(tx.source.user.id)
            elif hasattr(tx.source, 'id'):
                tg_id = str(tx.source.id)

            if not tg_id:
                continue

            amount = tx.amount
            date = int(tx.date.timestamp())
            charge_id = _normalize_charge_id(getattr(tx, "id", None))
            if not charge_id:
                continue

            # Safety: Skip very recent transactions (< 60s) to avoid race with webhook
            if (time.time() - date) < 60:
                continue

            cursor.execute(
                "SELECT processed_at, plan_id FROM transactions WHERE telegram_payment_charge_id=? LIMIT 1",
                (charge_id,),
            )
            existing_row = cursor.fetchone()
            if existing_row and existing_row[0]:
                continue

            if not existing_row:
                reconcile_window_sec_raw = os.getenv("MISSED_TX_RECONCILE_WINDOW_SEC", "7200")
                try:
                    reconcile_window_sec = max(0, int(reconcile_window_sec_raw))
                except ValueError:
                    reconcile_window_sec = 7200

                if reconcile_window_sec > 0:
                    try:
                        cursor.execute(
                            "SELECT id, plan_id "
                            "FROM transactions "
                            "WHERE tg_id=? AND amount=? "
                            "AND (telegram_payment_charge_id IS NULL OR telegram_payment_charge_id='') "
                            "AND date BETWEEN ? AND ? "
                            "ORDER BY ABS(date - ?) ASC "
                            "LIMIT 1",
                            (
                                tg_id,
                                amount,
                                date - reconcile_window_sec,
                                date + reconcile_window_sec,
                                date,
                            ),
                        )
                        candidate = cursor.fetchone()
                        if candidate:
                            candidate_id, candidate_plan_id = candidate
                            cursor.execute(
                                "UPDATE transactions SET telegram_payment_charge_id=? WHERE id=? "
                                "AND (telegram_payment_charge_id IS NULL OR telegram_payment_charge_id='')",
                                (charge_id, candidate_id),
                            )
                            conn.commit()
                            cursor.execute(
                                "SELECT processed_at, plan_id FROM transactions WHERE telegram_payment_charge_id=? LIMIT 1",
                                (charge_id,),
                            )
                            existing_row = cursor.fetchone()
                    except sqlite3.OperationalError:
                        pass

            should_extend = not existing_row

            now_sec = time.time()
            last_logged = _MISSED_TX_LOG_THROTTLE.get(str(charge_id))
            if should_extend and (last_logged is None or (now_sec - last_logged) >= 300):
                log_action(
                    f"WARNING: Found MISSING/UNPROCESSED payment: "
                    f"User {tg_id}, Amount {amount}, Date {date}, Charge {charge_id}. Recovering..."
                )
                _MISSED_TX_LOG_THROTTLE[str(charge_id)] = now_sec

            original_plan_id = existing_row[1] if existing_row else None
            plan_id = original_plan_id if original_plan_id else "unknown"
            if plan_id == "unknown":
                inferred = _infer_plan_id_from_amount(amount, current_prices)
                if inferred:
                    plan_id = inferred

            if plan_id == "unknown":
                if amount >= 900:
                    plan_id = "1_year"
                elif amount >= 250:
                    plan_id = "3_months"
                elif amount >= 100:
                    plan_id = "1_month"
                elif amount >= 60:
                    plan_id = "2_weeks"
                elif amount >= 40:
                    plan_id = "1_week"

            if existing_row and (not original_plan_id or original_plan_id == "unknown") and plan_id != "unknown":
                try:
                    cursor.execute(
                        "UPDATE transactions SET plan_id=? "
                        "WHERE telegram_payment_charge_id=? AND (plan_id IS NULL OR plan_id='' OR plan_id='unknown')",
                        (plan_id, charge_id),
                    )
                    conn.commit()
                except Exception:
                    pass

            if not existing_row:
                try:
                    cursor.execute(
                        "INSERT INTO transactions (tg_id, amount, date, plan_id, telegram_payment_charge_id, processed_at) "
                        "VALUES (?, ?, ?, ?, ?, NULL)",
                        (tg_id, amount, date, plan_id, charge_id),
                    )
                    conn.commit()
                except Exception as e:
                    log_action(f"ERROR saving missing tx: {e}")
                    continue

            days = 0
            if plan_id in current_prices:
                days = current_prices[plan_id]['days']
            elif plan_id == "1_year":
                days = 365
            elif plan_id == "3_months":
                days = 90
            elif plan_id == "1_month":
                days = 30
            elif plan_id == "2_weeks":
                days = 14
            elif plan_id == "1_week":
                days = 7

            if days <= 0:
                try:
                    cursor.execute(
                        "UPDATE transactions SET processed_at=? "
                        "WHERE telegram_payment_charge_id=? AND processed_at IS NULL",
                        (int(time.time()), charge_id),
                    )
                    conn.commit()
                except Exception as e:
                    log_action(f"ERROR marking unhandled tx as processed (charge_id: {charge_id}): {e}")
                continue

            if days > 0 and should_extend:
                try:
                    if plan_id == "ru_bridge":
                        new_expiry = await _add_days_ru_bridge(tg_id, days)
                        if new_expiry is None:
                            raise RuntimeError("RU-Bridge extension failed")
                    else:
                        await add_days_to_user(tg_id, days, context)
                    cursor.execute(
                        "UPDATE transactions SET processed_at=? "
                        "WHERE telegram_payment_charge_id=? AND processed_at IS NULL",
                        (int(time.time()), charge_id),
                    )
                    conn.commit()
                except Exception as e:
                    log_action(f"ERROR applying recovered tx (charge_id: {charge_id}): {e}")
                    continue

                try:
                    lang = get_lang(tg_id)
                    if plan_id == "ru_bridge":
                        msg_text = (
                            f"✅ *Payment Restored!*\n\nWe found a missing payment of {amount} Stars.\n"
                            f"Your RU-Bridge access has been extended by {days} days."
                        )
                        if lang == 'ru':
                            msg_text = (
                                f"✅ *Платеж восстановлен!*\n\nМы обнаружили потерянный платеж на {amount} Stars.\n"
                                f"Ваша RU-Bridge подписка продлена на {days} дн."
                            )
                    else:
                        msg_text = (
                            f"✅ *Payment Restored!*\n\nWe found a missing payment of {amount} Stars.\n"
                            f"Your subscription has been extended by {days} days."
                        )
                        if lang == 'ru':
                            msg_text = (
                                f"✅ *Платеж восстановлен!*\n\nМы обнаружили потерянный платеж на {amount} Stars.\n"
                                f"Ваша подписка продлена на {days} дн."
                            )

                    await context.bot.send_message(chat_id=tg_id, text=msg_text, parse_mode='Markdown')
                except Exception:
                    pass

                admin_msg = (
                    f"⚠️ **RESTORED PAYMENT**\n"
                    f"User: `{tg_id}`\n"
                    f"Amount: {amount}\n"
                    f"Plan: `{plan_id}`\n"
                    f"Added: {days} days\n"
                    f"Charge: `{charge_id}`"
                )
                await _send_admin_message(context, admin_msg)
            if days > 0 and not should_extend:
                try:
                    cursor.execute(
                        "UPDATE transactions SET processed_at=? "
                        "WHERE telegram_payment_charge_id=? AND processed_at IS NULL",
                        (int(time.time()), charge_id),
                    )
                    conn.commit()
                except Exception as e:
                    log_action(f"ERROR marking existing tx as processed (charge_id: {charge_id}): {e}")

        conn.close()

    except Exception as e:
        import traceback
        logging.error(f"Error in check_missed_transactions: {e}\n{traceback.format_exc()}")

async def check_winback_users(context: ContextTypes.DEFAULT_TYPE):
    """
    Check for users whose subscription expired 3-7 days ago and send them a promo code.
    Runs daily.

    Logic:
    1. Find users expired between 3 and 7 days ago.
    2. Check if this specific expiration event has been handled (using expiry timestamp).
    3. Ensure it wasn't just a trial expiration (check if they have payment history).
    4. Send promo.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return

        settings = json.loads(row[0])
        clients = settings.get('clients', [])

        current_time_ms = int(time.time() * 1000)
        day_ms = 24 * 3600 * 1000

        # Range: Expired between 3 and 7 days ago
        threshold_start = current_time_ms - (7 * day_ms)
        threshold_end = current_time_ms - (3 * day_ms)

        conn_bot = sqlite3.connect(BOT_DB_PATH)
        cursor_bot = conn_bot.cursor()

        # Get list of users who have EVER paid (to avoid sending win-back to trial abusers)
        cursor_bot.execute("SELECT DISTINCT tg_id FROM transactions")
        paid_users = set(row[0] for row in cursor_bot.fetchall())

        for client in clients:
            expiry = client.get('expiryTime', 0)
            tg_id = str(client.get('tgId', ''))

            if not tg_id or expiry == 0:
                continue

            # Filter 1: Must be a paid user (Retention strategy is for paying customers)
            if tg_id not in paid_users:
                continue

            # Filter 2: Check if in range (3-7 days ago)
            if threshold_start < expiry < threshold_end:

                # Filter 3: Check if THIS specific expiry event was already handled.
                # We use a unique key: winback_{expiry_timestamp}
                notification_key = f"winback_{expiry}"

                cursor_bot.execute("SELECT 1 FROM notifications WHERE tg_id=? AND type=?", (tg_id, notification_key))
                if cursor_bot.fetchone():
                    continue

                # Send Win-back
                try:
                    # Generate unique promo code
                    suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
                    code = f"WB{suffix}"

                    # Create promo in DB (3 days bonus)
                    cursor_bot.execute("INSERT OR IGNORE INTO promo_codes (code, days, max_uses) VALUES (?, ?, ?)", (code, 3, 1))

                    lang = get_lang(tg_id)
                    msg_text = (
                        "👋 **We miss you!**\n\n"
                        "Your subscription expired recently. We'd love to see you back!\n"
                        f"🎁 Here is a special gift: **3 Days Free Access**\n\n"
                        f"👇 Activate code: `{code}`"
                    )
                    if lang == 'ru':
                        msg_text = (
                            "👋 **Мы скучаем!**\n\n"
                            "Ваша подписка недавно истекла. Возвращайтесь!\n"
                            f"🎁 Ваш подарок: **3 дня бесплатно**\n\n"
                            f"👇 Активируйте код: `{code}`"
                        )

                    await context.bot.send_message(chat_id=tg_id, text=msg_text, parse_mode='Markdown')

                    # Mark as sent for THIS expiry timestamp
                    cursor_bot.execute("INSERT INTO notifications (tg_id, type, date) VALUES (?, ?, ?)",
                                       (tg_id, notification_key, int(time.time())))
                    conn_bot.commit()
                    logging.info(f"Sent Win-back to {tg_id} for expiry {expiry}")

                except Exception as e:
                    logging.error(f"Failed to send winback to {tg_id}: {e}")

        conn_bot.close()
    except Exception as e:
        logging.error(f"Error in check_winback_users: {e}")

async def main():
    init_db()
    try:
        updated = backfill_unknown_transaction_plan_ids()
        if updated > 0:
            log_action(f"Backfilled plan_id for {updated} transactions")
    except Exception:
        pass

    _start_multi_sub_server()

    # 1. Main Bot App
    request = HTTPXRequest(
        connect_timeout=10.0,
        read_timeout=20.0,
        write_timeout=20.0,
        pool_timeout=5.0,
        httpx_kwargs={"transport": httpx.AsyncHTTPTransport(local_address="0.0.0.0")},
    )
    app_main = ApplicationBuilder().token(TOKEN).request(request).post_init(post_init).build()
    app_main.add_error_handler(global_error_handler)
    register_handlers(app_main)

    # Job Queue for Main Bot
    job_queue = app_main.job_queue
    job_queue.run_repeating(check_expiring_subscriptions, interval=3600, first=10) # Changed to hourly
    job_queue.run_repeating(check_expired_trials, interval=3600, first=20) # New job
    job_queue.run_repeating(log_traffic_stats, interval=3600, first=5)
    job_queue.run_repeating(cleanup_flash_messages, interval=60, first=10)
    job_queue.run_repeating(detect_suspicious_activity, interval=300, first=30)
    job_queue.run_repeating(check_missed_transactions, interval=60, first=30)
    job_queue.run_repeating(monitor_thresholds_job, interval=_MONITOR_INTERVAL_SEC, first=60)
    if AUTO_SYNC_INTERVAL_SEC > 0:
        job_queue.run_repeating(_auto_sync_remote_nodes_job, interval=AUTO_SYNC_INTERVAL_SEC, first=30)

    # New jobs for Backup and Winback (Daily)
    # Run backup at ~4 AM (assuming start time is arbitrary, we just set interval=24h)
    job_queue.run_repeating(send_backup_to_admin_job, interval=86400, first=14400) # 24h, first run after 4h
    job_queue.run_repeating(check_winback_users, interval=86400, first=18000) # 24h, first run after 5h
    if _DAILY_REPORT_ENABLED > 0:
        first_report = _DAILY_REPORT_FIRST_SEC if _DAILY_REPORT_FIRST_SEC > 0 else 21600
        job_queue.run_repeating(send_daily_report_job, interval=86400, first=first_report)
    if _ERROR_DIGEST_INTERVAL_SEC > 0:
        first = _ERROR_DIGEST_FIRST_SEC if _ERROR_DIGEST_FIRST_SEC > 0 else 3600
        job_queue.run_repeating(send_error_digest_job, interval=_ERROR_DIGEST_INTERVAL_SEC, first=first)

    # Initialize Main Bot
    await app_main.initialize()
    await app_main.start()
    await app_main.updater.start_polling()

    me = await app_main.bot.get_me()
    print(f"🤖 Main Bot Started: @{me.username}")

    # 2. Support Bot App (Optional)
    if SUPPORT_BOT_TOKEN:
        try:
            app_support = ApplicationBuilder().token(SUPPORT_BOT_TOKEN).request(request).build()
            app_support.add_error_handler(global_error_handler)

            # Register Handler for Support Bot
            app_support.add_handler(CommandHandler('start', admin_bot_start_handler))
            app_support.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, admin_bot_reply_handler))

            # Cross-link bots
            app_main.bot_data['support_bot'] = app_support.bot
            app_support.bot_data['main_bot'] = app_main.bot

            await app_support.initialize()
            await app_support.start()
            await app_support.updater.start_polling()

            sup_me = await app_support.bot.get_me()
            print(f"🤖 Support Bot Started: @{sup_me.username}")
        except Exception as e:
            logging.error(f"Failed to start Support Bot: {e}")
    else:
        logging.info("Support Bot Token not provided. Running in Single Bot Mode.")

    # Keep alive
    stop_event = asyncio.Event()
    await stop_event.wait()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
