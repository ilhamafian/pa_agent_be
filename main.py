import os
import json
import uvicorn
from dotenv import load_dotenv
from openai import OpenAI
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from google_auth_oauthlib.flow import Flow

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
from tools.scheduler import start_scheduler
from utils.utils import clean_unicode, send_whatsapp_message, get_auth_url
from db.mongo import (
    oauth_states_collection, 
    oauth_tokens_collection,
    get_conversation_history,
    save_message_to_history,
    migrate_memory_to_mongodb
)

# === Setup ===
load_dotenv()

SCOPES = json.loads(os.getenv("SCOPES", "[]"))
TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
APP_URL = os.getenv("APP_URL")

app = FastAPI()

# === Middleware ===
origins = [
    "http://localhost:5173",
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === Globals ===
executor = ThreadPoolExecutor()
# Keep user_memory as fallback for when MongoDB is unavailable
user_memory = {}
tools = [create_event_tool, get_events_tool, create_event_reminder_tool, create_custom_reminder_tool, list_reminders_tool, create_task_tool, get_tasks_tool, update_task_status_tool]

now = datetime.now(ZoneInfo("Asia/Kuala_Lumpur"))
today_str = now.strftime("%Y-%m-%d")
tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")
redirect_uri = f"{APP_URL}/auth/google_callback"

with open("system_prompt.txt", "r", encoding="utf-8") as f:
    raw_prompt = f.read()
    system_prompt = raw_prompt.format(today=today_str, tomorrow=tomorrow_str)

print("🚀 FastAPI app started!")

# === Migrate existing in-memory conversations to MongoDB ===
if user_memory:
    print("🔄 Migrating existing conversations to MongoDB...")
    migrate_memory_to_mongodb(user_memory)
    user_memory.clear()  # Clear after migration
else:
    print("📭 No existing conversations to migrate")

# === Routes ===
@app.get("/")
def read_root():
    return {"hello": "world"}

@app.get("/auth/callback")
async def verify_webhook(request: Request):
    params = request.query_params
    if (
        params.get("hub.mode") == "subscribe"
        and params.get("hub.verify_token") == VERIFY_TOKEN
    ):
        return PlainTextResponse(content=params.get("hub.challenge"), status_code=200)
    return PlainTextResponse("Verification failed", status_code=403)

@app.post("/auth/callback")
async def receive_whatsapp(request: Request):
    data = await request.json()
    client = OpenAI(api_key=OPENAI_API_KEY)

    try:
        entry = data["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]
        messages = value.get("messages")

        if not messages:
            print("⚠️ No incoming WhatsApp message found.")
            return {"ok": True}

        message = messages[0]
        sender = message["from"]
        text = message["text"]["body"]

        user_id = sender
        user_input = text

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
                            reply = "📝 You have no tasks."
                        else:
                            reply_lines = ["📝 Your Tasks:"]
                            for task in tasks:
                                # Status text
                                status_text = task["status"].replace("_", " ").title()
                                # Priority emojis
                                priority_emoji = "🔴" if task["priority"] == "high" else "🟡" if task["priority"] == "medium" else "🟢"
                                reply_lines.append(f"{priority_emoji} {task['title']} - {status_text}")
                                if task.get('description'):
                                    reply_lines.append(f"   📄 {task['description']}")
                            reply = "\n".join(reply_lines)

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
                                f"Status: {args['status'].replace('_', ' ').title()}"
                            )
                        else:
                            reply = "❌ Task not found or update failed."

                    else:
                        reply = "❌ Unknown function requested."

                except AuthRequiredError:
                    auth_url = get_auth_url(user_id)
                    reply = f"🔐 Please authorize access to your calendar:\n{auth_url}"

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

    except Exception as e:
        print(f"Error in handle_message: {e}")

    return {"ok": True}

@app.get("/test")
async def test_page(request: Request):
    return PlainTextResponse("Test page reached!", status_code=200)

@app.get("/test/memory/{user_id}")
async def test_memory(user_id: str):
    """Test endpoint to view conversation history for a specific user"""
    try:
        history = get_conversation_history(user_id, user_memory)
        return {
            "user_id": user_id,
            "message_count": len(history),
            "messages": history,
            "source": "mongodb" if history and user_id not in user_memory else "fallback"
        }
    except Exception as e:
        return {"error": str(e), "user_id": user_id}

@app.get("/auth/google_callback")
async def auth_callback(request: Request):
    params = dict(request.query_params)
    state = params.get("state")
    code = params.get("code")

    if not state or not code:
        return RedirectResponse(
            url="https://pa-agent-fe.vercel.app/auth-result?status=error&reason=missing_state_or_code",
            status_code=303
        )

    state_data = oauth_states_collection.find_one({"state": state})
    if not state_data:
        return RedirectResponse(
            url="https://pa-agent-fe.vercel.app/auth-result?status=error&reason=invalid_state",
            status_code=303
        )

    user_id = state_data["user_id"]

    flow = Flow.from_client_secrets_file(
        "credentials.json",
        scopes=SCOPES,
        redirect_uri=redirect_uri,
        state=state
    )

    try:
        flow.fetch_token(code=code)
    except Exception as e:
        print("⚠️ fetch_token error:", e)
        return RedirectResponse(
            url="https://pa-agent-fe.vercel.app/auth-result?status=error&reason=fetch_token_failed",
            status_code=303
        )

    credentials = flow.credentials

    if not credentials or not credentials.token:
        print("❌ No credentials found after fetch_token")
        return RedirectResponse(
            url="https://pa-agent-fe.vercel.app/auth-result?status=error&reason=no_credentials",
            status_code=303
        )

    oauth_tokens_collection.update_one(
        {"user_id": user_id},
        {"$set": {"token": {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes": credentials.scopes,
            "expiry": credentials.expiry.isoformat() if credentials.expiry else None
        }}},
        upsert=True
    )

    return RedirectResponse(
        url="https://pa-agent-fe.vercel.app/auth-result?status=success",
        status_code=303
    )

# === Start Scheduler ===
start_scheduler()

# === Run Server ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
