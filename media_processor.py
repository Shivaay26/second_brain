import os
import json
import asyncio
import yt_dlp
import uuid
from typing import List
from PIL import Image
from storage import FOLDERS, ai_client
from pydub import AudioSegment
from google.genai import types
import re      
import math

# Make sure to import save_to_queue from your storage module if it isn't already
from storage import save_to_queue, ai_client, gemini_model

gemini_model = 'gemini-3.1-flash-lite'  # Global model configuration

async def batch_analyze_local_images(image_paths: List[str]) -> List[str]:
    """Opens a batch of local images, passes them to Gemini Vision, and returns OCR/descriptions."""
    if not image_paths:
        return []
        
    print(f"👁️ Batch analyzing {len(image_paths)} images in one request...")
    try:
        loaded_images = [Image.open(path) for path in image_paths if os.path.exists(path)]
        if not loaded_images:
            return ["[Image File Missing]"] * len(image_paths)
            
        vision_prompt = f"""
        I have provided {len(loaded_images)} images. 
        Analyze them in order. For each image, perform strict OCR and visual description.
        Return ONLY a JSON array of strings, where each string is the analysis for that specific image.
        """
        
        request_contents = loaded_images + [vision_prompt]
        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model=gemini_model,
            contents=request_contents
        )
        
        descriptions = json.loads(response.text)
        if len(descriptions) != len(image_paths):
            return [f"[Batch Processing Error: Output mismatch]\n{response.text}"] * len(image_paths)
            
        return descriptions
    except Exception as e:
        print(f"⛔ Image Batch Processing Error: {e}")
        return [f"[Failed to analyze image: {e}"] * len(image_paths)

async def process_audio_file_via_gemini(file_path: str) -> str:
    """
    Uploads a local audio track natively to Gemini.
    """
    if not os.path.exists(file_path):
        return "[Audio File Missing]"

    metadata_header = "### 🌐 [Audio: Full Recording]\n\n"

    # ==========================================
    # SHIP TO GEMINI (No Chunking)
    # ==========================================
    print(f"🎙️ Shipping to Gemini: {os.path.basename(file_path)}")
    uploaded_file = None
    try:
        uploaded_file = await asyncio.to_thread(
            ai_client.files.upload,
            file=file_path
        )
        
        audio_prompt = """
        Analyze this audio track perfectly. Provide a highly accurate transcription. 
        If you note specific acoustic shifts, emotional weight (urgency, fatigue, stress), 
        or environmental context, note it gracefully in brackets.
        """
        
        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model=gemini_model,
            contents=[uploaded_file, audio_prompt]
        )
        
        return metadata_header + response.text.strip()
        
    except Exception as e:
        print(f"⛔ Gemini Error: {e}")
        return f"[Failed to analyze audio directly: {e}]"
        
    finally:
        if uploaded_file:
            try:
                await asyncio.to_thread(ai_client.files.delete, name=uploaded_file.name)
            except Exception:
                pass


async def process_external_video(url: str) -> dict:
    """Extracts the raw audio track from a URL natively and routes it straight to Gemini."""
    print(f"🎬 Downloading raw audio stream from URL: {url}")
    
    base_id = str(uuid.uuid4())[:8]
    # Use %(ext)s so yt-dlp preserves the fast native format (webm/m4a)
    outtmpl_path = os.path.join(FOLDERS["audio"], f"{base_id}_raw.%(ext)s")
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': outtmpl_path,
        'quiet': True,
        'no_warnings': True,
        # Legacy FFmpeg conversion postprocessor REMOVED 🗑️
    }
    
    try:
        def run_ydl():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                # Safely capture the exact filename and native extension used
                actual_path = ydl.prepare_filename(info)
                return info.get('title', 'Unknown Title'), actual_path
                
        video_title, raw_audio_path = await asyncio.to_thread(run_ydl)
        print(f"✅ Download complete: {os.path.basename(raw_audio_path)} (No conversion overhead!)")
        
        # 2. Route the raw native file straight into your Gemini function
        analysis = await process_audio_file_via_gemini(raw_audio_path)
        
        # 3. Clean up the raw file
        if os.path.exists(raw_audio_path): 
            os.remove(raw_audio_path)
        print("🧹 Cleaned up temporary local audio tracks.")
        
        return {"title": video_title, "transcript": analysis}
        
    except Exception as e:
        print(f"⛔ External Media Processing Error: {e}")
        return {"title": "Error", "transcript": f"[Failed to process video stream: {e}]"}