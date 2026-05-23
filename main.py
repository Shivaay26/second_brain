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

gemini_model = 'gemini-3.1-flash-lite'
embedding_model = "gemini-embedding-2"

async def ask_command(update, context):
    """Interactive Vector Search Interface with URL retrieval."""
    question = " ".join(context.args)
    if not question:
        await update.message.reply_text("Please provide a question. Example: /ask What did I log yesterday?")
        return

    await update.message.reply_text("🧠 Searching memories...")
    try:
        embedding_response = ai_client.models.embed_content(
            model=embedding_model,
            contents=question,
            config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY", output_dimensionality=768)
        )
        
        search_response = qdrant_client.query_points(
            collection_name=COLLECTION_NAME,
            query=embedding_response.embeddings[0].values,
            limit=3
        )
        search_result = search_response.points

        if not search_result:
            await update.message.reply_text("No matching contexts found.")
            return

        retrieved_contexts = []
        for p in search_result:
            item_text = f"[{p.payload.get('timestamp')}] ({p.payload.get('category')}) {p.payload.get('content')}"
            if p.payload.get('source_url'):
                item_text += f" | Link to Watch: {p.payload.get('source_url')}"
            retrieved_contexts.append(item_text)
            
        context_str = "\n---\n".join(retrieved_contexts)

        system_instruction = (
            "You are a personal Second Brain. Answer using ONLY the provided memories. "
            "CRITICAL: If a 'Link to Watch' is present in the matching memory, you MUST explicitly "
            "include that exact clickable URL in your response so the user can rewatch it. Be conversational."
        )
        prompt = f"Memories:\n{context_str}\n\nQuestion: {question}"

        response = ai_client.models.generate_content(
            model=gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system_instruction),
        )
        await update.message.reply_text(response.text)

    except Exception as e:
        await update.message.reply_text(f"⛔ Error: {e}")


async def handle_incoming(update, context):
    """Routes media and text to the SQLite Queue"""
    message = update.message
    if not message: return
    
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


def main():
    if not TOKEN:
        raise ValueError("Critical Error: 'bot_token' missing from .env")
        
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("ask", ask_command))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_incoming))

    app.job_queue.run_repeating(
        run_compiler_job,
        interval=90,
        first=10,
        job_kwargs={"misfire_grace_time": 300}
    )

    app.job_queue.run_daily(
        compile_daily_summary,
        time=time(18, 30, tzinfo=timezone.utc),
    )

    app.job_queue.run_daily(
        compile_weekly_summary,
        time=time(18, 25, tzinfo=timezone.utc),
        days=(6,)
    )

    app.job_queue.run_daily(
        compile_monthly_summary,
        time=time(18, 20, tzinfo=timezone.utc),
    )

    print("🚀 Master Node Online. Interactive Mode & Auto-Compiler Active.")
    app.run_polling()

if __name__ == "__main__":
    main()