from pymongo import AsyncMongoClient
import os
import asyncio
from datetime import datetime
from typing import List, Dict, Optional
from dotenv import load_dotenv

# Load environment variables first
load_dotenv(dotenv_path=".env.local", override=True)

MONGO_URI = os.getenv("MONGO_URI")
MEMORY_MESSAGE_LIMIT = int(os.getenv("MEMORY_MESSAGE_LIMIT", "30"))

client = AsyncMongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=5000,
    socketTimeoutMS=30000,
    connectTimeoutMS=10000,
    maxPoolSize=100,  # you can increase a bit for high concurrency
)

db_name = os.environ.get("DB_NAME")
db = client[db_name]
users_collection = db["users"]
oauth_states_collection = db["oauth_states"]
oauth_tokens_collection = db["oauth_tokens"]
conversation_history_collection = db["conversation_history"]
reminders_collection = db["reminders"]

# Test connection and create index asynchronously
async def init_mongodb():
    try:
        await client.admin.command('ping')
        print("✅ MongoDB connection established successfully")
        
        # Create index on user_id for efficient querying
        await conversation_history_collection.create_index("user_id")
        print("✅ Created index on user_id for conversation_history collection")
    except Exception as e:
        print(f"❌ MongoDB initialization failed: {e}")

# Initialize in the background - moved to FastAPI lifespan in main.py
# asyncio.create_task(init_mongodb())

async def get_all_users():
    """Get all users from the users collection"""
    import time
    from pymongo.errors import ExecutionTimeout, ServerSelectionTimeoutError
    
    start_time = time.time()
    
    try:
        print("[MONGO] Starting get_all_users query...")
        # Access the users collection directly
        users_collection = db["users"]
        
        # First, check connection is alive
        print("[MONGO] Checking MongoDB connection...")
        await client.admin.command('ping')
        print("[MONGO] MongoDB connection is healthy")
        
        print("[MONGO] Executing find({}) on users collection...")
        # Only fetch the fields we need: _id and phone_number
        projection = {"_id": 1, "phone_number": 1}
        cursor = await users_collection.find({}, projection, batch_size=100).max_time_ms(30000)  # 30 second timeout
        print("[MONGO] Using projection to fetch only _id and phone_number fields")
        
        print("[MONGO] Converting cursor to list...")
        users = []
        count = 0
        async for user in cursor:
            users.append(user)
            count += 1
            if count % 100 == 0:
                print(f"[MONGO] Processed {count} users so far...")
        
        elapsed = time.time() - start_time
        print(f"[MONGO] Fetched {len(users)} users in {elapsed:.2f} seconds")
        
        # Transform the data to include user_id as string version of _id
        print("[MONGO] Transforming user_id fields...")
        for idx, user in enumerate(users):
            user['user_id'] = str(user['_id'])
            if (idx + 1) % 100 == 0:
                print(f"[MONGO] Transformed {idx + 1} users...")
        
        total_elapsed = time.time() - start_time
        print(f"[MONGO] Completed get_all_users, returning {len(users)} users (total time: {total_elapsed:.2f}s)")
        return users if users else []
        
    except ExecutionTimeout as e:
        print(f"[MONGO] ❌ Query timeout after 30 seconds: {e}")
        raise Exception(f"MongoDB query timeout: {e}")
    except ServerSelectionTimeoutError as e:
        print(f"[MONGO] ❌ Cannot connect to MongoDB server: {e}")
        raise Exception(f"MongoDB connection failed: {e}")
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"[MONGO] ❌ Error in get_all_users after {elapsed:.2f}s: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        raise

async def get_conversation_history(user_id: str) -> List[Dict]:
    """
    Get conversation history for a user from MongoDB.

    Args:
        user_id: The user's ID

    Returns:
        List of message dictionaries with 'role' and 'content' keys
    """
    try:
        # Get from MongoDB
        doc = await conversation_history_collection.find_one({"user_id": user_id})
        if doc and "messages" in doc:
            print(f"📥 Retrieved {len(doc['messages'])} messages from MongoDB for user {user_id}")
            return doc["messages"]
        else:
            print(f"📭 No conversation history found in MongoDB for user {user_id}")
            return []
    except Exception as e:
        print(f"❌ Error retrieving conversation history for user {user_id}: {e}")
        return []

async def save_message_to_history(user_id: str, message: Dict) -> bool:
    """
    Save a message to conversation history with automatic 30-message limit enforcement.

    Args:
        user_id: The user's ID
        message: Message dict with 'role' and 'content' keys

    Returns:
        True if saved successfully, False otherwise
    """
    try:
        # Use $push with $slice to maintain message limit automatically
        result = await conversation_history_collection.update_one(
            {"user_id": user_id},
            {
                "$push": {
                    "messages": {
                        "$each": [message],
                        "$slice": -MEMORY_MESSAGE_LIMIT  # Keep only the latest N messages
                    }
                },
                "$set": {"updated_at": datetime.now()}
            },
            upsert=True
        )

        if result.upserted_id or result.modified_count > 0:
            print(f"💾 Saved message to MongoDB for user {user_id}")
            return True
        else:
            print(f"⚠️ Failed to save message to MongoDB for user {user_id}")
            return False

    except Exception as e:
        print(f"❌ Error saving message to MongoDB for user {user_id}: {e}")
        return False

async def migrate_memory_to_mongodb() -> int:
    """
    Migration function for future use if needed.
    Currently not used since we're using MongoDB as primary storage.

    Returns:
        Always returns 0 since no migration is needed
    """
    print("📭 No migration needed - using MongoDB as primary storage")
    return 0

async def clear_conversation_history(user_id: str) -> bool:
    """
    Clear conversation history for a specific user.
    
    Args:
        user_id: The user's ID
    
    Returns:
        True if successful, False otherwise
    """
    try:
        result = await conversation_history_collection.delete_one({"user_id": user_id})
        if result.deleted_count > 0:
            print(f"🗑️ Cleared conversation history for user {user_id}")
            return True
        else:
            print(f"📭 No conversation history found for user {user_id}")
            return False
    except Exception as e:
        print(f"❌ Failed to clear conversation history for user {user_id}: {e}")
        return False
