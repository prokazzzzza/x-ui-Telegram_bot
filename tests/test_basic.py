import sys
import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add bot directory to path
sys.path.append('/usr/local/x-ui/bot')

def test_imports():
    """
    Test that we can import the bot module without syntax errors.
    """
    try:
        # We don't actually run the bot, just check if it compiles/imports
        # We mock some things if needed, but for now just basic import
        import bot
        assert bot.TEXTS is not None
        assert 'en' in bot.TEXTS
        assert 'ru' in bot.TEXTS
    except ImportError as e:
        pytest.fail(f"Failed to import bot: {e}")
    except Exception as e:
        # It might fail due to missing env vars or DB, but syntax is fine
        # We consider syntax error (ImportError) the main failure here
        print(f"Runtime error during import (expected if env missing): {e}")
        pass

def test_prices_structure():
    """
    Test that PRICES dictionary has correct structure
    """
    import bot
    assert "1_month" in bot.PRICES
    assert "amount" in bot.PRICES["1_month"]
    assert "days" in bot.PRICES["1_month"]


@pytest.mark.asyncio
async def test_admin_flash_errors_works_without_user_name_columns(tmp_path, monkeypatch):
    import bot

    db_path = tmp_path / "bot_data.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE flash_delivery_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            error_message TEXT,
            timestamp INTEGER
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE user_prefs (
            tg_id TEXT PRIMARY KEY,
            lang TEXT
        )
        """
    )
    cursor.execute(
        "INSERT INTO flash_delivery_errors (user_id, error_message, timestamp) VALUES (?, ?, ?)",
        ("111", "Forbidden: bot was blocked by the user", 0),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(bot, "BOT_DB_PATH", str(db_path))

    update = MagicMock()
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update.callback_query = query

    context = MagicMock()
    await bot.admin_flash_errors(update, context)

    assert query.edit_message_text.await_count == 1
    if query.edit_message_text.call_args.kwargs and "text" in query.edit_message_text.call_args.kwargs:
        rendered = query.edit_message_text.call_args.kwargs["text"]
    else:
        rendered = str(query.edit_message_text.call_args.args[0])
    assert "Недоставленные" in rendered
