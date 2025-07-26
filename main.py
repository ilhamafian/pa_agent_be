import os
import json
import re
import asyncio
import requests

from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update, Bot
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from concurrent.futures import ThreadPoolExecutor
from tools.calendar import create_event_tool, get_auth_url, get_events_tool, create_event, get_events, AuthRequiredError
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from db import oauth_states_collection, oauth_tokens_collection

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events"
]

load_dotenv()  # Make sure environment variables are loaded

app = FastAPI()
TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
bot = Bot(token=TOKEN)
executor = ThreadPoolExecutor()

now = datetime.now(ZoneInfo("Asia/Kuala_Lumpur"))
today_str = now.strftime("%Y-%m-%d")
tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")

with open("system_prompt.txt", "r", encoding="utf-8") as f:
    raw_prompt = f.read()
    system_prompt = raw_prompt.format(
        today=today_str,
        tomorrow=tomorrow_str,
    )

def escape_markdown(text: str) -> str:
    return re.sub(r'([_\*\[\]()~`>#+\-=|{}.!])', r'\\\1', text)

user_memory = {}

tools = [create_event_tool, get_events_tool]

@app.post("/webhook")
async def telegram_webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, bot)

    client = OpenAI(api_key=OPENAI_API_KEY)

    if update.message:
        user_id = update.message.chat.id
        user_input = update.message.text

    print("User ID: ", user_id)

    try:
        # Get user history
        history = user_memory.get(user_id, [])
        history.append({"role": "user", "content": user_input})

        # Prepare full messages
        messages = [{"role": "system", "content": system_prompt}] + history[-10:]

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tools=tools,
            tool_choice="auto"
        )

        message = response.choices[0].message
        print("message:", message)

        if message.tool_calls:
            for tool_call in message.tool_calls:
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

                await bot.send_message(chat_id=user_id, text=reply)
                history.append({"role": "assistant", "content": reply})
                user_memory[user_id] = history
                return

        # ✅ If no tool calls, fall back to regular model message
        if message.content:
            reply = message.content.strip()
            await bot.send_message(chat_id=user_id, text=reply)
            history.append({"role": "assistant", "content": reply})
            user_memory[user_id] = history

    except Exception as e:
        print(f"Error in handle_message: {e}")

    return {"ok": True}

@app.get("/set-webhook")
def set_webhook():
    ngrok_url = "https://1bb8ed3755d1.ngrok-free.app"  # 👈 Update to current ngrok URL
    webhook_url = f"{ngrok_url}/webhook"
    telegram_url = f"https://api.telegram.org/bot{TOKEN}/setWebhook?url={webhook_url}"

    response = requests.get(telegram_url)
    print("Telegram response:", response.text)  # 🧪 Debugging
    return JSONResponse(content=response.json())

from fastapi import Request
from fastapi.responses import HTMLResponse
from google_auth_oauthlib.flow import Flow

@app.get("/auth/callback")
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
        redirect_uri="https://1bb8ed3755d1.ngrok-free.app/auth/callback",
        state=state  # ✅ FIX HERE
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

    return HTMLResponse(content="✅ You're all set! You can now go back to Telegram.", status_code=200)
