import json
import asyncio
import os
from typing import List, Optional
from pydantic import BaseModel
from google.genai import types
from storage import fetch_pending_queue, update_queue_status
from services.notion_service import insert_task, insert_daily_log
from services.qdrant_service import insert_vector_batch
from services.llm_service import generate_structured_data
from media_processor import process_audio_file_via_gemini, process_external_video, batch_analyze_local_images, batch_process_short_media

from config import (
    image_batch_size, short_media_batch_size, video_audio_batch_size, 
    gemini_model, embedding_model, yt_domains, short_domains
)

class TaskSchema(BaseModel):
    task_name: str
    due_date: Optional[str] = None
    status: str = "Not started"

class LogSchema(BaseModel):
    title: str
    category: str
    content: str

class MemorySchema(BaseModel):
    category: str
    content: str
    source_url: Optional[str] = None

class TriageBlueprint(BaseModel):
    notion_tasks: List[TaskSchema] = []
    notion_logs: List[LogSchema] = []
    qdrant_memories: List[MemorySchema] = []

async def compile_batch():
    """Asynchronous compilation sequence with unified Qdrant batch saving."""
    all_rows = fetch_pending_queue()
    if not all_rows:
        return 

    processed_rows = []
    
    # 1. TEXT BATCHES
    text_rows = [r for r in all_rows if r['type'] == 'text' or (r['type'] == 'url' and not any(d in r['content'] for d in yt_domains + short_domains))]
    processed_rows.extend(text_rows)

    # 2. IMAGE BATCHING
    image_rows = [r for r in all_rows if r['type'] == 'image'][:image_batch_size]
    if image_rows:
        image_paths = [row['content'] for row in image_rows]
        try:
            descriptions = await batch_analyze_local_images(image_paths)
            for i, row in enumerate(image_rows):
                processed_rows.append({
                    "id": row["id"], "type": "text",
                    "content": f"[Visual Content Analysis]:\n{descriptions[i]}", 
                    "timestamp": row["timestamp"]
                })
        finally:
            for path in image_paths:
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except:
                        pass

    # 3. SHORT-FORM MEDIA BATCHING (Insta, TikTok, Twitter)
    short_media_rows = [r for r in all_rows if r['type'] == 'url' and any(d in r['content'] for d in short_domains)][:short_media_batch_size]
    if short_media_rows:
        urls = [r['content'] for r in short_media_rows]
        short_results = await batch_process_short_media(urls)
        
        for i, row in enumerate(short_media_rows):
            # Map the returned dictionaries back to the processed payload
            processed_rows.append({
                "id": row["id"], 
                "type": "text",
                "content": f"[Short Video Content]\nTitle: {short_results[i].get('title', 'Unknown')}\nURL: {row['content']}\nAnalysis:\n{short_results[i].get('transcript', 'Failed')}", 
                "timestamp": row["timestamp"]
            })

    # 4. HEAVY MEDIA PROCESSING (Native Audio & YouTube)
    heavy_media_rows = [r for r in all_rows if r['type'] == 'audio' or (r['type'] == 'url' and any(d in r['content'] for d in yt_domains))][:video_audio_batch_size]
    for row in heavy_media_rows:
        if row['type'] == 'audio':
            try:
                analysis_text = await process_audio_file_via_gemini(row['content'])
                processed_rows.append({
                    "id": row["id"], "type": "text",
                    "content": f"[Voice Note Evaluation]: {analysis_text}", "timestamp": row["timestamp"]
                })
                await asyncio.sleep(2) 
            finally:
                if os.path.exists(row['content']):
                    try:
                        os.remove(row['content'])
                    except:
                        pass
            
        elif row['type'] == 'url':
            video_data = await process_external_video(row['content'])
            processed_rows.append({
                "id": row["id"], "type": "text",
                "content": f"[Long-Form Video]\nTitle: {video_data['title']}\nURL: {row['content']}\nAnalysis:\n{video_data['transcript']}", 
                "timestamp": row["timestamp"]
            })
            await asyncio.sleep(2)

    if not processed_rows:
        return

    # --- TRIAGE SHIPPING ---
    batch_content = json.dumps(processed_rows, indent=2)
    
    system_prompt = """
    You are the structural triage router for a personal Second Brain system.
    You take a compiled JSON batch of inbox data items and route them accurately.
    
    ROUTING RULES:
    - notion_tasks: ONLY explicit action items with a clear verb ("attend", "submit", "complete", "buy"). Images are NEVER tasks.
    - notion_logs: ONLY structured observations, reflections, or notes with meaningful depth. Raw image descriptions are NEVER logs.
    - qdrant_memories: Everything else — images, fleeting thoughts, quick links, random captures.

    
    CRITICAL DATE RULES:
    - The user's local timezone is IST (UTC+5:30).
    - When generating 'due_date' strings with specific times, strictly use ISO 8601 format with the local offset appended, matching this exact style: YYYY-MM-DDTHH:mm:ss+05:30
    CRITICAL: You must preserve original URLs. If an incoming item contains a URL, pass it exactly into the 'url' or 'source_url' fields of your schema. Do not drop them.
    """
    
    print(f"📦 Shipping batch of {len(processed_rows)} elements to Gemini...")
    
    try:
        response_text = await generate_structured_data(system_prompt, f"Deconstruct this batch:\n\n{batch_content}", TriageBlueprint)
        blueprint = json.loads(response_text)
        vector_batch_payload = []

        for task in blueprint.get("notion_tasks", []):
            insert_task(task["task_name"], task.get("due_date"), task.get("status", "Not started"))
            vector_batch_payload.append({
                "content": f"Task: {task['task_name']} | Due: {task.get('due_date')}",
                "category": "Task",
                "source_url": None
            })

        for log in blueprint.get("notion_logs", []):
            insert_daily_log(log["title"], log["category"], log["content"])
            vector_batch_payload.append({
                "content": f"Log: {log['title']} | {log['content']}",
                "category": log["category"],
                "source_url": None
            })

        for memory in blueprint.get("qdrant_memories", []):
            vector_batch_payload.append({
                "content": memory["content"],
                "category": memory["category"],
                "source_url": memory.get("source_url")
            })

        if vector_batch_payload:
            insert_vector_batch(vector_batch_payload)

        processed_ids = [row['id'] for row in processed_rows]
        update_queue_status(processed_ids, "completed")
        print(f"✅ Successfully processed batch. IDs: {processed_ids}")
        
    except Exception as e:
        print(f"⛔ Compilation Error: {e}")