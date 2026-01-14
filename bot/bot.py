import logging
import sqlite3
import json
import uuid
import subprocess
import time
import datetime
import shutil
import os
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

# Load environment variables
load_dotenv()

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
PUBLIC_KEY = "T6c3nRb47HsltG6ojFbNImgouFB5ii6UrYYIs9xPf1A"
IP = "93.88.205.120"
PORT = 17343
SNI = "google.com"
SID = "b2"
TIMEZONE = ZoneInfo("Europe/Moscow")


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
        "shop_title": "🛒 **Select a Plan:**\n\nPay safely with Telegram Stars.",
        "btn_back": "🔙 Back",
        "label_1_month": "1 Month Subscription",
        "label_3_months": "3 Months Subscription",
        "label_6_months": "6 Months Subscription",
        "label_1_year": "1 Year Subscription",
        "invoice_title": "Maxi_VPN Subscription",
        "success_created": "✅ **Success!** Subscription created.\n\n📅 New Expiry: {expiry}\n\nUse '🚀 My Config' to get your connection key.",
        "success_extended": "✅ **Success!** Subscription extended.\n\n📅 New Expiry: {expiry}\n\nUse '🚀 My Config' to get your connection key.",
        "error_generic": "An error occurred. Please contact support.",
        "sub_expired": "⚠️ **Subscription Expired**\n\nYour subscription has expired. Please buy a new plan to restore access.",
        "sub_active": "✅ **Your Subscription is Active**\n\n📅 Expires: {expiry}\n\nKey:\n`{link}`",
        "sub_not_found": "❌ **No Subscription Found**\n\nYou don't have an active subscription. Please visit the shop.",
        "stats_title": "📊 **Your Stats**\n\n⬇️ Download: {down:.2f} GB\n⬆️ Upload: {up:.2f} GB\n📦 Total: {total:.2f} GB",
        "stats_no_sub": "No stats found. Subscription required.",
        "expiry_warning": "⚠️ **Subscription Expiring Soon!**\n\nYour VPN subscription will expire in less than 24 hours.\nPlease renew it to avoid service interruption.",
        "btn_renew": "💎 Renew Now",
        "btn_instructions": "📚 Setup Instructions",
        "lang_sel": "Language selected: English 🇬🇧",
        "trial_used": "⚠️ **Trial Already Used**\n\nYou have already used your trial period.\nActivated: {date}",
        "trial_activated": "🎉 **Trial Activated!**\n\nYou have received 3 days of free access.\nCheck '🚀 My Config' to connect.",
        "ref_title": "👥 **Referral Program**\n\nInvite friends and get bonuses!\n\n🔗 Your Link:\n`{link}`\n\n🎁 You have invited: {count} users.",
        "promo_prompt": "🎁 **Redeem Promo Code**\n\nPlease enter your promo code:",
        "promo_success": "✅ **Promo Code Redeemed!**\n\nAdded {days} days to your subscription.",
        "promo_invalid": "❌ **Invalid or Expired Code**",
        "promo_used": "⚠️ **Code Already Used**",
        "instr_menu": "📚 **Setup Instructions**\n\nChoose your device:",
        "btn_android": "📱 Android (v2RayTun)",
        "btn_ios": "🍎 iOS (V2Box)",
        "btn_pc": "💻 PC (Amnezia/Hiddify)",
        "instr_android": "📱 **Android Setup**\n\n1. Install **[v2RayTun](https://play.google.com/store/apps/details?id=com.v2raytun.android)** from Google Play.\n2. Copy your key from '🚀 My Config'.\n3. Open v2RayTun -> Tap 'Import' -> 'Import from Clipboard'.\n4. Tap the connection button.",
        "instr_ios": "🍎 **iOS Setup**\n\n1. Install **[V2Box](https://apps.apple.com/app/v2box-v2ray-client/id6446814690)** from App Store.\n2. Copy your key from '🚀 My Config'.\n3. Open V2Box, it should detect the key automatically.\n4. Tap 'Import' and then swipe to connect.",
        "instr_pc": "💻 **PC Setup**\n\n1. Install **[AmneziaVPN](https://amnezia.org/)** or **[Hiddify](https://github.com/hiddify/hiddify-next/releases)**.\n2. Copy your key from '🚀 My Config'.\n3. Open the app and paste the key (Import from Clipboard).\n4. Connect.",
        "plan_1_month": "1 Month",
        "plan_3_months": "3 Months",
        "plan_6_months": "6 Months",
        "plan_1_year": "1 Year",
        "plan_trial": "Trial (3 Days)",
        "plan_manual": "Manual",
        "plan_unlimited": "Unlimited",
        "sub_type_unknown": "Unknown",
        "stats_sub_type": "💳 Plan: {plan}",
        "rank_info": "\n🏆 **Your Rank:** #{rank} of {total}\n(Top {percent}% - Extend subscription to rank up!)"
    },
    "ru": {
        "welcome": "Добро пожаловать в Maxi_VPN! 🛡️\n\nПожалуйста, выберите язык:",
        "main_menu": "🚀 *Maxi_VPN* — Твой пропуск в свободный интернет!\n\n⚡️ Высокая скорость, анонимность и доступ к любым сервисам.\n💎 Оплата в один клик через Telegram Stars.",
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
        "error_generic": "Произошла ошибка. Пожалуйста, свяжитесь с поддержкой.",
        "sub_expired": "⚠️ *Подписка истекла*\n\nВаша подписка закончилась. Пожалуйста, купите новый план для восстановления доступа.",
        "sub_active": "✅ *Ваша подписка активна*\n\n📅 Истекает: {expiry}\n\nКлюч:\n`{link}`",
        "sub_not_found": "❌ *Подписка не найдена*\n\nУ вас нет активной подписки. Пожалуйста, перейдите в магазин.",
        "stats_title": "📊 *Ваша статистика*\n\n⬇️ Скачано: {down:.2f} GB\n⬆️ Загружено: {up:.2f} GB\n📦 Всего: {total:.2f} GB",
        "stats_no_sub": "Статистика не найдена. Требуется подписка.",
        "expiry_warning": "⚠️ *Подписка скоро истекает!*\n\nВаша VPN подписка истечет менее чем через 24 часа.\nПожалуйста, продлите её, чтобы избежать отключения.",
        "btn_renew": "💎 Продлить сейчас",
        "btn_instructions": "📚 Инструкция по настройке",
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
        "rank_info": "\n\n🏆 *Ваш статус в клубе:*\nВы занимаете *{rank}-е место* в рейтинге подписок из {total}.\n💡 Продлите подписку на больший срок, чтобы стать лидером!"
    }
}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

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
    
    conn.commit()
    conn.close()

