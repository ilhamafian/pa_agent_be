

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
    create_event,
    get_events,
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
from utils.utils import clean_unicode, get_auth_url, send_whatsapp_message

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
APP_URL = os.getenv("APP_URL")

user_memory = {}
tools = [create_event_tool, get_events_tool, create_event_reminder_tool, create_custom_reminder_tool, list_reminders_tool, create_task_tool, get_tasks_tool, update_task_status_tool]

now = datetime.now(ZoneInfo("Asia/Kuala_Lumpur"))
today_str = now.strftime("%Y-%m-%d")
tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")
redirect_uri = f"{APP_URL}/auth/google_callback"

with open("system_prompt.txt", "r", encoding="utf-8") as f:
    raw_prompt = f.read()
    system_prompt = raw_prompt.format(today=today_str, tomorrow=tomorrow_str)

async def assistant_response(sender: str, text: str):
    client = OpenAI(api_key=OPENAI_API_KEY)

    try:
        user_id = sender
        user_input = text

        print(f"Processing message from {user_id}: {user_input}")

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
                        time_display = (
                            f"Time: {args['time']} - {args['end_time']}\n"
                            if args.get("time") and args.get("end_time")
                            else "Time: All-day\n"
                        )
                        reply = (
                            f"📅 Calendar Event Created\n\n"
                            f"Title: {args['title']}\n"
                            f"Date: {args['date']}\n"
                            f"{time_display}"
                            f"Link: {result.get('htmlLink', 'Link unavailable')}"
                        )

                    elif function_name == "get_events":
                        reply = get_events(natural_range=args["natural_range"], user_id=user_id)

                    elif function_name == "create_event_reminder":
                        result = create_event_reminder(
                            event_title=args["event_title"],
                            minutes_before=args.get("minutes_before", 30),
                            event_date=args.get("event_date"),
                            event_time=args.get("event_time"),
                            user_id=user_id
                        )
                        reply = result["message"]

                    elif function_name == "create_custom_reminder":
                        result = create_custom_reminder(
                            message=args["message"],
                            remind_in=args["remind_in"],
                            user_id=user_id
                        )
                        reply = result["message"]

                    elif function_name == "list_reminders":
                        result = list_reminders(user_id=user_id)
                        reply = result["message"]

                    elif function_name == "create_task":
                        result = create_task(
                            title=args["title"],
                            priority=args.get("priority", "medium"),
                            description=args.get("description"),
                            user_id=user_id
                        )
                        priority_emoji = "🔴" if args.get("priority") == "high" else "🟡" if args.get("priority") == "medium" else "🟢"
                        reply = (
                            f"✅ Task Created\n\n"
                            f"Title: {args['title']}\n"
                            f"Priority: {priority_emoji} {args.get('priority', 'medium').title()}\n"
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
                            reply_lines = ["🗂️ *Your Tasks:*"]
                            
                            for idx, task in enumerate(tasks, start=1):
                                # Clean status
                                status_text = task["status"].replace("_", " ").title()
                                
                                # Priority emoji
                                priority = task.get("priority", "").lower()
                                priority_emoji = {
                                    "high": "🔴 High",
                                    "medium": "🟡 Medium",
                                    "low": "🟢 Low"
                                }.get(priority, "⚪ Unknown")
                                
                                # Task entry
                                reply_lines.append(f"*{idx}. {task['title']}*")
                                reply_lines.append(f"   📌 Status: _{status_text}_")
                                reply_lines.append(f"   🎯 Priority: {priority_emoji}")
                                
                                if task.get("description"):
                                    reply_lines.append(f"   📄 {task['description']}")
                                
                                reply_lines.append("")  # Add blank line for spacing
                            
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

                    else:
                        reply = "❌ Unknown function requested."

                except AuthRequiredError:
                    auth_url = get_auth_url(user_id)
                    reply = (
                        f"🔐 Oops! It seems like you haven't given me access to your calendar yet. "
                        f"Please authorize access through this link:\n{auth_url}\n\n"
                        f"Alternatively, you can manage your external application integration through your dashboard:\n"
                        f"https://lofy-assistant.vercel.app/dashboard/integration"
                    )

                safe_reply = clean_unicode(reply)
                await send_whatsapp_message(user_id, safe_reply)
                
                # Save assistant message to history
                assistant_message = {"role": "assistant", "content": reply}
                save_message_to_history(user_id, assistant_message, user_memory)
                
                return {"ok": True}

        if ai_message.content:
            reply = ai_message.content.strip()
            safe_reply = clean_unicode(reply)
            await send_whatsapp_message(user_id, safe_reply)
            
            # Save assistant message to history
            assistant_message = {"role": "assistant", "content": reply}
            save_message_to_history(user_id, assistant_message, user_memory)

        return {"ok": True}

    except Exception as e:
        print(f"Error in assistant_response: {e}")
        return {"ok": False, "error": str(e)}