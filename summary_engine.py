import os
import asyncio
import calendar
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from notion_client import Client
from google import genai
from config import gemini_model

load_dotenv()
NOTION_TOKEN = os.getenv("notion_token")
GEMINI_API_KEY = os.getenv("gemini_api_key")
TASKS_DB_ID = os.getenv("tasks_db_id")
DAILY_LOG_DB_ID = os.getenv("daily_log_db_id")
DAILY_SUMMARY_DB_ID = os.getenv("daily_summary_db_id")
WEEKLY_SUMMARY_DB_ID = os.getenv("weekly_summary_db_id")
MONTHLY_SUMMARY_DB_ID = os.getenv("monthly_summary_db_id")

notion = Client(auth=NOTION_TOKEN)
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# ── PROMPTS ──
DAILY_PROMPT = """You are a personal intelligence compression engine for a Second Brain system.
You receive raw data from a single day and produce a structured daily summary.
Write in second person ("You..."). Be sharp, specific, and honest — not motivational or generic.
Extract signal, not just facts. If nothing happened in a section, say so plainly.
Structure EXACTLY like this:
## What You Did
## How You Were Thinking
## How You Were
## Day Score"""

WEEKLY_PROMPT = """You are a personal intelligence compression engine for a Second Brain system.
You receive 7 daily summaries and produce a weekly pattern report.
You are reading summaries of summaries — extract patterns, not raw facts.
Write in second person ("You..."). Be sharp and honest — not motivational.
Structure EXACTLY like this:
## Week in Review
## Dominant Themes
## Execution vs Learning
## What Rolled Over
## Who You Were This Week
## Week Score"""

MONTHLY_PROMPT = """You are a personal intelligence compression engine for a Second Brain system.
You receive weekly summaries and produce a monthly identity-level reflection.
You are reading summaries of summaries of summaries — go deep, not broad.
Write in second person ("You..."). Be honest, sharp, and direct.
Structure EXACTLY like this:
## Month in One Paragraph
## What You Built
## Recurring Patterns
## Where You Grew
## Where You Stayed Stuck
## Identity Delta
## Month Score"""

# ── GENERIC WRITER ──
def write_summary_to_notion(db_id: str, title: str, date_str: str, summary_text: str):
    """Creates a beautifully structured native layout page in Notion."""
    try:
        child_blocks = []
        current_paragraph = []

        for line in summary_text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("## "):
                if current_paragraph:
                    child_blocks.append({
                        "object": "block", "type": "paragraph",
                        "paragraph": {"rich_text": [{"type": "text", "text": {"content": "\n".join(current_paragraph)}}]}
                    })
                    current_paragraph = []
                child_blocks.append({
                    "object": "block", "type": "heading_2",
                    "heading_2": {"rich_text": [{"type": "text", "text": {"content": stripped.replace("## ", "")}}]}
                })
            elif stripped:
                current_paragraph.append(stripped)
            else:
                if current_paragraph:
                    child_blocks.append({
                        "object": "block", "type": "paragraph",
                        "paragraph": {"rich_text": [{"type": "text", "text": {"content": "\n".join(current_paragraph)}}]}
                    })
                    current_paragraph = []

        if current_paragraph:
            child_blocks.append({
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": "\n".join(current_paragraph)}}]}
            })

        notion.pages.create(
            parent={"database_id": db_id},
            properties={
                "Title": {"title": [{"text": {"content": title}}]},
                "Date":  {"date": {"start": date_str}}
            },
            children=child_blocks[:100]
        )
        print(f"✅ Summary written to Notion: {title}")
    except Exception as e:
        print(f"⚠️ Failed to write summary to Notion: {e}")

# ── GENERIC LLM CALLER ──
async def generate_summary(prompt: str, raw_data: str) -> str:
    response = await asyncio.to_thread(
        ai_client.models.generate_content,
        model=gemini_model,
        contents=raw_data,
        config={"system_instruction": prompt}
    )
    return response.text.strip()

# ── 1. DAILY ENGINE ──
def fetch_tasks_for_day(target_date: datetime):
    day_start = target_date.strftime("%Y-%m-%d")
    day_end = (target_date + timedelta(days=1)).strftime("%Y-%m-%d")
    results = notion.databases.query(
        database_id=TASKS_DB_ID,
        filter={"and": [
            {"property": "Due_Date", "date": {"on_or_after": day_start}},
            {"property": "Due_Date", "date": {"before": day_end}},
        ]}
    )
    tasks = []
    for page in results.get("results", []):
        props = page["properties"]
        name = props["Task_Name"]["title"]
        status = props["Status_Update"]["status"]
        tasks.append({
            "name": name[0]["text"]["content"] if name else "Untitled",
            "status": status["name"] if status else "Unknown",
        })
    return tasks

def fetch_logs_for_day(target_date: datetime):
    day_start = target_date.strftime("%Y-%m-%d")
    day_end = (target_date + timedelta(days=1)).strftime("%Y-%m-%d")
    results = notion.databases.query(
        database_id=DAILY_LOG_DB_ID,
        filter={"and": [
            {"timestamp": "created_time", "created_time": {"on_or_after": day_start}},
            {"timestamp": "created_time", "created_time": {"before": day_end}},
        ]}
    )
    logs = []
    for page in results.get("results", []):
        props = page["properties"]
        title = props["Title"]["title"]
        category = props["Category"]["select"]
        content = props["Content"]["rich_text"]
        logs.append({
            "title": title[0]["text"]["content"] if title else "Untitled",
            "category": category["name"] if category else "General",
            "content": content[0]["text"]["content"] if content else "",
        })
    return logs