def get_lang(tg_id):
    try:
        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT lang FROM user_prefs WHERE tg_id=?", (str(tg_id),))
        row = cursor.fetchone()
        conn.close()
        if row:
            return row[0]
    except Exception as e:
        logging.error(f"DB Error: {e}")
    return "en"

def set_lang(tg_id, lang):
    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO user_prefs (tg_id, lang) VALUES (?, ?)", (str(tg_id), lang))
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

    if not row:
        # Show language selection
        keyboard = [
            [InlineKeyboardButton("English 🇬🇧", callback_data='set_lang_en')],
            [InlineKeyboardButton("Русский 🇷🇺", callback_data='set_lang_ru')]
        ]
        await update.message.reply_text("Please select your language / Пожалуйста, выберите язык:", reply_markup=InlineKeyboardMarkup(keyboard))
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

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, lang):
    tg_id = str(update.message.from_user.id)
    keyboard = [
        [InlineKeyboardButton(t("btn_buy", lang), callback_data='shop')],
        [InlineKeyboardButton(t("btn_trial", lang), callback_data='try_trial'), InlineKeyboardButton(t("btn_promo", lang), callback_data='enter_promo')],
        [InlineKeyboardButton(t("btn_config", lang), callback_data='get_config'), InlineKeyboardButton(t("btn_stats", lang), callback_data='stats')],
        [InlineKeyboardButton(t("btn_ref", lang), callback_data='referral')]
    ]
    if tg_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👮‍♂️ Админ панель", callback_data='admin_panel')])
        
    text = t("main_menu", lang)
    rank, total, percent = get_user_rank(tg_id)
    if rank:
        text += t("rank_info", lang).format(rank=rank, total=total, percent=percent)
        
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def show_main_menu_query(query, context, lang):
    tg_id = str(query.from_user.id)
    keyboard = [
        [InlineKeyboardButton(t("btn_buy", lang), callback_data='shop')],
        [InlineKeyboardButton(t("btn_trial", lang), callback_data='try_trial'), InlineKeyboardButton(t("btn_promo", lang), callback_data='enter_promo')],
        [InlineKeyboardButton(t("btn_config", lang), callback_data='get_config'), InlineKeyboardButton(t("btn_stats", lang), callback_data='stats')],
        [InlineKeyboardButton(t("btn_ref", lang), callback_data='referral')]
    ]
    if tg_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👮‍♂️ Админ панель", callback_data='admin_panel')])
        
    text = t("main_menu", lang)
    rank, total, percent = get_user_rank(tg_id)
    if rank:
        text += t("rank_info", lang).format(rank=rank, total=total, percent=percent)
        
    await context.bot.send_message(chat_id=query.from_user.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

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
    
    await query.edit_message_text(
        t("shop_title", lang),
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
        [InlineKeyboardButton(t("btn_ref", lang), callback_data='referral')]
    ]
    if tg_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👮‍♂️ Админ панель", callback_data='admin_panel')])

    text = t("main_menu", lang)
    rank, total, percent = get_user_rank(tg_id)
    if rank:
        text += t("rank_info", lang).format(rank=rank, total=total, percent=percent)

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

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
            
        await query.edit_message_text(
            t("trial_used", lang).format(date=date_str),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]]),
            parse_mode='Markdown'
        )
        return

    # Activate 3 days
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
    
    await query.edit_message_text(
        t("ref_title", lang).format(link=link, count=count),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]]),
        parse_mode='Markdown'
    )

