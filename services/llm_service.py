import os
import asyncio
from google import genai
from google.genai import types
from dotenv import load_dotenv
from config import gemini_model, embedding_model, VECTOR_SIZE

load_dotenv()
GEMINI_API_KEY = os.getenv("gemini_api_key")
ai_client = genai.Client(api_key=GEMINI_API_KEY)

async def generate_text(prompt: str, content: str) -> str:
    """Generates text from Gemini using a system prompt and raw content."""
    response = await asyncio.to_thread(
        ai_client.models.generate_content,
        model=gemini_model,
        contents=content,
        config={"system_instruction": prompt}
    )
    return response.text.strip()

async def generate_content(contents, prompt: str = None, config=None):
    """Generates Gemini content for multimodal or custom-config requests."""
    if prompt and config is None:
        config = {"system_instruction": prompt}

    return await asyncio.to_thread(
        ai_client.models.generate_content,
        model=gemini_model,
        contents=contents,
        config=config,
    )

async def generate_structured_data(prompt: str, content: str, schema):
    """Generates JSON structured data conforming to a Pydantic schema."""
    response = await asyncio.to_thread(
        ai_client.models.generate_content,
        model=gemini_model,
        contents=content,
        config=types.GenerateContentConfig(
            system_instruction=prompt,
            response_mime_type="application/json",
            response_schema=schema,
        )
    )
    return response.text

async def upload_file(file_path: str):
    """Uploads a local file to Gemini Files."""
    return await asyncio.to_thread(ai_client.files.upload, file=file_path)

async def get_file(name: str):
    """Fetches Gemini File metadata."""
    return await asyncio.to_thread(ai_client.files.get, name=name)

async def delete_file(name: str):
    """Deletes a Gemini File."""
    return await asyncio.to_thread(ai_client.files.delete, name=name)

def get_embeddings(contents: list, task_type: str = None):
    """Generates embeddings for a list of contents in a single network call."""
    config_kwargs = {"output_dimensionality": VECTOR_SIZE}
    if task_type:
        config_kwargs["task_type"] = task_type

    return ai_client.models.embed_content(
        model=embedding_model,
        contents=contents,
        config=types.EmbedContentConfig(**config_kwargs)
    )

async def get_embeddings_async(contents, task_type: str = None):
    """Async wrapper around Gemini embeddings."""
    return await asyncio.to_thread(get_embeddings, contents, task_type)
