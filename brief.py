import os
import asyncio
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from notion_client import Client
from google import genai
from google.genai import types

load_dotenv()

6455532575

NOTION_TOKEN     = os.getenv("notion_token")
GEMINI_API_KEY   = os.getenv("gemini_api_key")
TASKS_DB_ID      = os.getenv("tasks_db_id")
DAILY_SUMMARY_DB_ID = os.getenv("daily_summary_db_id")
JOURNAL_DB_ID    = os.getenv("journal_db_id")
DAILY_LOG_DB_ID  = os.getenv("daily_log_db_id")

notion    = Client(auth=NOTION_TOKEN)
ai_client = genai.Client(api_key=GEMINI_API_KEY)
gemini_model = "gemini-2.5-flash-preview-05-20"

# ── STATE FLAG ────────────────────────────────────────────────────────────────
waiting_for_journal = False

JOURNAL_QUESTIONS = """
📓 *Evening Journal — End of Day*

Answer all of these in one message, however you want:

1. What was the highlight of your day?
2. What drained you or felt like a waste?
3. What did you avoid that you shouldn't have?
4. What's one thing you learned or noticed today?
5. How are you feeling right now, honestly?
6. What do you want tomorrow to look like?
"""


# ── HELPERS ───────────────────────────────────────────────────────────────────

def get_ist_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)


def fetch_pending_tasks() -> list:
    try:
        results = notion.databases.query(
            database_id=TASKS_DB_ID,
            filter={
                "or": [
                    {"property": "Status_Update", "status": {"equals": "Not started"}},
                    {"property": "Status_Update", "status": {"equals": "In progress"}},
                ]
            }
        )
        tasks = []
        for page in results.get("results", []):
            props  = page["properties"]
            name   = props["Task_Name"]["title"]
            status = props["Status_Update"]["status"]
            due    = props["Due_Date"]["date"]
            tasks.append(
                f"  - {name[0]['text']['content'] if name else 'Untitled'} "
                f"[{status['name'] if status else 'Unknown'}]"
                f"{' | Due: ' + due['start'] if due else ''}"
            )
        return tasks
    except Exception as e:
        print(f"⚠️ Failed to fetch tasks for brief: {e}")
        return []


def fetch_yesterday_summary() -> str:
    try:
        now_ist    = get_ist_now()
        yesterday  = (now_ist - timedelta(days=1)).strftime("%Y-%m-%d")

        results = notion.databases.query(
            database_id=DAILY_SUMMARY_DB_ID,
            filter={"property": "Date", "date": {"equals": yesterday}}
        )
        pages = results.get("results", [])
        if not pages:
            return "No summary found for yesterday."

        page_id = pages[0]["id"]
        blocks  = notion.blocks.children.list(block_id=page_id)
        return " ".join(
            b["paragraph"]["rich_text"][0]["text"]["content"]
            for b in blocks.get("results", [])
            if b["type"] == "paragraph" and b["paragraph"]["rich_text"]
        )
    except Exception as e:
        print(f"⚠️ Failed to fetch yesterday summary: {e}")
        return "Could not retrieve yesterday's summary."


# ── BRIEF ─────────────────────────────────────────────────────────────────────

async def send_morning_brief(bot, chat_id: int, context=None):
    """Generates and sends the morning brief to the user."""
    print("📋 Generating morning brief...")
    try:
        tasks          = fetch_pending_tasks()
        yesterday_summary = fetch_yesterday_summary()
        today_str      = get_ist_now().strftime("%A, %B %d %Y")

        raw_data = f"""
TODAY: {today_str}

YESTERDAY'S SUMMARY:
{yesterday_summary}

PENDING TASKS:
{chr(10).join(tasks) or '  None'}
"""

        system_prompt = """
You are a sharp personal advisor delivering a morning brief for a Second Brain system.
Be direct, honest, and energising — not motivational or generic.
Write in second person ("You...").

Structure your output EXACTLY like this:

## Good Morning
One sharp sentence setting the tone for the day based on yesterday's data.

## Where You Left Off
What were you working on? What carried over? What's unfinished?

## Where You Failed Yesterday
Be honest. What didn't get done that should have? Any patterns of avoidance?

## What To Avoid Today
Based on yesterday's patterns — what behaviours, distractions, or traps to sidestep.

## How To Spend Your Energy Today
Top 2-3 things to focus on, ranked by importance. Be specific, not generic.

## Tasks Due Today
List any tasks due today. If none, say so plainly.
"""

        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model=gemini_model,
            contents=f"Generate morning brief:\n\n{raw_data}",
            config={"system_instruction": system_prompt}
        )

        await bot.send_message(chat_id=chat_id, text=f"☀️ *Morning Brief*\n\n{response.text}")
        print("✅ Morning brief sent.")

    except Exception as e:
        print(f"⚠️ Morning brief failed: {e}")
        await bot.send_message(chat_id=chat_id, text=f"⛔ Morning brief failed: {e}")