async def enter_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    lang = get_lang(tg_id)
    
    await query.edit_message_text(
        t("promo_prompt", lang),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]]),
        parse_mode='Markdown'
    )
    context.user_data['awaiting_promo'] = True

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
    
    keyboard = [
        [InlineKeyboardButton("📊 Статистика", callback_data='admin_stats')],
        [InlineKeyboardButton("🖥 Сервер", callback_data='admin_server')],
        [InlineKeyboardButton("💰 Настройка цен", callback_data='admin_prices')],
        [InlineKeyboardButton("🎁 Создать промокод", callback_data='admin_new_promo')],
        [InlineKeyboardButton("📢 Рассылка", callback_data='admin_broadcast')],
        [InlineKeyboardButton("📜 Журнал продаж", callback_data='admin_sales_log')],
        [InlineKeyboardButton("🔙 Главное меню", callback_data='back_to_main')]
    ]
    
    # We use edit_message_text if callback, reply if command
    if query:
        await query.edit_message_text("👮‍♂️ **Админ панель**\n\nВыберите действие:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.message.reply_text("👮‍♂️ **Админ панель**\n\nВыберите действие:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

def get_system_stats():
    # CPU
    try:
        with open('/proc/stat', 'r') as f:
            line = f.readline()
            parts = line.split()
            total_1 = sum(int(x) for x in parts[1:])
            idle_1 = int(parts[4])
        
        time.sleep(0.5)
        
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
        'disk_free': disk_free_gb
    }

