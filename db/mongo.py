from pymongo import MongoClient
import os
from datetime import datetime
from typing import List, Dict, Optional

MONGO_URI = os.getenv("MONGO_URI")
MEMORY_MESSAGE_LIMIT = int(os.getenv("MEMORY_MESSAGE_LIMIT", "30"))

client = MongoClient(MONGO_URI)

db = client["oauth_db"]
oauth_states_collection = db["oauth_states"]
oauth_tokens_collection = db["oauth_tokens"]
conversation_history_collection = db["conversation_history"]

# Create index on user_id for efficient querying
try:
    conversation_history_collection.create_index("user_id")
    print("✅ Created index on user_id for conversation_history collection")
except Exception as e:
    print(f"⚠️ Index creation failed (might already exist): {e}")

def get_all_users():
    users = list(oauth_tokens_collection.find({}))
    return users if users else []

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
