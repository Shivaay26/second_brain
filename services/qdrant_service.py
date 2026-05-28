import os
import uuid
from datetime import datetime, timezone
from typing import List, Dict
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, PointIdsList

from config import QDRANT_PATH, COLLECTION_NAME, VECTOR_SIZE
from services.llm_service import get_embeddings
from google.genai import types

qdrant_client = QdrantClient(path=QDRANT_PATH)

# Ensure vector collection exists
if not qdrant_client.collection_exists(COLLECTION_NAME):
    qdrant_client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE)
    )

def insert_vector_batch(memories: List[Dict]):
    """Upserts a batch of memories to Qdrant."""
    if not memories:
        return
        
    try:
        contents = [
            types.Content(parts=[types.Part.from_text(text=item["content"])]) 
            for item in memories
        ]
        
        embedding_response = get_embeddings(contents)
        
        points = []
        for item, embedding_obj in zip(memories, embedding_response.embeddings):
            point_id = str(uuid.uuid4())
            payload = {
                "category": item.get("category", "Uncategorized"),
                "content": item["content"],
                "source_url": item.get("source_url"),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            points.append(
                PointStruct(id=point_id, vector=embedding_obj.values, payload=payload)
            )
            
        qdrant_client.upsert(collection_name=COLLECTION_NAME, points=points)
        print(f"✅ Successfully batched and inserted {len(points)} vector memories.")
    except Exception as e:
        print(f"⚠️ Failed to batch insert vector memories: {e}")

def delete_vectors(point_ids: List[str]):
    """Deletes specific vectors from Qdrant by their point IDs."""
    if not point_ids: return
    try:
        qdrant_client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=PointIdsList(points=point_ids)
        )
        print(f"✅ Successfully deleted {len(point_ids)} vectors from Qdrant.")
    except Exception as e:
        print(f"⚠️ Failed to delete vectors {point_ids}: {e}")

def search_vectors(query_vector: list, limit: int = 5):
    """Searches for similar vectors in Qdrant."""
    return qdrant_client.search(
        collection_name=COLLECTION_NAME,
        query_vector=query_vector,
        limit=limit
    )

def query_vectors(query_vector: list, limit: int = 100):
    """Queries Qdrant for nearest memories using the current client API."""
    return qdrant_client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        limit=limit
    )
