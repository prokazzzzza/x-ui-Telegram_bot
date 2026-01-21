import pytest
import sqlite3
import os
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# Add bot directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../bot')))

from bot import check_promo, redeem_promo_db, get_lang

# Mock DB Path
TEST_DB_PATH = "test_bot_data.db"
BOT_DB_PATH = TEST_DB_PATH

@pytest.fixture
def setup_db():
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    
    conn = sqlite3.connect(TEST_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE promo_codes (code TEXT PRIMARY KEY, days INTEGER, max_uses INTEGER, used_count INTEGER DEFAULT 0)")
    cursor.execute("CREATE TABLE user_promos (tg_id TEXT, code TEXT, used_at INTEGER, PRIMARY KEY (tg_id, code))")
    cursor.execute("CREATE TABLE user_prefs (tg_id TEXT PRIMARY KEY, lang TEXT, trial_used INTEGER DEFAULT 0, referrer_id TEXT, trial_activated_at INTEGER)")
    
    # Insert dummy data
    cursor.execute("INSERT INTO promo_codes (code, days, max_uses, used_count) VALUES ('TEST10', 10, 5, 0)")
    cursor.execute("INSERT INTO promo_codes (code, days, max_uses, used_count) VALUES ('ONEUSE', 1, 1, 0)")
    cursor.execute("INSERT INTO promo_codes (code, days, max_uses, used_count) VALUES ('EXPIRED', 5, 1, 1)")
    
    conn.commit()
    conn.close()
    
    # Patch the global DB path in bot module (if possible, but since we import function, we might need to patch sqlite3.connect)
    with patch('bot.BOT_DB_PATH', TEST_DB_PATH):
        yield
        
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)

def test_check_promo_valid(setup_db):
    # Test valid code
    days, code = check_promo("TEST10", "123")
    assert days == 10
    assert code == "TEST10"
    
    # Test case insensitivity
    days, code = check_promo("test10", "123")
    assert days == 10
    assert code == "TEST10"

def test_check_promo_invalid(setup_db):
    days, code = check_promo("INVALID", "123")
    assert days is None
    assert code is None

def test_check_promo_expired(setup_db):
    days, code = check_promo("EXPIRED", "123")
    assert days is None
    assert code is None

def test_redeem_promo(setup_db):
    tg_id = "999"
    code = "ONEUSE"
    
    # Check before
    days, actual_code = check_promo(code, tg_id)
    assert days == 1
    
    # Redeem
    redeem_promo_db(actual_code, tg_id)
    
    # Check after (should be USED)
    # The check_promo logic checks max_uses FIRST.
    # Since ONEUSE has max_uses=1, and we just used it, used_count becomes 1.
    # 1 >= 1 is True, so it returns None (Expired/Max Used) instead of "USED".
    # This is actually correct behavior for global limit, but maybe confusing for the user who just used it.
    # However, if we want to test "USED" return, we need a code with higher limit.
    
    # Let's test with TEST10 which has limit 5
    code_multi = "TEST10"
    days, actual_code = check_promo(code_multi, tg_id)
    assert days == 10
    
    redeem_promo_db(actual_code, tg_id)
    
    result = check_promo(code_multi, tg_id)
    assert result == ("USED", actual_code)

    # Now back to ONEUSE which is now full
    result_one = check_promo(code, tg_id)
    # It should return None because max_uses check happens before user check?
    # Let's verify source code:
    # 1. Fetch code -> get max_uses, used_count
    # 2. if max_uses > 0 and used_count >= max_uses -> return None
    # 3. Check user_promos -> return USED
    
    # So yes, for a globally maxed out code, even if I used it, it says "None" (Invalid/Expired).
    # This effectively hides that I used it if it's dead for everyone.
    assert result_one == (None, None)

def test_get_lang(setup_db):
    conn = sqlite3.connect(TEST_DB_PATH)
    conn.execute("INSERT INTO user_prefs (tg_id, lang) VALUES ('111', 'en')")
    conn.commit()
    conn.close()
    
    assert get_lang("111") == "en"
    assert get_lang("222") == "ru" # Default
