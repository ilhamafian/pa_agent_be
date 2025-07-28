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
from tools.scheduler import start_scheduler
from utils.utils import clean_unicode, send_whatsapp_message, get_auth_url
from db.mongo import oauth_states_collection, oauth_tokens_collection

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
user_memory = {}
tools = [create_event_tool, get_events_tool]

now = datetime.now(ZoneInfo("Asia/Kuala_Lumpur"))
today_str = now.strftime("%Y-%m-%d")
tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")
redirect_uri = f"{APP_URL}/auth/google_callback"

with open("system_prompt.txt", "r", encoding="utf-8") as f:
    raw_prompt = f.read()
    system_prompt = raw_prompt.format(today=today_str, tomorrow=tomorrow_str)

print(" FastAPI app started!")

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
            print("\u26a0\ufe0f No incoming WhatsApp message found.")
            return {"ok": True}

        message = messages[0]
        sender = message["from"]
        text = message["text"]["body"]

        user_id = sender
        user_input = text

        history = user_memory.get(user_id, [])
        history.append({"role": "user", "content": user_input})

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
                            f"\ud83d\udcc5 Calendar Event Created\n\n"
                            f"Title: {args['title']}\n"
                            f"Date: {args['date']}\n"
                            f"{time_display}"
                            f"Link: {result.get('htmlLink', 'Link unavailable')}"
                        )

                    elif function_name == "get_events":
                        reply = get_events(natural_range=args["natural_range"], user_id=user_id)

                    else:
                        reply = "\u274c Unknown function requested."

                except AuthRequiredError:
                    auth_url = get_auth_url(user_id)
                    reply = f"\ud83d\udd10 Please authorize access to your calendar:\n{auth_url}"

                safe_reply = clean_unicode(reply)
                await send_whatsapp_message(user_id, safe_reply)
                history.append({"role": "assistant", "content": reply})
                user_memory[user_id] = history
                return {"ok": True}

        if ai_message.content:
            reply = ai_message.content.strip()
            safe_reply = clean_unicode(reply)
            await send_whatsapp_message(user_id, safe_reply)
            history.append({"role": "assistant", "content": reply})
            user_memory[user_id] = history

    except Exception as e:
        print(f"Error in handle_message: {e}")

    return {"ok": True}

@app.get("/test")
async def test_page(request: Request):
    return PlainTextResponse("Test page reached!", status_code=200)

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
        print("\u26a0\ufe0f fetch_token error:", e)
        return RedirectResponse(
            url="https://pa-agent-fe.vercel.app/auth-result?status=error&reason=fetch_token_failed",
            status_code=303
        )

    credentials = flow.credentials

    if not credentials or not credentials.token:
        print("\u274c No credentials found after fetch_token")
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
