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

def get_embeddings(contents: list):
    """Generates embeddings for a list of contents in a single network call."""
    return ai_client.models.embed_content(
        model=embedding_model,
        contents=contents,
        config=types.EmbedContentConfig(
            output_dimensionality=VECTOR_SIZE # Force to match existing Qdrant db
        )
    )
