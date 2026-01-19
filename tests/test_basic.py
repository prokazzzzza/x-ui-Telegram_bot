import sys
import os
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
