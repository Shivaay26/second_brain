import os
import json
import asyncio
import yt_dlp
import uuid
from typing import List
from PIL import Image
from google.genai import types
import re
from datetime import timedelta

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
        error_msg = str(e)
        print(f"⛔ Image Batch Processing Error: {error_msg}")
        
        # CRITICAL SAFETY GATE: Protect image data from API capacity spikes
        if "503" in error_msg or "UNAVAILABLE" in error_msg or "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
            print("🚨 Server capacity threshold reached! Raising bubble-up exception to save image data.")
            raise RuntimeError(f"Transient Google API Error: {error_msg}. Postponing image batch.")
            
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
        error_msg = str(e)
        print(f"⛔ Gemini Error: {error_msg}")
        
        # CRITICAL SAFETY GATE: Protect audio transcription from dropping during generation
        if "503" in error_msg or "UNAVAILABLE" in error_msg or "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
            print("🚨 Server capacity threshold reached! Raising bubble-up exception to save audio data.")
            raise RuntimeError(f"Transient Google API Error: {error_msg}. Postponing audio item.")
            
        return f"[Failed to analyze audio directly: {e}]"
        
    finally:
        # Cloud clean-up happens no matter what
        if uploaded_file:
            try:
                await asyncio.to_thread(ai_client.files.delete, name=uploaded_file.name)
            except Exception:
                pass


async def process_external_video(url: str) -> dict:
    """
    Strictly native Gemini ingestion for YouTube. 
    NO yt-dlp. NO proxies.
    If Gemini rejects it (400 error), it dies gracefully.
    """

    # --- THE ULTIMATE URL CLEANER ---
    # Extract EXACTLY the 11-character video ID, catching Shorts and Live links too
    video_id_match = re.search(r'(?:v=|\/v\/|youtu\.be\/|\/embed\/|\/shorts\/|\/live\/)([a-zA-Z0-9_-]{11})', url)
    
    if video_id_match:
        video_id = video_id_match.group(1)
        # Rebuild a perfectly pristine URL that Gemini loves
        clean_url = f"https://www.youtube.com/watch?v={video_id}"
        print(f"🧹 Sanitized URL: {clean_url}")
    else:
        clean_url = url # Fallback if it somehow doesn't match

    # --- YOUTUBE DIRECT ROUTE ONLY ---
    if "youtube.com" in clean_url or "youtu.be" in clean_url:
        print(f"🧠 Attempting strictly native YouTube URL ingestion (10-min cap)...")

        youtube_prompt = """
        You are an advanced multimodal transcription and analytical summarization engine.
        Analyze both the audio track and the sampled visual frames of this video.
        Provide a highly accurate transcription of the spoken content. 
        Use the visual elements (such as presentation slides, graphs, or on-screen text) to add crucial context to your structured summary.
        Focus your analytical energy on the core thesis statements and main talking points, synthesizing what is said with what is shown.
        """

        try:
            # Strictly matching the Gemini 3.5 documentation formatting
            request_content = types.Content(
                parts=[
                    types.Part(
                        file_data=types.FileData(file_uri=clean_url),
                        video_metadata=types.VideoMetadata(
                            start_offset='0s',
                            end_offset='600s', # Back up to 10 minutes!
                            fps=0.1            # 1 frame every 10 seconds to slash token costs
                        )
                    ),
                    types.Part(text=youtube_prompt)
                ]
            )

            response = await asyncio.to_thread(
                ai_client.models.generate_content,
                model=gemini_model,
                contents=request_content
            )

            return {
                "title": "YouTube Direct Ingestion Summary",
                "transcript": f"### 🌐 [YouTube: Direct Ingestion (10 Min Cap)]\n\n{response.text.strip()}"
            }

        except Exception as e:
            error_msg = str(e)
            print(f"⚠️ YouTube Direct Error: {error_msg}")
            
            # CRITICAL SAFETY GATE: Protect data on 503/429 transient spikes
            if "503" in error_msg or "UNAVAILABLE" in error_msg or "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                print("🚨 Server capacity threshold reached! Raising bubble-up exception to postpone data.")
                raise RuntimeError(f"Transient Google API Error: {error_msg}. Postponing item.")
            
            # HARD FAIL: If it's a 400, we accept defeat. No scraping fallbacks.
            print("❌ Gemini rejected the URL format natively. Marking as failed.")
            return {
                "title": "Error", 
                "transcript": f"[Failed: Gemini rejected the video natively. Likely Unlisted, Age-Restricted, or the 8-hour limit reached. Details: {error_msg}]"
            }

    # --- 2. CHEAP AUDIO ROUTE (Instagram, TikTok, etc.) ---
    print(f"🎬 Downloading raw audio stream from URL via yt-dlp: {url}")

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
        print(f"✅ Download complete: {os.path.basename(raw_audio_path)}")

        analysis = await process_audio_file_via_gemini(raw_audio_path)
        return {"title": video_title, "transcript": analysis}

    except Exception as e:
        error_msg = str(e)
        print(f"⛔ External Media Processing Error: {error_msg}")
        
        # Protect local audio conversion downstreams from dropping during API outages
        if "503" in error_msg or "UNAVAILABLE" in error_msg or "429" in error_msg:
            raise RuntimeError(f"Transient Google API Error during audio parsing: {error_msg}. Postponing item.")
            
        return {"title": "Error", "transcript": f"[Failed to process video stream: {e}]"}

    finally:
        if raw_audio_path and os.path.exists(raw_audio_path):
            try:
                os.remove(raw_audio_path)
                print("🧹 Cleaned up temporary local audio tracks.")
            except OSError:
                pass