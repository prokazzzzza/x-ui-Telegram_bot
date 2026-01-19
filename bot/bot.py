import logging
import sqlite3
import json
import uuid
import subprocess
import time
import datetime
import shutil
import os
import asyncio
import math
import html
import qrcode
from io import BytesIO
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

# Load environment variables
load_dotenv()

# Custom Logging to write new logs at the beginning of the file
def log_action(message):
    try:
        timestamp = datetime.datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
        entry = f"{timestamp} - {message}\n"
        
        content = ""
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                content = f.read()
                
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            f.write(entry + content)
            
        # Also print to console for debugging/journalctl
        print(f"LOG: {message}")
    except Exception as e:
        print(f"Logging failed: {e}")

# Disable root logger file handler to avoid noise
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.WARNING, # Only warnings/errors in console
    handlers=[
        logging.StreamHandler()
    ]
)

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButtonRequestUsers
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, PreCheckoutQueryHandler, MessageHandler, filters

# Config
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

if not TOKEN:
    logging.error("BOT_TOKEN not found in environment variables")
    exit(1)

if not ADMIN_ID:
    logging.warning("ADMIN_ID not found in environment variables")

DB_PATH = "/etc/x-ui/x-ui.db"
BOT_DB_PATH = "/usr/local/x-ui/bot/bot_data.db"
INBOUND_ID = 1
PUBLIC_KEY = os.getenv("PUBLIC_KEY")
IP = os.getenv("HOST_IP")
PORT = os.getenv("HOST_PORT")
if PORT:
    PORT = int(PORT)
else:
    PORT = None
    
SNI = os.getenv("SNI")
SID = os.getenv("SID")
TIMEZONE = ZoneInfo("Europe/Moscow")
LOG_FILE = "/usr/local/x-ui/bot/bot.log"

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
    "1_month": {"amount": 1, "days": 30},
    "3_months": {"amount": 3, "days": 90},
    "1_year": {"amount": 5, "days": 365}
}

