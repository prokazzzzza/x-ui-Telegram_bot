import sqlite3
import json
import logging

DB_PATH = "/etc/x-ui/x-ui.db"
INBOUND_ID = 1

logging.basicConfig(level=logging.INFO)

def fix_types():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUND_ID,))
        row = cursor.fetchone()
        
        if not row:
            logging.error("Inbound not found")
            return
            
        settings = json.loads(row[0])
        clients = settings.get('clients', [])
        
        modified = False
        
        for client in clients:
            tg_id = client.get('tgId')
            if isinstance(tg_id, str) and tg_id.isdigit():
                client['tgId'] = int(tg_id)
                modified = True
                logging.info(f"Fixed tgId for {client.get('email')}: {tg_id} -> {client['tgId']}")
            elif tg_id == "":
                # Keep empty string or change to 0? 
                # Existing clients have "". Let's keep "" if empty.
                pass
                
        if modified:
            new_settings = json.dumps(settings, indent=2)
            cursor.execute("UPDATE inbounds SET settings=? WHERE id=?", (new_settings, INBOUND_ID))
            conn.commit()
            logging.info("Database updated successfully.")
        else:
            logging.info("No changes needed.")
            
        conn.close()
        
    except Exception as e:
        logging.error(f"Error fixing DB: {e}")

if __name__ == "__main__":
    fix_types()
