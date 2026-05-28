import os
import asyncio
from datetime import datetime, timedelta, timezone
import calendar
from dotenv import load_dotenv
from notion_client import Client
from google import genai

load_dotenv()

NOTION_TOKEN = os.getenv("notion_token")
GEMINI_API_KEY = os.getenv("gemini_api_key")
WEEKLY_SUMMARY_DB_ID = os.getenv("weekly_summary_db_id")
MONTHLY_SUMMARY_DB_ID = os.getenv("monthly_summary_db_id")

notion = Client(auth=NOTION_TOKEN)
ai_client = genai.Client(api_key=GEMINI_API_KEY)
# Optimized to use your standard production model
from config import gemini_model


# ── 1. FETCHER ───────────────────────────────────────────────────────────────

def fetch_weekly_summaries_for_month(year: int, month: int) -> list:
    """Fetches all weekly summaries whose Date falls within the given month."""
    try:
        month_start = datetime(year, month, 1)
        last_day = calendar.monthrange(year, month)[1]
        month_end = datetime(year, month, last_day) + timedelta(days=1)

        results = notion.databases.query(
            database_id=WEEKLY_SUMMARY_DB_ID,
            filter={
                "and": [
                    {"property": "Date", "date": {"on_or_after": month_start.strftime("%Y-%m-%d")}},
                    {"property": "Date", "date": {"before": month_end.strftime("%Y-%m-%d")}},
                ]
            },
            sorts=[{"property": "Date", "direction": "ascending"}]
        )
        summaries = []
        for page in results.get("results", []):
            props = page["properties"]
            title   = props["Title"]["title"]
            content = props["Content"]["rich_text"]
            summaries.append({
                "title":   title[0]["text"]["content"] if title else "Untitled Week",
                "content": content[0]["text"]["content"] if content else "",
            })
        return summaries
    except Exception as e:
        print(f"⚠️ Failed to fetch weekly summaries: {e}")
        return []


# ── 2. GEMINI COMPRESSION ────────────────────────────────────────────────────

async def generate_monthly_summary(month_label: str, weekly_summaries: list) -> str:
    """Compresses weekly summaries into a monthly identity-level reflection."""

    raw_data = f"MONTH: {month_label}\n\n"
    for s in weekly_summaries:
        raw_data += f"=== {s['title']} ===\n{s['content']}\n\n"

    system_prompt = """
You are a personal intelligence compression engine for a Second Brain system.
You receive weekly summaries and produce a monthly identity-level reflection.
You are reading summaries of summaries of summaries — go deep, not broad.

Write in second person ("You..."). Be honest, sharp, and direct.
This is the highest compression layer — prioritize identity shifts over events.

Structure your output EXACTLY like this:

## Month in One Paragraph
A single dense paragraph capturing the arc of the month.

## What You Built
Concrete things that exist now that didn't exist at the start of the month.

## Recurring Patterns
What kept showing up week after week — positively or negatively?

## Where You Grew
Specific areas where you can see a measurable or qualitative shift.

## Where You Stayed Stuck
Honest assessment of avoidance, friction, or repeated failure points.

## Identity Delta
One paragraph. Who were you at the start of this month vs the end?

## Month Score
One line. Rate the month: Low / Medium / High signal. One sentence on why.
"""

    response = await asyncio.to_thread(
        ai_client.models.generate_content,
        model=gemini_model,
        contents=f"Compress this month:\n\n{raw_data}",
        config={"system_instruction": system_prompt}
    )
    return response.text.strip()


# ── 3. NOTION WRITER (Bypasses the 2,000 Property Limit) ─────────────────────

def write_monthly_summary_to_notion(month_label: str, month_start_str: str, summary_text: str):
    try:
        # We write the full text to the page BODY (children) to avoid character cutoffs
        notion.pages.create(
            parent={"database_id": MONTHLY_SUMMARY_DB_ID},
            properties={
                "Title": {"title": [{"text": {"content": f"Monthly Summary — {month_label}"}}]},
                "Date":  {"date": {"start": month_start_str}}
            },
            children=[
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": summary_text[:2000]}}]
                    }
                }
            ]
        )
        print(f"✅ Monthly summary written to Notion for {month_label}")
    except Exception as e:
        print(f"⚠️ Failed to write monthly summary to Notion: {e}")


# ── 4. MAIN ENTRY (Bot Architecture Compatible) ─────────────────────────────

async def compile_monthly_summary(context=None, year: int = None, month: int = None):
    """
    Main entry point. Handles both automatic Telegram cron calls and manual overrides.
    """
    # If no manual override is provided, calculate last month automatically
    if year is None or month is None:
        now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        
        # 🔥 CRITICAL GUARD CLAUSE: If running automatically via the bot, 
        # only proceed if today is the 1st day of the new month.
        if context is not None and now_ist.day != 1:
            return

        first_of_this_month = now_ist.replace(day=1)
        last_month = first_of_this_month - timedelta(days=1)
        year, month = last_month.year, last_month.month

    month_label = datetime(year, month, 1).strftime("%B %Y")
    month_start_str = datetime(year, month, 1).strftime("%Y-%m-%d")

    print(f"📅 Compiling monthly summary for {month_label}...")

    weekly_summaries = fetch_weekly_summaries_for_month(year, month)
    print(f"   → {len(weekly_summaries)} weekly summaries found")

    if not weekly_summaries:
        print(f"⚠️ No weekly summaries found for {month_label}. Skipping.")
        return

    summary = await generate_monthly_summary(month_label, weekly_summaries)
    write_monthly_summary_to_notion(month_label, month_start_str, summary)


if __name__ == "__main__":
    # Allows you to still execute the file directly via terminal for testing
    asyncio.run(compile_monthly_summary())