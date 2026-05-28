import os
import sqlite3
from datetime import datetime, timezone
from dotenv import load_dotenv

# Ensure env variables are loaded
load_dotenv()
TOKEN = os.getenv("bot_token")

from config import BASE_DATA_DIR, FOLDERS, DB_PATH

for folder_path in FOLDERS.values():
    os.makedirs(folder_path, exist_ok=True)

# --- SQLite Queue Functions ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS incoming_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp DATETIME NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            )
        """)
        conn.commit()

init_db()

def save_to_queue(msg_type, content):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO incoming_queue (type, content, timestamp, status) VALUES (?, ?, ?, ?)",
            (msg_type, content, datetime.now(timezone.utc).isoformat(), "pending")
        )
        conn.commit()
    print(f"📥 Saved to Queue: [{msg_type}]")

def fetch_pending_queue():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT id, type, content, timestamp FROM incoming_queue WHERE status = 'pending'")
        return [dict(row) for row in cursor.fetchall()]

def update_queue_status(processed_ids, status="completed"):
    if not processed_ids: return
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        placeholders = ",".join("?" for _ in processed_ids)
        cursor.execute(f"UPDATE incoming_queue SET status = ? WHERE id IN ({placeholders})", (status, *processed_ids))