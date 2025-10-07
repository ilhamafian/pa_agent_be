

from datetime import datetime, timedelta
import json
from zoneinfo import ZoneInfo
from openai import OpenAI
import os
from dotenv import load_dotenv
from db.mongo import get_conversation_history, save_message_to_history

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
    create_event_reminder_tool,
    create_custom_reminder_tool,
    list_reminders_tool,
    create_event_reminder,
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
from db.mongo import client

db = client["oauth_db"]
users_collection = db["users"]

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
APP_URL = os.getenv("APP_URL")

user_memory = {}
tools = [
    create_event_tool,
    get_events_tool,
    update_event_tool,
    delete_event_tool,
    create_event_reminder_tool,
    create_custom_reminder_tool,
    list_reminders_tool,
    create_task_tool,
    get_tasks_tool,
    update_task_status_tool,
    create_note_tool,
    search_notes_tool,
    retrieve_note_tool,
]

redirect_uri = f"{APP_URL}/auth/google_callback"

# Load the system prompt template once at module level
with open("system_prompt.txt", "r", encoding="utf-8") as f:
    system_prompt_template = f.read()

async def assistant_response(sender: str, text: str):
    client = OpenAI(api_key=OPENAI_API_KEY)

    try:
        phone_number = sender
        hashed_number = hash_data(sender)
        user = users_collection.find_one({"hashed_phone_number": hashed_number})
        
        if not user:
            print(f"❌ UNEXPECTED: User not found in assistant_response for sender: {sender}")
            print(f"❌ Hashed number: {hashed_number}")
            print(f"❌ This should not happen as main.py already checked user existence")
            
            # Try one more time to rule out transient database issues
            user_retry = users_collection.find_one({"hashed_phone_number": hashed_number})
            if user_retry:
                print(f"✅ RETRY SUCCESS: User found on second attempt")
                user = user_retry
            else:
                print(f"❌ RETRY FAILED: User still not found - potential database issue")
                await send_whatsapp_message(phone_number, "❌ Temporary issue. Please try again in a moment.")
                return {"ok": False, "error": "User not found after retry"}
        
        user_id = str(user["_id"])
        user_input = text

        print(f"Processing message from {user_id}: {user_input}")

        # Calculate current date/time fresh for each request
        now = datetime.now(ZoneInfo("Asia/Kuala_Lumpur"))
        today_str = now.strftime("%Y-%m-%d")
        tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        system_prompt = system_prompt_template.format(today=today_str, tomorrow=tomorrow_str)

        # Get conversation history from MongoDB (with fallback to in-memory)
        history = get_conversation_history(user_id, user_memory)
        
        # Add user message to history
        user_message = {"role": "user", "content": user_input}
        save_message_to_history(user_id, user_message, user_memory)
        history.append(user_message)

        # Prepare chat messages with system prompt + recent history (last 10 messages)
        chat_messages = [{"role": "system", "content": system_prompt}] + history[-10:]

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=chat_messages,
            tools=tools,
            tool_choice="auto"
        )

        ai_message = response.choices[0].message

        if ai_message.tool_calls:
            for tool_call in ai_message.tool_calls:
                function_name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)

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
                            from datetime import datetime, timedelta
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

                    elif function_name == "create_event_reminder":
                        result = create_event_reminder(
                            event_title=args["event_title"],
                            minutes_before=args.get("minutes_before", 30),
                            event_date=args.get("event_date"),
                            event_time=args.get("event_time"),
                            user_id=user_id,
                            phone_number=phone_number
                        )
                        reply = result["message"]

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
                await send_whatsapp_message(phone_number, safe_reply)
                
                # Save assistant message to history
                assistant_message = {"role": "assistant", "content": reply}
                save_message_to_history(user_id, assistant_message, user_memory)
                
                return {"ok": True}

        if ai_message.content:
            reply = ai_message.content.strip()
            safe_reply = clean_unicode(reply)
            await send_whatsapp_message(phone_number, safe_reply)
            
            # Save assistant message to history
            assistant_message = {"role": "assistant", "content": reply}
            save_message_to_history(user_id, assistant_message, user_memory)

        return {"ok": True}

    except Exception as e:
        print(f"Error in assistant_response: {e}")
        return {"ok": False, "error": str(e)}