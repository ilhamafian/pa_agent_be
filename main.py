import os
import json
from dotenv import load_dotenv
from openai import OpenAI
from telegram import Bot
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from concurrent.futures import ThreadPoolExecutor
from tools.calendar import create_event_tool, get_events_tool, create_event, get_events, AuthRequiredError
from utils.utils import send_whatsapp_message, get_auth_url
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from google_auth_oauthlib.flow import Flow
from db import oauth_states_collection, oauth_tokens_collection

load_dotenv()  # Make sure environment variables are loaded

SCOPES = json.loads(os.getenv("SCOPES", "[]"))
TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
APP_URL = os.getenv("APP_URL")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # or your deployed frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/status")
def get_status():
    return {
        "username": "Bossman",
        "status": "✅ You're all set!",
    }

bot = Bot(token=TOKEN)
executor = ThreadPoolExecutor()

now = datetime.now(ZoneInfo("Asia/Kuala_Lumpur"))
today_str = now.strftime("%Y-%m-%d")
tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")
redirect_uri = f"{APP_URL}/auth/google_callback"

with open("system_prompt.txt", "r", encoding="utf-8") as f:
    raw_prompt = f.read()
    system_prompt = raw_prompt.format(
        today=today_str,
        tomorrow=tomorrow_str,
    )

user_memory = {}

tools = [create_event_tool, get_events_tool]

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

        # 🧠 Get or initialize conversation history
        history = user_memory.get(user_id, [])
        history.append({"role": "user", "content": user_input})

        # 🔧 Create message payload for OpenAI
        chat_messages = [{"role": "system", "content": system_prompt}] + history[-10:]

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=chat_messages,
            tools=tools,
            tool_choice="auto"
        )

        ai_message = response.choices[0].message
        print("message:", ai_message)

        # 🛠️ Handle tool calls
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
                        if args.get("time") and args.get("end_time"):
                            time_display = f"Time: {args['time']} - {args['end_time']}\n"
                        else:
                            time_display = "Time: All-day\n"

                        reply = (
                            f"📅 Calendar Event Created\n\n"
                            f"Title: {args['title']}\n"
                            f"Date: {args['date']}\n"
                            f"{time_display}"
                            f"Link: {result.get('htmlLink', 'Link unavailable')}"
                        )

                    elif function_name == "get_events":
                        reply = get_events(natural_range=args["natural_range"], user_id=user_id)

                    else:
                        reply = "❌ Unknown function requested."

                except AuthRequiredError:
                    auth_url = get_auth_url(user_id)
                    reply = f"🔒 Please authorize access to your calendar:\n{auth_url}"

                await send_whatsapp_message(user_id, reply)
                history.append({"role": "assistant", "content": reply})
                user_memory[user_id] = history
                return {"ok": True}

        # 💬 Fallback: regular message (no function)
        if ai_message.content:
            reply = ai_message.content.strip()
            await send_whatsapp_message(user_id, reply)
            history.append({"role": "assistant", "content": reply})
            user_memory[user_id] = history

    except Exception as e:
        print(f"Error in handle_message: {e}")

    return {"ok": True}

@app.get("/auth/google_callback")
async def auth_callback(request: Request):
    params = dict(request.query_params)
    state = params.get("state")
    code = params.get("code")

    if not state or not code:
        return HTMLResponse(content="❌ Missing state or code from callback.", status_code=400)

    state_data = oauth_states_collection.find_one({"state": state})
    if not state_data:
        return HTMLResponse(content="❌ Invalid or expired state.", status_code=400)

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
        return HTMLResponse(content="❌ Auth failed while fetching token.", status_code=500)

    credentials = flow.credentials

    if not credentials or not credentials.token:
        print("❌ No credentials found after fetch_token")
        return HTMLResponse(content="❌ Credentials missing after token exchange.", status_code=500)

    print("✅ credentials fetched:", credentials.to_json())

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
    
    return FileResponse("ui/build/redirect/") 
    # return HTMLResponse(content="✅ You're all set! You can now go back to Telegram.", status_code=200)
