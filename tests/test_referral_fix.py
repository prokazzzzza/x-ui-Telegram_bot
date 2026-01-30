import pytest
import sqlite3
import os
import sys
from unittest.mock import patch

# Add bot directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../bot')))

from bot import set_referrer

TEST_DB_PATH = "test_referral_fix.db"

@pytest.fixture
def setup_db():
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)

    conn = sqlite3.connect(TEST_DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE user_prefs (
            tg_id TEXT PRIMARY KEY,
            lang TEXT,
            trial_used INTEGER DEFAULT 0,
            referrer_id TEXT,
            trial_activated_at INTEGER
        )
    ''')
    conn.commit()
    conn.close()

    with patch('bot.BOT_DB_PATH', TEST_DB_PATH):
        yield

    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)

def test_set_referrer_existing_user(setup_db):
    """
    Test that set_referrer correctly updates an existing user who has no referrer.
    """
    tg_id = "1001"
    referrer_id = "999"

    conn = sqlite3.connect(TEST_DB_PATH)
    # Simulate user already exists (e.g. from update_user_info)
    conn.execute("INSERT INTO user_prefs (tg_id, lang) VALUES (?, ?)", (tg_id, 'ru'))
    conn.commit()
    conn.close()

    # Action
    set_referrer(tg_id, referrer_id)

    # Verify
    conn = sqlite3.connect(TEST_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT referrer_id FROM user_prefs WHERE tg_id=?", (tg_id,))
    row = cursor.fetchone()
    conn.close()

    assert row is not None
    assert row[0] == referrer_id, "Referrer ID was not updated for existing user"

def test_set_referrer_new_user(setup_db):
    """
    Test that set_referrer works for a completely new user.
    """
    tg_id = "1002"
    referrer_id = "999"

    # Action
    set_referrer(tg_id, referrer_id)

    # Verify
    conn = sqlite3.connect(TEST_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT referrer_id FROM user_prefs WHERE tg_id=?", (tg_id,))
    row = cursor.fetchone()
    conn.close()

    assert row is not None
    assert row[0] == referrer_id

def test_set_referrer_already_has_referrer(setup_db):
    """
    Test that we do NOT overwrite existing referrer.
    """
    tg_id = "1003"
    original_referrer = "888"
    new_referrer = "999"

    conn = sqlite3.connect(TEST_DB_PATH)
    conn.execute("INSERT INTO user_prefs (tg_id, referrer_id) VALUES (?, ?)", (tg_id, original_referrer))
    conn.commit()
    conn.close()

    # Action
    set_referrer(tg_id, new_referrer)

    # Verify
    conn = sqlite3.connect(TEST_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT referrer_id FROM user_prefs WHERE tg_id=?", (tg_id,))
    row = cursor.fetchone()
    conn.close()

    assert row[0] == original_referrer, "Referrer ID should not be overwritten"
