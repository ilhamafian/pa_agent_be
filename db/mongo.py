from pymongo import MongoClient
import os
from datetime import datetime
from typing import List, Dict, Optional
from dotenv import load_dotenv

# Load environment variables first
load_dotenv(dotenv_path=".env.local", override=True)

MONGO_URI = os.getenv("MONGO_URI")
MEMORY_MESSAGE_LIMIT = int(os.getenv("MEMORY_MESSAGE_LIMIT", "30"))

client = MongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=5000,  # 5 second timeout for server selection
    socketTimeoutMS=30000,  # 30 second timeout for socket operations
    connectTimeoutMS=10000,  # 10 second timeout for initial connection
    maxPoolSize=50  # Increase connection pool size for concurrent requests
)

db = client["oauth_db"]
users_collection = db["users"]
oauth_states_collection = db["oauth_states"]
oauth_tokens_collection = db["oauth_tokens"]
conversation_history_collection = db["conversation_history"]

# Test connection
try:
    client.admin.command('ping')
    print("✅ MongoDB connection established successfully")
except Exception as e:
    print(f"❌ MongoDB connection failed: {e}")

# Create index on user_id for efficient querying
# Using create_index is idempotent - it won't recreate if already exists
try:
    conversation_history_collection.create_index("user_id")
    print("✅ Created/verified index on user_id for conversation_history collection")
except Exception as e:
    print(f"⚠️ Index creation failed: {e}")

def get_all_users():
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
        client.admin.command('ping')
        print("[MONGO] MongoDB connection is healthy")
        
        print("[MONGO] Executing find({}) on users collection...")
        # Only fetch the fields we need: _id and phone_number
        projection = {"_id": 1, "phone_number": 1}
        cursor = users_collection.find({}, projection, batch_size=100).max_time_ms(30000)  # 30 second timeout
        print("[MONGO] Using projection to fetch only _id and phone_number fields")
        
        print("[MONGO] Converting cursor to list...")
        users = []
        count = 0
        for user in cursor:
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

def get_conversation_history(user_id: str, fallback_memory: Dict = None) -> List[Dict]:
    """
    Get conversation history for a user from MongoDB.
    Falls back to in-memory storage if MongoDB is unavailable.
    
    Args:
        user_id: The user's ID
        fallback_memory: In-memory user_memory dict for fallback
    
    Returns:
        List of message dictionaries with 'role' and 'content' keys
    """
    try:
        # Try MongoDB first
        doc = conversation_history_collection.find_one({"user_id": user_id})
        if doc and "messages" in doc:
            print(f"📥 Retrieved {len(doc['messages'])} messages from MongoDB for user {user_id}")
            return doc["messages"]
        else:
            print(f"📭 No conversation history found in MongoDB for user {user_id}")
            return []
    except Exception as e:
        print(f"⚠️ MongoDB unavailable, falling back to in-memory storage: {e}")
        # Fallback to in-memory storage
        if fallback_memory and user_id in fallback_memory:
            return fallback_memory[user_id]
        return []

def save_message_to_history(user_id: str, message: Dict, fallback_memory: Dict = None) -> bool:
    """
    Save a message to conversation history with automatic 30-message limit enforcement.
    Falls back to in-memory storage if MongoDB is unavailable.
    
    Args:
        user_id: The user's ID
        message: Message dict with 'role' and 'content' keys
        fallback_memory: In-memory user_memory dict for fallback
    
    Returns:
        True if saved to MongoDB, False if fell back to memory
    """
    try:
        # Use $push with $slice to maintain message limit automatically
        result = conversation_history_collection.update_one(
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
            # Fall back to in-memory
            if fallback_memory is not None:
                if user_id not in fallback_memory:
                    fallback_memory[user_id] = []
                fallback_memory[user_id].append(message)
                # Manually enforce limit for in-memory storage
                if len(fallback_memory[user_id]) > MEMORY_MESSAGE_LIMIT:
                    fallback_memory[user_id] = fallback_memory[user_id][-MEMORY_MESSAGE_LIMIT:]
            return False
            
    except Exception as e:
        print(f"⚠️ MongoDB error, saving to in-memory storage: {e}")
        # Fall back to in-memory storage
        if fallback_memory is not None:
            if user_id not in fallback_memory:
                fallback_memory[user_id] = []
            fallback_memory[user_id].append(message)
            # Manually enforce limit for in-memory storage
            if len(fallback_memory[user_id]) > MEMORY_MESSAGE_LIMIT:
                fallback_memory[user_id] = fallback_memory[user_id][-MEMORY_MESSAGE_LIMIT:]
        return False

def migrate_memory_to_mongodb(user_memory: Dict) -> int:
    """
    Migrate existing in-memory conversation data to MongoDB.
    
    Args:
        user_memory: Current in-memory user_memory dict
    
    Returns:
        Number of users migrated
    """
    if not user_memory:
        print("📭 No in-memory conversations to migrate")
        return 0
    
    migrated_count = 0
    
    for user_id, messages in user_memory.items():
        try:
            # Apply message limit during migration
            limited_messages = messages[-MEMORY_MESSAGE_LIMIT:] if len(messages) > MEMORY_MESSAGE_LIMIT else messages
            
            result = conversation_history_collection.update_one(
                {"user_id": user_id},
                {
                    "$set": {
                        "user_id": user_id,
                        "messages": limited_messages,
                        "updated_at": datetime.now()
                    }
                },
                upsert=True
            )
            
            if result.upserted_id or result.modified_count > 0:
                print(f"🔄 Migrated {len(limited_messages)} messages for user {user_id}")
                migrated_count += 1
            
        except Exception as e:
            print(f"❌ Failed to migrate user {user_id}: {e}")
    
    print(f"✅ Migration complete: {migrated_count} users migrated to MongoDB")
    return migrated_count

def clear_conversation_history(user_id: str) -> bool:
    """
    Clear conversation history for a specific user.
    
    Args:
        user_id: The user's ID
    
    Returns:
        True if successful, False otherwise
    """
    try:
        result = conversation_history_collection.delete_one({"user_id": user_id})
        if result.deleted_count > 0:
            print(f"🗑️ Cleared conversation history for user {user_id}")
            return True
        else:
            print(f"📭 No conversation history found for user {user_id}")
            return False
    except Exception as e:
        print(f"❌ Failed to clear conversation history for user {user_id}: {e}")
        return False
