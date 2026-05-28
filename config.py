# config.py
import os
from datetime import time, timezone

# --- AI Models ---
gemini_model = 'gemini-3.1-flash-lite'
embedding_model = "gemini-embedding-2"

# --- System Variables ---
MY_CHAT_ID = 6455532575
MAX_HISTORY = 15

# --- Batch Sizes ---
image_batch_size = 500
short_media_batch_size = 50
video_audio_batch_size = 5

# --- Domain Routing ---
yt_domains = ['youtube.com', 'youtu.be']
short_domains = ['instagram.com', 'tiktok.com', 'twitter.com', 'x.com']

# --- File Paths & Folder Structures ---
BASE_DATA_DIR = "second_brain_data"
DB_PATH = os.path.join(BASE_DATA_DIR, "queue.db")
QDRANT_PATH = os.path.join(BASE_DATA_DIR, "qdrant_db")

FOLDERS = {
    "image": os.path.join(BASE_DATA_DIR, "images"),
    "video": os.path.join(BASE_DATA_DIR, "videos"),
    "audio": os.path.join(BASE_DATA_DIR, "audio"),
    "document": os.path.join(BASE_DATA_DIR, "documents")
}

# --- Database & Qdrant Configs ---
COLLECTION_NAME = "second_brain_memories"
VECTOR_SIZE = 768

# --- Cron Jobs Times ---
COMPILER_JOB_INTERVAL = 600
COMPILER_JOB_FIRST = 10
JOURNAL_PROMPT_TIME = time(16, 30, tzinfo=timezone.utc)
MONTHLY_SUMMARY_TIME = time(18, 20, tzinfo=timezone.utc)
WEEKLY_SUMMARY_TIME = time(18, 25, tzinfo=timezone.utc)
DAILY_SUMMARY_TIME = time(18, 30, tzinfo=timezone.utc)
MORNING_BRIEF_TIME = time(2, 30, tzinfo=timezone.utc)
