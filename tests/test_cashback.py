import sqlite3
import os

BOT_DB_PATH = "/usr/local/x-ui/bot/bot_data.db"

def test_schema():
    if not os.path.exists(BOT_DB_PATH):
        print(f"DB not found at {BOT_DB_PATH}")
        return

    conn = sqlite3.connect(BOT_DB_PATH)
    cursor = conn.cursor()

    # Check user_prefs columns
    cursor.execute("PRAGMA table_info(user_prefs)")
    columns = [row[1] for row in cursor.fetchall()]
    print(f"user_prefs columns: {columns}")

    if 'balance' in columns:
        print("PASS: 'balance' column exists in user_prefs")
    else:
        print("FAIL: 'balance' column missing in user_prefs")

    # Check referral_bonuses table
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='referral_bonuses'")
    if cursor.fetchone():
        print("PASS: 'referral_bonuses' table exists")
        cursor.execute("PRAGMA table_info(referral_bonuses)")
        columns = [row[1] for row in cursor.fetchall()]
        print(f"referral_bonuses columns: {columns}")
    else:
        print("FAIL: 'referral_bonuses' table missing")

    conn.close()

if __name__ == "__main__":
    test_schema()
