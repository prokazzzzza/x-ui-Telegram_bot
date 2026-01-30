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
    assert "Неизвестно" in rendered