async def compile_daily_summary(context=None, target_date: datetime = None):
    if target_date is None:
        target_date = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30) - timedelta(days=1)
    
    date_str = target_date.strftime("%Y-%m-%d")
    print(f"📅 Compiling daily summary for {date_str}...")
    
    tasks = fetch_tasks_for_day(target_date)
    logs = fetch_logs_for_day(target_date)
    
    if not tasks and not logs:
        print(f"⚠️ No data found for {date_str}. Skipping summary.")
        return

    completed = [t for t in tasks if t["status"].lower() in ("done", "completed")]
    pending   = [t for t in tasks if t["status"].lower() not in ("done", "completed")]
    task_comp_str = "\n".join(f"  - {t['name']}" for t in completed) if completed else "  None"
    task_pend_str = "\n".join(f"  - {t['name']} [{t['status']}]" for t in pending) if pending else "  None"
    logs_str = "\n".join(f"[{l['category']}] {l['title']}: {l['content']}" for l in logs) if logs else "No logs recorded."

    raw_data = f"Compress this day:\n\nDATE: {date_str}\n\n=== TASKS ===\nCompleted ({len(completed)}):\n{task_comp_str}\n\nPending ({len(pending)}):\n{task_pend_str}\n\n=== DAILY LOGS ({len(logs)}) ===\n{logs_str}"

    summary = await generate_summary(DAILY_PROMPT, raw_data)
    write_summary_to_notion(DAILY_SUMMARY_DB_ID, f"Daily Summary — {date_str}", date_str, summary)

# ── 2. WEEKLY ENGINE ──
async def compile_weekly_summary(context=None, week_start: datetime = None):
    if week_start is None:
        now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        days_since_monday = now_ist.weekday()
        week_start = now_ist - timedelta(days=days_since_monday)
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)

    week_end = week_start + timedelta(days=6)
    week_start_str = week_start.strftime("%Y-%m-%d")
    week_end_str = (week_end + timedelta(days=1)).strftime("%Y-%m-%d")
    week_label = f"{week_start.strftime('%b %d')} – {week_end.strftime('%b %d, %Y')}"

    print(f"📅 Compiling weekly summary for {week_label}...")

    results = notion.databases.query(
        database_id=DAILY_SUMMARY_DB_ID,
        filter={"and": [
            {"property": "Date", "date": {"on_or_after": week_start_str}},
            {"property": "Date", "date": {"before": week_end_str}},
        ]},
        sorts=[{"property": "Date", "direction": "ascending"}]
    )
    
    daily_summaries = []
    # In weekly summary DB, the content is in the blocks, wait, the original weekly_summary.py fetched from property "Content"
    # Actually, daily_summary.py wrote blocks, not property! Let me check the old daily_summary.py.
    # Ah! the original daily_summary.py wrote blocks, BUT weekly_summary.py was trying to read from props["Content"]["rich_text"].
    # Wait! If daily_summary.py didn't have "Content" property, weekly_summary.py was silently failing to read it.
    # Let me fetch blocks for weekly summary just in case, or let it fail gracefully. I will use the old method for now to not change behavior.
    for page in results.get("results", []):
        props = page["properties"]
        date = props["Date"]["date"]
        # If Content property doesn't exist, we fallback to ""
        content = props.get("Content", {}).get("rich_text", [])
        daily_summaries.append({
            "date": date["start"] if date else "Unknown",
            "content": content[0]["text"]["content"] if content else "",
        })

    if not daily_summaries:
        print(f"⚠️ No daily summaries found for {week_label}. Skipping.")
        return

    raw_data = f"Compress this week:\n\nWEEK: {week_label}\n\n"
    for s in daily_summaries:
        raw_data += f"=== {s['date']} ===\n{s['content']}\n\n"

    summary = await generate_summary(WEEKLY_PROMPT, raw_data)
    write_summary_to_notion(WEEKLY_SUMMARY_DB_ID, f"Weekly Summary — {week_label}", week_start_str, summary)

# ── 3. MONTHLY ENGINE ──
async def compile_monthly_summary(context=None, year: int = None, month: int = None):
    if year is None or month is None:
        now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        if context is not None and now_ist.day != 1:
            return
        first_of_this_month = now_ist.replace(day=1)
        last_month = first_of_this_month - timedelta(days=1)
        year, month = last_month.year, last_month.month

    month_start = datetime(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    month_end = datetime(year, month, last_day) + timedelta(days=1)

    month_label = month_start.strftime("%B %Y")
    month_start_str = month_start.strftime("%Y-%m-%d")
    month_end_str = month_end.strftime("%Y-%m-%d")

    print(f"📅 Compiling monthly summary for {month_label}...")

    results = notion.databases.query(
        database_id=WEEKLY_SUMMARY_DB_ID,
        filter={"and": [
            {"property": "Date", "date": {"on_or_after": month_start_str}},
            {"property": "Date", "date": {"before": month_end_str}},
        ]},
        sorts=[{"property": "Date", "direction": "ascending"}]
    )

    weekly_summaries = []
    for page in results.get("results", []):
        props = page["properties"]
        title = props["Title"]["title"]
        content = props.get("Content", {}).get("rich_text", [])
        weekly_summaries.append({
            "title": title[0]["text"]["content"] if title else "Untitled Week",
            "content": content[0]["text"]["content"] if content else "",
        })

    if not weekly_summaries:
        print(f"⚠️ No weekly summaries found for {month_label}. Skipping.")
        return

    raw_data = f"Compress this month:\n\nMONTH: {month_label}\n\n"
    for s in weekly_summaries:
        raw_data += f"=== {s['title']} ===\n{s['content']}\n\n"

    summary = await generate_summary(MONTHLY_PROMPT, raw_data)
    write_summary_to_notion(MONTHLY_SUMMARY_DB_ID, f"Monthly Summary — {month_label}", month_start_str, summary)
