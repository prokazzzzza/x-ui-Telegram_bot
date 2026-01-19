import sqlite3
import json
import os

DB_PATH = '/etc/x-ui/x-ui.db'
INBOUND_ID = 1

def inspect_db():
    if not os.path.exists(DB_PATH):
        print(f"Error: DB not found at {DB_PATH}")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Get settings
        cursor.execute("SELECT id, settings FROM inbounds")
        rows = cursor.fetchall()
        
        print(f"Found {len(rows)} inbounds.")
        
        for r in rows:
            iid, settings_json = r
            print(f"\n--- Inbound ID: {iid} ---")
            try:
                settings = json.loads(settings_json)
                clients = settings.get('clients', [])
                print(f"Client count: {len(clients)}")
                
                if clients:
                    # Print keys of the first client to understand structure
                    c = clients[0]
                    print("Client Keys:", list(c.keys()))
                    print("Sample Client Data (Partial):")
                    for k, v in c.items():
                        if k in ['email', 'id', 'tgId', 'remark', 'comment', '_comment', 'subId']:
                            print(f"  {k}: {v}")
                            
                    # Check for empty comments specifically
                    empty_count = 0
                    for c in clients:
                        remark = c.get('remark')
                        comment = c.get('comment')
                        _comment = c.get('_comment')
                        if not remark and not comment and not _comment:
                            empty_count += 1
                            
                    print(f"Clients with EMPTY comment/remark: {empty_count}")
                    
            except Exception as e:
                print(f"JSON Parse Error: {e}")
                
        conn.close()

    except Exception as e:
        print(f"DB Error: {e}")

if __name__ == "__main__":
    inspect_db()
