from datetime import datetime
import os
import json
import pytz
import uvicorn
from db.mongo import client
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from concurrent.futures import ThreadPoolExecutor
from google_auth_oauthlib.flow import Flow
from user import router as user_router
from settings import router as settings_router
from integrations import router as integrations_router
from dashboard import router as dashboard_router

# Internal Imports
from tools.scheduler import start_scheduler
from llm import assistant_response
from db.mongo import oauth_states_collection, oauth_tokens_collection
from utils.utils import encrypt_phone, hash_data, send_whatsapp_message

db = client["oauth_db"]
users_collection = db["users"]
integrations_collection = db["integrations"]

# === Setup ===
load_dotenv()

SCOPES = json.loads(os.getenv("SCOPES", "[]"))
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
APP_URL = os.getenv("APP_URL")
FRONTEND_URL = os.getenv("FRONTEND_URL")

app = FastAPI()

# === Middleware ===
origins = [
    "http://localhost:5173",
    FRONTEND_URL
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
redirect_uri = f"{APP_URL}/auth/google_callback"

print("🚀 FastAPI app started!")

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

        print(f"📨 Received message from {sender}: {text}")

        # ✅ Step 1: Check if user exists in MongoDB
        encrypted_sender = encrypt_phone(sender)
        user = users_collection.find_one({"phone_number": encrypted_sender})

        if not user:
            print(f"👤 New user detected: {sender} — initiating onboarding.")

            # Step 3: Send onboarding message
            onboarding_url = f"{FRONTEND_URL}/onboarding?phone_number={sender}"
            onboarding_message = (
                "👋 Hello! I’m *Lofy*, your personal WhatsApp assistant built to help you stay organized — effortlessly.\n\n"
                "With Lofy, you can:\n"
                "- 📅 Schedule events using natural language (like 'Lunch with Sarah tomorrow at 1pm')\n"
                "- ⏰ Set reminders for anything — even 'remind me in 3 hours to check the oven'\n"
                "- ✅ Manage tasks with priorities like high 🔴, medium 🟡, and low 🟢\n"
                "- 🧾 Detect and auto-schedule bookings from templates (great for freelancers and service providers)\n\n"
                f"To activate your account and unlock these features, tap below:\n👉 {onboarding_url}\n\n"
                "Once you're in, just message me here anytime. I’ve got your back! 💪"
            )

            await send_whatsapp_message(sender, onboarding_message)

            # Stop further processing
            return {"ok": True}

        # ✅ Step 4: Proceed to assistant only if user exists
        return await assistant_response(sender, text)

    except Exception as e:
        print(f"❌ Error in receive_whatsapp: {e}")
        return {"ok": False, "error": str(e)}

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
            url=f"{FRONTEND_URL}/auth-result?status=error&reason=missing_state_or_code",
            status_code=303
        )

    state_data = oauth_states_collection.find_one({"state": state})
    if not state_data:
        return RedirectResponse(
            url=f"{FRONTEND_URL}/auth-result?status=error&reason=invalid_state",
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
            url=f"{FRONTEND_URL}/auth-result?status=error&reason=fetch_token_failed",
            status_code=303
        )

    credentials = flow.credentials

    if not credentials or not credentials.token:
        print("❌ No credentials found after fetch_token")
        return RedirectResponse(
            url=f"{FRONTEND_URL}/auth-result?status=error&reason=no_credentials",
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

    # Update integrations.google_calendar.enabled to True for this user
    integrations_collection.update_one(
        {"user_id": user_id},
        {"$set": {"integrations.google_calendar.enabled": True}},
        upsert=True
    )

    return RedirectResponse(
        url=f"{FRONTEND_URL}/auth-result?status=success",
        status_code=303
    )

# Register your user API routes
app.include_router(user_router)
app.include_router(settings_router)
app.include_router(integrations_router)
app.include_router(dashboard_router)

# === Start Scheduler ===
start_scheduler()

# === Run Server ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
