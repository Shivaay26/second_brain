import os
import asyncio
from datetime import time, timezone
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from google.genai import types

# Import from our modular system
from storage import TOKEN, FOLDERS, ai_client, qdrant_client, COLLECTION_NAME, save_to_queue
from ai_engine import compile_batch

from daily_summary import compile_daily_summary
from weekly_summary import compile_weekly_summary
from monthly_summary import compile_monthly_summary

from datetime import datetime, timedelta, timezone
from storage import TASKS_DB_ID, DAILY_LOG_DB_ID, DAILY_SUMMARY_DB_ID, WEEKLY_SUMMARY_DB_ID, MONTHLY_SUMMARY_DB_ID, notion

# 🔥 FIX 1: Import the whole module to preserve state and prevent NameErrors
import brief_journal 

gemini_model = 'gemini-3.1-flash-lite'
embedding_model = "gemini-embedding-2"

MY_CHAT_ID = 6455532575

conversation_history = []
MAX_HISTORY = 15

async def run_morning_brief(context: ContextTypes.DEFAULT_TYPE):
    await brief_journal.send_morning_brief(context.bot, chat_id=MY_CHAT_ID)

async def run_journal_prompt(context: ContextTypes.DEFAULT_TYPE):
    await brief_journal.send_journal_prompt(context.bot, chat_id=MY_CHAT_ID)

async def reflect_command(update, context):
    """Fetches daily/weekly/monthly summaries and reflects on them."""
    query = " ".join(context.args).lower().strip()
    await update.message.reply_text("🔍 Pulling summaries...")
    
    try:
        summaries = []
        fetch_daily   = not query or any(w in query for w in ["day", "today", "yesterday", "daily"])
        fetch_weekly  = not query or any(w in query for w in ["week", "weekly"])
        fetch_monthly = not query or any(w in query for w in ["month", "monthly"])

        def read_page_body(page_id: str) -> str:
            blocks = notion.blocks.children.list(block_id=page_id)
            return " ".join(
                b["paragraph"]["rich_text"][0]["text"]["content"]
                for b in blocks.get("results", [])
                if b["type"] == "paragraph" and b["paragraph"]["rich_text"]
            )

        # 🔥 FIX 2: Wrapping Notion Database Queries in thread to prevent bot freeze
        if fetch_daily:
            daily_raw = await asyncio.to_thread(
                notion.databases.query,
                database_id=DAILY_SUMMARY_DB_ID,
                sorts=[{"property": "Date", "direction": "descending"}],
                page_size=7
            )
            for page in daily_raw.get("results", []):
                date = page["properties"]["Date"]["date"]
                body = await asyncio.to_thread(read_page_body, page["id"])
                summaries.append(f"[DAILY — {date['start'] if date else 'Unknown'}]\n{body}")

        if fetch_weekly:
            weekly_raw = await asyncio.to_thread(
                notion.databases.query,
                database_id=WEEKLY_SUMMARY_DB_ID,
                sorts=[{"property": "Date", "direction": "descending"}],
                page_size=4
            )
            for page in weekly_raw.get("results", []):
                date = page["properties"]["Date"]["date"]
                body = await asyncio.to_thread(read_page_body, page["id"])
                summaries.append(f"[WEEKLY — {date['start'] if date else 'Unknown'}]\n{body}")

        if fetch_monthly:
            monthly_raw = await asyncio.to_thread(
                notion.databases.query,
                database_id=MONTHLY_SUMMARY_DB_ID,
                sorts=[{"property": "Date", "direction": "descending"}],
                page_size=3
            )
            for page in monthly_raw.get("results", []):
                date = page["properties"]["Date"]["date"]
                body = await asyncio.to_thread(read_page_body, page["id"])
                summaries.append(f"[MONTHLY — {date['start'] if date else 'Unknown'}]\n{body}")

        if not summaries:
            await update.message.reply_text("No summaries found yet. Let the system run for a few days first.")
            return

        context_str = "\n\n---\n\n".join(summaries)
        history_str = ""
        if conversation_history:
            history_str = "=== CONVERSATION HISTORY ===\n"
            for turn in conversation_history[-MAX_HISTORY:]:
                history_str += f"You: {turn['question']}\nAssistant: {turn['answer']}\n---\n"

        system_instruction = """
You are a personal Second Brain reflection engine.
You have access to the user's daily, weekly, and monthly summaries and conversation history.
Use conversation history to understand follow-up questions and maintain context across the session.
Answer retrospective questions honestly and sharply.
Identify patterns, growth, and stuck points when relevant.
Be direct — not motivational. Write in second person ("You...").
Never say "based on the context" — just reflect naturally.
"""
        prompt = f"{history_str}\nSummaries:\n{context_str}\n\nQuestion: {query or 'Give me a general reflection on my recent activity.'}"
        
        # 🔥 FIX 2: Gemini Generation wrapped in thread
        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model=gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system_instruction),
        )
        answer = response.text

        conversation_history.append({"question": query or "general reflection", "answer": answer})
        if len(conversation_history) > MAX_HISTORY:
            conversation_history.pop(0)

        await update.message.reply_text(answer)

    except Exception as e:
        await update.message.reply_text(f"⛔ Error: {e}")


