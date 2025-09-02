
from datetime import datetime
import pytz
import uuid
from db.mongo import client
from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()

# Notes collection setup
db = client["oauth_db"]
notes_collection = db["notes"]

# OpenAI client for embeddings
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

class AuthRequiredError(Exception):
    pass

def create_note(user_id: str = None, content: str = None, title: str = None) -> dict:
    if user_id is None:
        raise ValueError("Missing user_id in create_note() call!")
    
    print("Creating note for user_id:", user_id, type(user_id))
    
    # Validate content (required)
    if not content:
        raise ValueError("content is required")
    
    # Auto-generate title if not provided
    if not title:
        try:
            # Use OpenAI to generate a meaningful title
            title_response = openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "system", 
                        "content": "Generate a concise, descriptive title (max 50 characters) for the following note content. The title should capture the main topic or essence of the note."
                    },
                    {
                        "role": "user", 
                        "content": content
                    }
                ],
                max_tokens=20,
                temperature=0.3
            )
            title = title_response.choices[0].message.content.strip()
            
            # Ensure title doesn't exceed 50 characters
            if len(title) > 50:
                title = title[:47] + "..."
                
        except Exception as e:
            print(f"Error generating AI title, falling back to simple method: {e}")
            # Fallback to simple method if AI generation fails
            words = content.split()
            title = " ".join(words[:8])  # Take first 8 words
            if len(title) > 50:
                title = title[:47] + "..."
    
    # Generate embedding for the content
    try:
        embedding_response = openai_client.embeddings.create(
            model="text-embedding-3-small",
            input=content
        )
        embedding = embedding_response.data[0].embedding
    except Exception as e:
        print(f"Error generating embedding: {e}")
        raise ValueError("Failed to generate embedding for note content")
    
    # Create note object
    tz = pytz.timezone("Asia/Kuala_Lumpur")
    now = datetime.now(tz)
    
    note = {
        'id': str(uuid.uuid4()),  # Unique note ID
        'user_id': user_id,
        'title': title,
        'content': content,
        'embedding': embedding,
        'created_at': now
    }
    
    print("Note object:", {**note, 'embedding': f"[{len(embedding)} dimensions]"})  # Don't print full embedding
    
    # Insert note into MongoDB
    result = notes_collection.insert_one(note)
    
    print(f'Note created with ID: {result.inserted_id}')
    return {
        'id': note['id'],
        'title': note['title'],
        'content': note['content'],
        'created_at': note['created_at']
    }

def search_notes(user_id: str = None, query: str = None, k: int = 5) -> list:
    """Search notes using vector similarity for a user."""
    
    if user_id is None:
        raise ValueError("Missing user_id in search_notes() call!")
    
    if not query:
        raise ValueError("query is required for searching notes")
    
    print(f"=== SEARCH NOTES DEBUG ===")
    print(f"Searching notes for user_id: {user_id} (type: {type(user_id)})")
    print(f"Query: '{query}', k: {k}")
    
    # First, let's check what notes exist for this user
    try:
        total_notes = notes_collection.count_documents({"user_id": user_id})
        print(f"Total notes for user {user_id}: {total_notes}")
        
        # Show a sample of existing notes for debugging
        sample_notes = list(notes_collection.find(
            {"user_id": user_id},
            {"title": 1, "content": 1, "_id": 0}
        ).limit(3))
        print(f"Sample notes for user: {sample_notes}")
    except Exception as e:
        print(f"Error checking existing notes: {e}")
    
    # Generate embedding for the search query
    try:
        print(f"Generating embedding for query: '{query}'")
        embedding_response = openai_client.embeddings.create(
            model="text-embedding-3-small",
            input=query
        )
        query_embedding = embedding_response.data[0].embedding
        print(f"Generated embedding with {len(query_embedding)} dimensions")
    except Exception as e:
        print(f"Error generating query embedding: {e}")
        raise ValueError("Failed to generate embedding for search query")
    
    try:
        # Perform MongoDB Atlas vector search
        # IMPORTANT: To use vector search, you need to create a vector index in MongoDB Atlas:
        # 1. Go to your MongoDB Atlas cluster
        # 2. Navigate to Search > Create Search Index
        # 3. Choose "JSON Editor" and use this configuration:
        # {
        #   "fields": [
        #     {
        #       "path": "embedding",
        #       "type": "vector",
        #       "numDimensions": 1536,
        #       "similarity": "cosine"
        #     },
        #     {
        #       "path": "user_id",
        #       "type": "filter"
        #     }
        #   ]
        # }
        # 4. Name the index: "notes_vector_index"
        pipeline = [
            {
                "$vectorSearch": {
                    "index": "notes_vector_index",  # Vector index name in MongoDB Atlas
                    "path": "embedding",
                    "queryVector": query_embedding,
                    "numCandidates": k * 10,  # Search more candidates for better results
                    "limit": k,
                    "filter": {"user_id": user_id}  # Filter by user
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "id": 1,
                    "title": 1,
                    "content": 1,
                    "created_at": 1,
                    "score": {"$meta": "vectorSearchScore"}
                }
            }
        ]
        
        print(f"Executing vector search pipeline: {pipeline}")
        results = list(notes_collection.aggregate(pipeline))
        
        print(f"Vector search results: {len(results)} notes found")
        for i, result in enumerate(results):
            print(f"Result {i+1}: Title='{result.get('title', 'N/A')}', Score={result.get('score', 'N/A')}")
        
        return results
        
    except Exception as e:
        print(f"Vector search failed, falling back to text search: {e}")
        print(f"Error type: {type(e).__name__}")
        print(f"Error details: {str(e)}")
        
        # Fallback to text search if vector search fails
        fallback_query = {
            "user_id": user_id,
            "$or": [
                {"title": {"$regex": query, "$options": "i"}},
                {"content": {"$regex": query, "$options": "i"}}
            ]
        }
        print(f"Fallback query: {fallback_query}")
        
        fallback_results = list(notes_collection.find(
            fallback_query,
            {
                "_id": 0,
                "id": 1,
                "title": 1,
                "content": 1,
                "created_at": 1
            }
        ).limit(k))
        
        print(f"Fallback search found {len(fallback_results)} notes for user {user_id}")
        for i, result in enumerate(fallback_results):
            print(f"Fallback Result {i+1}: Title='{result.get('title', 'N/A')}'")
        
        return fallback_results

# Tool definitions for OpenAI function calling
create_note_tool = {
    "type": "function",
    "function": {
        "name": "create_note",
        "description": "Creates a note with title, content, and generates an embedding for search. If title is not provided, you must generate a title from the content.",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The content/body of the note"
                },
                "title": {
                    "type": "string",
                    "description": "Mandatory title for the note. If not provided, you must generate a title from the content"
                }
            },
            "required": ["content", "title"]
        }
    }
}

search_notes_tool = {
    "type": "function",
    "function": {
        "name": "search_notes",
        "description": "Search through user's notes using semantic similarity based on the query. Returns the most relevant notes.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to find relevant notes"
                },
                "k": {
                    "type": "integer",
                    "description": "Number of notes to return (default: 5, max: 10)"
                }
            },
            "required": ["query"]
        }
    }
}