

from datetime import datetime, timedelta
import json
from typing import List, Dict
from zoneinfo import ZoneInfo
from openai import OpenAI
import os
from dotenv import load_dotenv
from db.mongo import get_conversation_history, save_message_to_history, conversation_history_collection
from cachetools import TTLCache
import asyncio

# Internal Imports
from tools.calendar import (
    create_event_tool,
    get_events_tool,
    update_event_tool,
    delete_event_tool,
    create_event,
    get_events,
    update_event,
    delete_event,
    AuthRequiredError
)
from tools.reminder import (
    create_custom_reminder_tool,
    list_reminders_tool,
    create_custom_reminder,
    list_reminders
)
from tools.task import (
    create_task_tool,
    get_tasks_tool,
    update_task_status_tool,
    create_task,
    get_tasks,
    update_task_status
)
from tools.notes import (
    create_note_tool,
    search_notes_tool,
    retrieve_note_tool,
    create_note,
    search_notes,
    retrieve_note
)
from utils.utils import clean_unicode, encrypt_phone, get_auth_url, hash_data, send_whatsapp_message
from db.mongo import users_collection

load_dotenv(dotenv_path=".env.local", override=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
APP_URL = os.getenv("APP_URL")

# Conversation history cache - configurable via environment variables
CONVERSATION_CACHE_SIZE = int(os.getenv("CONVERSATION_CACHE_SIZE", "5000"))
CONVERSATION_CACHE_TTL = int(os.getenv("CONVERSATION_CACHE_TTL", "300"))  # 5 minutes default
USER_LOCKS_CACHE_SIZE = int(os.getenv("USER_LOCKS_CACHE_SIZE", "10000"))
USER_LOCKS_CACHE_TTL = int(os.getenv("USER_LOCKS_CACHE_TTL", "600"))  # 10 minutes default

conversation_cache = TTLCache(maxsize=CONVERSATION_CACHE_SIZE, ttl=CONVERSATION_CACHE_TTL)
user_locks = TTLCache(maxsize=USER_LOCKS_CACHE_SIZE, ttl=USER_LOCKS_CACHE_TTL)

print(f"🔧 Cache initialized: Conversation cache (size={CONVERSATION_CACHE_SIZE}, ttl={CONVERSATION_CACHE_TTL}s), User locks (size={USER_LOCKS_CACHE_SIZE}, ttl={USER_LOCKS_CACHE_TTL}s)")
cache_lock = asyncio.Lock()  # Global lock for lock management

async def get_cached_conversation_history(user_id: str) -> List[Dict]:
    """
    Get conversation history with caching to reduce database load.
    Thread-safe and async-safe implementation.

    Args:
        user_id: The user's ID

    Returns:
        List of message dictionaries with 'role' and 'content' keys
    """
    # Try to get from cache first (fast path)
    if user_id in conversation_cache:
        print(f"📋 Cache hit for user {user_id}")
        return conversation_cache[user_id]

    # Cache miss - need to load from database with proper locking
    # Use per-user lock to prevent cache stampede
    async with cache_lock:
        if user_id not in user_locks:
            user_locks[user_id] = asyncio.Lock()

    user_lock = user_locks[user_id]

    async with user_lock:
        # Double-check: another coroutine might have populated the cache while we waited for the lock
        if user_id in conversation_cache:
            print(f"📋 Cache hit for user {user_id} (after lock)")
            return conversation_cache[user_id]

        # Cache miss - load from database
        print(f"💾 Cache miss for user {user_id} - loading from MongoDB")
        history = await get_conversation_history(user_id)

        # Store in cache for future requests (atomic operation)
        conversation_cache[user_id] = history

        return history

async def clear_user_cache(user_id: str) -> bool:
    """
    Clear cached conversation history for a specific user.
    Thread-safe implementation.

    Args:
        user_id: The user's ID

    Returns:
        True if cache entry existed and was cleared, False otherwise
    """
    try:
        # Clear from conversation cache
        if user_id in conversation_cache:
            del conversation_cache[user_id]

        # Clear from user locks cache
        if user_id in user_locks:
            del user_locks[user_id]

        print(f"🗑️ Cleared cache for user {user_id}")
        return True
    except Exception as e:
        print(f"❌ Error clearing cache for user {user_id}: {e}")
        return False

def get_cache_stats() -> Dict:
    """
    Get cache statistics for monitoring.

    Returns:
        Dictionary with cache statistics including configuration
    """
    return {
        "conversation_cache": {
            "maxsize": conversation_cache.maxsize,
            "currsize": conversation_cache.currsize,
            "ttl": conversation_cache.ttl,
            "hit_rate": "N/A (tracking not implemented)",  # Future enhancement
        },
        "user_locks": {
            "maxsize": user_locks.maxsize,
            "currsize": user_locks.currsize,
            "ttl": user_locks.ttl,
        },
        "configuration": {
            "conversation_cache_size": CONVERSATION_CACHE_SIZE,
            "conversation_cache_ttl_seconds": CONVERSATION_CACHE_TTL,
            "user_locks_cache_size": USER_LOCKS_CACHE_SIZE,
            "user_locks_cache_ttl_seconds": USER_LOCKS_CACHE_TTL,
        },
        "memory_usage": {
            "conversation_cache_entries": conversation_cache.currsize,
            "user_locks_entries": user_locks.currsize,
            "estimated_memory_mb": "N/A (calculated on demand)",  # Future enhancement
        }
    }

async def warm_cache_for_active_users(limit: int = 100) -> Dict:
    """
    Proactively warm the cache with conversation history for recently active users.
    This improves performance by pre-loading frequently accessed data.

    Args:
        limit: Maximum number of active users to warm cache for

    Returns:
        Dictionary with warming statistics
    """
    try:
        print(f"🔥 Starting cache warming for top {limit} active users...")

        # Find users with recent conversation activity, sorted by most recent
        pipeline = [
            {"$match": {"messages": {"$exists": True, "$ne": []}}},
            {"$addFields": {"last_message_time": {"$max": "$messages.created_at"}}},
            {"$sort": {"last_message_time": -1}},
            {"$limit": limit}
        ]

        cursor = conversation_history_collection.aggregate(pipeline)
        active_users = await cursor.to_list(length=None)

        warmed_count = 0
        errors = []

        for user_doc in active_users:
            user_id = user_doc["user_id"]
            try:
                # Check if already in cache to avoid unnecessary work
                if user_id not in conversation_cache:
                    # Load conversation history into cache
                    history = user_doc.get("messages", [])
                    conversation_cache[user_id] = history
                    warmed_count += 1
                    print(f"🔥 Warmed cache for user {user_id} ({len(history)} messages)")
                else:
                    print(f"⏭️ User {user_id} already in cache, skipping")
            except Exception as e:
                error_msg = f"Error warming cache for user {user_id}: {e}"
                print(f"❌ {error_msg}")
                errors.append(error_msg)

        print(f"✅ Cache warming completed: {warmed_count}/{len(active_users)} users warmed")

        return {
            "warmed_users": warmed_count,
            "total_active_users": len(active_users),
            "errors": errors,
            "cache_size_after_warming": conversation_cache.currsize
        }

    except Exception as e:
        error_msg = f"Cache warming failed: {e}"
        print(f"❌ {error_msg}")
        return {
            "warmed_users": 0,
            "total_active_users": 0,
            "errors": [error_msg],
            "cache_size_after_warming": conversation_cache.currsize
        }

async def schedule_cache_warming(interval_minutes: int = 15) -> None:
    """
    Schedule periodic cache warming for active users.
    This runs in the background to maintain cache performance.

    Args:
        interval_minutes: How often to run cache warming (in minutes)
    """
    while True:
        try:
            await asyncio.sleep(interval_minutes * 60)  # Convert to seconds
            await warm_cache_for_active_users()
        except asyncio.CancelledError:
            print("🔥 Cache warming scheduler cancelled")
            break
        except Exception as e:
            print(f"❌ Error in cache warming scheduler: {e}")
            # Continue running even if there's an error

tools = [
    create_event_tool,
    get_events_tool,
    update_event_tool,
    delete_event_tool,
    create_custom_reminder_tool,
    list_reminders_tool,
    create_task_tool,
    get_tasks_tool,
    update_task_status_tool,
    create_note_tool,
    search_notes_tool,
    retrieve_note_tool,
]

def _flatten_response_tools(tools_list):
    """Flatten legacy tool schema {"type":"function","function":{...}}
    into Responses API schema {"type":"function","name":..., "parameters":...}.
    Safe to call on already-flat tools.
    """
    flattened = []
    for t in tools_list:
        if isinstance(t, dict) and t.get("type") == "function" and "function" in t:
            f = t.get("function") or {}
            flattened.append({
                "type": "function",
                "name": f.get("name"),
                "description": f.get("description", ""),
                "parameters": f.get("parameters", {"type": "object", "properties": {}})
            })
        else:
            flattened.append(t)
    return flattened

redirect_uri = f"{APP_URL}/auth/google_callback"

# Load the system prompt template once at module level
with open("ai/prompts/system_prompt.txt", "r", encoding="utf-8") as f:
    system_prompt_template = f.read()

async def assistant_response(sender: str, text: str, playground_mode: bool = False):
    client = OpenAI(api_key=OPENAI_API_KEY)

    try:
        phone_number = sender
        hashed_number = hash_data(sender)
        user = await users_collection.find_one({"hashed_phone_number": hashed_number})
        
        if not user:
            print(f"❌ UNEXPECTED: User not found in assistant_response for sender: {sender}")
            print(f"❌ Hashed number: {hashed_number}")
            print(f"❌ This should not happen as main.py already checked user existence")
            
            # Try one more time to rule out transient database issues
            user_retry = await users_collection.find_one({"hashed_phone_number": hashed_number})
            if user_retry:
                print(f"✅ RETRY SUCCESS: User found on second attempt")
                user = user_retry
            else:
                print(f"❌ RETRY FAILED: User still not found - potential database issue")
                await send_whatsapp_message(phone_number, "❌ Temporary issue. Please try again in a moment.")
                return {"ok": False, "error": "User not found after retry"}
        
        # Extract user metadata after confirming user exists
        about_yourself = user["metadata"]["about_yourself"]
        profession = user["metadata"]["profession"]
        language = user["language"]
        
        user_id = str(user["_id"])
        user_input = text

        print(f"Processing message from {user_id}: {user_input}")

        # Calculate current date/time fresh for each request
        now = datetime.now(ZoneInfo("Asia/Kuala_Lumpur"))
        today_str = now.strftime("%Y-%m-%d")
        tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        system_prompt = system_prompt_template.format(today=today_str, tomorrow=tomorrow_str, about_yourself=about_yourself, profession=profession, language=language)

        # Get conversation history from cache (falls back to MongoDB)
        history = await get_cached_conversation_history(user_id)

        # Add user message to history
        user_message = {"role": "user", "content": user_input}
        await save_message_to_history(user_id, user_message)

        # Update cache with new message (maintain limit) - thread-safe
        async with cache_lock:
            if user_id in conversation_cache:
                conversation_cache[user_id].append(user_message)
                # Keep only the latest messages (same limit as MongoDB)
                from db.mongo import MEMORY_MESSAGE_LIMIT
                if len(conversation_cache[user_id]) > MEMORY_MESSAGE_LIMIT:
                    conversation_cache[user_id] = conversation_cache[user_id][-MEMORY_MESSAGE_LIMIT:]

        history.append(user_message)

        # Prepare chat messages with system prompt + recent history (last 10 messages)
        chat_messages = [{"role": "system", "content": system_prompt}] + history[-10:]

        response = client.responses.create(
            model="gpt-4o-mini",
            input=chat_messages,
            tools=_flatten_response_tools(tools),
            tool_choice="auto"
        )

        # Extract function tool calls from Responses API output
        tool_calls = []
        try:
            for item in getattr(response, "output", []):
                if getattr(item, "type", None) == "function_call":
                    tool_calls.append(item)
        except Exception:
            tool_calls = []

        if tool_calls:
            for tool_call in tool_calls:
                function_name = tool_call.name
                args = json.loads(tool_call.arguments)

                try:
                    if function_name == "create_event":
                        result = create_event(
                            title=args["title"],
                            date=args["date"],
                            time=args.get("time"),
                            end_time=args.get("end_time"),
                            description=args.get("description"),
                            user_id=user_id
                        )
                        # Determine time display based on what was provided
                        if args.get("time") and args.get("end_time"):
                            time_display = f"Time: {args['time']} - {args['end_time']}\n"
                        elif args.get("time"):
                            # Calculate end time (1 hour after start)
                            start = datetime.strptime(args['time'], "%H:%M")
                            end = start + timedelta(hours=1)
                            time_display = f"Time: {args['time']} - {end.strftime('%H:%M')} (1 hour)\n"
                        else:
                            time_display = "Time: All-day\n"
                        
                        reply = (
                            f"📅 Calendar Event Created\n\n"
                            f"Title: {args['title']}\n"
                            f"Date: {args['date']}\n"
                            f"{time_display}"
                        )

                    elif function_name == "get_events":
                        reply = get_events(natural_range=args["natural_range"], user_id=user_id)

                    elif function_name == "create_custom_reminder":
                        result = create_custom_reminder(
                            message=args["message"],
                            remind_in=args["remind_in"],
                            user_id=user_id,
                            phone_number=phone_number
                        )
                        reply = result["message"]

                    elif function_name == "list_reminders":
                        result = list_reminders(user_id=user_id)
                        reply = result["message"]

                    elif function_name == "create_task":
                        # Get the priority, defaulting to medium
                        task_priority = args.get("priority", "medium")
                        result = create_task(
                            title=args["title"],
                            priority=task_priority,
                            description=args.get("description"),
                            user_id=user_id
                        )
                        priority_emoji = "🔴" if task_priority == "high" else "🟡" if task_priority == "medium" else "🟢"
                        reply = (
                            f"✅ Task Created\n\n"
                            f"Title: {args['title']}\n"
                            f"Priority: {priority_emoji} {task_priority.title()}\n"
                            f"Status: Pending"
                        )

                    elif function_name == "get_tasks":
                        tasks = get_tasks(
                            user_id=user_id,
                            status=args.get("status"),
                            priority=args.get("priority")
                        )
                        
                        if not tasks:
                            reply = "📝 You have no tasks at the moment."
                        else:
                            # Group tasks by status
                            pending_tasks = [t for t in tasks if t.get("status") == "pending"]
                            in_progress_tasks = [t for t in tasks if t.get("status") == "in_progress"]
                            completed_tasks = [t for t in tasks if t.get("status") == "completed"]

                            reply_lines = [""]

                            sections = [
                                ("📋 Pending Tasks", pending_tasks),
                                ("⚙️ In Progress Tasks", in_progress_tasks),
                                ("✅ Completed Tasks", completed_tasks),
                            ]

                            for section_title, section_tasks in sections:
                                if not section_tasks:
                                    continue

                                reply_lines.append(section_title)
                                reply_lines.append("─" * len(section_title))

                                for idx, task in enumerate(section_tasks, start=1):
                                    # Priority emoji
                                    priority = task.get("priority", "").lower()
                                    priority_emoji = {
                                        "high": "🔴 High",
                                        "medium": "🟡 Medium",
                                        "low": "🟢 Low",
                                    }.get(priority, "⚪ Unknown")

                                    reply_lines.append(f"{idx}. {task['title']}")
                                    reply_lines.append(f"    Priority: {priority_emoji}")

                                    if task.get("description"):
                                        reply_lines.append(f"    Description: {task['description']}")

                                    reply_lines.append("")  # Blank line after each task

                                reply_lines.append("")  # Blank line between sections

                            reply = "\n".join(reply_lines).strip()

                    elif function_name == "update_task_status":
                        result = update_task_status(
                            task_title=args["task_title"],
                            status=args["status"],
                            user_id=user_id
                        )
                        if result:
                            reply = (
                                f"✅ Task Updated\n\n"
                                f"Title: {result['title']}\n"
                                f"Status:   {args['status'].replace('_', ' ').title()}"
                            )
                        else:
                            reply = "❌ Task not found or update failed."

                    elif function_name == "update_event":
                        result = update_event(
                            user_id=user_id,
                            original_title=args["original_title"],
                            new_title=args.get("new_title"),
                            new_date=args.get("new_date"),
                            new_start_time=args.get("new_start_time"),
                            new_end_time=args.get("new_end_time"),
                            new_description=args.get("new_description")
                        )
                        reply = result

                    elif function_name == "delete_event":
                        result = delete_event(
                            user_id=user_id,
                            title=args["title"]
                        )
                        reply = result

                    elif function_name == "create_note":
                        result = create_note(
                            user_id=user_id,
                            content=args["content"],
                            title=args.get("title")
                        )
                        reply = (
                            f"📝 Note Created\n\n"
                            f"Title: {result['title']}\n"
                            f"Content: {result['content'][:100]}{'...' if len(result['content']) > 100 else ''}\n"
                            f"Created: {result['created_at'].strftime('%Y-%m-%d %H:%M')}"
                        )

                    elif function_name == "search_notes":
                        notes = search_notes(
                            user_id=user_id,
                            query=args["query"],
                            k=args.get("k", 5)
                        )
                        
                        if not notes:
                            reply = f"🔍 No notes found matching '{args['query']}'"
                        else:
                            reply_lines = [f"🔍 Found {len(notes)} note(s) for '{args['query']}':\n"]
                            
                            for idx, note in enumerate(notes, 1):
                                # Format created_at if it exists
                                created_str = ""
                                if note.get("created_at"):
                                    try:
                                        if hasattr(note["created_at"], "strftime"):
                                            created_str = f" ({note['created_at'].strftime('%Y-%m-%d')})"
                                        else:
                                            created_str = f" ({str(note['created_at'])[:10]})"
                                    except:
                                        pass
                                
                                reply_lines.append(f"{idx}. {note['title']}{created_str}")
                                
                                # Show score if available (from vector search)
                                if note.get("score"):
                                    reply_lines.append(f"   Relevance: {note['score']:.2f}")
                                
                                # Truncate content for preview
                                content_preview = note['content'][:150]
                                if len(note['content']) > 150:
                                    content_preview += "..."
                                reply_lines.append(f"   {content_preview}")
                                reply_lines.append("")  # Blank line between notes
                            
                            if len(notes) > 0:
                                reply_lines.append("Please type the number (1, 2, or 3) to view the full content of a note.")
                            
                            reply = "\n".join(reply_lines).strip()

                    elif function_name == "retrieve_note":
                        try:
                            selected_note = retrieve_note(
                                user_id=user_id,
                                selection=args["selection"]
                            )
                            
                            # Format created_at if it exists
                            created_str = ""
                            if selected_note.get("created_at"):
                                try:
                                    if hasattr(selected_note["created_at"], "strftime"):
                                        created_str = f"\nCreated: {selected_note['created_at'].strftime('%Y-%m-%d %H:%M')}"
                                    else:
                                        created_str = f"\nCreated: {str(selected_note['created_at'])}"
                                except:
                                    pass
                            
                            reply = f"📄 {selected_note['title']}{created_str}\n\n{selected_note['content']}"
                            
                        except ValueError as e:
                            error_msg = str(e)
                            if "Invalid selection" in error_msg:
                                reply = "❌ Invalid selection. Please choose a number between 1 and 3 from the search results."
                            elif "No previous search results" in error_msg:
                                reply = "❌ No previous search results found. Please search for notes first before selecting one."
                            else:
                                reply = f"❌ {error_msg}"

                    else:
                        reply = "❌ Unknown function requested."

                except AuthRequiredError:
                    auth_url = get_auth_url(user_id)
                    reply = (
                        f"🔐 Oops! It seems like you haven't given me access to your calendar yet. "
                        f"Please authorize access through this link:\n{auth_url}\n\n"
                        f"Alternatively, you can manage your external app integration through your dashboard:\n"
                        f"https://lofy-assistant.com/dashboard/integration"
                    )

                safe_reply = clean_unicode(reply)
                
                if not playground_mode:
                    await send_whatsapp_message(phone_number, safe_reply)
                
                # Save assistant message to history
                assistant_message = {"role": "assistant", "content": reply}
                await save_message_to_history(user_id, assistant_message)

                # Update cache with assistant message - thread-safe
                async with cache_lock:
                    if user_id in conversation_cache:
                        conversation_cache[user_id].append(assistant_message)
                        # Keep only the latest messages (same limit as MongoDB)
                        from db.mongo import MEMORY_MESSAGE_LIMIT
                        if len(conversation_cache[user_id]) > MEMORY_MESSAGE_LIMIT:
                            conversation_cache[user_id] = conversation_cache[user_id][-MEMORY_MESSAGE_LIMIT:]
                
                if playground_mode:
                    return {"ok": True, "message": safe_reply}
                return {"ok": True}

        # If no tool calls, send the assistant's text output
        output_text = getattr(response, "output_text", "") or ""
        if output_text:
            reply = output_text.strip()
            safe_reply = clean_unicode(reply)
            
            if not playground_mode:
                await send_whatsapp_message(phone_number, safe_reply)
            
            # Save assistant message to history
            assistant_message = {"role": "assistant", "content": reply}
            await save_message_to_history(user_id, assistant_message)

            # Update cache with assistant message - thread-safe
            async with cache_lock:
                if user_id in conversation_cache:
                    conversation_cache[user_id].append(assistant_message)
                    # Keep only the latest messages (same limit as MongoDB)
                    from db.mongo import MEMORY_MESSAGE_LIMIT
                    if len(conversation_cache[user_id]) > MEMORY_MESSAGE_LIMIT:
                        conversation_cache[user_id] = conversation_cache[user_id][-MEMORY_MESSAGE_LIMIT:]

            if playground_mode:
                return {"ok": True, "message": safe_reply}

        return {"ok": True}

    except Exception as e:
        print(f"Error in assistant_response: {e}")
        return {"ok": False, "error": str(e)}