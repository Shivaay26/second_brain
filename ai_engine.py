import json
import asyncio
import os
from typing import List, Optional
from pydantic import BaseModel
from google.genai import types
from storage import (
    ai_client, fetch_pending_queue, update_queue_status,
    insert_task, insert_daily_log, insert_content_vault, insert_vector_batch
)
from media_processor import process_audio_file_via_gemini, process_external_video, batch_analyze_local_images

image_batch_size = 10
video_audio_batch_size = 5
gemini_model = 'gemini-3.1-flash-lite'  
embedding_model = "gemini-embedding-2"  

class TaskSchema(BaseModel):
    task_name: str
    due_date: Optional[str] = None
    status: str = "Not started"

class LogSchema(BaseModel):
    title: str
    category: str
    content: str

class VaultSchema(BaseModel):
    title: str
    url: str
    summary: str

class MemorySchema(BaseModel):
    category: str
    content: str
    source_url: Optional[str] = None

class TriageBlueprint(BaseModel):
    notion_tasks: List[TaskSchema] = []
    notion_logs: List[LogSchema] = []
    notion_vault: List[VaultSchema] = []
    qdrant_memories: List[MemorySchema] = []

async def compile_batch():
    """Asynchronous compilation sequence with unified Qdrant batch saving."""
    all_rows = fetch_pending_queue()
    if not all_rows:
        return 

    processed_rows = []
    
    # 1. TEXT BATCHES
    text_rows = [r for r in all_rows if r['type'] in ['text', 'url'] and not any(d in r['content'] for d in ['youtube.com', 'youtu.be', 'instagram.com'])]
    processed_rows.extend(text_rows)

    # 2. IMAGE BATCHING
    image_rows = [r for r in all_rows if r['type'] == 'image'][:image_batch_size]
    if image_rows:
        image_paths = [row['content'] for row in image_rows]
        descriptions = await batch_analyze_local_images(image_paths)
        
        for i, row in enumerate(image_rows):
            processed_rows.append({
                "id": row["id"], "type": "text",
                "content": f"[Visual Content Analysis]:\n{descriptions[i]}", 
                "timestamp": row["timestamp"]
            })
            if os.path.exists(row['content']): os.remove(row['content'])

    # 3. NATIVE AUDIO & VIDEO CAPPING
    media_rows = [r for r in all_rows if r['type'] == 'audio' or (r['type'] == 'url' and any(d in r['content'] for d in ['youtube.com', 'youtu.be', 'instagram.com']))][:video_audio_batch_size]
    for row in media_rows:
        if row['type'] == 'audio':
            # Routed straight through our native audio analyzer
            analysis_text = await process_audio_file_via_gemini(row['content'])
            processed_rows.append({
                "id": row["id"], "type": "text",
                "content": f"[Voice Note Evaluation]: {analysis_text}", "timestamp": row["timestamp"]
            })
            if os.path.exists(row['content']): os.remove(row['content'])
            await asyncio.sleep(2) 
            
        elif row['type'] == 'url':
            video_data = await process_external_video(row['content'])
            processed_rows.append({
                "id": row["id"], "type": "text",
                "content": f"[Video Content]\nTitle: {video_data['title']}\nURL: {row['content']}\nAnalysis:\n{video_data['transcript']}", 
                "timestamp": row["timestamp"]
            })
            await asyncio.sleep(2)

    if not processed_rows:
        return

    # --- TRIAGE SHIPPING ---
    batch_content = json.dumps(processed_rows, indent=2)
    
    # --- TRIAGE SHIPPING (INDIVIDUAL PROCESSING) ---
    system_prompt = """
    You are the structural triage router for a personal Second Brain system.
    Analyze this SINGLE inbox item and route it accurately.
    1. Tasks -> 'notion_tasks'
    2. Deep observations, logs -> 'notion_logs'
    3. High-value learning resources -> 'notion_vault' (Always route videos/educational links here)
    4. Rapid links, fleeting thoughts -> 'qdrant_memories' (Exclusive to Vector DB)
    
    CRITICAL DATE & URL RULES:
    - The user's local timezone is IST (UTC+5:30).
    - When generating 'due_date' strings, use format: YYYY-MM-DDTHH:mm:ss+05:30
    - Preserve original URLs exactly. Do not drop them.
    """
    
    print(f"📦 Triaging {len(processed_rows)} elements individually to guarantee isolation...")
    
    vector_batch_payload = []
    processed_ids = []

    # Process each item ONE-BY-ONE through the LLM
    for row in processed_rows:
        try:
            response = await asyncio.to_thread(
                ai_client.models.generate_content,
                model=gemini_model,
                contents=f"Deconstruct this item:\n\n{row['content']}",
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_schema=TriageBlueprint,
                )
            )
            
            blueprint = json.loads(response.text)
            
            # Route to Notion & stage for unified Vector Batch
            for task in blueprint.get("notion_tasks", []):
                insert_task(task["task_name"], task.get("due_date"), task.get("status", "Not started"))
                vector_batch_payload.append({
                    "content": f"Task: {task['task_name']} | Due: {task.get('due_date')}",
                    "category": "Task", "source_url": None
                })

            for log in blueprint.get("notion_logs", []):
                insert_daily_log(log["title"], log["category"], log["content"])
                vector_batch_payload.append({
                    "content": f"Log: {log['title']} | {log['content']}",
                    "category": log["category"], "source_url": None
                })

            for asset in blueprint.get("notion_vault", []):
                insert_content_vault(asset["title"], asset["url"], asset["summary"])
                vector_batch_payload.append({
                    "content": f"Vault: {asset['title']} | {asset['summary']}",
                    "category": "Vault", "source_url": asset["url"]
                })

            for memory in blueprint.get("qdrant_memories", []):
                vector_batch_payload.append({
                    "content": memory["content"],
                    "category": memory["category"], "source_url": memory.get("source_url")
                })
                
            processed_ids.append(row["id"])
            await asyncio.sleep(1) # Tiny safety buffer between API calls
            
        except Exception as e:
            print(f"⛔ Triage Error on row {row['id']}: {e}")

    # Commit ALL collected items to Qdrant at once!
    if vector_batch_payload:
        insert_vector_batch(vector_batch_payload)

    # Mark database queue items as processed
    if processed_ids:
        update_queue_status(processed_ids, "completed")
        print(f"✅ Successfully processed queue. IDs: {processed_ids}")