async def done_command(update, context):
    """Marks a matching task as Done in Notion."""
    task_query = " ".join(context.args).strip()
    if not task_query:
        await update.message.reply_text("Usage: /done <task name>\nExample: /done ISB session")
        return

    await update.message.reply_text(f"🔍 Looking for: '{task_query}'...")

    try:
        # 🔥 FIX 2: Threaded Notion request
        results = await asyncio.to_thread(
            notion.databases.query,
            database_id=TASKS_DB_ID,
            filter={
                "or": [
                    {"property": "Status_Update", "status": {"equals": "Not started"}},
                    {"property": "Status_Update", "status": {"equals": "In progress"}},
                ]
            }
        )

        pages = results.get("results", [])
        if not pages:
            await update.message.reply_text("No pending tasks found.")
            return

        task_list = []
        for page in pages:
            props = page["properties"]
            name = props["Task_Name"]["title"]
            task_list.append({
                "id": page["id"],
                "name": name[0]["text"]["content"] if name else "Untitled"
            })

        task_names = "\n".join(f"{i+1}. {t['name']}" for i, t in enumerate(task_list))
        
        # 🔥 FIX 2: Threaded Gemini request
        match_response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model=gemini_model,
            contents=f'Given this query: "{task_query}"\nAnd this task list:\n{task_names}\n\nReply with ONLY the number of the best matching task. If nothing matches reasonably, reply with 0.'
        )

        match_index = int(match_response.text.strip()) - 1

        if match_index < 0 or match_index >= len(task_list):
            await update.message.reply_text(f"❌ No matching task found for '{task_query}'.")
            return

        matched = task_list[match_index]

        # 🔥 FIX 2: Threaded Notion Update
        await asyncio.to_thread(
            notion.pages.update,
            page_id=matched["id"],
            properties={"Status_Update": {"status": {"name": "Done"}}}
        )

        await update.message.reply_text(f"✅ Marked as Done: {matched['name']}")

    except Exception as e:
        await update.message.reply_text(f"⛔ Error: {e}")


async def ask_command(update, context):
    question = " ".join(context.args)
    if not question:
        await update.message.reply_text("Please provide a question. Example: /ask What did I log yesterday?")
        return

    await update.message.reply_text("🧠 Searching memories...")
    try:
        # 🔥 FIX 2: Re-added thread wrappers from our earlier sessions
        embedding_response = await asyncio.to_thread(
            ai_client.models.embed_content,
            model=embedding_model,
            contents=question,
            config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY", output_dimensionality=768)
        )
        
        search_response = await asyncio.to_thread(
            qdrant_client.query_points,
            collection_name=COLLECTION_NAME,
            query=embedding_response.embeddings[0].values,
            limit=3
        )
        
        retrieved_contexts = []
        for p in search_response.points:
            item_text = f"[{p.payload.get('timestamp')}] ({p.payload.get('category')}) {p.payload.get('content')}"
            if p.payload.get('source_url'):
                item_text += f" | Link: {p.payload.get('source_url')}"
            retrieved_contexts.append(item_text)

        now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        
        tasks_raw = await asyncio.to_thread(
            notion.databases.query,
            database_id=TASKS_DB_ID,
            filter={"or": [{"property": "Status_Update", "status": {"equals": "Not started"}}, {"property": "Status_Update", "status": {"equals": "In progress"}}]}
        )
        
        tasks_lines = []
        for page in tasks_raw.get("results", []):
            props = page["properties"]
            name   = props["Task_Name"]["title"]
            status = props["Status_Update"]["status"]
            due    = props["Due_Date"]["date"]
            tasks_lines.append(f"  - {name[0]['text']['content'] if name else 'Untitled'} [{status['name'] if status else 'Unknown'}]{' | Due: ' + due['start'] if due else ''}")

        today_str    = now_ist.strftime("%Y-%m-%d")
        tomorrow_str = (now_ist + timedelta(days=1)).strftime("%Y-%m-%d")
        
        logs_raw = await asyncio.to_thread(
            notion.databases.query,
            database_id=DAILY_LOG_DB_ID,
            filter={"and": [{"timestamp": "created_time", "created_time": {"on_or_after": today_str}}, {"timestamp": "created_time", "created_time": {"before": tomorrow_str}}]}
        )
        
        logs_lines = []
        for page in logs_raw.get("results", []):
            props = page["properties"]
            title    = props["Title"]["title"]
            category = props["Category"]["select"]
            content  = props["Content"]["rich_text"]
            logs_lines.append(f"  [{category['name'] if category else 'General'}] {title[0]['text']['content'] if title else 'Untitled'}: {content[0]['text']['content'] if content else ''}")

        notion_context = f"=== LIVE NOTION STATE ===\n\nPENDING TASKS ({len(tasks_lines)}):\n{chr(10).join(tasks_lines) or '  None'}\n\nTODAY'S LOGS ({len(logs_lines)}):\n{chr(10).join(logs_lines) or '  None'}\n"

        history_str = ""
        if conversation_history:
            history_str = "=== CONVERSATION HISTORY ===\n"
            for turn in conversation_history[-MAX_HISTORY:]:
                history_str += f"You: {turn['question']}\nAssistant: {turn['answer']}\n---\n"

        system_instruction = """
You are a personal Second Brain assistant with access to semantic memories, live Notion state, and conversation history.
Use conversation history to understand follow-up questions and maintain context across the session.
Answer using the full context — memories for deep/historical questions, Notion for current tasks and today's activity.
Be direct and conversational. If a URL is present in memories, include it.
Never say "based on the context provided" — just answer naturally.
"""
        prompt = f"{history_str}\nSemantic Memories:\n{chr(10).join(retrieved_contexts) or 'None'}\n\n{notion_context}\n\nQuestion: {question}\n"
        
        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model=gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system_instruction),
        )
        answer = response.text

        conversation_history.append({"question": question, "answer": answer})
        if len(conversation_history) > MAX_HISTORY:
            conversation_history.pop(0)

        await update.message.reply_text(answer)

    except Exception as e:
        await update.message.reply_text(f"⛔ Error: {e}")