# ── JOURNAL PROMPT ────────────────────────────────────────────────────────────

async def send_journal_prompt(bot, chat_id: int, context=None):
    """Sends journal questions at 10 PM and sets the waiting flag."""
    global waiting_for_journal
    waiting_for_journal = True
    print("📓 Sending journal prompt...")
    await bot.send_message(chat_id=chat_id, text=JOURNAL_QUESTIONS, parse_mode="Markdown")


# ── JOURNAL PROCESSOR ─────────────────────────────────────────────────────────

async def process_journal_entry(text: str) -> str:
    """Takes the user's raw text dump and structures it into a journal entry."""
    today_str = get_ist_now().strftime("%Y-%m-%d")

    system_prompt = """
You are processing a raw end-of-day journal dump for a personal Second Brain system.
The user has answered 6 questions in one free-form message.
Structure their response into a clean, honest journal entry.
Preserve their voice and exact sentiments — do not sanitize or motivate.
Write in first person as if the user wrote it themselves.

Structure EXACTLY like this:

## Highlight
What stood out positively today.

## What Drained Me
What felt like a waste or sapped energy.

## What I Avoided
Honest account of avoidance or friction.

## What I Learned
Key insight or observation from the day.

## How I Feel
Raw emotional state, as stated.

## Tomorrow
What the user wants from tomorrow.
"""

    response = await asyncio.to_thread(
        ai_client.models.generate_content,
        model=gemini_model,
        contents=f"Structure this journal entry:\n\n{text}",
        config={"system_instruction": system_prompt}
    )
    return response.text.strip()


async def save_journal_to_notion(entry_text: str):
    """Saves structured journal entry to Journal DB and Daily Log."""
    today_str   = get_ist_now().strftime("%Y-%m-%d")
    title       = f"Journal — {today_str}"

    try:
        # Write to Journal DB
        notion.pages.create(
            parent={"database_id": JOURNAL_DB_ID},
            properties={
                "Title": {"title": [{"text": {"content": title}}]},
                "Date":  {"date": {"start": today_str}}
            },
            children=[{
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": entry_text[:2000]}}]
                }
            }]
        )
        print(f"✅ Journal entry saved to Notion for {today_str}")

        # Also write to Daily Log so it feeds into daily summary
        notion.pages.create(
            parent={"database_id": DAILY_LOG_DB_ID},
            properties={
                "Title":    {"title": [{"text": {"content": f"Evening Journal — {today_str}"}}]},
                "Category": {"select": {"name": "Journal"}},
                "Content":  {"rich_text": [{"text": {"content": entry_text[:2000]}}]}
            }
        )
        print(f"✅ Journal entry mirrored to Daily Log.")

    except Exception as e:
        print(f"⚠️ Failed to save journal to Notion: {e}")


async def handle_journal_response(update, bot):
    """Called from handle_incoming when waiting_for_journal is True."""
    global waiting_for_journal
    waiting_for_journal = False

    await bot.send_message(
        chat_id=update.message.chat_id,
        text="📓 Got it. Processing your journal entry..."
    )

    entry_text = await process_journal_entry(update.message.text)
    await save_journal_to_notion(entry_text)

    await bot.send_message(
        chat_id=update.message.chat_id,
        text=f"✅ Journal saved.\n\n{entry_text}"
    )