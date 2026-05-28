import os
import json
import asyncio
import yt_dlp
import uuid
from typing import List
from PIL import Image
from google.genai import types
import re

from config import FOLDERS
from services.llm_service import generate_content, upload_file, get_file, delete_file

def check_capacity_error(e: Exception, context: str):
    error_msg = str(e)
    if any(code in error_msg for code in ["503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED"]):
        print(f"🚨 Server capacity threshold reached in {context}! Raising bubble-up exception to save data.")
        raise RuntimeError(f"Transient Google API Error: {error_msg}. Postponing item.")

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
        response = await generate_content(
            contents=request_contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        
        descriptions = json.loads(response.text)
        if len(descriptions) != len(image_paths):
            return [f"[Batch Processing Error: Output mismatch]\n{response.text}"] * len(image_paths)
            
        return descriptions
        
    except Exception as e:
        check_capacity_error(e, "Image Batch")
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
        uploaded_file = await upload_file(file_path)
        
        print("⏳ Waiting for Google servers to index the audio...")
        while True:
            info = await get_file(uploaded_file.name)
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
        
        response = await generate_content(
            contents=[uploaded_file, audio_prompt]
        )
        
        return metadata_header + response.text.strip()
        
    except Exception as e:
        check_capacity_error(e, "Audio Processing")
        print(f"⛔ Gemini Error: {e}")
        return f"[Failed to analyze audio directly: {e}]"
        
    finally:
        # Cloud clean-up happens no matter what
        if uploaded_file:
            try:
                await delete_file(uploaded_file.name)
            except Exception:
                pass


async def process_external_video(url: str) -> dict:
    """
    Strictly native Gemini ingestion for YouTube. 
    NO yt-dlp. NO proxies. Heavily optimized for long-form content.
    """
    # Extract EXACTLY the 11-character video ID, catching Shorts and Live links too
    video_id_match = re.search(r'(?:v=|\/v\/|youtu\.be\/|\/embed\/|\/shorts\/|\/live\/)([a-zA-Z0-9_-]{11})', url)
    
    if video_id_match:
        video_id = video_id_match.group(1)
        clean_url = f"https://www.youtube.com/watch?v={video_id}"
        print(f"🧹 Sanitized URL: {clean_url}")
    else:
        clean_url = url

    print(f"🧠 Attempting strictly native YouTube URL ingestion (10-min cap) for: {clean_url}")

    youtube_prompt = """
    You are an advanced multimodal transcription and analytical summarization engine.
    Analyze both the audio track and the sampled visual frames of this video.
    Provide a highly accurate transcription of the spoken content. 
    Use the visual elements (such as presentation slides, graphs, or on-screen text) to add crucial context to your structured summary.
    Focus your analytical energy on the core thesis statements and main talking points, synthesizing what is said with what is shown.
    """

    try:
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

        response = await generate_content(
            contents=request_content
        )

        return {
            "title": "YouTube Direct Ingestion",
            "transcript": f"### 🌐 [YouTube: Direct Ingestion (10 Min Cap)]\n\n{response.text.strip()}"
        }

    except Exception as e:
        check_capacity_error(e, "YouTube Direct")
        print(f"⚠️ YouTube Direct Error: {e}")
        print("❌ Gemini rejected the URL format natively. Marking as failed.")
        return {
            "title": "Error", 
            "transcript": f"[Failed: Gemini rejected the video natively. Likely Unlisted, Age-Restricted, or the 8-hour limit reached. Details: {e}]"
        }


async def batch_process_short_media(urls: List[str]) -> List[dict]:
    """
    Takes a batch of short-form URLs (Instagram, TikTok, Twitter), 
    concurrently downloads their audio locally, and processes them all in ONE single Gemini API call.
    """
    if not urls:
        return []

    print(f"🎬 Batching {len(urls)} short-form media links concurrently...")
    
    # --- 1. CONCURRENT LOCAL DOWNLOADS ---
    async def download_single_audio(url: str):
        base_id = str(uuid.uuid4())[:8]
        outtmpl_path = os.path.join(FOLDERS["audio"], f"{base_id}_raw.%(ext)s")
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': outtmpl_path,
            'quiet': True,
            'no_warnings': True,
        }
        try:
            def run_ydl():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    return info.get('title', 'Unknown Title'), ydl.prepare_filename(info), url
            
            return await asyncio.to_thread(run_ydl)
        except Exception as e:
            print(f"⚠️ yt-dlp failed for {url}: {e}")
            return "Error", None, url

    download_results = await asyncio.gather(*(download_single_audio(u) for u in urls))
    valid_downloads = [res for res in download_results if res[1] and os.path.exists(res[1])]
    
    if not valid_downloads:
        return [{"title": "Error", "transcript": "[All local downloads failed]", "url": u} for u in urls]

    # --- 2. MEGA UPLOAD TO GEMINI ---
    uploaded_files = []
    request_contents = []
    
    try:
        print(f"🎙️ Uploading {len(valid_downloads)} audio tracks to Gemini...")
        for i, (title, path, url) in enumerate(valid_downloads):
            uploaded_file = await upload_file(path)
            uploaded_files.append((uploaded_file, title, url, path))
            
            request_contents.append(f"Audio Track {i+1} (Title: {title}):")
            request_contents.append(uploaded_file)

        print("⏳ Waiting for Google servers to index all tracks...")
        for uploaded, _, _, _ in uploaded_files:
            while True:
                info = await get_file(uploaded.name)
                state = str(getattr(info, "state", ""))
                if "ACTIVE" in state or state == "2":
                    break
                if "FAILED" in state or state == "3":
                    raise RuntimeError(f"File {uploaded.name} failed to index server-side.")
                await asyncio.sleep(3)

        # --- 3. THE SINGLE API CALL ---
        print(f"✅ All {len(valid_downloads)} files ready. Transcribing everything in one shot...")
        batch_prompt = f"""
        I have provided {len(valid_downloads)} short audio tracks.
        Analyze each of them perfectly.
        Return a strict JSON array of strings containing EXACTLY {len(valid_downloads)} elements.
        Element 1 must be the transcript/analysis for Audio Track 1, Element 2 for Audio Track 2, etc.
        """
        request_contents.append(batch_prompt)
        
        response = await generate_content(
            contents=request_contents,
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        
        transcripts = json.loads(response.text)
        
        # --- 4. MAP RESULTS ---
        final_results = []
        for i, (uploaded, title, url, path) in enumerate(uploaded_files):
            transcript = transcripts[i] if i < len(transcripts) else "[Mapping Error: Gemini dropped this output]"
            final_results.append({
                "title": title,
                "url": url,
                "transcript": f"### 🌐 [Short-Form Audio Batch]\n\n{transcript}"
            })
            
        return final_results

    except Exception as e:
        check_capacity_error(e, "Short Media Batch")
        print(f"⛔ Batch Audio Processing Error: {e}")
        return [{"title": "Error", "transcript": f"[Batch processing failed: {e}]", "url": u} for u in urls]

    finally:
        # --- 5. AGGRESSIVE CLEANUP ---
        for uploaded, _, _, local_path in uploaded_files:
            try:
                if os.path.exists(local_path):
                    os.remove(local_path)
                await delete_file(uploaded.name)
            except Exception:
                pass
        print("🧹 Cleaned up all temporary local and cloud batch files.")