async def clear_command(update, context):
    """Clears the conversation history."""
    conversation_history.clear()
    await update.message.reply_text("🧹 Conversation history cleared.")


async def handle_incoming(update, context):
    """Routes media and text to the SQLite Queue or Journal"""
    message = update.message
    if not message: return

    # 🔥 FIX 1: Corrected namespace reference for waiting_for_journal
    if brief_journal.waiting_for_journal and message.text:
        await brief_journal.handle_journal_response(update, context.bot)
        return
    
    msg_type, content = None, None

    if message.text:
        msg_type = "url" if message.text.startswith("http") else "text"
        content = message.text
    elif message.photo:
        msg_type = "image"
        file = await message.photo[-1].get_file()
        content = os.path.join(FOLDERS["image"], f"{message.photo[-1].file_unique_id}.jpg")
        await file.download_to_drive(content)
    elif message.video:
        msg_type = "video"
        file = await message.video.get_file()
        ext = message.video.mime_type.split("/")[-1] if message.video.mime_type else "mp4"
        content = os.path.join(FOLDERS["video"], f"{message.video.file_unique_id}.{ext}")
        await file.download_to_drive(content)
    elif message.voice or message.audio:
        msg_type = "audio"
        audio_obj = message.voice if message.voice else message.audio
        file = await audio_obj.get_file()
        ext = "ogg" if message.voice else "mp3"
        content = os.path.join(FOLDERS["audio"], f"{audio_obj.file_unique_id}.{ext}")
        await file.download_to_drive(content)

    if msg_type:
        save_to_queue(msg_type, content)
        await message.reply_text(f"📥 Queued: [{msg_type.upper()}]")


async def run_compiler_job(context: ContextTypes.DEFAULT_TYPE):
    """Background task that runs the AI triage loop"""
    try:
        await compile_batch()
    except Exception as e:
        print(f"Background Compiler failed: {e}")


async def setup_menu_commands(application: Application):
    """🔥 BONUS UPGRADE: Persistent Telegram Menu Button"""
    commands = [
        ("ask", "Ask your Second Brain a question"),
        ("reflect", "Reflect on Daily/Weekly/Monthly summaries"),
        ("done", "Mark a Notion task as completed"),
        ("clear", "Clear chat context history")
    ]
    await application.bot.set_my_commands(commands)


def main():
    if not TOKEN:
        raise ValueError("Critical Error: 'bot_token' missing from .env")
        
    # Set up the Application
    app = Application.builder().token(TOKEN).post_init(setup_menu_commands).build()

    # Handlers
    app.add_handler(CommandHandler("ask", ask_command))
    app.add_handler(CommandHandler("reflect", reflect_command))
    app.add_handler(CommandHandler("done", done_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_incoming))

    # Jobs
    app.job_queue.run_repeating(
        run_compiler_job,
        interval=600,
        first=10,
        job_kwargs={"misfire_grace_time": 300}
    )

    app.job_queue.run_daily(compile_daily_summary, time=time(18, 30, tzinfo=timezone.utc))
    app.job_queue.run_daily(compile_weekly_summary, time=time(18, 25, tzinfo=timezone.utc), days=(6,))
    app.job_queue.run_daily(compile_monthly_summary, time=time(18, 20, tzinfo=timezone.utc))
    
    # Brief & Journal triggers
    app.job_queue.run_daily(run_morning_brief, time(2, 30, tzinfo=timezone.utc))
    app.job_queue.run_daily(run_journal_prompt, time(16, 30, tzinfo=timezone.utc))

    print("🚀 Master Node Online. Interactive Mode & Auto-Compiler Active.")
    app.run_polling()


if __name__ == "__main__":
    main()