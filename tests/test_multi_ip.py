import json
import os
import sqlite3
import sys
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.append("/usr/local/x-ui/bot")

os.environ["BOT_TOKEN"] = "test_token"
os.environ["ADMIN_ID"] = "999"

import bot


@pytest.mark.asyncio
async def test_detect_suspicious_activity_creates_event(tmp_path, monkeypatch):
    db_path = tmp_path / "bot_data.db"
    monkeypatch.setattr(bot, "BOT_DB_PATH", str(db_path))
    bot.init_db()

    now = int(time.time())
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO connection_logs (email, ip, timestamp, country_code) VALUES (?, ?, ?, ?)",
        ("tg_111", "1.1.1.1", now - 10, "US"),
    )
    cur.execute(
        "INSERT INTO connection_logs (email, ip, timestamp, country_code) VALUES (?, ?, ?, ?)",
        ("tg_111", "2.2.2.2", now - 5, "DE"),
    )
    conn.commit()
    conn.close()

    await bot.detect_suspicious_activity(MagicMock())

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT email, ips, last_seen, count FROM suspicious_events WHERE email=?",
        ("tg_111",),
    ).fetchone()
    conn.close()

    assert row is not None
    email, ips, last_seen, count = row
    assert email == "tg_111"
    assert "1.1.1.1" in ips
    assert "2.2.2.2" in ips
    assert last_seen >= now - 60
    assert count >= 1


@pytest.mark.asyncio
async def test_admin_suspicious_users_hides_old_events(tmp_path, monkeypatch):
    bot_db_path = tmp_path / "bot_data.db"
    xui_db_path = tmp_path / "xui.db"

    monkeypatch.setattr(bot, "BOT_DB_PATH", str(bot_db_path))
    monkeypatch.setattr(bot, "DB_PATH", str(xui_db_path))
    monkeypatch.setattr(bot, "INBOUND_ID", 1)
    monkeypatch.setattr(bot, "SUSPICIOUS_EVENTS_LOOKBACK_SEC", 3600)

    bot.init_db()

    conn_xui = sqlite3.connect(str(xui_db_path))
    cur_xui = conn_xui.cursor()
    cur_xui.execute("CREATE TABLE inbounds (id INTEGER PRIMARY KEY, settings TEXT)")
    cur_xui.execute("INSERT INTO inbounds (id, settings) VALUES (?, ?)", (1, json.dumps({"clients": []})))
    conn_xui.commit()
    conn_xui.close()

    now = int(time.time())
    conn = sqlite3.connect(str(bot_db_path))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO suspicious_events (email, ips, timestamp, last_seen, count) VALUES (?, ?, ?, ?, ?)",
        ("tg_old", "🇺🇸 1.1.1.1", now - 172800, now - 172800, 1),
    )
    cur.execute(
        "INSERT INTO suspicious_events (email, ips, timestamp, last_seen, count) VALUES (?, ?, ?, ?, ?)",
        ("tg_new", "🇺🇸 2.2.2.2, 🇩🇪 3.3.3.3", now - 120, now - 120, 2),
    )
    conn.commit()
    conn.close()

    query = AsyncMock()
    query.data = "admin_suspicious_0"
    query.from_user = MagicMock()
    query.from_user.id = 999

    update = MagicMock()
    update.callback_query = query

    context = MagicMock()

    await bot.admin_suspicious_users(update, context)

    assert query.edit_message_text.await_count == 1
    sent_text = query.edit_message_text.await_args.args[0]
    assert "tg_new" in sent_text
    assert "tg_old" not in sent_text