# Localization
TEXTS = {
    "en": {
        "welcome": "Welcome to Maxi_VPN Bot! 🛡️\n\nPlease select your language:",
        "main_menu": "Welcome to Maxi_VPN! 🛡️\n\nPurchase a subscription using Telegram Stars to get high-speed secure access.",
        "btn_buy": "💎 Buy Subscription",
        "btn_config": "🚀 My Config",
        "btn_stats": "📊 My Stats",
        "btn_trial": "🆓 Free Trial (3 Days)",
        "btn_ref": "👥 Referrals",
        "btn_promo": "🎁 Redeem Promo",
        "shop_title": "🛒 Select a Plan:\n\nPay safely with Telegram Stars.",
        "btn_back": "🔙 Back",
        "label_1_month": "1 Month Subscription",
        "label_3_months": "3 Months Subscription",
        "label_6_months": "6 Months Subscription",
        "label_1_year": "1 Year Subscription",
        "invoice_title": "Maxi_VPN Subscription",
        "success_created": "✅ Success! Subscription created.\n\n📅 New Expiry: {expiry}\n\nUse '🚀 My Config' to get your connection key.",
        "success_extended": "✅ Success! Subscription extended.\n\n📅 New Expiry: {expiry}\n\nUse '🚀 My Config' to get your connection key.",
        "success_updated": "✅ Success! Subscription updated.\n\n📅 New Expiry: {expiry}\n\nUse '🚀 My Config' to get your connection key.",
        "error_generic": "An error occurred. Please contact support.",
        "sub_expired": "⚠️ Subscription Expired\n\nYour subscription has expired. Please buy a new plan to restore access.",
        "sub_active": "✅ Your Subscription is Active\n\n📅 Expires: {expiry}\n\nKey:\n`{link}`",
        "sub_not_found": "❌ No Subscription Found\n\nYou don't have an active subscription. Please visit the shop.",
        "stats_title": "📊 Your Stats\n\n⬇️ Download: {down:.2f} GB\n⬆️ Upload: {up:.2f} GB\n📦 Total: {total:.2f} GB",
        "stats_no_sub": "No stats found. Subscription required.",
        "expiry_warning": "⚠️ Subscription Expiring Soon!\n\nYour VPN subscription will expire in less than 24 hours.\nPlease renew it to avoid service interruption.",
        "btn_renew": "💎 Renew Now",
        "btn_instructions": "📚 Setup Instructions",
        "lang_sel": "Language selected: English 🇬🇧",
        "trial_used": "⚠️ Trial Already Used\n\nYou have already used your trial period.\nActivated: {date}",
        "trial_activated": "🎉 Trial Activated!\n\nYou have received 3 days of free access.\nCheck '🚀 My Config' to connect.",
        "ref_title": "👥 *Referral Program*\n\nInvite friends and get bonuses!\n\n🔗 Your Link:\n`{link}`\n\n🎁 You have invited: {count} users.",
        "promo_prompt": "🎁 *Redeem Promo Code*\n\nPlease enter your promo code:",
        "promo_success": "✅ *Promo Code Redeemed!*\n\nAdded {days} days to your subscription.",
        "promo_invalid": "❌ *Invalid or Expired Code*",
        "promo_used": "⚠️ *Code Already Used*",
        "instr_menu": "📚 *Setup Instructions*\n\nChoose your device:",
        "btn_android": "📱 Android (v2RayTun)",
        "btn_ios": "🍎 iOS (V2Box)",
        "btn_pc": "💻 PC (Amnezia/Hiddify)",
        "instr_android": "📱 *Android Setup*\n\n1. Install *[v2RayTun](https://play.google.com/store/apps/details?id=com.v2raytun.android)* from Google Play.\n2. Copy your key from '🚀 My Config'.\n3. Open v2RayTun -> Tap 'Import' -> 'Import from Clipboard'.\n4. Tap the connection button.",
        "instr_ios": "🍎 *iOS Setup*\n\n1. Install *[V2Box](https://apps.apple.com/app/v2box-v2ray-client/id6446814690)* from App Store.\n2. Copy your key from '🚀 My Config'.\n3. Open V2Box, it should detect the key automatically.\n4. Tap 'Import' and then swipe to connect.",
        "instr_pc": "💻 *PC Setup*\n\n1. Install *[AmneziaVPN](https://amnezia.org/)* or *[Hiddify](https://github.com/hiddify/hiddify-next/releases)*.\n2. Copy your key from '🚀 My Config'.\n3. Open the app and paste the key (Import from Clipboard).\n4. Connect.",
        "plan_1_month": "1 Month",
        "plan_3_months": "3 Months",
        "plan_6_months": "6 Months",
        "plan_1_year": "1 Year",
        "plan_trial": "Trial (3 Days)",
        "plan_manual": "Manual",
        "plan_unlimited": "Unlimited",
        "sub_type_unknown": "Unknown",
        "stats_sub_type": "💳 Plan: {plan}",
        "rank_info": "\n🏆 Your Rank: #{rank} of {total}\n(Top {percent}% - Extend subscription to rank up!)",
        "btn_admin_stats": "📊 Statistics",
        "btn_admin_server": "🖥 Server",
        "btn_admin_prices": "💰 Pricing",
        "btn_admin_promos": "🎁 Promo Codes",
        "btn_admin_poll": "📊 Polls",
        "btn_admin_broadcast": "📢 Broadcast",
        "btn_admin_sales": "📜 Sales Log",
        "btn_admin_backup": "💾 Backup",
        "btn_admin_logs": "📜 Logs",
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
        "admin_server_live_title": "🖥 *Server Status (LIVE 🟢)*",
        "cpu_label": "🧠 *CPU:*",
        "ram_label": "💾 *RAM:*",
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
        "stats_users": "👥 *Bot Users:*",
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
        "btn_sync_nicks": "🔄 Sync Nicknames",
        "sync_start": "Syncing...",
        "sync_error_inbound": "❌ X-UI Inbound not found.",
        "sync_progress": "🔄 Syncing: {current}/{total}",
        "sync_complete": "✅ Sync complete!\n\nUpdated: {updated}\nFailed: {failed}\n\n⚠️ X-UI restarted to update names.",
        "users_list_title": "📋 *{title}*",
        "title_all": "All Clients",
        "title_active": "Active Clients",
        "title_expiring": "Expiring Soon (<7d)",
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
        "flash_menu_title": "⚡ *Flash Promo*\n\nSelect a promo code to broadcast temporarily:",
        "btn_flash_delete_all": "🧨 Delete All Flash",
        "flash_select_prompt": "⚡ Selected Code: `{code}`\n\nEnter message lifetime in minutes (e.g., 60).\nMessage will be deleted for all users after this time.",
        "flash_broadcast_start": "⏳ Starting Flash Broadcast (ALL)...",
        "flash_msg_title": "🔥 <b>CATCH THE PROMO CODE!</b> 🔥",
        "flash_msg_body": "Hurry to redeem the secret code!\n\n👇 Click to reveal:\n<tg-spoiler><code>{code}</code></tg-spoiler>\n\n⏳ <b>Expires at {time}</b>\n(in {dur} min)",
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
        "live_monitor_starting": "Starting Live Monitor...",
        "remaining_days": "⏳ Remaining: {days} days",
        "remaining_hours": "⏳ Remaining: {hours} hours",
        "error_invalid_id": "❌ Error: Invalid ID",
        "status_unbound": "Unbound",
        "sub_active_html": "✅ <b>Your subscription is active</b>\n\n📅 Expires: {expiry}",
        "sub_recommendation": "\n\n👇 <b>Subscription recommended</b>\n        (Tap link to copy)\n\n📋 <b>Subscription Link:</b>\n<code>{link}</code>\n\n🔑 <b>Access Key:</b> (Tap to reveal)\n<tg-spoiler><code>{key}</code></tg-spoiler>",
        "expiry_unlimited": "Unlimited",
        "stats_your_title": "📊 *Your Statistics*",
        "stats_today": "📅 *Today:*",
        "stats_week": "📅 *This Week:*",
        "stats_month": "📅 *This Month:*",
        "stats_total": "📦 *Total:*",
        "stats_expires": "⏳ *Expires:*",
        "unlimited_text": "♾️ Unlimited"
    },
    "ru": {
        "error_invalid_id": "❌ Ошибка: Некорректный ID",
        "status_unbound": "Не привязан",
        "sub_active_html": "✅ <b>Ваша подписка активна</b>\n\n📅 Истекает: {expiry}",
        "sub_recommendation": "\n\n👇 <b>Рекомендуется использовать подписку</b>\n        (Нажмите на ссылку для копирования)\n\n📋 <b>Ссылка подписки:</b>\n<code>{link}</code>\n\n🔑 <b>Ключ доступа:</b> (Нажмите чтобы развернуть)\n<tg-spoiler><code>{key}</code></tg-spoiler>",
        "expiry_unlimited": "Безлимит",
        "stats_your_title": "📊 *Ваша статистика*",
        "stats_today": "📅 *За сегодня:*",
        "stats_week": "📅 *За неделю:*",
        "stats_month": "📅 *За месяц:*",
        "stats_total": "📦 *Всего:*",
        "stats_expires": "⏳ *Истекает:*",
        "unlimited_text": "♾️ Безлимит",
        "welcome": "Добро пожаловать в Maxi-VPN! 🛡️\n\nПожалуйста, выберите язык:",
        "main_menu": "🚀 Maxi-VPN — Твой пропуск в свободный интернет!\n\n⚡️ Высокая скорость, анонимность и доступ к любым сервисам.\n💎 Оплата в один клик через Telegram Stars.",
        "btn_buy": "💎 Купить подписку",
        "btn_config": "🚀 Мой конфиг",
        "btn_stats": "📊 Моя статистика",
        "btn_trial": "🆓 Пробный период (3 дня)",
        "btn_ref": "👥 Рефералка",
        "btn_promo": "🎁 Промокод",
        "shop_title": "🛒 *Выберите план:*\n\nБезопасная оплата через Telegram Stars.",
        "btn_back": "🔙 Назад",
        "label_1_month": "Подписка на 1 месяц",
        "label_3_months": "Подписка на 3 месяца",
        "label_6_months": "Подписка на 6 месяцев",
        "label_1_year": "Подписка на 1 год",
        "invoice_title": "Maxi_VPN Подписка",
        "success_created": "✅ *Успешно!* Подписка создана.\n\n📅 Истекает: {expiry}\n\nНажмите '🚀 Мой конфиг', чтобы получить ключ.",
        "success_extended": "✅ *Успешно!* Подписка продлена.\n\n📅 Истекает: {expiry}\n\nНажмите '🚀 Мой конфиг', чтобы получить ключ.",
        "success_updated": "✅ *Успешно!* Подписка обновлена.\n\n📅 Истекает: {expiry}\n\nНажмите '🚀 Мой конфиг', чтобы получить ключ.",
        "error_generic": "Произошла ошибка. Пожалуйста, свяжитесь с поддержкой.",
        "sub_expired": "⚠️ *Подписка истекла*\n\nВаша подписка закончилась. Пожалуйста, купите новый план для восстановления доступа.",
        "sub_active": "✅ *Ваша подписка активна*\n\n📅 Истекает: {expiry}\n\nКлюч:\n`{link}`",
        "sub_not_found": "❌ *Подписка не найдена*\n\nУ вас нет активной подписки. Пожалуйста, перейдите в магазин.",
        "stats_title": "📊 *Ваша статистика*\n\n⬇️ Скачано: {down:.2f} GB\n⬆️ Загружено: {up:.2f} GB\n📦 Всего: {total:.2f} GB",
        "stats_no_sub": "Статистика не найдена. Требуется подписка.",
        "expiry_warning": "⚠️ *Подписка скоро истекает!*\n\nВаша VPN подписка истечет менее чем через 24 часа.\nПожалуйста, продлите её, чтобы избежать отключения.",
        "btn_renew": "💎 Продлить сейчас",
        "btn_instructions": "📚 Инструкция по настройке",
        "btn_lang": "🌐 Язык",
        "lang_sel": "Выбран язык: Русский 🇷🇺",
        "trial_used": "⚠️ *Пробный период уже использован*\n\nВы уже активировали свои 3 дня бесплатно.\nДата активации: {date}",
        "trial_activated": "🎉 *Пробный период активирован!*\n\nВам начислено 3 дня доступа.\nНажмите '🚀 Мой конфиг' для подключения.",
        "ref_title": "👥 *Реферальная программа*\n\nПриглашайте друзей и получайте бонусы!\n\n🔗 Ваша ссылка:\n`{link}`\n\n🎁 Вы пригласили: {count} пользователей.",
        "promo_prompt": "🎁 *Активация промокода*\n\nПожалуйста, отправьте боту ваш промокод:",
        "promo_success": "✅ *Промокод активирован!*\n\nДобавлено {days} дней к вашей подписке.",
        "promo_invalid": "❌ *Неверный или истекший код*",
        "promo_used": "⚠️ *Код уже использован вами*",
        "instr_menu": "📚 *Инструкция по настройке*\n\nВыберите ваше устройство:",
        "btn_android": "📱 Android (v2RayTun)",
        "btn_ios": "🍎 iOS (V2Box)",
        "btn_pc": "💻 PC (Amnezia/Hiddify)",
        "instr_android": "📱 *Настройка Android*\n\n1. Скачайте *[v2RayTun](https://play.google.com/store/apps/details?id=com.v2raytun.android)* из Google Play.\n2. Скопируйте ваш ключ из '🚀 Мой конфиг'.\n3. Откройте v2RayTun -> нажмите 'Import' -> 'Import from Clipboard'.\n4. Нажмите кнопку подключения.",
        "instr_ios": "🍎 *Настройка iOS*\n\n1. Скачайте *[V2Box](https://apps.apple.com/app/v2box-v2ray-client/id6446814690)* из App Store.\n2. Скопируйте ваш ключ из '🚀 Мой конфиг'.\n3. Откройте V2Box, он должен автоматически предложить добавить ключ.\n4. Нажмите 'Import', выберите сервер и сдвиньте переключатель для подключения.",
        "instr_pc": "💻 *Настройка PC*\n\n1. Установите *[AmneziaVPN](https://amnezia.org/)* или *[Hiddify](https://github.com/hiddify/hiddify-next/releases)*.\n2. Скопируйте ваш ключ из '🚀 Мой конфиг'.\n3. Откройте приложение и вставьте ключ (Import from Clipboard).\n4. Подключитесь.",
        "plan_1_month": "1 Месяц",
        "plan_3_months": "3 Месяца",
        "plan_6_months": "6 Месяцев",
        "plan_1_year": "1 Год",
        "plan_trial": "Пробный (3 дня)",
        "plan_manual": "Ручная",
        "plan_unlimited": "Безлимит",
        "sub_type_unknown": "Неизвестно",
        "stats_sub_type": "💳 Тариф: {plan}",
        "remaining_days": "⏳ Осталось: {days} дн.",
        "remaining_hours": "⏳ Осталось: {hours} ч.",
        "rank_info": "\n\n🏆 Ваш статус в клубе:\nВы занимаете {rank}-е место в рейтинге подписок из {total}.\n💡 Продлите подписку на больший срок, чтобы стать лидером!",
        "btn_admin_stats": "📊 Статистика",
        "btn_admin_server": "🖥 Сервер",
        "btn_admin_prices": "💰 Настройка цен",
        "btn_admin_promos": "🎁 Промокоды",
        "btn_admin_poll": "📊 Опросы",
        "btn_admin_broadcast": "📢 Рассылка",
        "btn_admin_sales": "📜 Журнал продаж",
        "btn_admin_backup": "💾 Бэкап",
        "btn_admin_logs": "📜 Логи",
        "btn_main_menu_back": "🔙 Главное меню",
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
        "admin_server_live_title": "🖥 *Состояние сервера (LIVE 🟢)*",
        "cpu_label": "🧠 *CPU:*",
        "ram_label": "💾 *RAM:*",
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
        "stats_users": "👥 *Пользователи бота:*",
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
        "btn_sync_nicks": "🔄 Обновить ники",
        "sync_start": "Синхронизация...",
        "sync_error_inbound": "❌ X-UI Inbound not found.",
        "sync_progress": "🔄 Синхронизация: {current}/{total}",
        "sync_complete": "✅ Синхронизация завершена!\n\nОбновлено: {updated}\nОшибок: {failed}\n\n⚠️ X-UI был перезапущен для обновления имен в панели.",
        "users_list_title": "📋 *{title}*",
        "title_all": "Все клиенты",
        "title_active": "Активные клиенты",
        "title_expiring": "Скоро истекают (<7д)",
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
        "promo_created": "✅ Промокод `{code}` создан на {days} дн. ({limit} активаций).",
        "promo_format_error": "❌ Неверный формат. Используйте: `КОД ДНИ ЛИМИТ`",
        "flash_menu_title": "⚡ *Flash Промокод*\n\nВыберите промокод, который хотите отправить во временной рассылке:",
        "btn_flash_delete_all": "🧨 Удалить все Flash",
        "flash_select_prompt": "⚡ Выбран промокод: `{code}`\n\nВведите время жизни сообщения в минутах (например: 60).\nПо истечении этого времени сообщение будет удалено у всех пользователей.",
        "flash_broadcast_start": "⏳ Запуск Flash-рассылки (ВСЕМ)...",
        "flash_msg_title": "🔥 <b>УСПЕЙ ПОЙМАТЬ ПРОМОКОД!</b> 🔥",
        "flash_msg_body": "Успей активировать секретный промокод!\n\n👇 Нажми, чтобы увидеть:\n<tg-spoiler><code>{code}</code></tg-spoiler>\n\n⏳ <b>Предложение сгорит в {time}</b>\n(через {dur} мин)",
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
        "live_monitor_starting": "Запуск Live мониторинга..."
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
    except: pass
    try:
        cursor.execute("ALTER TABLE user_prefs ADD COLUMN referrer_id TEXT")
    except: pass
    try:
        cursor.execute("ALTER TABLE user_prefs ADD COLUMN trial_activated_at INTEGER")
    except: pass
    try:
        cursor.execute("ALTER TABLE user_prefs ADD COLUMN username TEXT")
    except: pass
    try:
        cursor.execute("ALTER TABLE user_prefs ADD COLUMN first_name TEXT")
    except: pass
    try:
        cursor.execute("ALTER TABLE user_prefs ADD COLUMN last_name TEXT")
    except: pass
    
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
        CREATE TABLE IF NOT EXISTS prices (
            key TEXT PRIMARY KEY,
            amount INTEGER,
            days INTEGER
        )
    ''')
    
    # Initialize default prices if empty
    cursor.execute("SELECT COUNT(*) FROM prices")
    if cursor.fetchone()[0] == 0:
        cursor.executemany("INSERT INTO prices (key, amount, days) VALUES (?, ?, ?)", [
            ("1_month", 1, 30),
            ("3_months", 3, 90),
            ("6_months", 450, 180),
            ("1_year", 5, 365)
        ])
    else:
        # Ensure 6_months exists
        cursor.execute("INSERT OR IGNORE INTO prices (key, amount, days) VALUES (?, ?, ?)", ("6_months", 450, 180))

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
    
    conn.commit()
    conn.close()

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
    # Only set if not exists
    cursor.execute("INSERT OR IGNORE INTO user_prefs (tg_id, referrer_id) VALUES (?, ?)", (str(tg_id), str(referrer_id)))
    # If exists but referrer is null, update? No, usually first touch counts. 
    # But insert or ignore handles 'new' users.
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
    cursor.execute("SELECT days, max_uses, used_count FROM promo_codes WHERE code=?", (code,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None # Invalid
        
    days, max_uses, used_count = row
    if max_uses > 0 and used_count >= max_uses:
        conn.close()
        return None # Expired/Max used
        
    # Check if user used it
    cursor.execute("SELECT 1 FROM user_promos WHERE tg_id=? AND code=?", (str(tg_id), code))
    if cursor.fetchone():
        conn.close()
        return "USED"
        
    conn.close()
    return days

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
        
    prices_dict = {}
    for r in rows:
        prices_dict[r[0]] = {"amount": r[1], "days": r[2]}
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
        
        # Filter clients with valid expiry or unlimited (0)
        # We want to rank by expiryTime descending.
        # 0 is unlimited, should be at top.
        # But for comparison, let's treat 0 as very large number.
        
        valid_clients = []
        user_expiry = None
        
        current_time_ms = int(time.time() * 1000)
        
        for c in clients:
            expiry = c.get('expiryTime', 0)
            tid = str(c.get('tgId', ''))
            
            # Treat 0 as infinity (e.g., year 3000)
            sort_val = expiry if expiry > 0 else 32503680000000 # Year 3000
            
            # Include only active or unlimited? Or all?
            # User wants "competition". Usually implies active users.
            # Let's include everyone who is enabled or has future expiry?
            # If I am expired, I should be at bottom.
            
            valid_clients.append({
                'tg_id': tid,
                'sort_val': sort_val
            })
            
            if tid == tg_id:
                user_expiry = sort_val
        
        if user_expiry is None:
            return None, len(valid_clients), 0
            
        # Sort descending
        valid_clients.sort(key=lambda x: x['sort_val'], reverse=True)
        
        rank = -1
        for idx, item in enumerate(valid_clients):
            if item['tg_id'] == tg_id:
                rank = idx + 1
                break
                
        total = len(valid_clients)
        percent = int(((total - rank) / total) * 100) if total > 0 else 0
        # Wait, top 1 is (10-1)/10 = 90%? No. Top 1/10 is top 10%.
        # Percentile: (total - rank + 1) / total * 100 ?
        # Rank 1 of 100 -> Top 1%
        percent_top = int((rank / total) * 100) if total > 0 else 0
        if percent_top == 0: percent_top = 1
        
        return rank, total, percent_top
        
    except Exception as e:
        logging.error(f"Error calculating rank: {e}")
        return None, 0, 0

def t(key, lang="en"):
    return TEXTS.get(lang, TEXTS["en"]).get(key, key)

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
        [InlineKeyboardButton(t("btn_buy", lang), callback_data='shop')],
        [InlineKeyboardButton(t("btn_trial", lang), callback_data='try_trial'), InlineKeyboardButton(t("btn_promo", lang), callback_data='enter_promo')],
        [InlineKeyboardButton(t("btn_config", lang), callback_data='get_config'), InlineKeyboardButton(t("btn_stats", lang), callback_data='stats')],
        [InlineKeyboardButton(t("btn_ref", lang), callback_data='referral'), InlineKeyboardButton(t("btn_lang", lang), callback_data='change_lang')]
    ]
    if tg_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton(t("btn_admin_panel", lang), callback_data='admin_panel')])
        
    text = t("main_menu", lang)
    rank, total, percent = get_user_rank(tg_id)
    if rank:
        text += t("rank_info", lang).format(rank=rank, total=total, percent=percent)
        
    # Check for welcome image
    welcome_photo_path = "welcome.jpg"
    if os.path.exists(welcome_photo_path):
        try:
            with open(welcome_photo_path, 'rb') as photo:
                await update.message.reply_photo(photo=photo, caption=text, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
             logging.error(f"Failed to send welcome photo: {e}")
             await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_main_menu_query(query, context, lang):
    tg_id = str(query.from_user.id)
    keyboard = [
        [InlineKeyboardButton(t("btn_buy", lang), callback_data='shop')],
        [InlineKeyboardButton(t("btn_trial", lang), callback_data='try_trial'), InlineKeyboardButton(t("btn_promo", lang), callback_data='enter_promo')],
        [InlineKeyboardButton(t("btn_config", lang), callback_data='get_config'), InlineKeyboardButton(t("btn_stats", lang), callback_data='stats')],
        [InlineKeyboardButton(t("btn_ref", lang), callback_data='referral'), InlineKeyboardButton(t("btn_lang", lang), callback_data='change_lang')]
    ]
    if tg_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton(t("btn_admin_panel", lang), callback_data='admin_panel')])
        
    text = t("main_menu", lang)
    rank, total, percent = get_user_rank(tg_id)
    if rank:
        text += t("rank_info", lang).format(rank=rank, total=total, percent=percent)
        
    # Check for welcome image - DISABLED for query (text only to avoid issues)
    # welcome_photo_path = "welcome.jpg"
    # if os.path.exists(welcome_photo_path):
    #     try:
    #         # For query, we can't easily edit text to photo.
    #         # We delete previous message and send new photo.
    #         await query.message.delete()
    #         with open(welcome_photo_path, 'rb') as photo:
    #              await context.bot.send_photo(chat_id=query.from_user.id, photo=photo, caption=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    #     except Exception as e:
    #          logging.error(f"Failed to send welcome photo (query): {e}")
    #          await context.bot.send_message(chat_id=query.from_user.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    # else:
    await context.bot.send_message(chat_id=query.from_user.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard))

async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    
    current_prices = get_prices()
    
    keyboard = []
    # Order: 1_month, 3_months, 6_months, 1_year
    order = ["1_month", "3_months", "6_months", "1_year"]
    
    for key in order:
        if key in current_prices:
            data = current_prices[key]
            label_key = f"label_{key}"
            label = t(label_key, lang)
            keyboard.append([InlineKeyboardButton(f"{label} - {data['amount']} ⭐️", callback_data=f'buy_{key}')])
    
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

async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    
    keyboard = [
        [InlineKeyboardButton(t("btn_buy", lang), callback_data='shop')],
        [InlineKeyboardButton(t("btn_trial", lang), callback_data='try_trial'), InlineKeyboardButton(t("btn_promo", lang), callback_data='enter_promo')],
        [InlineKeyboardButton(t("btn_config", lang), callback_data='get_config'), InlineKeyboardButton(t("btn_stats", lang), callback_data='stats')],
        [InlineKeyboardButton(t("btn_ref", lang), callback_data='referral'), InlineKeyboardButton(t("btn_lang", lang), callback_data='change_lang')]
    ]
    if tg_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton(t("btn_admin_panel", lang), callback_data='admin_panel')])

    text = t("main_menu", lang)
    rank, total, percent = get_user_rank(tg_id)
    if rank:
        text += t("rank_info", lang).format(rank=rank, total=total, percent=percent)

    # Revert to text-only main menu
    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        if "Message is not modified" not in str(e):
             await query.message.delete()
             await context.bot.send_message(chat_id=query.from_user.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard))

async def try_trial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    
    user_data = get_user_data(tg_id)
    if user_data['trial_used']:
        date_str = "Unknown"
        if user_data.get('trial_activated_at'):
            date_str = datetime.datetime.fromtimestamp(user_data['trial_activated_at'], tz=TIMEZONE).strftime("%d.%m.%Y %H:%M")
            
        text = t("trial_used", lang).format(date=date_str)
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
        return

    # Activate 3 days
    log_action(f"ACTION: User {tg_id} (@{query.from_user.username}) activated TRIAL subscription.")
    await process_subscription(tg_id, 3, update, context, lang, is_callback=True)
    mark_trial_used(tg_id)
    
    # We need to send a separate message or edit properly because process_subscription sends messages too.
    # Actually process_subscription uses update.message.reply_text, which might fail on callback query if not handled.
    # Let's fix process_subscription to handle callback query or we just reuse logic.
    # Wait, process_subscription currently expects update.message.
    # I should refactor process_subscription to be more flexible.

async def referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    
    bot_username = context.bot.username
    link = f"https://t.me/{bot_username}?start={tg_id}"
    count = count_referrals(tg_id)
    
    text = t("ref_title", lang).format(link=link, count=count)
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

async def backup_db(context: ContextTypes.DEFAULT_TYPE = None):
    try:
        backup_dir = "/usr/local/x-ui/bot/backups"
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)
            
        timestamp = datetime.datetime.now(TIMEZONE).strftime("%Y-%m-%d_%H-%M-%S")
        
        # Backup Bot DB
        if os.path.exists(BOT_DB_PATH):
            shutil.copy2(BOT_DB_PATH, f"{backup_dir}/bot_data_{timestamp}.db")
            
        # Backup X-UI DB
        if os.path.exists(DB_PATH):
            shutil.copy2(DB_PATH, f"{backup_dir}/x-ui_{timestamp}.db")
            
        # Cleanup old backups (keep last 20 files)
        files = sorted([os.path.join(backup_dir, f) for f in os.listdir(backup_dir)], key=os.path.getmtime)
        if len(files) > 20: 
            for f in files[:-20]:
                os.remove(f)
                
        logging.info(f"Backup completed: {timestamp}")
        return True
    except Exception as e:
        logging.error(f"Backup failed: {e}")
        return False

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

async def admin_create_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    await query.answer(t("backup_starting", lang))
    
    success = await backup_db()
    
    if success:
        await context.bot.send_message(chat_id=query.from_user.id, text=t("backup_success", lang))
    else:
        await context.bot.send_message(chat_id=query.from_user.id, text=t("backup_error", lang))

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        tg_id = str(query.from_user.id)
        msg_func = query.edit_message_text
    else:
        tg_id = str(update.message.from_user.id)
        msg_func = update.message.reply_text

    if tg_id != ADMIN_ID:
        return
    
    lang = get_lang(tg_id)
    
    keyboard = [
        [InlineKeyboardButton(t("btn_admin_stats", lang), callback_data='admin_stats')],
        [InlineKeyboardButton(t("btn_admin_server", lang), callback_data='admin_server')],
        [InlineKeyboardButton(t("btn_admin_prices", lang), callback_data='admin_prices')],
        [InlineKeyboardButton(t("btn_admin_promos", lang), callback_data='admin_promos_menu')],
        [InlineKeyboardButton(t("btn_admin_poll", lang), callback_data='admin_poll_menu')],
        [InlineKeyboardButton(t("btn_admin_broadcast", lang), callback_data='admin_broadcast')],
        [InlineKeyboardButton(t("btn_admin_sales", lang), callback_data='admin_sales_log')],
        [InlineKeyboardButton(t("btn_admin_backup", lang), callback_data='admin_create_backup')],
        [InlineKeyboardButton(t("btn_admin_logs", lang), callback_data='admin_logs')],
        [InlineKeyboardButton(t("btn_main_menu_back", lang), callback_data='back_to_main')]
    ]
    
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
    except:
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
    except:
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
    except:
        ram_usage = 0
        ram_total_gb = 0
        ram_used_gb = 0

    # Disk
    try:
        disk = shutil.disk_usage('/')
        disk_total_gb = disk.total / (1024**3)
        disk_used_gb = disk.used / (1024**3)
        disk_free_gb = disk.free / (1024**3)
        disk_usage = (disk.used / disk.total) * 100
    except:
        disk_usage = 0
        disk_total_gb = 0
        disk_used_gb = 0
        disk_free_gb = 0
        
    return {
        'cpu': cpu_usage,
        'ram_usage': ram_usage,
        'ram_total': ram_total_gb,
        'ram_used': ram_used_gb,
        'disk_usage': disk_usage,
        'disk_total': disk_total_gb,
        'disk_used': disk_used_gb,
        'disk_free': disk_free_gb,
        'rx_speed': rx_speed,
        'tx_speed': tx_speed
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
    except:
        pass # Ignore if already answered
    
    stats = await get_system_stats()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    
    tx_speed_str = format_bytes(stats['tx_speed']) + "/s"
    rx_speed_str = format_bytes(stats['rx_speed']) + "/s"
    
    text = f"{t('admin_server_title', lang)}\n\n" \
           f"{t('cpu_label', lang)} {stats['cpu']:.1f}%\n" \
           f"{t('ram_label', lang)} {stats['ram_usage']:.1f}% ({stats['ram_used']:.2f} / {stats['ram_total']:.2f} GB)\n" \
           f"{t('disk_label', lang)} {stats['disk_usage']:.1f}%\n" \
           f"{t('disk_used', lang)} {stats['disk_used']:.2f} GB\n" \
           f"{t('disk_free', lang)} {stats['disk_free']:.2f} GB\n" \
           f"{t('disk_total', lang)} {stats['disk_total']:.2f} GB\n\n" \
           f"{t('traffic_speed_title', lang)}\n" \
           f"{t('upload_label', lang)}\n{tx_speed_str}\n" \
           f"{t('download_label', lang)}\n{rx_speed_str}\n\n" \
           f"{t('updated_label', lang)} {datetime.datetime.now(TIMEZONE).strftime('%H:%M:%S')}"

    keyboard = [
        [InlineKeyboardButton(t("btn_live_monitor", lang), callback_data='admin_server_live')],
        [InlineKeyboardButton(t("btn_refresh", lang), callback_data='admin_server')],
        [InlineKeyboardButton(t("btn_back_admin", lang), callback_data='admin_panel')]
    ]
    
    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    except Exception as e:
        # If message content is same (Telegram API error), we just ignore or answer
        if "Message is not modified" not in str(e):
             await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_server_live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    await query.answer(t("live_monitor_starting", lang))
    
    context.user_data['live_monitoring_active'] = True
    
    # Run in background task to not block updates
    asyncio.create_task(run_live_monitor(update, context))

async def run_live_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
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
            
            text = f"{t('admin_server_live_title', lang)}\n\n" \
                   f"{t('cpu_label', lang)} {stats['cpu']:.1f}%\n" \
                   f"{t('ram_label', lang)} {stats['ram_usage']:.1f}% ({stats['ram_used']:.2f} / {stats['ram_total']:.2f} GB)\n" \
                   f"{t('disk_label', lang)} {stats['disk_usage']:.1f}%\n" \
                   f"{t('disk_used', lang)} {stats['disk_used']:.2f} GB\n" \
                   f"{t('disk_free', lang)} {stats['disk_free']:.2f} GB\n" \
                   f"{t('disk_total', lang)} {stats['disk_total']:.2f} GB\n\n" \
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
            chat_id = update.effective_chat.id
            message_id = update.effective_message.message_id
            
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
         except: pass

async def admin_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    
    current_prices = get_prices()
    
    keyboard = []
    order = ["1_month", "3_months", "6_months", "1_year"]
    
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
        "1_month": t("plan_1_month", lang),
        "3_months": t("plan_3_months", lang),
        "6_months": t("plan_6_months", lang),
        "1_year": t("plan_1_year", lang)
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
    
    cursor.execute("SELECT SUM(amount) FROM transactions")
    total_revenue = cursor.fetchone()[0] or 0
    
    cursor.execute("SELECT COUNT(*) FROM transactions")
    total_sales = cursor.fetchone()[0]
    
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
    cursor.execute("SELECT COUNT(DISTINCT email) FROM client_traffics WHERE last_online > ?", (threshold,))
    online_users = cursor.fetchone()[0]
    
    conn.close()
    
    active_subs = 0
    total_clients = 0
    
    active_trials = 0
    expired_trials = 0
    
    if row:
        settings = json.loads(row[0])
        clients = settings.get('clients', [])
        total_clients = len(clients)
        
        for client in clients:
            expiry = client.get('expiryTime', 0)
            enable = client.get('enable', False)
            tg_id = str(client.get('tgId', ''))
            
            # Count overall active
            if enable:
                if expiry == 0 or expiry > current_time_ms:
                    active_subs += 1
            
            # Count trial stats
            if tg_id in pure_trial_users:
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

    text = f"{t('stats_header', lang)}\n\n" \
           f"{t('stats_users', lang)} {total_users}\n" \
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
            InlineKeyboardButton(t("btn_users_trial", lang), callback_data='admin_users_trial_0')
        ],
        [InlineKeyboardButton(t("btn_sync_nicks", lang), callback_data='admin_sync_nicks')],
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
        
        if tg_id and tg_id.isdigit():
            try:
                # 1. Fetch from Telegram
                chat = await context.bot.get_chat(tg_id)
                uname = chat.username
                fname = chat.first_name
                lname = chat.last_name
                
                # 2. Update Bot DB
                update_user_info(tg_id, uname, fname, lname)
                
                # 3. Update X-UI Email (if needed)
                # Format: tg_{ID}_{Username} or tg_{ID}_{FirstName}
                # Sanitize: Alphanumeric only + underscores
                
                base_name = ""
                if uname:
                    base_name = uname
                elif fname:
                    base_name = fname
                    
                # Sanitize
                import re
                clean_name = re.sub(r'[^a-zA-Z0-9]', '', base_name)
                if not clean_name: clean_name = "User"
                
                # User requested email to be just tg_ID, and name in comment
                new_email = f"tg_{tg_id}"
                old_email = client.get('email')
                
                # Update comment if changed
                old_comment = client.get('comment', '')
                if old_comment != clean_name:
                    client['comment'] = clean_name
                    clients[i] = client
                    changed = True
                
                if old_email != new_email:
                    # Check for duplicates? X-UI might complain if duplicate.
                    # But tg_ID is unique usually.
                    
                    # Update Client Object
                    client['email'] = new_email
                    clients[i] = client
                    changed = True
                    
                    # Update client_traffics to preserve stats
                    try:
                        conn.execute("UPDATE client_traffics SET email=? WHERE email=?", (new_email, old_email))
                        # Also update local history if any
                        conn_bot = sqlite3.connect(BOT_DB_PATH)
                        conn_bot.execute("UPDATE traffic_history SET email=? WHERE email=?", (new_email, old_email))
                        conn_bot.commit()
                        conn_bot.close()
                    except: pass
                
                updated_count += 1
            except Exception:
                failed_count += 1
        
        # Update progress
        if (i + 1) % 2 == 0 or (i + 1) == total:
            try:
                await progress_msg.edit_text(t("sync_progress", lang).format(current=i+1, total=total))
            except: pass
            
        await asyncio.sleep(0.05)
        
    if changed:
        # Save X-UI settings
        new_settings = json.dumps(settings, indent=2)
        cursor.execute("UPDATE inbounds SET settings=? WHERE id=?", (new_settings, INBOUND_ID))
        conn.commit()
        # Restart X-UI
        # subprocess.run(["systemctl", "restart", "x-ui"])
        proc = await asyncio.create_subprocess_exec("systemctl", "restart", "x-ui")
        await proc.wait()
        
    try:
        await progress_msg.edit_text(t("sync_complete", lang).format(updated=updated_count, failed=failed_count))
    except: pass
    
    # Return to stats
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
        except:
            page = 0
    else:
        # fallback
        filter_type = 'all'
        try:
            page = int(parts[-1])
        except:
            page = 0
        
    ITEMS_PER_PAGE = 10
    
    # Special handling for 'trial' filter: source from DB + X-UI
    display_items = []
    
    if filter_type == 'trial':
        # 1. Fetch all trial users from BOT DB
        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT tg_id FROM user_prefs WHERE trial_used=1")
        trial_rows = cursor.fetchall() # [(tg_id,), ...]
        conn.close()
        
        # 2. Fetch X-UI clients for mapping
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
        row = cursor.fetchone()
        conn.close()
        
        xui_clients_map = {}
        if row:
            settings = json.loads(row[0])
            for c in settings.get('clients', []):
                tid = str(c.get('tgId', ''))
                if tid:
                    xui_clients_map[tid] = c
        
        for r in trial_rows:
            tg_id = str(r[0])
            client = xui_clients_map.get(tg_id)
            
            if client:
                # Exists in X-UI
                email = client.get('email', 'Unknown')
                status = "🟢" if client.get('enable') else "🔴"
                uid = client.get('id')
                display_items.append({
                    'label': f"{status} {email}",
                    'callback': f"admin_u_{uid}",
                    'sort_key': email.lower()
                })
            else:
                # Deleted from X-UI
                display_items.append({
                    'label': f"❌ {tg_id} (Del)",
                    'callback': f"admin_db_detail_{tg_id}",
                    'sort_key': f"zz_{tg_id}" # Bottom
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
        except:
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
        
        # Filtering
        filtered_clients = []
        current_time = int(time.time() * 1000)
        
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
            expiry = c.get('expiryTime', 0)
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
            elif filter_type == 'online':
                if c.get('email') in online_emails:
                    filtered_clients.append(c)
        
        # Sort and map to display items
        filtered_clients.sort(key=lambda x: x.get('email', '').lower())
        
        for c in filtered_clients:
            status = "🟢" if c.get('enable') else "🔴"
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
    if total_pages == 0: total_pages = 1
    
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
        if "(@" not in label and "(" not in label and "tg_" in label:
             tg_id_str = item.get('tg_id')
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
                         if lname: name += f" {lname}"
                         label = f"{label} ({name})"
                 except: pass

        keyboard.append([InlineKeyboardButton(label, callback_data=item['callback'])])
        
    # Navigation
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️", callback_data=f'admin_users_{filter_type}_{page-1}'))
    
    filter_icons = {'all': '👥', 'active': '🟢', 'expiring': '⏳', 'online': '⚡', 'trial': '🆓'}
    nav_row.append(InlineKeyboardButton(f"{filter_icons.get(filter_type, '')} {page+1}/{total_pages}", callback_data='noop'))
    
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("➡️", callback_data=f'admin_users_{filter_type}_{page+1}'))
    
    keyboard.append(nav_row)
        
    keyboard.append([InlineKeyboardButton(t("btn_back_stats", lang), callback_data='admin_stats')])
    
    title_map = {
        'all': t("title_all", lang),
        'active': t("title_active", lang),
        'expiring': t("title_expiring", lang),
        'online': t("title_online", lang),
        'trial': t("title_trial", lang)
    }
    await query.edit_message_text(t("users_list_title", lang).format(title=title_map.get(filter_type, 'Clients')), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

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
    cursor.execute("SELECT up, down, last_online FROM client_traffics WHERE email=?", (email,))
    traffic_row = cursor.fetchone()
    conn.close()
    
    # Default values from settings
    up = client.get('up', 0)
    down = client.get('down', 0)
    enable_val = client.get('enable', False)
    expiry_ms = client.get('expiryTime', 0)
    total_limit = client.get('total', 0)
    last_online = 0
    
    if traffic_row:
        if traffic_row[0] is not None: up = traffic_row[0]
        if traffic_row[1] is not None: down = traffic_row[1]
        if traffic_row[2] is not None: last_online = traffic_row[2]

    # Calculations
    up_gb = up / (1024**3)
    down_gb = down / (1024**3)
    total_used_gb = up_gb + down_gb
    
    limit_str = f"{total_limit / (1024**3):.2f} GB" if total_limit > 0 else f"♾️ {t('plan_unlimited', lang)}"
    
    current_time_ms = int(time.time() * 1000)
    
    # Online status (10 seconds threshold)
    is_online = (current_time_ms - last_online) < 10 * 1000 if last_online > 0 else False
    online_status = t("status_online", lang) if is_online else t("status_offline", lang)
    
    # Active status
    is_enabled_str = t("status_yes", lang) if enable_val else t("status_no", lang)
    
    # Subscription status
    is_sub_active = (expiry_ms == 0) or (expiry_ms > current_time_ms)
    sub_active_str = t("status_yes", lang) if is_sub_active else t("status_no", lang)
    
    # Hours left
    if expiry_ms == 0:
        hours_left = "♾️"
    elif expiry_ms > current_time_ms:
        diff_ms = expiry_ms - current_time_ms
        hours_left = f"{int(diff_ms / (1000 * 3600))}"
    else:
        hours_left = "0"
        
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
                if db_lname: username += f" {db_lname}"
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
        except Exception as e:
            # logging.error(f"Failed to resolve username for {tg_id_val}: {e}")
            pass
            
    else:
        tg_id_val = t("status_unbound", lang)
        username = "-"
        trial_status_str = f"❓ {t('trial_unknown', lang)}"
    
    text = f"""{t('user_detail_email', lang)} {email}
{t('user_detail_tgid', lang)} {tg_id_val}
{t('user_detail_nick', lang)} {username}
{t('user_detail_enabled', lang)} {is_enabled_str}
{t('user_detail_online', lang)} {online_status}
{t('user_detail_sub', lang)} {sub_active_str}
{t('user_detail_trial', lang)} {trial_status_str}
{t('user_detail_expires', lang)} {hours_left} {t('hours_left', lang)}
{t('user_detail_up', lang)} ↑{up_gb:.2f}GB
{t('user_detail_down', lang)} ↓{down_gb:.2f}GB
{t('user_detail_total', lang)} ↑↓{total_used_gb:.2f}GB {t('user_detail_from', lang)} {limit_str}

{t('updated_label', lang)} {current_time_str}"""
    
    keyboard = []
    if show_reset_trial:
        keyboard.append([InlineKeyboardButton(t("btn_reset_trial", lang), callback_data=f'admin_reset_trial_{uid}')])
        
    keyboard.append([InlineKeyboardButton(t("btn_rebind", lang), callback_data=f'admin_rebind_{uid}')])
    keyboard.append([InlineKeyboardButton(t("btn_delete_user", lang), callback_data=f'admin_del_client_ask_{uid}')])
    keyboard.append([InlineKeyboardButton(t("btn_back_list", lang), callback_data='admin_users_0')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


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
    for r in rows:
        code, days, max_uses, used_count = r
        limit_str = "♾️" if max_uses <= 0 else f"{max_uses}"
        text += f"🏷 `{code}`\n{t('promo_item_days', lang).format(days=days)}\n{t('promo_item_used', lang).format(used=used_count, limit=limit_str)}\n\n"
        
    # Split if too long (simple check)
    if len(text) > 4000:
        text = text[:4000] + "\n...(обрезано)"
        
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='admin_promos_menu')]]),
        parse_mode='Markdown'
    )

async def admin_promo_uses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        page = int(query.data.split('_')[3])
    except:
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
    if total_pages == 0: total_pages = 1
    
    if page >= total_pages: page = total_pages - 1
    if page < 0: page = 0
    
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
        if len(name) > 30: name = name[:27] + "..."
        
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
    except:
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
    text = f"👤 <b>Промокоды пользователя</b>\n{safe_name}\n<code>{tg_id}</code>\n\n"
    
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
        keyboard.append([InlineKeyboardButton("🗑 Аннулировать промокод", callback_data=f'admin_revoke_menu_{tg_id}')])
        
    keyboard.append([InlineKeyboardButton("🔙 К списку", callback_data='admin_promo_uses_0')])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

async def admin_revoke_promo_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = query.data.split('_')[3]
    
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
        keyboard.append([InlineKeyboardButton(f"{code} (-{days} дн.)", callback_data=f'admin_revoke_conf_{tg_id}_{code}')])
        
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f'admin_promo_u_{tg_id}')])
    
    await query.edit_message_text("🗑 *Аннулирование промокода*\n\nВыберите промокод для отмены (срок подписки уменьшится):", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_revoke_promo_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split('_')
    # admin_revoke_conf_TGID_CODE
    tg_id = parts[3]
    code = parts[4]
    
    # Get days
    days = 0
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT days FROM promo_codes WHERE code=?", (code,))
    row = cursor.fetchone()
    if row: days = row[0]
    conn.close()
    
    keyboard = [
        [InlineKeyboardButton("✅ Да, аннулировать", callback_data=f'admin_revoke_act_{tg_id}_{code}')],
        [InlineKeyboardButton("❌ Отмена", callback_data=f'admin_revoke_menu_{tg_id}')]
    ]
    
    await query.edit_message_text(f"⚠️ Вы уверены, что хотите аннулировать промокод `{code}` для пользователя `{tg_id}`?\n\nСрок подписки уменьшится на {days} дней.", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_revoke_promo_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split('_')
    tg_id = parts[3]
    code = parts[4]
    
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

async def admin_flash_delete_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Удаление...")
    
    try:
        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, chat_id, message_id FROM flash_messages")
        rows = cursor.fetchall()
        
        deleted_count = 0
        for row in rows:
            db_id, chat_id, msg_id = row
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except:
                pass
            deleted_count += 1
            
        cursor.execute("DELETE FROM flash_messages")
        conn.commit()
        conn.close()
        
        await query.message.reply_text(f"✅ Принудительно удалено {deleted_count} сообщений.")
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

async def cleanup_flash_messages(context: ContextTypes.DEFAULT_TYPE):
    try:
        current_ts = int(time.time())
        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, chat_id, message_id FROM flash_messages WHERE delete_at <= ?", (current_ts,))
        rows = cursor.fetchall()
        
        if not rows:
            conn.close()
            return
            
        deleted_count = 0
        for row in rows:
            db_id, chat_id, msg_id = row
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception as e:
                # Message might be already deleted or user blocked bot
                pass
            
            # Remove from DB regardless of success (we tried)
            cursor.execute("DELETE FROM flash_messages WHERE id=?", (db_id,))
            deleted_count += 1
            
        conn.commit()
        conn.close()
        if deleted_count > 0:
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
        cursor.execute("SELECT tg_id, amount, date, plan_id FROM transactions ORDER BY date DESC LIMIT 20")
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            await query.edit_message_text(
                t("sales_log_empty", lang),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back_admin", lang), callback_data='admin_panel')]]),
                parse_mode='Markdown'
            )
            return

        text = t("sales_log_title", lang)
        
        for row in rows:
            tg_id, amount, date_ts, plan_id = row
            date_str = datetime.datetime.fromtimestamp(date_ts, tz=TIMEZONE).strftime("%d.%m %H:%M")
            
            # Localize plan name
            plan_display = TEXTS[lang].get(f"plan_{plan_id}", plan_id)
            
            text += f"📅 `{date_str}` | 🆔 `{tg_id}`\n💳 {plan_display} | 💰 {amount} XTR\n\n"
            
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
        except Exception as e:
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
    except:
        pass

async def admin_reset_trial_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # admin_rt_db_TGID
    try:
        tg_id = query.data.split('_')[3]
    except:
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
    except:
        return
        
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_prefs WHERE tg_id=?", (tg_id,))
    cursor.execute("DELETE FROM user_promos WHERE tg_id=?", (tg_id,))
    conn.commit()
    conn.close()
    
    await query.edit_message_text(f"✅ Пользователь `{tg_id}` удален из базы бота.", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 В админ панель", callback_data='admin_panel')]]))

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
    if total_pages == 0: total_pages = 1
    
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
        if len(name_display) > 30: name_display = name_display[:27] + "..."
        
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
        
        # Sync users from X-UI DB to Bot DB to ensure all active clients are available
        try:
            conn_xui = sqlite3.connect(DB_PATH)
            cursor_xui = conn_xui.cursor()
            cursor_xui.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
            row = cursor_xui.fetchone()
            conn_xui.close()
            
            if row:
                settings = json.loads(row[0])
                clients = settings.get('clients', [])
                
                conn_bot = sqlite3.connect(BOT_DB_PATH)
                cursor_bot = conn_bot.cursor()
                
                for client in clients:
                    tg_id_client = client.get('tgId')
                    email = client.get('email', '')
                    
                    if tg_id_client:
                        tg_id_str = str(tg_id_client)
                        # Check if user exists
                        cursor_bot.execute("SELECT tg_id FROM user_prefs WHERE tg_id=?", (tg_id_str,))
                        if not cursor_bot.fetchone():
                            # Add basic info if missing
                            # Use email as first_name to identify user
                            cursor_bot.execute("INSERT INTO user_prefs (tg_id, lang, first_name) VALUES (?, ?, ?)", (tg_id_str, 'ru', email))
                
                conn_bot.commit()
                conn_bot.close()
        except Exception as e:
            logging.error(f"Error syncing users for broadcast: {e}")
        
        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT tg_id, first_name, username FROM user_prefs")
        users = cursor.fetchall()
        conn.close()
        
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
        except:
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
        except:
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
    if target == 'en': target_name = t("btn_broadcast_en", lang)
    if target == 'ru': target_name = t("btn_broadcast_ru", lang)
    
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
    text = update.message.text
    
    # Admin actions
    if tg_id == ADMIN_ID:
        action = context.user_data.get('admin_action')
        
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
                subprocess.run(["systemctl", "restart", "x-ui"])
                
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
            if not text: return
            try:
                parts = text.split()
                if len(parts) != 3:
                    raise ValueError
                code, days, limit = parts[0], int(parts[1]), int(parts[2])
                
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
            except:
                await update.message.reply_text("❌ Неверный формат. Используйте: `КОД ДНИ ЛИМИТ`")
            return



        elif action == 'awaiting_price_amount':
            try:
                if not text: raise ValueError
                amount = int(text)
                if amount <= 0: raise ValueError
                
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
                    order = ["1_month", "3_months", "6_months", "1_year"]
                    labels = {
                        "1_month": "1 Месяц",
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
            except:
                await update.message.reply_text("❌ Ошибка. Введите целое положительное число.")
            return

        elif action == 'awaiting_flash_duration':
            if not text: return
            try:
                duration = int(text)
                if duration <= 0: raise ValueError
                
                code = context.user_data.get('flash_code')
                
                # Start broadcasting
                status_msg = await update.message.reply_text("⏳ Запуск Flash-рассылки (ВСЕМ)...")
                
                # Fetch all users
                conn = sqlite3.connect(BOT_DB_PATH)
                cursor = conn.cursor()
                
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
                            if tid: users.append((str(tid),))
                except: pass
                
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
                msg_text = f"🔥 <b>УСПЕЙ ПОЙМАТЬ ПРОМОКОД!</b> 🔥\n\nУспей активировать секретный промокод!\n\n👇 Нажми, чтобы увидеть:\n<tg-spoiler><code>{code}</code></tg-spoiler>\n\n⏳ <b>Предложение сгорит в {end_time_str}</b>\n(через {duration} мин)"
                
                conn = sqlite3.connect(BOT_DB_PATH)
                cursor = conn.cursor()
                
                for user in users:
                    user_id = user[0]
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
                         pass
                
                conn.commit()
                conn.close()
                
                await status_msg.edit_text(f"✅ Flash-рассылка завершена.\n\n📤 Отправлено: {sent}\n🚫 Не доставлено: {blocked}\n⏱ Время жизни: {duration} мин.")
                
                context.user_data['admin_action'] = None
                context.user_data['flash_code'] = None
                
            except Exception as e:
                logging.error(f"Flash broadcast error: {e}")
                await update.message.reply_text("❌ Ошибка. Введите число минут.")
            return

        elif action == 'awaiting_broadcast_users_input':
            if not text: return
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
            if target == 'en': target_name = "English (en)"
            if target == 'ru': target_name = "Русский (ru)"
            if target == 'individual': target_name = f"Индивидуально: {len(users)}"
            
            status_msg = await update.message.reply_text(f"⏳ Рассылка запущена ({target_name})...")
            
            for user in users:
                user_id = user[0]
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
            if not text: return
            target_id = text.strip()
            # Simple validation
            if not target_id.isdigit():
                await update.message.reply_text("❌ Ошибка: ID должен состоять из цифр.")
                return
                
            await admin_user_db_detail(update, context, target_id)
            context.user_data['admin_action'] = None
            return

        elif action == 'awaiting_poll_question':
            if not text: return
            context.user_data['poll_question'] = text.strip()
            context.user_data['admin_action'] = 'awaiting_poll_options'
            
            await update.message.reply_text(t("poll_ask_options", lang))
            return
            
        elif action == 'awaiting_poll_options':
            if not text: return
            options = [opt.strip() for opt in text.split('\n') if opt.strip()]
            
            if len(options) < 2:
                await update.message.reply_text("❌ Ошибка: Должно быть минимум 2 варианта ответа.")
                return
            
            if len(options) > 10:
                 await update.message.reply_text("❌ Ошибка: Максимум 10 вариантов ответа.")
                 return
                 
            context.user_data['poll_options'] = options
            question = context.user_data.get('poll_question')
            
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

    if context.user_data.get('awaiting_promo'):
        if not text: return
        tg_id = str(update.message.from_user.id)
        lang = get_lang(tg_id)
        code = text.strip()
        
        days = check_promo(code, tg_id)
        
        if days == "USED":
             await update.message.reply_text(t("promo_used", lang))
        elif days is None:
             await update.message.reply_text(t("promo_invalid", lang))
        else:
             username = update.message.from_user.username or update.message.from_user.first_name
             log_action(f"ACTION: User {tg_id} (@{username}) redeemed promo code: {code} ({days} days).")
             redeem_promo_db(code, tg_id)
             
             # Send success message immediately
             await update.message.reply_text(t("promo_success", lang).format(days=days), parse_mode='Markdown')
             
             await process_subscription(tg_id, days, update, context, lang)
             
             # Celebration animation
             import asyncio
             msg = await update.message.reply_text("🎆")
             await asyncio.sleep(0.5)
             await msg.edit_text("🎆 🎇")
             await asyncio.sleep(0.5)
             await msg.edit_text("🎆 🎇 ✨")
             await asyncio.sleep(0.5)
             await msg.edit_text("🎉 ПРОМОКОД АКТИВИРОВАН! 🎉")
             
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
    title = t("invoice_title", lang)
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
    payment = update.message.successful_payment
    payload = payment.invoice_payload
    current_prices = get_prices()
    plan = current_prices.get(payload)
    
    if not plan:
        return

    tg_id = str(update.message.from_user.id)
    lang = get_lang(tg_id)
    days_to_add = plan['days']
    
    # Record transaction
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO transactions (tg_id, amount, date, plan_id) VALUES (?, ?, ?, ?)", 
                   (tg_id, plan['amount'], int(time.time()), payload))
    conn.commit()
    conn.close()
    
    log_action(f"ACTION: User {tg_id} (@{update.message.from_user.username}) purchased subscription: {payload} ({plan['amount']} XTR).")
    
    # Celebration animation for Payment
    import asyncio
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
        admin_msg = f"💰 *Новая продажа!*\n\n👤 Пользователь: @{buyer_username} (`{tg_id}`)\n💳 Тариф: {plan_name}\n💸 Сумма: {plan['amount']} Stars"
        
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_msg, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Failed to notify admin: {e}")

    await process_subscription(tg_id, days_to_add, update, context, lang)
    
    # Check Referral Bonus (7 days for referrer)
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT referrer_id FROM user_prefs WHERE tg_id=?", (tg_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row and row[0]:
        referrer_id = row[0]
        # Grant 7 days to referrer
        try:
            # We need to process subscription for referrer. 
            # Note: process_subscription usually expects 'update' to reply to.
            # But here we are processing for someone else (referrer).
            # We need a separate function or modify process_subscription to support 'silent' or 'notify_user' mode.
            # Let's call process_subscription but we can't pass 'update' because it points to 'payer'.
            # We will refactor logic slightly.
            
            # Actually, process_subscription uses 'update.message.reply_text' or 'edit_message_text'.
            # If we pass update, it replies to payer.
            # We need to notify referrer separately.
            
            await add_days_to_user(referrer_id, 7, context)
            
            # Notify referrer
            ref_lang = get_lang(referrer_id)
            msg_text = f"🎉 **Referral Bonus!**\n\nUser you invited has purchased a subscription.\nYou received +7 days!"
            if ref_lang == 'ru':
                msg_text = f"🎉 **Реферальный бонус!**\n\nПриглашенный вами пользователь купил подписку.\nВам начислено +7 дней!"
                
            try:
                await context.bot.send_message(chat_id=referrer_id, text=msg_text, parse_mode='Markdown')
            except:
                pass # User might have blocked bot
                
        except Exception as e:
            logging.error(f"Error granting referral bonus: {e}")

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

    cursor.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(settings), INBOUND_ID))
    conn.commit()
    conn.close()
    
    subprocess.run(["systemctl", "restart", "x-ui"])

async def process_subscription(tg_id, days_to_add, update, context, lang, is_callback=False):
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
            return
            
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
                
            # Update comment with latest nickname if available
            try:
                user = None
                if update.callback_query:
                    user = update.callback_query.from_user
                elif update.message:
                    user = update.message.from_user
                
                if user:
                    import re
                    uname = user.username
                    fname = user.first_name
                    base_name = uname if uname else fname
                    if not base_name: base_name = "User"
                    clean_name = re.sub(r'[^a-zA-Z0-9]', '', base_name)
                    if not clean_name: clean_name = "User"
                    
                    user_client['comment'] = clean_name
            except: pass
                
            user_client['expiryTime'] = new_expiry
            user_client['enable'] = True
            user_client['updated_at'] = current_time_ms
            clients[client_index] = user_client
            
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
            uname_val = "User"
            try:
                # Check DB first
                conn_db = sqlite3.connect(BOT_DB_PATH)
                cursor_db = conn_db.cursor()
                cursor_db.execute("SELECT username, first_name FROM user_prefs WHERE tg_id=?", (tg_id,))
                row_db = cursor_db.fetchone()
                conn_db.close()
                
                if row_db:
                    if row_db[0]: uname_val = row_db[0]
                    elif row_db[1]: uname_val = row_db[1]
                else:
                    # Fetch
                    chat = await context.bot.get_chat(tg_id)
                    if chat.username: uname_val = chat.username
                    elif chat.first_name: uname_val = chat.first_name
            except: pass
            
            import re
            clean_name = re.sub(r'[^a-zA-Z0-9]', '', uname_val)
            if not clean_name: clean_name = "User"
            
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
                "comment": clean_name,
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
                     conn.execute("UPDATE client_traffics SET expiry_time=? WHERE email=?", (new_expiry, email))
                 except: pass

        cursor.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(settings), INBOUND_ID))
        conn.commit()
        conn.close()
        
        subprocess.run(["systemctl", "restart", "x-ui"])
        
        if new_expiry == 0:
            if lang == 'ru':
                expiry_date = "Безлимит"
            else:
                expiry_date = "Unlimited"
        else:
            expiry_date = datetime.datetime.fromtimestamp(new_expiry / 1000, tz=TIMEZONE).strftime('%d.%m.%Y %H:%M')
        
        text = t(msg_key, lang).format(expiry=expiry_date)
        
        keyboard = [
            [InlineKeyboardButton(t("btn_config", lang), callback_data='get_config')],
            [InlineKeyboardButton(t("btn_instructions", lang), callback_data='instructions')],
            [InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]
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
            
            # Retrieve Reality Settings from Inbound Settings (row[0])
            inbound_settings_json = json.loads(row[0])
            stream_settings = inbound_settings_json.get('stream_settings', {})
            # Note: stream_settings might be a JSON string or dict depending on X-UI version
            # In previous tool output, we saw stream_settings as a key in row_dict, but here we only fetched 'settings' column from inbounds table.
            # Wait, the SELECT query was: SELECT settings FROM inbounds WHERE id=?
            # The 'settings' column in database only contains client list mostly.
            # The REAL stream settings are in 'stream_settings' column.
            # We need to fetch stream_settings column as well.
            pass
            
            # Direct VLESS link
            # vless://UUID@IP:PORT?type=tcp&encryption=none&security=reality&pbk=KEY&fp=chrome&sni=google.com&sid=b2&spx=%2F#tg_ID
            
            # We need to fetch stream_settings from DB to be accurate
            conn2 = sqlite3.connect(DB_PATH)
            cursor2 = conn2.cursor()
            cursor2.execute("SELECT stream_settings FROM inbounds WHERE id=?", (INBOUND_ID,))
            row_ss = cursor2.fetchone()
            conn2.close()
            
            spx_val = "%2F" # Default
            if row_ss:
                 try:
                     ss = json.loads(row_ss[0])
                     reality = ss.get('realitySettings', {})
                     settings_inner = reality.get('settings', {})
                     spiderX = settings_inner.get('spiderX', '/')
                     import urllib.parse
                     spx_val = urllib.parse.quote(spiderX)
                 except: pass

            flow_part = f"&flow={client_flow}" if client_flow else ""
            
            vless_link = f"vless://{u_uuid}@{IP}:{PORT}?type=tcp&encryption=none&security=reality&pbk={PUBLIC_KEY}&fp=chrome&sni={SNI}&sid={SID}&spx={spx_val}{flow_part}#{client_email}"
            
            # Subscription URL
            conn_set = sqlite3.connect(DB_PATH)
            cursor_set = conn_set.cursor()
            cursor_set.execute("SELECT key, value FROM settings WHERE key IN ('subEnable', 'subPort', 'subPath', 'webPort', 'webBasePath', 'webCertFile', 'subCertFile')")
            rows_set = cursor_set.fetchall()
            conn_set.close()
            
            settings_map = {k: v for k, v in rows_set}
            
            sub_enable = settings_map.get('subEnable', 'false') == 'true'
            sub_port = settings_map.get('subPort', '2096')
            sub_path = settings_map.get('subPath', '/sub/')
            web_port = settings_map.get('webPort', '2053')
            web_base_path = settings_map.get('webBasePath', '/')
            web_cert = settings_map.get('webCertFile', '')
            sub_cert = settings_map.get('subCertFile', '')
            
            protocol = "http"
            port = web_port
            path = sub_path
            
            if sub_enable:
                port = sub_port
                path = sub_path
                if sub_cert: protocol = "https"
            else:
                # Fallback to web port
                port = web_port
                # Ensure web_base_path ends with / if not empty
                if web_base_path and not web_base_path.endswith('/'):
                    web_base_path += '/'
                if not web_base_path.startswith('/'):
                     web_base_path = '/' + web_base_path
                     
                # path = web_base_path + sub_path (without leading slash if web_base_path has it)
                if sub_path.startswith('/'):
                    path = web_base_path + sub_path[1:]
                else:
                    path = web_base_path + sub_path
                    
                if web_cert: protocol = "https"

            sub_id = user_client.get('subId')
            if sub_id:
                sub_link = f"{protocol}://{IP}:{port}{path}{sub_id}"
            else:
                sub_link = f"{protocol}://{IP}:{port}{path}{u_uuid}"
            
            remaining_str = ""
            if expiry_ms == 0:
                expiry_str = t("expiry_unlimited", lang)
            else:
                expiry_str = datetime.datetime.fromtimestamp(expiry_ms / 1000, tz=TIMEZONE).strftime('%d.%m.%Y %H:%M')
                
                # Calculate remaining
                diff = expiry_ms - int(time.time() * 1000)
                if diff > 0:
                    days = diff / (1000 * 3600 * 24)
                    if days < 1:
                        hours = int(diff / (1000 * 3600))
                        if hours < 1: hours = 1
                        remaining_str = t("remaining_hours", lang).format(hours=hours)
                    else:
                        remaining_str = t("remaining_days", lang).format(days=int(days))
                
            msg_text = t("sub_active_html", lang).format(expiry=expiry_str)
            if remaining_str:
                msg_text += f"\n{remaining_str}"
            
            msg_text += t("sub_recommendation", lang).format(link=html.escape(sub_link), key=html.escape(vless_link))
            
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
                except:
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
                except:
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
             except:
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
            cursor_bot.execute("SELECT plan_id FROM transactions WHERE tg_id=? ORDER BY date DESC LIMIT 1", (tg_id,))
            last_tx = cursor_bot.fetchone()
            if last_tx:
                p_id = last_tx[0]
                sub_plan = t(f"plan_{p_id}", lang)
            else:
                cursor_bot.execute("SELECT trial_used FROM user_prefs WHERE tg_id=?", (tg_id,))
                pref = cursor_bot.fetchone()
                if pref and pref[0]:
                    sub_plan = t("plan_trial", lang)

        now = datetime.datetime.now(TIMEZONE)
        today_str = now.strftime("%Y-%m-%d")
        
        # 1. Day (Today usage)
        # Usage = Current - (Value at start of day OR yesterday's end)
        # We need the value from YESTERDAY to calculate Today's usage.
        yesterday_str = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        cursor_bot.execute("SELECT up, down FROM traffic_history WHERE email=? AND date=?", (email, yesterday_str))
        yesterday_row = cursor_bot.fetchone()
        
        if yesterday_row:
            day_up = max(0, current_up - yesterday_row[0])
            day_down = max(0, current_down - yesterday_row[1])
        else:
            # If no yesterday record, assume today is first day or all current is today? 
            # Or maybe we have a record for today that is updated hourly.
            # But 'current' is live.
            # If we don't have yesterday, maybe try to find max from previous days?
            # For simplicity, if no history, show current as today (not accurate but fallback)
            day_up = current_up
            day_down = current_down
            
        # 2. Week (Last 7 days)
        week_start = (now - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        cursor_bot.execute("SELECT up, down FROM traffic_history WHERE email=? AND date=?", (email, week_start))
        week_row = cursor_bot.fetchone()
        
        if week_row:
            week_up = max(0, current_up - week_row[0])
            week_down = max(0, current_down - week_row[1])
        else:
            # Try to find oldest record within 7 days
            cursor_bot.execute("SELECT up, down FROM traffic_history WHERE email=? AND date >= ? ORDER BY date ASC LIMIT 1", (email, week_start))
            oldest_week_row = cursor_bot.fetchone()
            if oldest_week_row:
                week_up = max(0, current_up - oldest_week_row[0])
                week_down = max(0, current_down - oldest_week_row[1])
            else:
                week_up = current_up
                week_down = current_down

        # 3. Month (Last 30 days)
        month_start = (now - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
        cursor_bot.execute("SELECT up, down FROM traffic_history WHERE email=? AND date >= ? ORDER BY date ASC LIMIT 1", (email, month_start))
        month_row = cursor_bot.fetchone()
        
        if month_row:
            month_up = max(0, current_up - month_row[0])
            month_down = max(0, current_down - month_row[1])
        else:
            month_up = current_up
            month_down = current_down
            
        conn_bot.close()
        
        # Expiry formatting
        if expiry_time == 0:
            expiry_str = t("unlimited_text", lang)
        else:
            expiry_dt = datetime.datetime.fromtimestamp(expiry_time / 1000, tz=TIMEZONE)
            expiry_str = expiry_dt.strftime("%d.%m.%Y %H:%M")
            
        text = f"""{t("stats_your_title", lang)}

{t("stats_sub_type", lang).format(plan=sub_plan)}

{t("stats_today", lang)}
⬇️ {format_bytes(day_down)}  ⬆️ {format_bytes(day_up)}

{t("stats_week", lang)}
⬇️ {format_bytes(week_down)}  ⬆️ {format_bytes(week_up)}

{t("stats_month", lang)}
⬇️ {format_bytes(month_down)}  ⬆️ {format_bytes(month_up)}

{t("stats_total", lang)}
⬇️ {format_bytes(current_down)}  ⬆️ {format_bytes(current_up)}
∑ {format_bytes(current_total)}

{t("stats_expires", lang)} {expiry_str}"""

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
    
    platform = query.data.split('_')[1] # android, ios, pc
    text = t(f"instr_{platform}", lang)
    
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
            
        conn_bot.commit()
        conn_bot.close()
        
    except Exception as e:
        logging.error(f"Error logging traffic: {e}")

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
        one_day_ms = 24 * 60 * 60 * 1000
        
        for client in clients:
            expiry_time = client.get('expiryTime', 0)
            tg_id = client.get('tgId')
            
            if expiry_time > 0 and tg_id:
                time_left = expiry_time - current_time
                
                if 0 < time_left <= one_day_ms:
                     try:
                        # Fetch user lang
                        user_lang = get_lang(tg_id)
                        await context.bot.send_message(
                            chat_id=tg_id,
                            text=t("expiry_warning", user_lang),
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_renew", user_lang), callback_data='shop')]]),
                            parse_mode='Markdown'
                        )
                        logging.info(f"Sent expiry warning to {tg_id}")
                     except Exception as ex:
                        logging.warning(f"Failed to send warning to {tg_id}: {ex}")

        conn.close()
    except Exception as e:
        logging.error(f"Error in check_expiring_subscriptions: {e}")

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

async def admin_delete_client_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # admin_del_client_ask_UUID
    try:
        uid = query.data.split('_', 4)[4]
    except:
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
    except:
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
        except: pass
        
    conn.commit()
    conn.close()
    
    # Restart X-UI
    subprocess.run(["systemctl", "restart", "x-ui"])
    
    await query.edit_message_text(
        f"✅ Клиент успешно удален из X-UI.\nX-UI перезапущен.",
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
    except:
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
    except:
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
    except: pass
    
    all_users = set([u[0] for u in users] + xui_users)
    
    sent = 0
    blocked = 0
    
    status_msg = await query.edit_message_text(f"⏳ Рассылка опроса запущена ({len(all_users)} пользователей)...")
    
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
            
    await status_msg.edit_text(
        f"✅ Рассылка опроса завершена.\n\n📤 Отправлено: {sent}\n🚫 Не доставлено: {blocked}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back_admin", lang), callback_data='admin_poll_menu')]])
    )

if __name__ == '__main__':
    init_db()
    application = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CallbackQueryHandler(set_language, pattern='^set_lang_'))
    application.add_handler(CallbackQueryHandler(change_lang, pattern='^change_lang$'))
    application.add_handler(CallbackQueryHandler(shop, pattern='^shop$'))
    application.add_handler(CallbackQueryHandler(back_to_main, pattern='^back_to_main$'))
    application.add_handler(CallbackQueryHandler(initiate_payment, pattern='^buy_'))
    application.add_handler(CallbackQueryHandler(get_config, pattern='^get_config$'))
    application.add_handler(CallbackQueryHandler(stats, pattern='^stats$'))
    application.add_handler(CallbackQueryHandler(try_trial, pattern='^try_trial$'))
    application.add_handler(CallbackQueryHandler(enter_promo, pattern='^enter_promo$'))
    application.add_handler(CallbackQueryHandler(referral, pattern='^referral$'))
    application.add_handler(CallbackQueryHandler(instructions, pattern='^instructions$'))
    application.add_handler(CallbackQueryHandler(show_instruction, pattern='^instr_'))
    
    application.add_handler(CommandHandler('admin', admin_panel))
    application.add_handler(CallbackQueryHandler(admin_panel, pattern='^admin_panel$'))
    application.add_handler(CallbackQueryHandler(admin_stats, pattern='^admin_stats$'))
    application.add_handler(CallbackQueryHandler(admin_sync_nicknames, pattern='^admin_sync_nicks$'))
    application.add_handler(CallbackQueryHandler(admin_server, pattern='^admin_server$'))
    application.add_handler(CallbackQueryHandler(admin_server_live, pattern='^admin_server_live$'))
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
    application.add_handler(CallbackQueryHandler(admin_revoke_promo_menu, pattern='^admin_revoke_menu_'))
    application.add_handler(CallbackQueryHandler(admin_revoke_promo_confirm, pattern='^admin_revoke_conf_'))
    application.add_handler(CallbackQueryHandler(admin_revoke_promo_action, pattern='^admin_revoke_act_'))
    application.add_handler(CallbackQueryHandler(admin_broadcast, pattern='^admin_broadcast$'))
    application.add_handler(CallbackQueryHandler(admin_broadcast_target, pattern='^admin_broadcast_(all|en|ru|individual|toggle|page|confirm).*'))
    application.add_handler(CallbackQueryHandler(admin_poll_menu, pattern='^admin_poll_menu$'))
    application.add_handler(CallbackQueryHandler(admin_poll_new, pattern='^admin_poll_new$'))
    application.add_handler(CallbackQueryHandler(admin_poll_send, pattern='^admin_poll_send$'))
    application.add_handler(CallbackQueryHandler(handle_poll_vote, pattern='^poll_vote_'))
    application.add_handler(CallbackQueryHandler(handle_poll_refresh, pattern='^poll_refresh_'))
    application.add_handler(CallbackQueryHandler(admin_sales_log, pattern='^admin_sales_log$'))
    application.add_handler(CallbackQueryHandler(admin_create_backup, pattern='^admin_create_backup$'))
    application.add_handler(CallbackQueryHandler(admin_view_logs, pattern='^admin_logs$'))
    application.add_handler(CallbackQueryHandler(admin_clear_logs, pattern='^admin_clear_logs$'))
    
    application.add_handler(CallbackQueryHandler(admin_search_user, pattern='^admin_search_user$'))
    application.add_handler(CallbackQueryHandler(admin_db_detail_callback, pattern='^admin_db_detail_'))
    application.add_handler(CallbackQueryHandler(admin_reset_trial_db, pattern='^admin_rt_db_'))
    application.add_handler(CallbackQueryHandler(admin_delete_user_db, pattern='^admin_del_db_'))
    application.add_handler(CallbackQueryHandler(admin_delete_client_ask, pattern='^admin_del_client_ask_'))
    application.add_handler(CallbackQueryHandler(admin_delete_client_confirm, pattern='^admin_del_client_confirm_'))
    
    application.add_handler(CallbackQueryHandler(admin_flash_menu, pattern='^admin_flash_menu$'))
    application.add_handler(CallbackQueryHandler(admin_flash_select, pattern='^admin_flash_sel_'))
    
    application.add_handler(MessageHandler(~filters.COMMAND, handle_message))
    
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    
    job_queue = application.job_queue
    job_queue.run_repeating(check_expiring_subscriptions, interval=86400, first=10)
    job_queue.run_repeating(log_traffic_stats, interval=3600, first=5) # Every hour
    job_queue.run_repeating(cleanup_flash_messages, interval=60, first=10) # Every minute
    
    print("Bot started...")
    application.run_polling()