async def admin_server(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Обновление данных...")
    
    stats = get_system_stats()
    
    text = f"""🖥 **Состояние сервера**

🧠 **CPU:** {stats['cpu']:.1f}%
💾 **RAM:** {stats['ram_usage']:.1f}% ({stats['ram_used']:.2f} / {stats['ram_total']:.2f} GB)
💿 **Disk:** {stats['disk_usage']:.1f}%
├ Использовано: {stats['disk_used']:.2f} GB
├ Свободно: {stats['disk_free']:.2f} GB
└ Всего: {stats['disk_total']:.2f} GB

🔄 Обновлено: {datetime.datetime.now(TIMEZONE).strftime("%H:%M:%S")}"""

    keyboard = [
        [InlineKeyboardButton("🔄 Обновить", callback_data='admin_server')],
        [InlineKeyboardButton("🔙 Назад", callback_data='admin_panel')]
    ]
    
    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    except Exception as e:
        # If message content is same (Telegram API error), we just ignore or answer
        if "Message is not modified" not in str(e):
             await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    current_prices = get_prices()
    
    keyboard = []
    order = ["1_month", "3_months", "6_months", "1_year"]
    labels = {
        "1_month": "1 Месяц",
        "3_months": "3 Месяца",
        "6_months": "6 Месяцев",
        "1_year": "1 Год"
    }
    
    for key in order:
        if key in current_prices:
            amount = current_prices[key]['amount']
            keyboard.append([InlineKeyboardButton(f"{labels[key]}: {amount} ⭐️ (Изменить)", callback_data=f'admin_edit_price_{key}')])
            
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='admin_panel')])
    
    await query.edit_message_text(
        "💰 **Настройка цен**\n\nВыберите тариф для изменения стоимости:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def admin_edit_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    key = query.data.split('_', 3)[3] # admin_edit_price_KEY
    
    context.user_data['edit_price_key'] = key
    context.user_data['admin_action'] = 'awaiting_price_amount'
    
    labels = {
        "1_month": "1 Месяц",
        "3_months": "3 Месяца",
        "6_months": "6 Месяцев",
        "1_year": "1 Год"
    }
    
    await query.edit_message_text(
        f"✏️ **Изменение цены: {labels.get(key, key)}**\n\nВведите новую стоимость в Telegram Stars (целое число):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data='admin_prices')]]),
        parse_mode='Markdown'
    )

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
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

    text = f"""📊 **Статистика**
    
👥 **Пользователи бота:** {total_users}
⚡ **Пользователи онлайн:** {online_users}
🔌 **Всего клиентов:** {total_clients}
✅ **Активные клиенты:** {active_subs}
🆓 **Пробные подписки:** {active_trials}
❌ **Истекшие пробные:** {expired_trials}
💰 **Выручка:** {total_revenue} ⭐️
🛒 **Продажи:** {total_sales}
"""
    keyboard = [
        [
            InlineKeyboardButton("👥 Все", callback_data='admin_users_all_0'),
            InlineKeyboardButton("🟢 Активные", callback_data='admin_users_active_0'),
            InlineKeyboardButton("⏳ Скоро истекают", callback_data='admin_users_expiring_0')
        ],
        [
            InlineKeyboardButton("⚡ Онлайн", callback_data='admin_users_online_0'),
            InlineKeyboardButton("🆓 Пробный период", callback_data='admin_users_trial_0')
        ],
        [InlineKeyboardButton("🔙 Назад", callback_data='admin_panel')]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_users_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
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
            await query.edit_message_text("❌ Входящее соединение не найдено.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data='admin_stats')]]))
            return

        settings = json.loads(row[0])
        clients = settings.get('clients', [])
        
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
            display_items.append({
                'label': f"{status} {email}",
                'callback': f"admin_u_{uid}"
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
        keyboard.append([InlineKeyboardButton(item['label'], callback_data=item['callback'])])
        
    # Navigation
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️", callback_data=f'admin_users_{filter_type}_{page-1}'))
    
    filter_icons = {'all': '👥', 'active': '🟢', 'expiring': '⏳', 'online': '⚡', 'trial': '🆓'}
    nav_row.append(InlineKeyboardButton(f"{filter_icons.get(filter_type, '')} {page+1}/{total_pages}", callback_data='noop'))
    
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("➡️", callback_data=f'admin_users_{filter_type}_{page+1}'))
    
    keyboard.append(nav_row)
        
    keyboard.append([InlineKeyboardButton("🔙 Назад к статистике", callback_data='admin_stats')])
    
    title_map = {'all': 'Все клиенты', 'active': 'Активные клиенты', 'expiring': 'Скоро истекают (<7д)', 'online': 'Онлайн клиенты', 'trial': 'Использовали пробный (Все)'}
    await query.edit_message_text(f"📋 **{title_map.get(filter_type, 'Clients')}**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_reset_trial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
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
        
        await context.bot.send_message(chat_id=query.from_user.id, text=f"✅ Пробный период для {client.get('email')} сброшен.")
        
        # Refresh details
        await admin_user_detail(update, context)
    else:
         await context.bot.send_message(chat_id=query.from_user.id, text="❌ Не удалось найти Telegram ID пользователя.")

async def admin_user_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
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
        await query.edit_message_text("❌ Клиент не найден.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data='admin_users_0')]]))
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
    
    limit_str = f"{total_limit / (1024**3):.2f} GB" if total_limit > 0 else "♾️ Безлимит"
    
    current_time_ms = int(time.time() * 1000)
    
    # Online status (10 seconds threshold)
    is_online = (current_time_ms - last_online) < 10 * 1000 if last_online > 0 else False
    online_status = "🟢 Онлайн" if is_online else "🔴 Офлайн"
    
    # Active status
    is_enabled_str = "✅ Да" if enable_val else "❌ Нет"
    
    # Subscription status
    is_sub_active = (expiry_ms == 0) or (expiry_ms > current_time_ms)
    sub_active_str = "✅ Да" if is_sub_active else "❌ Нет"
    
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
    trial_status_str = "❓ Неизвестно"
    show_reset_trial = False
    
    if client.get('tgId'):
        tg_id_val = str(client.get('tgId'))
        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT trial_used FROM user_prefs WHERE tg_id=?", (tg_id_val,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            if row[0]:
                trial_status_str = "✅ Использован"
                show_reset_trial = True
            else:
                trial_status_str = "❌ Не использован"
        else:
             trial_status_str = "❌ Не использован (нет в базе)"
    else:
         tg_id_val = "Не привязан"
    
    text = f"""📧 Email: {email}
🆔 TG ID: {tg_id_val}
🔌 Включен: {is_enabled_str}
📶 Соединение: {online_status}
📅 Подписка: {sub_active_str}
🆓 Пробный период: {trial_status_str}
⏳ Истекает через: {hours_left} Часов
🔼 Исходящий трафик: ↑{up_gb:.2f}GB
🔽 Входящий трафик: ↓{down_gb:.2f}GB
📊 Всего: ↑↓{total_used_gb:.2f}GB из {limit_str}

🔄 Обновлено: {current_time_str}"""
    
    keyboard = []
    if show_reset_trial:
        keyboard.append([InlineKeyboardButton("🔄 Сбросить пробный период", callback_data=f'admin_reset_trial_{uid}')])
        
    keyboard.append([InlineKeyboardButton("🔄 Перепривязать пользователя", callback_data=f'admin_rebind_{uid}')])
    keyboard.append([InlineKeyboardButton("🔙 Назад к списку", callback_data='admin_users_0')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_rebind_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Expected format: admin_rebind_UUID
    try:
        # Split by 'admin_rebind_' and take the rest
        # data: admin_rebind_123-456
        uid = query.data[len("admin_rebind_"):]
    except IndexError:
        await query.message.reply_text("Ошибка: некорректный ID")
        return

    context.user_data['rebind_uid'] = uid
    context.user_data['admin_action'] = 'awaiting_rebind_contact'
    
    keyboard = [
        [KeyboardButton("👤 Выбрать пользователя", request_users=KeyboardButtonRequestUsers(request_id=1, user_is_bot=False, max_quantity=1))],
        [KeyboardButton("🔙 Отмена")]
    ]
    
    # We need to send a new message for reply keyboard, or delete previous and send new
    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text=f"👤 **Перепривязка пользователя**\nUUID: `{uid}`\n\nПожалуйста, выберите пользователя через кнопку ниже или отправьте контакт.",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True),
        parse_mode='Markdown'
    )

async def admin_new_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "🎁 **Создать промокод**\n\nОтправьте детали промокода в формате:\n`CODE DAYS LIMIT`\n\nПример: `NEWYEAR 30 100`",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data='admin_panel')]]),
        parse_mode='Markdown'
    )
    context.user_data['admin_action'] = 'awaiting_promo_data'

async def admin_search_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "🔍 **Поиск пользователя**\n\nОтправьте **Telegram ID** пользователя для поиска в базе данных.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data='admin_panel')]]),
        parse_mode='Markdown'
    )
    context.user_data['admin_action'] = 'awaiting_search_user'


