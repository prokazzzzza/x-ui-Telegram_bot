import sys
import inspect
import sqlite3
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add bot directory to path
sys.path.append('/usr/local/x-ui/bot')

def test_handler_functions_exist():
    """
    Test that critical handler functions are defined in the bot module.
    This ensures we don't accidentally rename or delete them without updating registration.
    """
    import bot

    handlers = [
        'start',
        'admin_panel',
        'admin_stats',
        'admin_user_detail',
        'admin_edit_limit_ip', # The one we just fixed
        'admin_poll_menu',
        'handle_poll_vote',
        'admin_ip_history'
    ]

    for handler_name in handlers:
        assert hasattr(bot, handler_name), f"Handler function {handler_name} not found in bot module"
        assert inspect.iscoroutinefunction(getattr(bot, handler_name)), f"{handler_name} should be an async function"


def test_escape_markdown_escapes_special_chars() -> None:
    import bot

    assert bot._escape_markdown("a_b*c`d[e") == r"a\_b\*c\`d\[e"


@pytest.mark.asyncio
async def test_stats_renders_unknown_plan_without_raw_key(tmp_path, monkeypatch) -> None:
    import bot

    xui_db_path = tmp_path / "xui.db"
    bot_db_path = tmp_path / "bot.db"

    monkeypatch.setattr(bot, "DB_PATH", str(xui_db_path))
    monkeypatch.setattr(bot, "BOT_DB_PATH", str(bot_db_path))
    bot.init_db()

    conn_xui = sqlite3.connect(str(xui_db_path))
    cur_xui = conn_xui.cursor()
    cur_xui.execute(
        """
        CREATE TABLE client_traffics (
            email TEXT,
            up INTEGER,
            down INTEGER,
            expiry_time INTEGER
        )
        """
    )
    now_ms = int(time.time() * 1000)
    cur_xui.execute(
        "INSERT INTO client_traffics (email, up, down, expiry_time) VALUES (?, ?, ?, ?)",
        ("tg_111", 123, 456, now_ms + 7 * 24 * 60 * 60 * 1000),
    )
    conn_xui.commit()
    conn_xui.close()

    conn_bot = sqlite3.connect(str(bot_db_path))
    conn_bot.execute("INSERT OR REPLACE INTO user_prefs (tg_id, lang) VALUES (?, ?)", ("111", "ru"))
    conn_bot.execute(
        "INSERT INTO transactions (tg_id, amount, date, plan_id) VALUES (?, ?, ?, ?)",
        ("111", 1, int(time.time()), "unknown"),
    )
    conn_bot.commit()
    conn_bot.close()

    update = MagicMock()
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.from_user.id = 111
    query.message = MagicMock()
    query.message.delete = AsyncMock()
    update.callback_query = query

    context = MagicMock()
    context.bot = AsyncMock()

    await bot.stats(update, context)

    assert query.edit_message_text.await_count == 1
    rendered = str(query.edit_message_text.call_args.args[0])
    assert "plan_unknown" not in rendered
    assert "1 Месяц" in rendered


