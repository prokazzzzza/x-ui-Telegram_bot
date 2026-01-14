import sqlite3
import json
import logging
import uuid

DB_PATH = "/etc/x-ui/x-ui.db"
INBOUND_ID = 1

logging.basicConfig(level=logging.INFO)

def fix_traffics():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 1. Get all clients from inbounds
        cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
        row = cursor.fetchone()
        
        if not row:
            logging.error("Inbound not found")
            conn.close()
            return
            
        settings = json.loads(row[0])
        clients = settings.get('clients', [])
        
        fixed_count = 0
        
        for client in clients:
            email = client.get('email')
            if not email: continue
            
            # Check if exists in client_traffics
            cursor.execute("SELECT 1 FROM client_traffics WHERE email=?", (email,))
            if not cursor.fetchone():
                # Insert missing record
                logging.info(f"Adding missing client_traffics for {email}")
                
                # Default values
                enable = 1 if client.get('enable') else 0
                expiry_time = client.get('expiryTime', 0)
                
                # We need to insert
                cursor.execute("""
                    INSERT INTO client_traffics (inbound_id, enable, email, up, down, expiry_time, total, reset, all_time, last_online)
                    VALUES (?, ?, ?, 0, 0, ?, 0, 0, 0, 0)
                """, (INBOUND_ID, enable, email, expiry_time))
                fixed_count += 1
                
        if fixed_count > 0:
            conn.commit()
            logging.info(f"Successfully fixed {fixed_count} clients.")
        else:
            logging.info("No missing traffic records found.")
            
        conn.close()
        
    except Exception as e:
        logging.error(f"Error fixing traffics: {e}")

if __name__ == "__main__":
    fix_traffics()