async def admin_sales_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        conn = sqlite3.connect(BOT_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT tg_id, amount, date, plan_id FROM transactions ORDER BY date DESC LIMIT 20")
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            await query.edit_message_text(
                "📜 **Журнал продаж**\n\nПродаж пока нет.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data='admin_panel')]]),
                parse_mode='Markdown'
            )
            return

        text = "📜 **Журнал продаж (последние 20)**\n\n"
        
        for row in rows:
            tg_id, amount, date_ts, plan_id = row
            date_str = datetime.datetime.fromtimestamp(date_ts, tz=TIMEZONE).strftime("%d.%m %H:%M")
            
            # Try to localize plan name using Russian as default for admin
            plan_display = TEXTS['ru'].get(f"plan_{plan_id}", plan_id)
            
            text += f"📅 `{date_str}` | 🆔 `{tg_id}`\n💳 {plan_display} | 💰 {amount} XTR\n\n"
            
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data='admin_panel')]]),
            parse_mode='Markdown'
        )
    except Exception as e:
        logging.error(f"Error in sales log: {e}")
        await query.edit_message_text("Ошибка при загрузке лога.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data='admin_panel')]]))

async def admin_user_db_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, tg_id):
    user_data = get_user_data(tg_id)
    lang = get_lang(tg_id)
    
    trial_status = "❌ Не использован"
    trial_date = ""
    if user_data['trial_used']:
        trial_status = "✅ Использован"
        if user_data.get('trial_activated_at'):
            trial_date = datetime.datetime.fromtimestamp(user_data['trial_activated_at'], tz=TIMEZONE).strftime("%d.%m.%Y %H:%M")
            
    text = f"""👤 **Информация о пользователе (DB)**
    
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
    
    await query.edit_message_text(
        "📢 **Рассылка сообщений**\n\nОтправьте сообщение, которое хотите отправить ВСЕМ пользователям.\nПоддерживается Markdown.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data='admin_panel')]]),
        parse_mode='Markdown'
    )
    context.user_data['admin_action'] = 'awaiting_broadcast'

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.message.from_user.id)
    lang = get_lang(tg_id)
    text = update.message.text
    
    # Admin actions
    if tg_id == ADMIN_ID:
        action = context.user_data.get('admin_action')
        
        # Handle Cancel Button for Rebind
        if text == "🔙 Отмена" and action == 'awaiting_rebind_contact':
            context.user_data['admin_action'] = None
            context.user_data['rebind_uid'] = None
            await update.message.reply_text("🔙 Отменено.", reply_markup=ReplyKeyboardRemove())
            # Show admin panel again
            keyboard = [
                [InlineKeyboardButton("📊 Статистика", callback_data='admin_stats')],
                [InlineKeyboardButton("🎁 Создать промокод", callback_data='admin_new_promo')],
                [InlineKeyboardButton("📢 Рассылка", callback_data='admin_broadcast')],
        [InlineKeyboardButton("📜 Журнал продаж", callback_data='admin_sales_log')],
        [InlineKeyboardButton("🔙 Главное меню", callback_data='back_to_main')]
    ]
            await update.message.reply_text("👮‍♂️ **Админ панель**\n\nВыберите действие:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
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
            for client in clients:
                if client.get('id') == uid:
                    client['tgId'] = int(target_tg_id) if target_tg_id.isdigit() else target_tg_id
                    client['updated_at'] = int(time.time() * 1000)
                    client_email = client.get('email')
                    found = True
                    break
            
            if found:
                new_settings = json.dumps(settings, indent=2)
                cursor.execute("UPDATE inbounds SET settings=? WHERE id=?", (new_settings, INBOUND_ID))
                conn.commit()
                conn.close()
                
                # Restart X-UI
                subprocess.run(["systemctl", "restart", "x-ui"])
                
                await update.message.reply_text(f"✅ **Успешно!**\nКлиент `{client_email}` перепривязан к Telegram ID `{target_tg_id}`.\nX-UI перезапущен.", parse_mode='Markdown', reply_markup=ReplyKeyboardRemove())
                
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
                
                await update.message.reply_text(f"✅ Promo `{code}` created for {days} days ({limit} uses).")
                context.user_data['admin_action'] = None
            except:
                await update.message.reply_text("❌ Invalid format. Use: `CODE DAYS LIMIT`")
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

        elif action == 'awaiting_broadcast':
            if not text: return
            msg = text
            conn = sqlite3.connect(BOT_DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT tg_id FROM user_prefs")
            users = cursor.fetchall()
            conn.close()
            
            sent = 0
            for user in users:
                try:
                    await context.bot.send_message(chat_id=user[0], text=msg, parse_mode='Markdown')
                    sent += 1
                except: pass
            
            await update.message.reply_text(f"✅ Broadcast sent to {sent} users.")
            context.user_data['admin_action'] = None
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
             redeem_promo_db(code, tg_id)
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
             
             await update.message.reply_text(t("promo_success", lang).format(days=days), parse_mode='Markdown')
             
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
    
    # Celebration animation for Payment
    import asyncio
    msg = await update.message.reply_text("🎆")
    await asyncio.sleep(0.5)
    await msg.edit_text("🎆 🎇")
    await asyncio.sleep(0.5)
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
                await update.callback_query.edit_message_text("Error: Inbound not found.")
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
            
            # Policy: 
            # 1. If current is unlimited (0), KEEP IT 0. Don't overwrite with finite trial.
            # 2. If current is finite:
            #    a. If active (expiry > now), add to expiry.
            #    b. If expired (expiry < now), set to now + days.
            
            if current_expiry == 0:
                new_expiry = 0 # Remain unlimited
            elif current_expiry < current_time_ms:
                new_expiry = current_time_ms + ms_to_add
            else:
                new_expiry = current_expiry + ms_to_add
                
            user_client['expiryTime'] = new_expiry
            user_client['enable'] = True
            user_client['updated_at'] = current_time_ms
            clients[client_index] = user_client
            
            msg_key = "success_extended"
            
            # Special case: If unlimited, we might want to tell user "You have unlimited, no changes made" 
            # but usually extending unlimited is just ... unlimited.
            if current_expiry == 0:
                 # If unlimited, we don't change expiry, but we might want to re-enable if disabled
                 pass
        else:
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
                "comment": "",
                "reset": 0
            }
            clients.append(new_client)
            settings['clients'] = clients
            msg_key = "success_created"
            
            # Insert into client_traffics
            cursor.execute("""
                INSERT INTO client_traffics (inbound_id, enable, email, up, down, expiry_time, total, reset, all_time, last_online)
                VALUES (?, ?, ?, 0, 0, ?, 0, 0, 0, 0)
            """, (INBOUND_ID, 1, f"tg_{tg_id}", new_expiry))
            
        cursor.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(settings), INBOUND_ID))
        conn.commit()
        conn.close()
        
        subprocess.run(["systemctl", "restart", "x-ui"])
        
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
             await update.callback_query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)
        else:
             await update.message.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)
        
    except Exception as e:
        logging.error(f"Error processing subscription: {e}")
        if is_callback:
             await update.callback_query.edit_message_text(t("error_generic", lang))
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
            await query.edit_message_text(
                "Error: Inbound not found.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]])
            )
            conn.close()
            return
            
        settings = json.loads(row[0])
        clients = settings.get('clients', [])
        
        user_client = None
        for client in clients:
            if str(client.get('tgId')) == tg_id or client.get('email') == f"tg_{tg_id}":
                user_client = client
                break
        
        conn.close()

        if user_client:
            expiry_ms = user_client.get('expiryTime', 0)
            current_ms = int(time.time() * 1000)
            
            if expiry_ms > 0 and expiry_ms < current_ms:
                 await query.edit_message_text(
                     t("sub_expired", lang),
                     reply_markup=InlineKeyboardMarkup([
                         [InlineKeyboardButton(t("btn_buy", lang), callback_data='shop')],
                         [InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]
                     ]),
                     parse_mode='Markdown'
                 )
                 return

            u_uuid = user_client['id']
            link = f"vless://{u_uuid}@{IP}:{PORT}?security=reality&encryption=none&pbk={PUBLIC_KEY}&headerType=none&fp=chrome&type=tcp&flow=xtls-rprx-vision&sni={SNI}&sid={SID}#VPN_{username}"
            
            if expiry_ms == 0:
                expiry_str = "Unlimited"
            else:
                expiry_str = datetime.datetime.fromtimestamp(expiry_ms / 1000, tz=TIMEZONE).strftime('%d.%m.%Y %H:%M')
                
            await query.edit_message_text(
                t("sub_active", lang).format(expiry=expiry_str, link=link),
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(t("btn_instructions", lang), callback_data='instructions')],
                    [InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]
                ])
            )
        else:
            await query.edit_message_text(
                t("sub_not_found", lang),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(t("btn_buy", lang), callback_data='shop')],
                    [InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]
                ]),
                parse_mode='Markdown'
            )
        
    except Exception as e:
        logging.error(f"Error: {e}")
        await query.edit_message_text(
            t("error_generic", lang),
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
                user_client = next((c for c in clients if c.get('email') == email), None)
                if user_client:
                    current_up = user_client.get('up', 0)
                    current_down = user_client.get('down', 0)
                    expiry_time = user_client.get('expiryTime', 0)
                    found = True
        
        conn.close()
        
        if not found:
             await query.edit_message_text(
                 t("stats_no_sub", lang), 
                 parse_mode='Markdown',
                 reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]])
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
            expiry_str = "♾️ Безлимит"
        else:
            expiry_dt = datetime.datetime.fromtimestamp(expiry_time / 1000, tz=TIMEZONE)
            expiry_str = expiry_dt.strftime("%d.%m.%Y %H:%M")
            
        text = f"""📊 **Ваша статистика**

