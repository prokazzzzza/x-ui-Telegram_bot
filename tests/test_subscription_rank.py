import sys
import os
import unittest
from unittest.mock import MagicMock, patch
import json
import time

# Add bot directory to path
sys.path.append('/usr/local/x-ui/bot')

class TestSubscriptionRank(unittest.TestCase):
    
    def test_get_user_rank_subscription(self):
        # We need to import bot inside the test or patch before import if bot does init logic
        # bot.py loads config on import, so we might need to mock db there too if we were strictly unit testing,
        # but here we just want to test the function logic.
        import bot
        
        # Patch sqlite3.connect via bot module reference to ensure we catch it
        with patch('bot.sqlite3.connect') as mock_connect:
            # Mock DB connection and cursor
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_connect.return_value = mock_conn
            mock_conn.cursor.return_value = mock_cursor
            
            # Prepare test data
            # Current time
            current_time_ms = int(time.time() * 1000)
            day_ms = 24 * 3600 * 1000
            
            # Clients setup:
            # 1. Unlimited (expiry=0) -> Should be Rank 1 (36500 days)
            # 2. Active 20 days -> Rank 2
            # 3. Active 10 days -> Rank 3
            # 4. Expired -> Rank 4 (-1 days)
            
            clients = [
                {'email': 'active_20@test.com', 'expiryTime': current_time_ms + 20 * day_ms},
                {'email': 'unlimited@test.com', 'expiryTime': 0},
                {'email': 'expired@test.com', 'expiryTime': current_time_ms - 5 * day_ms},
                {'email': 'active_10@test.com', 'expiryTime': current_time_ms + 10 * day_ms}
            ]
            
            settings = {'clients': clients}
            # The query is SELECT settings FROM inbounds WHERE id=?
            # fetchone returns a tuple (settings_json_string,)
            row_data = (json.dumps(settings),)
            
            mock_cursor.fetchone.return_value = row_data
            
            # --- Run Assertions ---
            
            # 1. Verify Unlimited User
            rank, total, days = bot.get_user_rank_subscription('unlimited@test.com')
            print(f"Unlimited User: Rank={rank}, Days={days}")
            self.assertEqual(rank, 1, "Unlimited user should be rank 1")
            self.assertEqual(total, 4)
            self.assertEqual(days, 36500)
            
            # 2. Verify Active User (20 days)
            rank, total, days = bot.get_user_rank_subscription('active_20@test.com')
            print(f"Active User (20d): Rank={rank}, Days={days}")
            self.assertEqual(rank, 2, "Active user (20d) should be rank 2")
            self.assertAlmostEqual(days, 20, delta=0.5)
            
            # 3. Verify Active User (10 days)
            rank, total, days = bot.get_user_rank_subscription('active_10@test.com')
            print(f"Active User (10d): Rank={rank}, Days={days}")
            self.assertEqual(rank, 3, "Active user (10d) should be rank 3")
            self.assertAlmostEqual(days, 10, delta=0.5)
            
            # 4. Verify Expired User
            rank, total, days = bot.get_user_rank_subscription('expired@test.com')
            print(f"Expired User: Rank={rank}, Days={days}")
            self.assertEqual(rank, 4, "Expired user should be rank 4")
            self.assertEqual(days, 0)
            
            # 5. Verify Non-existent User
            rank, total, days = bot.get_user_rank_subscription('nonexistent@test.com')
            self.assertEqual(rank, -1)

if __name__ == '__main__':
    unittest.main()
