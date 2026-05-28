import os
from notion_client import Client
from dotenv import load_dotenv

# Ensure env variables are loaded
load_dotenv()

# --- Initialize Client ---
NOTION_TOKEN = os.getenv("notion_token")
notion = Client(auth=NOTION_TOKEN)

# --- Database IDs ---
TASKS_DB_ID = os.getenv("tasks_db_id")
DAILY_LOG_DB_ID = os.getenv("daily_log_db_id")
DAILY_SUMMARY_DB_ID = os.getenv("daily_summary_db_id")
WEEKLY_SUMMARY_DB_ID = os.getenv("weekly_summary_db_id")
MONTHLY_SUMMARY_DB_ID = os.getenv("monthly_summary_db_id")
JOURNAL_DB_ID = os.getenv("journal_db_id")

# --- Core Service Functions ---
def create_page(db_id: str, properties: dict, children: list = None):
    """Creates a new page in a Notion database."""
    kwargs = {"parent": {"database_id": db_id}, "properties": properties}
    if children:
        kwargs["children"] = children
    return notion.pages.create(**kwargs)

def query_database(db_id: str, filter_dict: dict = None, sorts: list = None, **extra_kwargs):
    """Queries a Notion database with optional filters and sorts."""
    kwargs = {"database_id": db_id}
    if filter_dict:
        kwargs["filter"] = filter_dict
    if sorts:
        kwargs["sorts"] = sorts
    kwargs.update(extra_kwargs)
    return notion.databases.query(**kwargs)

def update_page(page_id: str, properties: dict):
    """Updates properties of an existing Notion page."""
    return notion.pages.update(page_id=page_id, properties=properties)

def get_page_blocks(block_id: str):
    """Fetches the child blocks (content) of a specific Notion page or block."""
    return notion.blocks.children.list(block_id=block_id)

def insert_task(task_name, due_date, status="Not started"):
    """Specific helper to insert a task into the Task DB."""
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
            if "T" in due_date and not any(x in due_date[10:] for x in ["+", "-", "Z"]):
                due_date = f"{due_date}+05:30"
            properties["Due_Date"] = {"date": {"start": due_date}}
        create_page(TASKS_DB_ID, properties)
    except Exception as e:
        print(f"⚠️ Failed to insert task '{task_name}' to Notion: {e}")

def insert_daily_log(title, category, content):
    """Specific helper to insert a daily log."""
    try:
        properties = {
            "Title": {"title": [{"text": {"content": title}}]},
            "Category": {"select": {"name": category.capitalize() if category else "Journal"}},
            "Content": {"rich_text": [{"text": {"content": content}}]}
        }
        create_page(DAILY_LOG_DB_ID, properties)
    except Exception as e:
        print(f"⚠️ Failed to insert daily log '{title}' to Notion: {e}")
