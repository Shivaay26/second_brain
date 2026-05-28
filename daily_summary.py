import os
import asyncio
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from notion_client import Client
from google import genai

load_dotenv()

NOTION_TOKEN = os.getenv("notion_token")
GEMINI_API_KEY = os.getenv("gemini_api_key")

TASKS_DB_ID = os.getenv("tasks_db_id")
DAILY_LOG_DB_ID = os.getenv("daily_log_db_id")
CONTENT_VAULT_DB_ID = os.getenv("content_vault_db_id")
DAILY_SUMMARY_DB_ID = os.getenv("daily_summary_db_id")

notion = Client(auth=NOTION_TOKEN)
ai_client = genai.Client(api_key=GEMINI_API_KEY)
from config import gemini_model


# ── 1. FETCHERS ──────────────────────────────────────────────────────────────

def _date_filter(date_property: str, target_date: datetime) -> dict:
    """Builds a Notion filter for a specific calendar day in IST."""
    day_start = target_date.strftime("%Y-%m-%d")
    day_end   = (target_date + timedelta(days=1)).strftime("%Y-%m-%d")
    return {
        "and": [
            {"property": date_property, "date": {"on_or_after": day_start}},
            {"property": date_property, "date": {"before": day_end}},
        ]
    }


def fetch_tasks_for_day(target_date: datetime) -> list:
    """Fetches all tasks whose Due_Date falls on target_date."""
    try:
        results = notion.databases.query(
            database_id=TASKS_DB_ID,
            filter=_date_filter("Due_Date", target_date)
        )
        tasks = []
        for page in results.get("results", []):
            props = page["properties"]
            name   = props["Task_Name"]["title"]
            status = props["Status_Update"]["status"]
            tasks.append({
                "name":   name[0]["text"]["content"] if name else "Untitled",
                "status": status["name"] if status else "Unknown",
            })
        return tasks
    except Exception as e:
        print(f"⚠️ Failed to fetch tasks: {e}")
        return []


def fetch_logs_for_day(target_date: datetime) -> list:
    """Fetches daily logs by Notion created_time."""
    try:
        day_start = target_date.strftime("%Y-%m-%d")
        day_end   = (target_date + timedelta(days=1)).strftime("%Y-%m-%d")

        results = notion.databases.query(
            database_id=DAILY_LOG_DB_ID,
            filter={
                "and": [
                    {"timestamp": "created_time", "created_time": {"on_or_after": day_start}},
                    {"timestamp": "created_time", "created_time": {"before": day_end}},
                ]
            }
        )
        logs = []
        for page in results.get("results", []):
            props = page["properties"]
            title    = props["Title"]["title"]
            category = props["Category"]["select"]
            content  = props["Content"]["rich_text"]
            logs.append({
                "title":    title[0]["text"]["content"] if title else "Untitled",
                "category": category["name"] if category else "General",
                "content":  content[0]["text"]["content"] if content else "",
            })
        return logs
    except Exception as e:
        print(f"⚠️ Failed to fetch daily logs: {e}")
        return []


def fetch_vault_for_day(target_date: datetime) -> list:
    """Fetches Content Vault items created on target_date."""
    try:
        day_start = target_date.strftime("%Y-%m-%d")
        day_end   = (target_date + timedelta(days=1)).strftime("%Y-%m-%d")

        results = notion.databases.query(
            database_id=CONTENT_VAULT_DB_ID,
            filter={
                "and": [
                    {"timestamp": "created_time", "created_time": {"on_or_after": day_start}},
                    {"timestamp": "created_time", "created_time": {"before": day_end}},
                ]
            }
        )
        items = []
        for page in results.get("results", []):
            props = page["properties"]
            title   = props["Title"]["title"]
            url     = props["URL"]["url"]
            summary = props["Summary"]["rich_text"]
            items.append({
                "title":   title[0]["text"]["content"] if title else "Untitled",
                "url":     url or "N/A",
                "summary": summary[0]["text"]["content"] if summary else "",
            })
        return items
    except Exception as e:
        print(f"⚠️ Failed to fetch vault items: {e}")
        return []


# ── 2. GEMINI COMPRESSION ────────────────────────────────────────────────────

