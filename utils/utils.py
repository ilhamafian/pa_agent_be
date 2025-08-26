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
from datetime import datetime, timedelta
import pytz
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from db.mongo import oauth_states_collection, oauth_tokens_collection
from cryptography.fernet import Fernet

load_dotenv()  # Make sure environment variables are loaded

SCOPES = json.loads(os.getenv("SCOPES", "[]"))
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
APP_URL = os.getenv("APP_URL")
SECRET_KEY = os.getenv("TOKEN_SECRET_KEY")
ALGORITHM = "HS256"
fernet = Fernet(os.getenv("PHONE_ENCRYPTION_KEY"))

security = HTTPBearer()

redirect_uri = f"{APP_URL}/auth/google_callback"

def clean_unicode(text):
    return text.encode("utf-8", errors="replace").decode("utf-8")

def hash_data(data: str) -> str:
    """Hash sensitive data using SHA-256"""
    return hashlib.sha256(data.encode()).hexdigest()

def encrypt_phone(phone_number: str) -> str:
    return fernet.encrypt(phone_number.encode()).decode()

def decrypt_phone(encrypted_number: str) -> str:
    return fernet.decrypt(encrypted_number.encode()).decode()

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
    
    # Add spam analysis debugging
    spam_indicators = {
        "message_length": len(message),
        "contains_urls": "http" in message.lower(),
        "url_count": message.lower().count("http"),
        "contains_spam_words": any(word in message.lower() for word in [
            'expired', 'reconnect', 'access', 'token', 'renew', 'refresh', 
            'click here', 'tap here', 'urgent', 'verify', 'suspended'
        ]),
        "has_multiple_links": message.count("https://") > 1,
        "contains_auth_url": "accounts.google.com" in message,
        "message_type": "token_expiration" if any(word in message.lower() for word in ['calendar', 'connection', 'refresh']) else "regular"
    }
    
    print(f"[SPAM ANALYSIS] Message to {recipient_id[:5]}...")
    for key, value in spam_indicators.items():
        print(f"[SPAM ANALYSIS] {key}: {value}")
    print(f"[SPAM ANALYSIS] First 100 chars: {message[:100]}...")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=data, headers=headers)
            
            # Parse the response safely
            response_text = response.text
            print(f"WhatsApp Send Response: {response.status_code}")
            print(f"Response length: {len(response_text)} characters")
            
            # Try to parse as JSON for better handling
            response_json = None
            try:
                response_json = response.json()
                print(f"Response JSON parsed successfully")
                # Only print the essential parts to avoid log truncation
                if response_json.get("messages"):
                    message_id = response_json["messages"][0].get("id")
                    print(f"Message ID: {message_id}")
            except Exception as json_error:
                print(f"Failed to parse response as JSON: {json_error}")
                print(f"Raw response text (first 200 chars): {response_text[:200]}")
                response_json = None
            
            # Return a result object for the scheduler
            if response.status_code == 200:
                result = {
                    "status": "success", 
                    "status_code": response.status_code, 
                    "response_json": response_json,
                    "message_id": response_json.get("messages", [{}])[0].get("id") if response_json else None
                }
                print(f"WhatsApp function returning success result")
                return result
            else:
                result = {
                    "status": "error", 
                    "status_code": response.status_code, 
                    "response_text": response_text[:500],  # Limit response text
                    "response_json": response_json
                }
                print(f"WhatsApp function returning error result")
                return result
    except Exception as e:
        print(f"WhatsApp Send Error: {e}")
        import traceback
        print(f"Full error traceback: {traceback.format_exc()}")
        return {"status": "error", "error": str(e)}

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

def get_dashboard_events(user_id: str):
    """
    Get events for the current day (starting at 00:00am) through the next 3 days.
    Returns events in JSON format with title, date (DD-MM-YYYY), and time.
    """
    # Check if user has valid OAuth token
    token_data = oauth_tokens_collection.find_one({"user_id": user_id})
    if not token_data:
        return {"events": [], "error": "AUTH_REQUIRED"}
    
    try:
        # Initialize Google Calendar service
        creds = Credentials.from_authorized_user_info(token_data["token"], SCOPES)
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        
        # Set timezone to Asia/Kuala_Lumpur
        tz = pytz.timezone("Asia/Kuala_Lumpur")
        now = datetime.now(tz)
        
        # Set start time to beginning of current day (00:00am)
        start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Set end time to end of the day 3 days from now (23:59:59)
        end_time = start_time + timedelta(days=4) - timedelta(seconds=1)
        
        # Fetch events from Google Calendar
        events_result = service.events().list(
            calendarId='primary',
            timeMin=start_time.isoformat(),
            timeMax=end_time.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get("items", [])
        
        # Format events for dashboard
        formatted_events = []
        for event in events:
            title = event.get("summary", "No Title")
            
            # Get start date/time information
            start_dt_str = event["start"].get("dateTime")
            end_dt_str = event["end"].get("dateTime")
            start_date_str = event["start"].get("date")
            end_date_str = event["end"].get("date")
            
            # Determine if it's an all-day event
            is_all_day = start_date_str is not None
            
            if is_all_day:
                # All-day event
                event_date = datetime.strptime(start_date_str, "%Y-%m-%d")
                formatted_date = event_date.strftime("%d-%m-%Y")
                formatted_time = "All-day"
            else:
                # Timed event
                try:
                    start_dt = datetime.fromisoformat(start_dt_str)
                    end_dt = datetime.fromisoformat(end_dt_str)
                    
                    # Format date as DD-MM-YYYY
                    formatted_date = start_dt.strftime("%d-%m-%Y")
                    
                    # Format time as HH:MM - HH:MM
                    formatted_time = f"{start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}"
                except Exception:
                    # Fallback if datetime parsing fails
                    formatted_date = "Unknown"
                    formatted_time = "All-day"
            
            formatted_events.append({
                "title": title,
                "date": formatted_date,
                "time": formatted_time
            })
        
        return {"events": formatted_events}
        
    except Exception as e:
        print(f"Error fetching dashboard events: {e}")
        return {"events": [], "error": str(e)}