@pytest.mark.asyncio
async def test_monitor_traffic_alert_prefers_top_talker_client(tmp_path, monkeypatch) -> None:
    import bot

    xui_db_path = tmp_path / "xui.db"
    bot_db_path = tmp_path / "bot.db"

    monkeypatch.setattr(bot, "DB_PATH", str(xui_db_path))
    monkeypatch.setattr(bot, "BOT_DB_PATH", str(bot_db_path))
    bot.init_db()

    conn_bot = sqlite3.connect(str(bot_db_path))
    conn_bot.execute(
        "INSERT OR REPLACE INTO user_prefs (tg_id, lang, username) VALUES (?, ?, ?)",
        ("999", "ru", "admin"),
    )
    conn_bot.execute(
        "INSERT OR REPLACE INTO user_prefs (tg_id, lang, username) VALUES (?, ?, ?)",
        ("111", "ru", "Isalyf"),
    )
    conn_bot.execute(
        "INSERT OR REPLACE INTO connection_logs (email, ip, timestamp, country_code) VALUES (?, ?, ?, ?)",
        ("tg_111", "46.148.59.107", 1060, "RU"),
    )
    conn_bot.execute(
        "INSERT OR REPLACE INTO connection_logs (email, ip, timestamp, country_code) VALUES (?, ?, ?, ?)",
        ("client_big", "1.2.3.4", 1050, "NL"),
    )
    conn_bot.commit()
    conn_bot.close()

    conn_xui = sqlite3.connect(str(xui_db_path))
    cur = conn_xui.cursor()
    cur.execute("CREATE TABLE inbounds (id INTEGER PRIMARY KEY, remark TEXT, tag TEXT)")
    cur.execute(
        "CREATE TABLE client_traffics (inbound_id INTEGER, enable INTEGER, email TEXT, up INTEGER, down INTEGER, last_online INTEGER)"
    )
    cur.execute("INSERT INTO inbounds (id, remark, tag) VALUES (1, 'NL_ASUS_VPS', 'tag1')")
    cur.execute("INSERT INTO inbounds (id, remark, tag) VALUES (2, 'RU_IN', 'tag2')")
    t0_ms = 1000 * 1000
    cur.execute(
        "INSERT INTO client_traffics (inbound_id, enable, email, up, down, last_online) VALUES (1, 1, 'client_big', 0, 0, ?)",
        (t0_ms,),
    )
    cur.execute(
        "INSERT INTO client_traffics (inbound_id, enable, email, up, down, last_online) VALUES (2, 1, 'tg_111', 1000, 1000, ?)",
        (t0_ms,),
    )
    conn_xui.commit()
    conn_xui.close()

    monkeypatch.setattr(bot, "ADMIN_ID", "999")
    monkeypatch.setattr(bot, "INBOUND_ID", 1)
    monkeypatch.setattr(bot, "_MONITOR_ALERT_COOLDOWN_SEC", 0)
    monkeypatch.setattr(bot, "_MONITOR_ENABLED", 1)
    monkeypatch.setattr(bot, "_MONITOR_INTERVAL_SEC", 60)

    bot._MONITOR_NET_LAST = None
    bot._MONITOR_TRAFFIC_EMA_BPS = None
    bot._MONITOR_ONLINE_EMA = None
    bot._MONITOR_LAST_ALERT_TS.clear()
    bot._MONITOR_CLIENT_LAST_TS = None
    bot._MONITOR_CLIENT_LAST_TOTALS.clear()
    bot._MONITOR_CLIENT_LAST_SUM = None

    sent: list[str] = []

    async def _fake_send_admin_message(_context, text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(bot, "_send_admin_message", _fake_send_admin_message)

    current_time = {"t": 1000.0}
    monkeypatch.setattr(bot.time, "time", lambda: float(current_time["t"]))

    await bot.monitor_thresholds_job(MagicMock())

    conn_xui = sqlite3.connect(str(xui_db_path))
    cur = conn_xui.cursor()
    t1_ms = 1060 * 1000
    cur.execute(
        "UPDATE client_traffics SET up=?, down=?, last_online=? WHERE email='client_big'",
        (2_000_000_000, 0, t1_ms),
    )
    cur.execute(
        "UPDATE client_traffics SET up=?, down=?, last_online=? WHERE email='tg_111'",
        (11_000, 1_000, t1_ms),
    )
    conn_xui.commit()
    conn_xui.close()

    current_time["t"] = 1060.0
    await bot.monitor_thresholds_job(MagicMock())

    msg = next((m for m in sent if "аномальный трафик" in m), "")
    assert msg
    assert "NL\\_ASUS\\_VPS" in msg
    assert "@Isalyf" not in msg


@pytest.mark.asyncio
async def test_monitor_traffic_alert_fetches_username_when_missing(tmp_path, monkeypatch) -> None:
    import bot

    xui_db_path = tmp_path / "xui.db"
    bot_db_path = tmp_path / "bot.db"

    monkeypatch.setattr(bot, "DB_PATH", str(xui_db_path))
    monkeypatch.setattr(bot, "BOT_DB_PATH", str(bot_db_path))
    bot.init_db()

    conn_bot = sqlite3.connect(str(bot_db_path))
    conn_bot.execute(
        "INSERT OR REPLACE INTO user_prefs (tg_id, lang, username) VALUES (?, ?, ?)",
        ("999", "ru", "admin"),
    )
    conn_bot.execute(
        "INSERT OR REPLACE INTO connection_logs (email, ip, timestamp, country_code) VALUES (?, ?, ?, ?)",
        ("tg_980794782", "95.37.253.189", 1060, "RU"),
    )
    conn_bot.commit()
    conn_bot.close()

    conn_xui = sqlite3.connect(str(xui_db_path))
    cur = conn_xui.cursor()
    cur.execute("CREATE TABLE inbounds (id INTEGER PRIMARY KEY, remark TEXT, tag TEXT)")
    cur.execute(
        "CREATE TABLE client_traffics (inbound_id INTEGER, enable INTEGER, email TEXT, up INTEGER, down INTEGER, last_online INTEGER)"
    )
    cur.execute("INSERT INTO inbounds (id, remark, tag) VALUES (1, 'Vless', 'tag1')")
    t0_ms = 1000 * 1000
    cur.execute(
        "INSERT INTO client_traffics (inbound_id, enable, email, up, down, last_online) VALUES (1, 1, 'tg_980794782', 0, 0, ?)",
        (t0_ms,),
    )
    conn_xui.commit()
    conn_xui.close()

    monkeypatch.setattr(bot, "ADMIN_ID", "999")
    monkeypatch.setattr(bot, "INBOUND_ID", 1)
    monkeypatch.setattr(bot, "_MONITOR_ALERT_COOLDOWN_SEC", 0)
    monkeypatch.setattr(bot, "_MONITOR_ENABLED", 1)
    monkeypatch.setattr(bot, "_MONITOR_INTERVAL_SEC", 60)

    bot._MONITOR_NET_LAST = None
    bot._MONITOR_TRAFFIC_EMA_BPS = None
    bot._MONITOR_ONLINE_EMA = None
    bot._MONITOR_LAST_ALERT_TS.clear()
    bot._MONITOR_CLIENT_LAST_TS = None
    bot._MONITOR_CLIENT_LAST_TOTALS.clear()
    bot._MONITOR_CLIENT_LAST_SUM = None

    sent: list[str] = []

    async def _fake_send_admin_message(_context, text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(bot, "_send_admin_message", _fake_send_admin_message)

    current_time = {"t": 1000.0}
    monkeypatch.setattr(bot.time, "time", lambda: float(current_time["t"]))

    context = MagicMock()
    context.application = MagicMock()
    context.application.bot = AsyncMock()
    chat = MagicMock()
    chat.username = "Nick980"
    chat.first_name = "Nick"
    chat.last_name = "Test"
    context.application.bot.get_chat = AsyncMock(return_value=chat)

    await bot.monitor_thresholds_job(context)

    conn_xui = sqlite3.connect(str(xui_db_path))
    cur = conn_xui.cursor()
    t1_ms = 1060 * 1000
    cur.execute(
        "UPDATE client_traffics SET up=?, down=?, last_online=? WHERE email='tg_980794782'",
        (2_000_000_000, 0, t1_ms),
    )
    conn_xui.commit()
    conn_xui.close()

    current_time["t"] = 1060.0
    await bot.monitor_thresholds_job(context)

    msg = next((m for m in sent if "аномальный трафик" in m), "")
    assert msg
    assert "@Nick980" in msg
    assert "tg\\_980794782" not in msg
