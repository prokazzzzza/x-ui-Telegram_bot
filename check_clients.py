import sqlite3
import json

DB_PATH = "/etc/x-ui/x-ui.db"
INBOUND_ID = 1

try:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
    row = cursor.fetchone()
    conn.close()

    if row:
        settings = json.loads(row[0])
        clients = settings.get('clients', [])
        print(f"Total clients: {len(clients)}")
        if clients:
            c = clients[0]
            print("First client keys:", c.keys())
            print("First client sample:", json.dumps(c, indent=2))
    else:
        print("Inbound not found")
except Exception as e:
    print(f"Error: {e}")
