import unittest
import sqlite3
import os
import sys
import json
import time
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock

# Add bot directory to path
sys.path.append('/usr/local/x-ui/bot')

# Mock environment variables before importing bot
os.environ['BOT_TOKEN'] = 'test_token'
os.environ['ADMIN_ID'] = '123456789'
os.environ['REF_BONUS_DAYS'] = '7'

# Mock DB paths
TEST_BOT_DB = '/tmp/test_bot_data.db'
TEST_XUI_DB = '/tmp/test_xui.db'

# Patch DB paths in bot module
import bot
bot.BOT_DB_PATH = TEST_BOT_DB
bot.DB_PATH = TEST_XUI_DB

class TestReferralSystem(unittest.TestCase):
    def setUp(self):
        # Clean up old DBs
        if os.path.exists(TEST_BOT_DB):
            os.remove(TEST_BOT_DB)
        if os.path.exists(TEST_XUI_DB):
            os.remove(TEST_XUI_DB)
            
        # Init Bot DB
        bot.init_db()
        
        # Init X-UI DB (mock schema)
        conn = sqlite3.connect(TEST_XUI_DB)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE inbounds (id INTEGER PRIMARY KEY, settings TEXT, stream_settings TEXT, port INTEGER)")
        cursor.execute("CREATE TABLE client_traffics (id INTEGER PRIMARY KEY, inbound_id INTEGER, enable INTEGER, email TEXT, up INTEGER, down INTEGER, expiry_time INTEGER, total INTEGER, reset INTEGER, all_time INTEGER, last_online INTEGER)")
        
        # Insert dummy inbound
        settings = {
            "clients": []
        }
        cursor.execute("INSERT INTO inbounds (id, settings, port) VALUES (?, ?, ?)", (1, json.dumps(settings), 443))
        conn.commit()
        conn.close()

    def tearDown(self):
        if os.path.exists(TEST_BOT_DB):
            os.remove(TEST_BOT_DB)
        if os.path.exists(TEST_XUI_DB):
            os.remove(TEST_XUI_DB)

    def test_set_referrer(self):
        # 1. New user
        bot.set_referrer("1001", "2001")
        data = bot.get_user_data("1001")
        self.assertEqual(data['referrer_id'], "2001")
        
        # 2. Existing user (should not change)
        bot.set_referrer("1001", "3001")
        data = bot.get_user_data("1001")
        self.assertEqual(data['referrer_id'], "2001")
        
        # 3. Existing user with NULL referrer
        conn = sqlite3.connect(TEST_BOT_DB)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO user_prefs (tg_id) VALUES (?)", ("1002",))
        conn.commit()
        conn.close()
        
        bot.set_referrer("1002", "2002")
        data = bot.get_user_data("1002")
        self.assertEqual(data['referrer_id'], "2002")

    async def async_test_successful_payment(self):
        # Setup: Referrer 2001, User 1001
        bot.set_referrer("1001", "2001")
        
        # Mock Update and Context
        update = MagicMock()
        context = MagicMock()
        
        update.message.from_user.id = 1001
        update.message.from_user.username = "testuser"
        update.message.successful_payment.invoice_payload = "1_month"
        update.message.successful_payment.total_amount = 100 # 100 Stars
        
        # Mock bot methods
        context.bot.send_message = AsyncMock()
        
        # Mock update.message.reply_text and the returned message.edit_text
        msg_mock = AsyncMock()
        msg_mock.edit_text = AsyncMock()
        update.message.reply_text = AsyncMock(return_value=msg_mock)
        
        # Run successful_payment
        # We need to await it
        await bot.successful_payment(update, context)
        
        # Verify:
        # 1. Transaction recorded
        conn = sqlite3.connect(TEST_BOT_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT amount, plan_id FROM transactions WHERE tg_id=?", ("1001",))
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 100)
        self.assertEqual(row[1], "1_month")
        
        # 2. Referral Bonus (Balance)
        # 10% of 100 = 10
        cursor.execute("SELECT balance FROM user_prefs WHERE tg_id=?", ("2001",))
        row = cursor.fetchone()
        # Referrer might not exist in user_prefs if not set up, but set_referrer creates user entry?
        # No, set_referrer only creates the referred user ("1001"). 
        # Referrer ("2001") entry is created/updated in successful_payment logic?
        # Let's check logic: UPDATE user_prefs SET balance ...
        # If user 2001 doesn't exist, UPDATE does nothing.
        # We need to ensure referrer exists.
        
        # Fix: Create referrer first
        cursor.execute("INSERT OR IGNORE INTO user_prefs (tg_id) VALUES (?)", ("2001",))
        conn.commit()
        
        # Re-run payment logic simulation (resetting tx?)
        # Or just run it again.
        await bot.successful_payment(update, context)
        
        cursor.execute("SELECT balance FROM user_prefs WHERE tg_id=?", ("2001",))
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 10) # 10 Stars
        
        # 3. Referral Bonus (Table)
        cursor.execute("SELECT amount, type FROM referral_bonuses WHERE referrer_id=? AND referred_id=?", ("2001", "1001"))
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 10)
        self.assertEqual(row[1], "cashback")
        
        conn.close()

    def test_payment_wrapper(self):
        asyncio.run(self.async_test_successful_payment())

if __name__ == '__main__':
    unittest.main()
