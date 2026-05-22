import os
import json
import asyncio
import yt_dlp
import uuid
from typing import List
from PIL import Image
from google.genai import types

from storage import FOLDERS, ai_client, gemini_model

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
        Return a JSON array of strings, where each string is the analysis for that specific image.
        """
        
        request_contents = loaded_images + [vision_prompt]
        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model=gemini_model,
            contents=request_contents,
            # Native SDK configuration forces raw JSON and prevents markdown fence bugs
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        
        descriptions = json.loads(response.text)
        if len(descriptions) != len(image_paths):
            return [f"[Batch Processing Error: Output mismatch]\n{response.text}"] * len(image_paths)
            
        return descriptions
    except Exception as e:
        print(f"⛔ Image Batch Processing Error: {e}")
        return [f"[Failed to analyze image: {e}]"] * len(image_paths)


async def process_audio_file_via_gemini(file_path: str) -> str:
    """
    Uploads a local audio track natively to Gemini and waits for it to be ready.
    """
    if not os.path.exists(file_path):
        return "[Audio File Missing]"

    metadata_header = "### 🌐 [Audio: Full Recording]\n\n"

    print(f"🎙️ Shipping to Gemini: {os.path.basename(file_path)}")
    uploaded_file = None
    try:
        uploaded_file = await asyncio.to_thread(
            ai_client.files.upload,
            file=file_path
        )
        
        print("⏳ Waiting for Google servers to index the audio...")
        while True:
            info = await asyncio.to_thread(ai_client.files.get, name=uploaded_file.name)
            state = str(getattr(info, "state", ""))
            
            if "ACTIVE" in state or state == "2":
                break
            if "FAILED" in state or state == "3":
                return "[Gemini Processing Failed server-side]"
            
            await asyncio.sleep(5)
            
        print("✅ File is ready. Transcribing...")
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
        # Cloud clean-up happens no matter what
        if uploaded_file:
            try:
                await asyncio.to_thread(ai_client.files.delete, name=uploaded_file.name)
            except Exception:
                pass


async def process_external_video(url: str) -> dict:
    """Extracts the raw audio track from a URL natively and routes it straight to Gemini."""
    print(f"🎬 Downloading raw audio stream from URL: {url}")
    
    base_id = str(uuid.uuid4())[:8]
    outtmpl_path = os.path.join(FOLDERS["audio"], f"{base_id}_raw.%(ext)s")
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': outtmpl_path,
        'quiet': True,
        'no_warnings': True,
    }
    
    raw_audio_path = None
    try:
        def run_ydl():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                actual_path = ydl.prepare_filename(info)
                return info.get('title', 'Unknown Title'), actual_path
                
        video_title, raw_audio_path = await asyncio.to_thread(run_ydl)
        print(f"✅ Download complete: {os.path.basename(raw_audio_path)} (No conversion overhead!)")
        
        analysis = await process_audio_file_via_gemini(raw_audio_path)
        return {"title": video_title, "transcript": analysis}
        
    except Exception as e:
        print(f"⛔ External Media Processing Error: {e}")
        return {"title": "Error", "transcript": f"[Failed to process video stream: {e}]"}
        
    finally:
        # Fixed: Guaranteed disk cleanup happens even if Gemini API errors out mid-run
        if raw_audio_path and os.path.exists(raw_audio_path): 
            try:
                os.remove(raw_audio_path)
                print("Public 🧹 Cleaned up temporary local audio tracks.")
            except OSError:
                pass