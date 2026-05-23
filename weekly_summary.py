import os
import asyncio
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from notion_client import Client
from google import genai

load_dotenv()

NOTION_TOKEN = os.getenv("notion_token")
GEMINI_API_KEY = os.getenv("gemini_api_key")
DAILY_SUMMARY_DB_ID = os.getenv("daily_summary_db_id")
WEEKLY_SUMMARY_DB_ID = os.getenv("weekly_summary_db_id")

notion = Client(auth=NOTION_TOKEN)
ai_client = genai.Client(api_key=GEMINI_API_KEY)
gemini_model = "gemini-3.1-flash-lite"  # Standardized production model


# ── 1. FETCHER ───────────────────────────────────────────────────────────────

def fetch_daily_summaries_for_week(week_start: datetime) -> list:
    """Fetches all daily summaries from Daily_Summary DB for the given 7-day window."""
    try:
        week_end = week_start + timedelta(days=7)
        results = notion.databases.query(
            database_id=DAILY_SUMMARY_DB_ID,
            filter={
                "and": [
                    {"property": "Date", "date": {"on_or_after": week_start.strftime("%Y-%m-%d")}},
                    {"property": "Date", "date": {"before": week_end.strftime("%Y-%m-%d")}},
                ]
            },
            sorts=[{"property": "Date", "direction": "ascending"}]
        )
        summaries = []
        for page in results.get("results", []):
            props = page["properties"]
            date    = props["Date"]["date"]
            content = props["Content"]["rich_text"]
            summaries.append({
                "date":    date["start"] if date else "Unknown",
                "content": content[0]["text"]["content"] if content else "",
            })
        return summaries
    except Exception as e:
        print(f"⚠️ Failed to fetch daily summaries: {e}")
        return []


# ── 2. GEMINI COMPRESSION ────────────────────────────────────────────────────

async def generate_weekly_summary(week_label: str, daily_summaries: list) -> str:
    """Compresses 7 daily summaries into a weekly pattern report."""

    raw_data = f"WEEK: {week_label}\n\n"
    for s in daily_summaries:
        raw_data += f"=== {s['date']} ===\n{s['content']}\n\n"

    system_prompt = """
You are a personal intelligence compression engine for a Second Brain system.
You receive 7 daily summaries and produce a weekly pattern report.
You are reading summaries of summaries — extract patterns, not raw facts.

Write in second person ("You..."). Be sharp and honest — not motivational.
If a day was missing or empty, note it plainly.

Structure your output EXACTLY like this:

## Week in Review
2-3 sentence arc of the week. What kind of week was it overall?

## Dominant Themes
What ideas, topics, or domains kept appearing across multiple days?

## Execution vs Learning
Were you mostly doing or mostly consuming? What was the ratio?

## What Rolled Over
Tasks or intentions that kept appearing but didn't complete.

## Who You Were This Week
Energy and cognitive pattern across the week. 

## Week Score
One line. Rate the week: Low / Medium / High signal. One sentence on why.
"""

    response = await asyncio.to_thread(
        ai_client.models.generate_content,
        model=gemini_model,
        contents=f"Compress this week:\n\n{raw_data}",
        config={"system_instruction": system_prompt}
    )
    return response.text.strip()


# ── 3. NOTION WRITER (Bypasses the 2,000 Property Limit) ─────────────────────

def write_weekly_summary_to_notion(week_label: str, week_start_str: str, summary_text: str):
    try:
        # Pushing the summary into the page block content (children) instead of properties column
        notion.pages.create(
            parent={"database_id": WEEKLY_SUMMARY_DB_ID},
            properties={
                "Title": {"title": [{"text": {"content": f"Weekly Summary — {week_label}"}}]},
                "Date":  {"date": {"start": week_start_str}}
            },
            children=[
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": summary_text}}]
                    }
                }
            ]
        )
        print(f"✅ Weekly summary written to Notion for {week_label}")
    except Exception as e:
        print(f"⚠️ Failed to write weekly summary to Notion: {e}")


# ── 4. MAIN ENTRY (Bot Architecture Compatible) ─────────────────────────────

async def compile_weekly_summary(context=None, week_start: datetime = None):
    """
    Main entry point. Handles both automatic Telegram cron triggers and manual runs.
    """
    if week_start is None:
        now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        
        # 🔥 FIX: Properly target Monday of the week closing out right now
        days_since_monday = now_ist.weekday()
        week_start = now_ist - timedelta(days=days_since_monday)
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)

    week_end = week_start + timedelta(days=6)
    week_label = f"{week_start.strftime('%b %d')} – {week_end.strftime('%b %d, %Y')}"
    week_start_str = week_start.strftime("%Y-%m-%d")

    print(f"📅 Compiling weekly summary for {week_label}...")

    daily_summaries = fetch_daily_summaries_for_week(week_start)
    print(f"   → {len(daily_summaries)} daily summaries found")

    if not daily_summaries:
        print(f"⚠️ No daily summaries found for {week_label}. Skipping.")
        return

    summary = await generate_weekly_summary(week_label, daily_summaries)
    write_weekly_summary_to_notion(week_label, week_start_str, summary)


if __name__ == "__main__":
    # Allows you to still execute the file directly via terminal for manual testing
    asyncio.run(compile_weekly_summary())