async def generate_daily_summary(date_str: str, tasks: list, logs: list, vault: list) -> str:
    """Sends the day's raw data to Gemini and gets back a structured summary."""

    completed = [t for t in tasks if t["status"].lower() in ("done", "completed")]
    pending   = [t for t in tasks if t["status"].lower() not in ("done", "completed")]

    # Cleaner visual string generation formatting
    task_comp_str = "\n".join(f"  - {t['name']}" for t in completed) if completed else "  None"
    task_pend_str = "\n".join(f"  - {t['name']} [{t['status']}]" for t in pending) if pending else "  None"
    logs_str = "\n".join(f"[{l['category']}] {l['title']}: {l['content']}" for l in logs) if logs else "No logs recorded."
    vault_str = "\n".join(f"- {v['title']}: {v['summary']}" for v in vault) if vault else "Nothing saved."

    raw_data = f"""
DATE: {date_str}

=== TASKS ===
Completed ({len(completed)}):
{task_comp_str}

Pending ({len(pending)}):
{task_pend_str}

=== DAILY LOGS ({len(logs)}) ===
{logs_str}

=== CONTENT VAULT ({len(vault)} items consumed) ===
{vault_str}
"""

    system_prompt = """
You are a personal intelligence compression engine for a Second Brain system.
You receive raw data from a single day and produce a structured daily summary.

Write in second person ("You..."). Be sharp, specific, and honest — not motivational or generic.
Extract signal, not just facts. If nothing happened in a section, say so plainly.

Structure your output EXACTLY like this (use these exact headers):

## What You Did
Concise objective summary of tasks and output. Note what completed and what didn't.

## How You Were Thinking
What themes, ideas, or questions dominated the day? What were you learning vs executing?

## How You Were
Tone and energy inferred from the content. Were you focused, scattered, anxious, curious?

## Day Score
One line. Rate the day's signal density: Low / Medium / High. One sentence on why.
"""

    response = await asyncio.to_thread(
        ai_client.models.generate_content,
        model=gemini_model,
        contents=f"Compress this day:\n\n{raw_data}",
        config={"system_instruction": system_prompt}
    )

    return response.text.strip()


# ── 3. NOTION WRITER (With Markdown-to-Block Parser) ─────────────────────────

def write_summary_to_notion(date_str: str, summary_text: str):
    """Creates a beautifully structured native layout page in the Daily_Summary DB."""
    try:
        # Parse the plain text response into clean native Notion blocks
        child_blocks = []
        current_paragraph = []

        for line in summary_text.split("\n"):
            stripped = line.strip()
            
            if stripped.startswith("## "):
                # Clear pending paragraphs before adding a heading block
                if current_paragraph:
                    child_blocks.append({
                        "object": "block", "type": "paragraph",
                        "paragraph": {"rich_text": [{"type": "text", "text": {"content": "\n".join(current_paragraph)}}]}
                    })
                    current_paragraph = []
                
                child_blocks.append({
                    "object": "block",
                    "type": "heading_2",
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

        # Commit everything as native document body content
        notion.pages.create(
            parent={"database_id": DAILY_SUMMARY_DB_ID},
            properties={
                "Title": {"title": [{"text": {"content": f"Daily Summary — {date_str}"}}]},
                "Date":  {"date": {"start": date_str}}
            },
            children=child_blocks[:100]  # Safe threshold boundary for Notion API page creation caps
        )
        print(f"✅ Daily summary written to Notion for {date_str}")
    except Exception as e:
        print(f"⚠️ Failed to write summary to Notion: {e}")


# ── 4. MAIN ENTRY (Bot Architecture Compatible) ─────────────────────────────

async def compile_daily_summary(context=None, target_date: datetime = None):
    """
    Main entry point. Handles both automatic Telegram cron execution loops and manual run operations.
    """
    if target_date is None:
        # Midnight IST execution defaults back to processing yesterday's data trace
        target_date = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        target_date = target_date - timedelta(days=1)

    date_str = target_date.strftime("%Y-%m-%d")
    print(f"📅 Compiling daily summary for {date_str}...")

    tasks = fetch_tasks_for_day(target_date)
    logs  = fetch_logs_for_day(target_date)
    vault = fetch_vault_for_day(target_date)

    print(f"   → {len(tasks)} tasks | {len(logs)} logs | {len(vault)} vault items")

    if not tasks and not logs and not vault:
        print(f"⚠️ No data found for {date_str}. Skipping summary.")
        return

    summary = await generate_daily_summary(date_str, tasks, logs, vault)
    write_summary_to_notion(date_str, summary)


if __name__ == "__main__":
    # Allows separate stand-alone terminal debugging scripts to execute cleanly
    asyncio.run(compile_daily_summary())