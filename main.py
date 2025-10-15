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
from routers.user import router as user_router
from routers.settings import router as settings_router
from routers.integrations import router as integrations_router
from routers.dashboard import router as dashboard_router
from routers.admin import router as admin_router
from contextlib import asynccontextmanager
from routers.reminder import router as reminder_router
# Internal Imports
from tools.scheduler import start_scheduler
from ai.workflows.assistant import assistant_response
from db.mongo import oauth_states_collection, oauth_tokens_collection
from utils.utils import hash_data, send_whatsapp_message

# === Setup ===
load_dotenv(dotenv_path=".env.local", override=True)

SCOPES = json.loads(os.getenv("SCOPES", "[]"))
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
APP_URL = os.getenv("APP_URL")
FRONTEND_URL = os.getenv("FRONTEND_URL")


db_name = os.environ.get("DB_NAME")
db = client[db_name]
users_collection = db["users"]
integrations_collection = db["integrations"]

# ✅ Define lifespan first
@asynccontextmanager
async def lifespan(app: FastAPI):
    # === Startup ===
    print("🚀 Starting FastAPI lifespan setup...")
    
    # Initialize MongoDB connection and create indexes
    from db.mongo import init_mongodb
    await init_mongodb()
    
    # Initialize calendar indexes
    from tools.calendar import init_calendar_indexes
    await init_calendar_indexes()
    
    # Initialize Cloud Tasks scheduler for daily reminders
    await start_scheduler()
    
    yield  # ✅ Allow FastAPI to run

    # === Shutdown ===
    print("🛑 Shutting down FastAPI app...")
    await client.close()
    print("✅ MongoDB connection closed")

# ✅ Now create app with lifespan handler
app = FastAPI(lifespan=lifespan)

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

# Add request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    print(f"\n[REQUEST] {request.method} {request.url.path}")
    print(f"[REQUEST] Headers: {dict(request.headers)}")
    print(f"[REQUEST] Client: {request.client}")
    
    response = await call_next(request)
    
    print(f"[RESPONSE] Status: {response.status_code}")
    return response

# === Globals ===
executor = ThreadPoolExecutor()
redirect_uri = f"{APP_URL}/auth/google_callback"

print("🚀 FastAPI app started!")
print(f"DB_NAME: {db_name}")

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

@app.post("/playground")
async def admin_chat(request: Request):
    data = await request.json()
    sender = "601234567890"
    text = data["message"]
    print(f"Admin chat data: {data}")
    return await assistant_response(sender, text, True)

@app.post("/worker/process-message")
async def process_message_worker(request: Request):
    """
    Worker endpoint for processing queued WhatsApp messages.
    Called by Cloud Tasks asynchronously.
    """
    try:
        data = await request.json()
        sender = data.get("sender")
        text = data.get("text")
        message_id = data.get("message_id")
        timestamp = data.get("timestamp")
        
        print(f"\n[WORKER] Processing queued message")
        print(f"[WORKER] Sender: {sender}")
        print(f"[WORKER] Text: {text}")
        print(f"[WORKER] Message ID: {message_id}")
        print(f"[WORKER] Queued at: {timestamp}")
        
        if not sender or not text:
            print(f"[WORKER] ❌ Missing required fields: sender={sender}, text={text}")
            return {"status": "error", "message": "Missing sender or text"}
        
        # Process the message through the assistant
        result = await assistant_response(sender, text)
        
        print(f"[WORKER] ✅ Message processed successfully")
        return {"status": "success", "result": result}
        
    except Exception as e:
        print(f"[WORKER] ❌ Error processing message: {e}")
        import traceback
        print(f"[WORKER] Full traceback: {traceback.format_exc()}")
        return {"status": "error", "message": str(e)}

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
        message_id = message.get("id")  # WhatsApp message ID for deduplication

        print(f"📨 Received message from {sender}: {text} (ID: {message_id})")

        # ✅ Step 1: Check if user exists in MongoDB
        hashed_sender = hash_data(sender)
        print(f"Hashed sender: {hashed_sender}")
        user = await users_collection.find_one({"hashed_phone_number": hashed_sender})

        if not user:
            print(f"👤 New user detected: {sender} — initiating onboarding.")

            # Step 3: Send onboarding message
            onboarding_url = f"{FRONTEND_URL}/onboarding?phone_number={sender}"
            onboarding_message = (
                "👋 Hello! I'm *Lofy*, your personal WhatsApp assistant built to help you stay organized — effortlessly.\n\n"
                "With Lofy, you can:\n"
                "- 📅 Schedule events using natural language (like 'Lunch with Sarah tomorrow at 1pm')\n"
                "- ⏰ Set reminders for anything — even 'remind me in 3 hours to check the oven'\n"
                "- ✅ Manage tasks with priorities like high 🔴, medium 🟡, and low 🟢\n"
                "- 📝 Save personal notes and search them later with smart suggestions\n\n"
                "- 🧾 Detect and auto-schedule bookings from templates (great for freelancers and service providers)\n\n"
                f"To activate your account and unlock these features, tap below:\n👉 {onboarding_url}\n\n"
                "Lofy Assistant, created by Ilham Ghazi & Meor Izzuddin\n\n"
            )

            await send_whatsapp_message(sender, onboarding_message)

            # Stop further processing
            return {"ok": True}
        
        # ✅ Step 4: Enqueue message for async processing (fast webhook response)
        from utils.cloud_tasks import enqueue_message
        
        try:
            await enqueue_message(sender, text, message_id)
            print(f"✅ Message from {sender} queued successfully")
            return {"ok": True, "status": "queued"}
        except Exception as enqueue_error:
            print(f"❌ Failed to enqueue message: {enqueue_error}")
            # Fallback to inline processing if queue fails
            print(f"⚠️ Falling back to inline processing")
            return await assistant_response(sender, text)

    except Exception as e:
        print(f"❌ Error in receive_whatsapp: {e}")
        return {"ok": False, "error": str(e)}

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

    state_data = await oauth_states_collection.find_one({"state": state})
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

    await oauth_tokens_collection.update_one(
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
    await integrations_collection.update_one(
        {"user_id": user_id},
        {"$set": {"integrations.google_calendar.enabled": True}},
        upsert=True
    )

    return RedirectResponse(
        url=f"{FRONTEND_URL}/auth-result?status=success",
        status_code=303
    )

# Register your user API routes
print("\n" + "="*80)
print("[MAIN] Registering routers...")
print("="*80)

print("[MAIN] Registering admin_router...")
app.include_router(admin_router)
print("[MAIN] ✅ Admin router registered")

print("[MAIN] Registering user_router...")
app.include_router(user_router)
print("[MAIN] ✅ User router registered")

print("[MAIN] Registering settings_router...")
app.include_router(settings_router)
print("[MAIN] ✅ Settings router registered")

print("[MAIN] Registering integrations_router...")
app.include_router(integrations_router)
print("[MAIN] ✅ Integrations router registered")

print("[MAIN] Registering reminder_router...")
app.include_router(reminder_router)
print("[MAIN] ✅ Reminder router registered")

print("[MAIN] Registering dashboard_router...")
app.include_router(dashboard_router)
print("[MAIN] ✅ Dashboard router registered")

print("\n[MAIN] All registered routes:")
for route in app.routes:
    if hasattr(route, 'methods') and hasattr(route, 'path'):
        print(f"  - {list(route.methods)} {route.path}")
print("="*80 + "\n")

# === Run Server ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
