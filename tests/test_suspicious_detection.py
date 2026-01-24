
import sqlite3
import time
import os
import sys

def analyze_suspicious_v2(rows, window=60):
    """
    Sliding Window Logic:
    Detects if multiple IPs are used within 'window' seconds of each other.
    """
    # Group by email
    user_logs = {}
    for row in rows:
        email, ip, ts, cc = row
        if email not in user_logs: user_logs[email] = []
        user_logs[email].append({'ip': ip, 'ts': ts, 'cc': cc})
        
    suspicious_users = []
    
    for email, logs in user_logs.items():
        # Sort by time
        logs.sort(key=lambda x: x['ts'])
        
        detected_ips = set()
        suspicious_count = 0
        
        # Check for overlaps
        # Simple O(N^2) or Sliding Window
        # Given N is small (10 mins logs), O(N^2) is fine.
        # But O(N) sliding window is better.
        
        # We want to know if there exists any pair (L1, L2) such that:
        # L1.ip != L2.ip AND abs(L1.ts - L2.ts) <= window
        
        # Sliding window approach:
        # Maintain a window of logs where (last.ts - first.ts) <= window
        # In this window, check unique IPs.
        
        start = 0
        current_window_ips = {} # IP -> count in window
        
        has_suspicious = False
        
        for end in range(len(logs)):
            current_log = logs[end]
            
            # Add to window
            ip = current_log['ip']
            current_window_ips[ip] = current_window_ips.get(ip, 0) + 1
            
            # Shrink window from start
            while logs[end]['ts'] - logs[start]['ts'] > window:
                remove_ip = logs[start]['ip']
                current_window_ips[remove_ip] -= 1
                if current_window_ips[remove_ip] == 0:
                    del current_window_ips[remove_ip]
                start += 1
                
            # Check distinct IPs
            if len(current_window_ips) > 1:
                has_suspicious = True
                suspicious_count += 1
                for ip_key in current_window_ips:
                    # Find cc for this ip (naive)
                    cc = next((l['cc'] for l in logs if l['ip'] == ip_key), None)
                    detected_ips.add((ip_key, cc))
                    
        if has_suspicious:
            suspicious_users.append({
                'email': email,
                'ips': detected_ips,
                'minutes': 1 # Dummy count for now
            })
            
    return suspicious_users

def run_test():
    print("Running Suspicious Activity Detection Test (Sliding Window)...")
    
    # 1. Setup Data
    base_time = 1700000000 
    
    rows = []
    
    # Scenario A: Normal User (Single IP)
    rows.append(('user1@test', '1.1.1.1', base_time, 'US'))
    rows.append(('user1@test', '1.1.1.1', base_time + 30, 'US'))
    
    # Scenario B: Traveling User (IP changed > 60s)
    rows.append(('user2@test', '2.2.2.2', base_time, 'DE'))
    rows.append(('user2@test', '3.3.3.3', base_time + 65, 'FR')) # 65s gap -> Not Suspicious if window=60
    
    # Scenario C: Suspicious (Overlap < 60s)
    rows.append(('user3@test', '4.4.4.4', base_time, 'US'))
    rows.append(('user3@test', '5.5.5.5', base_time + 10, 'GB'))
    
    # Scenario D: Suspicious Edge (Boundary)
    # 59s gap -> Suspicious
    rows.append(('user4@test', '6.6.6.6', base_time, 'US'))
    rows.append(('user4@test', '7.7.7.7', base_time + 59, 'GB'))
    
    # Scenario E: Not Suspicious Edge
    # 61s gap -> Not Suspicious
    rows.append(('user5@test', '8.8.8.8', base_time, 'US'))
    rows.append(('user5@test', '9.9.9.9', base_time + 61, 'GB'))
    
    # 2. Run Analysis
    results = analyze_suspicious_v2(rows, window=60)
    
    # 3. Verify
    suspicious_emails = [r['email'] for r in results]
    print(f"Suspicious Emails Detected: {suspicious_emails}")
    
    errors = []
    
    if 'user1@test' in suspicious_emails:
        errors.append("❌ User 1 (Normal) incorrectly flagged.")
    else:
        print("✅ User 1 (Normal) passed.")
        
    if 'user2@test' in suspicious_emails:
        errors.append("❌ User 2 (Traveling) incorrectly flagged.")
    else:
        print("✅ User 2 (Traveling) passed.")
        
    if 'user3@test' not in suspicious_emails:
        errors.append("❌ User 3 (Suspicious) NOT flagged.")
    else:
        print("✅ User 3 (Suspicious) correctly flagged.")
        
    if 'user4@test' not in suspicious_emails:
        errors.append("❌ User 4 (Suspicious Edge 59s) NOT flagged.")
    else:
        print("✅ User 4 (Suspicious Edge 59s) correctly flagged.")
        
    if 'user5@test' in suspicious_emails:
        errors.append("❌ User 5 (61s gap) incorrectly flagged.")
    else:
        print("✅ User 5 (61s gap) passed.")
        
    if errors:
        print("\nERRORS Found:")
        for e in errors:
            print(e)
        sys.exit(1)
    else:
        print("\nAll scenarios passed successfully.")

if __name__ == "__main__":
    run_test()