{t("stats_sub_type", lang).format(plan=sub_plan)}

📅 **За сегодня:**
⬇️ {format_bytes(day_down)}  ⬆️ {format_bytes(day_up)}

📅 **За неделю:**
⬇️ {format_bytes(week_down)}  ⬆️ {format_bytes(week_up)}

📅 **За месяц:**
⬇️ {format_bytes(month_down)}  ⬆️ {format_bytes(month_up)}

📦 **Всего:**
⬇️ {format_bytes(current_down)}  ⬆️ {format_bytes(current_up)}
∑ {format_bytes(current_total)}

⏳ **Истекает:** {expiry_str}"""

        await query.edit_message_text(
            text, 
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("btn_back", lang), callback_data='back_to_main')]])
        )

    except Exception as e:
        logging.error(e)
        await query.edit_message_text(
            t("error_generic", lang),
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
    
    await query.edit_message_text(
        t("instr_menu", lang),
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
    
    await query.edit_message_text(
        text,
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

if __name__ == '__main__':
    init_db()
    application = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CallbackQueryHandler(set_language, pattern='^set_lang_'))
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
    application.add_handler(CallbackQueryHandler(admin_server, pattern='^admin_server$'))
    application.add_handler(CallbackQueryHandler(admin_rebind_user, pattern='^admin_rebind_'))
    application.add_handler(CallbackQueryHandler(admin_users_list, pattern='^admin_users_'))
    application.add_handler(CallbackQueryHandler(admin_user_detail, pattern='^admin_u_'))
    application.add_handler(CallbackQueryHandler(admin_reset_trial, pattern='^admin_reset_trial_'))
    application.add_handler(CallbackQueryHandler(admin_prices, pattern='^admin_prices$'))
    application.add_handler(CallbackQueryHandler(admin_edit_price, pattern='^admin_edit_price_'))
    application.add_handler(CallbackQueryHandler(admin_new_promo, pattern='^admin_new_promo$'))
    application.add_handler(CallbackQueryHandler(admin_broadcast, pattern='^admin_broadcast$'))
    application.add_handler(CallbackQueryHandler(admin_sales_log, pattern='^admin_sales_log$'))
    
    application.add_handler(CallbackQueryHandler(admin_search_user, pattern='^admin_search_user$'))
    application.add_handler(CallbackQueryHandler(admin_db_detail_callback, pattern='^admin_db_detail_'))
    application.add_handler(CallbackQueryHandler(admin_reset_trial_db, pattern='^admin_rt_db_'))
    application.add_handler(CallbackQueryHandler(admin_delete_user_db, pattern='^admin_del_db_'))
    
    application.add_handler(MessageHandler(~filters.COMMAND, handle_message))
    
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    
    job_queue = application.job_queue
    job_queue.run_repeating(check_expiring_subscriptions, interval=86400, first=10)
    job_queue.run_repeating(log_traffic_stats, interval=3600, first=5) # Every hour
    
    print("Bot started...")
    application.run_polling()
