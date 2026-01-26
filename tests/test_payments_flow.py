import os
import sys
import sqlite3
import json
import time
import datetime
import pytest
from unittest.mock import AsyncMock, MagicMock

sys.path.append('/usr/local/x-ui/bot')

os.environ['BOT_TOKEN'] = 'test_token'
os.environ['ADMIN_ID'] = '999'

import bot


def _prepare_bot_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE user_prefs (
            tg_id TEXT PRIMARY KEY,
            lang TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id TEXT,
            amount INTEGER,
            date INTEGER,
            plan_id TEXT
        )
    """)
    cursor.execute("INSERT INTO user_prefs (tg_id, lang) VALUES ('999', 'ru')")
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_successful_payment_records_transaction_and_notifies_admin_via_support_bot(tmp_path, monkeypatch):
    db_path = tmp_path / "bot_data.db"
    _prepare_bot_db(str(db_path))
    monkeypatch.setattr(bot, "BOT_DB_PATH", str(db_path))
    monkeypatch.setattr(bot, "ADMIN_ID", "999")
    monkeypatch.setattr(bot, "ADMIN_ID_INT", 999)

    payload = "1_month"
    monkeypatch.setattr(bot, "get_prices", lambda: {payload: {"amount": 100, "days": 30}})
    bot.process_subscription = AsyncMock()

    update = MagicMock()
    update.message = MagicMock()
    msg_mock = MagicMock()
    msg_mock.edit_text = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=msg_mock)
    update.message.from_user.id = 111
    update.message.from_user.username = "user1"
    update.message.successful_payment = MagicMock()
    update.message.successful_payment.invoice_payload = payload
    update.message.successful_payment.total_amount = 100

    context = MagicMock()
    context.bot = AsyncMock()
    support_bot = AsyncMock()
    context.bot_data = {"support_bot": support_bot}

    await bot.successful_payment(update, context)

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT tg_id, amount, plan_id FROM transactions").fetchone()
    conn.close()
    assert row == ("111", 100, payload)

    assert support_bot.send_message.call_count >= 1
    assert any(call.kwargs.get("chat_id") == "999" for call in support_bot.send_message.call_args_list)
    bot.process_subscription.assert_awaited()


@pytest.mark.asyncio
async def test_successful_payment_notifies_admin_via_main_bot_when_support_missing(tmp_path, monkeypatch):
    db_path = tmp_path / "bot_data.db"
    _prepare_bot_db(str(db_path))
    monkeypatch.setattr(bot, "BOT_DB_PATH", str(db_path))
    monkeypatch.setattr(bot, "ADMIN_ID", "999")
    monkeypatch.setattr(bot, "ADMIN_ID_INT", 999)

    payload = "1_month"
    monkeypatch.setattr(bot, "get_prices", lambda: {payload: {"amount": 100, "days": 30}})
    bot.process_subscription = AsyncMock()

    update = MagicMock()
    update.message = MagicMock()
    msg_mock = MagicMock()
    msg_mock.edit_text = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=msg_mock)
    update.message.from_user.id = 222
    update.message.from_user.username = "user2"
    update.message.successful_payment = MagicMock()
    update.message.successful_payment.invoice_payload = payload
    update.message.successful_payment.total_amount = 100

    context = MagicMock()
    context.bot = AsyncMock()
    context.bot_data = {}

    await bot.successful_payment(update, context)

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT tg_id, amount, plan_id FROM transactions").fetchone()
    conn.close()
    assert row == ("222", 100, payload)

    assert any(call.kwargs.get("chat_id") == "999" for call in context.bot.send_message.call_args_list)
    bot.process_subscription.assert_awaited()


@pytest.mark.asyncio
async def test_successful_payment_unknown_payload_still_records_transaction_and_notifies_admin(tmp_path, monkeypatch):
    db_path = tmp_path / "bot_data.db"
    _prepare_bot_db(str(db_path))
    monkeypatch.setattr(bot, "BOT_DB_PATH", str(db_path))
    monkeypatch.setattr(bot, "ADMIN_ID", "999")
    monkeypatch.setattr(bot, "ADMIN_ID_INT", 999)

    payload = "unknown_plan"
    monkeypatch.setattr(bot, "get_prices", lambda: {"1_month": {"amount": 100, "days": 30}})
    bot.process_subscription = AsyncMock()

    update = MagicMock()
    update.message = MagicMock()
    msg_mock = MagicMock()
    msg_mock.edit_text = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=msg_mock)
    update.message.from_user.id = 333
    update.message.from_user.username = "user3"
    update.message.successful_payment = MagicMock()
    update.message.successful_payment.invoice_payload = payload
    update.message.successful_payment.total_amount = 150

    context = MagicMock()
    context.bot = AsyncMock()
    context.bot_data = {}

    await bot.successful_payment(update, context)

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT tg_id, amount, plan_id FROM transactions").fetchone()
    conn.close()
    assert row == ("333", 150, payload)

    assert any("Unknown Plan Paid" in call.kwargs.get("text", "") for call in context.bot.send_message.call_args_list)
    bot.process_subscription.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_payment_db_insert_failure_does_not_block_subscription(tmp_path, monkeypatch):
    db_path = tmp_path / "bot_data.db"
    _prepare_bot_db(str(db_path))
    monkeypatch.setattr(bot, "BOT_DB_PATH", str(db_path))
    monkeypatch.setattr(bot, "ADMIN_ID", "999")
    monkeypatch.setattr(bot, "ADMIN_ID_INT", 999)

    payload = "1_month"
    monkeypatch.setattr(bot, "get_prices", lambda: {payload: {"amount": 100, "days": 30}})
    bot.process_subscription = AsyncMock()

    original_connect = sqlite3.connect
    calls = {"count": 0}

    def _connect_with_failure(path, *args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise sqlite3.OperationalError("insert failed")
        return original_connect(path, *args, **kwargs)

    monkeypatch.setattr(bot.sqlite3, "connect", _connect_with_failure)

    update = MagicMock()
    update.message = MagicMock()
    msg_mock = MagicMock()
    msg_mock.edit_text = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=msg_mock)
    update.message.from_user.id = 444
    update.message.from_user.username = "user4"
    update.message.successful_payment = MagicMock()
    update.message.successful_payment.invoice_payload = payload
    update.message.successful_payment.total_amount = 100

    context = MagicMock()
    context.bot = AsyncMock()
    context.bot_data = {}

    await bot.successful_payment(update, context)

    bot.process_subscription.assert_awaited()


@pytest.mark.asyncio
async def test_successful_payment_admin_notification_failure_does_not_block_subscription(tmp_path, monkeypatch):
    db_path = tmp_path / "bot_data.db"
    _prepare_bot_db(str(db_path))
    monkeypatch.setattr(bot, "BOT_DB_PATH", str(db_path))
    monkeypatch.setattr(bot, "ADMIN_ID", "999")
    monkeypatch.setattr(bot, "ADMIN_ID_INT", 999)

    payload = "1_month"
    monkeypatch.setattr(bot, "get_prices", lambda: {payload: {"amount": 100, "days": 30}})
    bot.process_subscription = AsyncMock()

    update = MagicMock()
    update.message = MagicMock()
    msg_mock = MagicMock()
    msg_mock.edit_text = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=msg_mock)
    update.message.from_user.id = 555
    update.message.from_user.username = "user5"
    update.message.successful_payment = MagicMock()
    update.message.successful_payment.invoice_payload = payload
    update.message.successful_payment.total_amount = 100

    context = MagicMock()
    context.bot = AsyncMock()
    context.bot.send_message.side_effect = Exception("notify failed")
    support_bot = AsyncMock()
    support_bot.send_message.side_effect = Exception("notify failed")
    context.bot_data = {"support_bot": support_bot}

    await bot.successful_payment(update, context)

    bot.process_subscription.assert_awaited()


@pytest.mark.asyncio
async def test_add_days_to_user_updates_client_traffics_expiry(tmp_path, monkeypatch):
    xui_db_path = tmp_path / "xui.db"
    conn = sqlite3.connect(xui_db_path)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE inbounds (id INTEGER PRIMARY KEY, settings TEXT)")
    cursor.execute("""
        CREATE TABLE client_traffics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inbound_id INTEGER,
            enable INTEGER,
            email TEXT,
            up INTEGER,
            down INTEGER,
            expiry_time INTEGER,
            total INTEGER,
            reset INTEGER,
            all_time INTEGER,
            last_online INTEGER
        )
    """)
    current_time_ms = int(time.time() * 1000)
    expiry_time = current_time_ms + 10 * 24 * 60 * 60 * 1000
    settings = {
        "clients": [
            {
                "id": "client-1",
                "email": "tg_1948009078",
                "limitIp": 0,
                "totalGB": 0,
                "expiryTime": expiry_time,
                "enable": True,
                "tgId": 1948009078,
                "subId": "sub-1",
                "flow": "",
                "created_at": current_time_ms,
                "updated_at": current_time_ms,
                "comment": "User",
                "reset": 0
            }
        ]
    }
    cursor.execute("INSERT INTO inbounds (id, settings) VALUES (?, ?)", (1, json.dumps(settings)))
    cursor.execute("""
        INSERT INTO client_traffics (inbound_id, enable, email, up, down, expiry_time, total, reset, all_time, last_online)
        VALUES (?, ?, ?, 0, 0, ?, 0, 0, 0, 0)
    """, (1, 0, "tg_1948009078", expiry_time - 1000))
    conn.commit()
    conn.close()

    monkeypatch.setattr(bot, "DB_PATH", str(xui_db_path))
    monkeypatch.setattr(bot, "INBOUND_ID", 1)
    monkeypatch.setattr(bot.subprocess, "run", lambda *args, **kwargs: None)

    await bot.add_days_to_user("1948009078", 30, MagicMock())

    conn = sqlite3.connect(xui_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT settings FROM inbounds WHERE id=1")
    updated_settings = json.loads(cursor.fetchone()[0])
    updated_client = next(c for c in updated_settings.get("clients", []) if c.get("email") == "tg_1948009078")
    updated_expiry = updated_client["expiryTime"]
    cursor.execute("SELECT expiry_time, enable FROM client_traffics WHERE email=?", ("tg_1948009078",))
    row = cursor.fetchone()
    conn.close()

    assert row[0] == updated_expiry
    assert row[1] == 1


def test_format_expiry_display_variants():
    now = datetime.datetime(2026, 1, 25, 12, 0, tzinfo=bot.TIMEZONE)
    now_ms = int(now.timestamp() * 1000)

    expiry_long = now + datetime.timedelta(days=400, hours=3, minutes=15)
    expiry_long_ms = int(expiry_long.timestamp() * 1000)
    assert bot.format_expiry_display(expiry_long_ms, "ru", now_ms) == expiry_long.strftime("%d.%m.%Y %H:%M")

    expiry_mid = now + datetime.timedelta(days=5, hours=2, minutes=10)
    expiry_mid_ms = int(expiry_mid.timestamp() * 1000)
    assert bot.format_expiry_display(expiry_mid_ms, "ru", now_ms) == expiry_mid.strftime("%d.%m %H:%M")

    expiry_day = now + datetime.timedelta(days=1, hours=2, minutes=5)
    expiry_day_ms = int(expiry_day.timestamp() * 1000)
    assert bot.format_expiry_display(expiry_day_ms, "ru", now_ms) == "01;02:05"

    expiry_hours = now + datetime.timedelta(hours=3, minutes=7)
    expiry_hours_ms = int(expiry_hours.timestamp() * 1000)
    assert bot.format_expiry_display(expiry_hours_ms, "ru", now_ms) == "03:07"

    expiry_minutes = now + datetime.timedelta(minutes=3)
    expiry_minutes_ms = int(expiry_minutes.timestamp() * 1000)
    assert bot.format_expiry_display(expiry_minutes_ms, "ru", now_ms) == "3 мин."

    assert bot.format_expiry_display(0, "ru", now_ms) == "Безлимит"
