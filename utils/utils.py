import hashlib
from fastapi import Depends, HTTPException, logger
import httpx
import os
import json
import asyncio
import threading
from jose import jwt
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from dotenv import load_dotenv
from google_auth_oauthlib.flow import Flow
from datetime import datetime
from db.mongo import oauth_states_collection

load_dotenv()  # Make sure environment variables are loaded

SCOPES = json.loads(os.getenv("SCOPES", "[]"))
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
APP_URL = os.getenv("APP_URL")
SECRET_KEY = os.getenv("TOKEN_SECRET_KEY")
ALGORITHM = "HS256"

security = HTTPBearer()

redirect_uri = f"{APP_URL}/auth/google_callback"

def clean_unicode(text):
    return text.encode("utf-8", errors="replace").decode("utf-8")

def hash_data(data: str) -> str:
    """Hash sensitive data using SHA-256"""
    return hashlib.sha256(data.encode()).hexdigest()

async def send_whatsapp_message(recipient_id: str, message: str):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": recipient_id,
        "type": "text",
        "text": {"body": message}
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=data, headers=headers)
        print("WhatsApp Send Response:", response.status_code, response.text)

def get_auth_url(user_id):
    print("Entered get_auth_url")
    flow = Flow.from_client_secrets_file(
        "credentials.json",
        scopes=SCOPES,
        redirect_uri=redirect_uri  # 🔁 You handle this below
    )

    auth_url, state = flow.authorization_url(
        prompt='consent',
        access_type='offline',
        include_granted_scopes='true'
    )

    oauth_states_collection.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "state": state,
                "created_at": datetime.now()
            }
        },
        upsert=True
    )

    return auth_url

# Background asyncio event loop managed in its own thread
# Expose a getter to safely retrieve it at runtime
event_loop = None
event_loop_ready = threading.Event()

def _run_bg_loop():
    global event_loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    event_loop = loop
    event_loop_ready.set()
    loop.run_forever()

threading.Thread(target=_run_bg_loop, daemon=True).start()

def get_event_loop():
    if event_loop is None:
        # Wait briefly for the background loop to initialize
        event_loop_ready.wait(timeout=5)
    return event_loop

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    print("Authorization header received")

    token = credentials.credentials
    print(f"Token starts with: {token[:10]}... (length: {len(token)})")

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        print(f"Decoded JWT payload: {payload}")
    except jwt.ExpiredSignatureError:
        print("JWT token has expired")
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as e:
        print(f"Invalid JWT token: {e}")
        raise HTTPException(status_code=401, detail="Invalid token")

    if "user_id" not in payload:
        print("Decoded JWT payload missing 'user_id'")
        raise HTTPException(status_code=401, detail="Invalid token payload")

    return payload