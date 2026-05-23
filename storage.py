import os
import sqlite3
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv
from google import genai
from google.genai import types
from notion_client import Client
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from typing import Optional, List, Dict

# 1. Load Environment Variables
load_dotenv()

gemini_model = 'gemini-3.1-flash-lite'  # Define the Gemini model to use globally
embedding_model = "gemini-embedding-2"  # Define the embedding model globally
JOURNAL_DB_ID = os.getenv("journal_db_id")

# Tokens
TOKEN = os.getenv("bot_token")
GEMINI_API_KEY = os.getenv("gemini_api_key")
NOTION_TOKEN = os.getenv("notion_token")

# Database IDs
TASKS_DB_ID = os.getenv("tasks_db_id")
DAILY_LOG_DB_ID = os.getenv("daily_log_db_id")
DAILY_SUMMARY_DB_ID  = os.getenv("daily_summary_db_id")
WEEKLY_SUMMARY_DB_ID = os.getenv("weekly_summary_db_id")
MONTHLY_SUMMARY_DB_ID = os.getenv("monthly_summary_db_id")
CONTENT_VAULT_DB_ID = os.getenv("content_vault_db_id")

# 2. Setup Directory Structure
BASE_DATA_DIR = "second_brain_data"
FOLDERS = {
    "image": os.path.join(BASE_DATA_DIR, "images"),
    "video": os.path.join(BASE_DATA_DIR, "videos"),
    "audio": os.path.join(BASE_DATA_DIR, "audio"),
    "document": os.path.join(BASE_DATA_DIR, "documents")
}

for folder_path in FOLDERS.values():
    os.makedirs(folder_path, exist_ok=True)

DB_PATH = os.path.join(BASE_DATA_DIR, "queue.db")
QDRANT_PATH = os.path.join(BASE_DATA_DIR, "qdrant_db")
COLLECTION_NAME = "second_brain_memories"

# 3. Initialize Global Clients (Groq safely removed)
ai_client = genai.Client(api_key=GEMINI_API_KEY)
notion = Client(auth=NOTION_TOKEN) if NOTION_TOKEN else None
qdrant_client = QdrantClient(path=QDRANT_PATH)

# Ensure vector collection exists
if not qdrant_client.collection_exists(COLLECTION_NAME):
    qdrant_client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=768, distance=Distance.COSINE)
    )

# 4. SQLite Queue Functions
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

# 5. Export / Write Functions
def insert_task(task_name, due_date, status="Not started"):
    try:
        status_map = {
            "to do": "Not started", "not started": "Not started", "pending": "Not started",
            "backlog": "Not started", "in progress": "In progress", "doing": "In progress",
            "done": "Done", "completed": "Done"
        }
        cleaned_status = status_map.get(status.lower().strip(), "Not started")
        
        properties = {
            "Task_Name": {"title": [{"text": {"content": task_name}}]},
            "Status_Update": {"status": {"name": cleaned_status}}
        }
        
        if due_date:
            # CORE FIX: If a time is provided ('T') but no timezone offset exists (+, -, Z)
            # Append your local timezone offset (e.g., +05:30 for IST)
            if "T" in due_date and not any(x in due_date[10:] for x in ["+", "-", "Z"]):
                due_date = f"{due_date}+05:30"
                
            properties["Due_Date"] = {"date": {"start": due_date}}
        
        notion.pages.create(parent={"database_id": TASKS_DB_ID}, properties=properties)
        
    except Exception as e:
        print(f"⚠️ Failed to insert task '{task_name}' to Notion: {e}")

def insert_daily_log(title, category, content):
    try:
        properties = {
            "Title": {"title": [{"text": {"content": title}}]},
            "Category": {"select": {"name": category.capitalize() if category else "Journal"}},
            "Content": {"rich_text": [{"text": {"content": content}}]}
        }
        notion.pages.create(parent={"database_id": DAILY_LOG_DB_ID}, properties=properties)
    except Exception as e:
        print(f"⚠️ Failed to insert daily log '{title}' to Notion: {e}")

def insert_content_vault(title, url, summary):
    try:
        properties = {
            "Title": {"title": [{"text": {"content": title}}]},
            "URL": {"url": url},
            "Summary": {"rich_text": [{"text": {"content": summary}}]}
        }
        notion.pages.create(parent={"database_id": CONTENT_VAULT_DB_ID}, properties=properties)
    except Exception as e:
        print(f"⚠️ Failed to insert content vault item '{title}' to Notion: {e}")

def insert_vector_batch(memories: List[Dict]):
    """
    Takes a list of memory dictionaries and upserts them to Qdrant in ONE network call.
    Format expects: [{"content": "...", "category": "...", "source_url": "..."}, ...]
    """
    if not memories:
        return
        
    try:
        # Wrap each text in a types.Content object so Gemini knows to generate SEPARATE vectors!
        contents = [
            types.Content(parts=[types.Part.from_text(text=item["content"])]) 
            for item in memories
        ]
        
        # 1. Generate the batched embedding vectors (ONE Network Dependency!)
        embedding_response = ai_client.models.embed_content(
            model=embedding_model,
            contents=contents,
            config=types.EmbedContentConfig(
                output_dimensionality=768 # Force 768 to match your existing local Qdrant db
                # Note: task_type is deprecated in gemini-embedding-2, so it is removed.
            )
        )
        
        # 2. Build the PointStructs and map the payloads back together
        points = []
        for item, embedding_obj in zip(memories, embedding_response.embeddings):
            point_id = str(uuid.uuid4())
            
            payload = {
                "category": item.get("category", "Uncategorized"),
                "content": item["content"],
                "source_url": item.get("source_url"),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            points.append(
                PointStruct(
                    id=point_id, 
                    vector=embedding_obj.values, 
                    payload=payload
                )
            )
            
        # 3. Commit to Qdrant DB in one sweep
        qdrant_client.upsert(
            collection_name=COLLECTION_NAME,
            points=points
        )
        print(f"✅ Successfully batched and inserted {len(points)} vector memories.")
        
    except Exception as e:
        print(f"⚠️ Failed to batch insert vector memories due to error: {e}")