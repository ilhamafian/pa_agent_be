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
import dateparser

load_dotenv(dotenv_path=".env.local", override=True)  # Make sure environment variables are loaded

# Load environment variables with logging
print(f"\n{'='*80}")
print(f"[INIT] Loading environment variables...")
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
    
    url = f"https://graph.facebook.com/v23.0/{PHONE_NUMBER_ID}/messages"
    
    current_token = WHATSAPP_TOKEN
    
    if not current_token:
        error_msg = "Missing WHATSAPP_TOKEN in environment"
        return {"status": "error", "error": error_msg, "error_type": "missing_token_env"}
    
    headers = {
        "Authorization": f"Bearer {current_token}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": recipient_id,
        "type": "text",
        "text": {"body": message}
    }

    if recipient_id == "601234567890":
        print(f"[WHATSAPP_MESSAGE] Sending to admin: {message}")
        return {"status": "success", "message": message}
    else: 
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=data, headers=headers)
                
                response_text = response.text
                
                response_json = None
                try:
                    response_json = response.json()
                    
                except Exception as json_error:
                    response_json = None
                
                if response.status_code == 401:
                    return {"status": "error", "status_code": 401, "response_text": response_text[:500], "response_json": response_json}
                
                # Return a result object for the scheduler
                if response.status_code == 200:
                    result = {
                        "status": "success", 
                        "status_code": response.status_code, 
                        "response_json": response_json,
                        "message_id": response_json.get("messages", [{}])[0].get("id") if response_json else None
                    }
                    return result
                else:
                    result = {"status": "error", "status_code": response.status_code, "response_text": response_text[:500], "response_json": response_json}
                    return result
        except Exception as e:
            return {"status": "error", "error": str(e)}

async def send_whatsapp_template(recipient_id: str, template_name: str, language_code: str = "en"):
    """
    Send a WhatsApp template message (for users outside 24-hour window).
    Template must be pre-approved in Meta Business Manager.
    """
    url = f"https://graph.facebook.com/v23.0/{PHONE_NUMBER_ID}/messages"
    
    current_token = WHATSAPP_TOKEN
    
    if not current_token:
        error_msg = "Missing WHATSAPP_TOKEN in environment"
        return {"status": "error", "error": error_msg, "error_type": "missing_token_env"}
    
    headers = {
        "Authorization": f"Bearer {current_token}",
        "Content-Type": "application/json"
    }
    
    data = {
        "messaging_product": "whatsapp",
        "to": recipient_id,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {
                "code": language_code
            }
        }
    }
    
    print(f"[WHATSAPP_TEMPLATE] Sending to: {recipient_id}")
    print(f"[WHATSAPP_TEMPLATE] Template: {template_name}, Language: {language_code}")
    print(f"[WHATSAPP_TEMPLATE] Full payload: {json.dumps(data, indent=2)}")
    print(f"[WHATSAPP_TEMPLATE] URL: {url}")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=data, headers=headers)
            
            print(f"[WHATSAPP_TEMPLATE] Response status: {response.status_code}")
            print(f"[WHATSAPP_TEMPLATE] Response body: {response.text}")
            
            response_text = response.text
            response_json = None
            
            try:
                response_json = response.json()
            except Exception as json_error:
                response_json = None
            
            if response.status_code == 200:
                result = {
                    "status": "success", 
                    "status_code": response.status_code, 
                    "response_json": response_json,
                    "message_id": response_json.get("messages", [{}])[0].get("id") if response_json else None
                }
                return result
            else:
                result = {
                    "status": "error", 
                    "status_code": response.status_code, 
                    "response_text": response_text[:500], 
                    "response_json": response_json
                }
                return result
    except Exception as e:
        return {"status": "error", "error": str(e)}

async def get_auth_url(user_id):
    print("Entered get_auth_url")
    # Create flow in executor since it's blocking
    loop = asyncio.get_running_loop()
    flow = await loop.run_in_executor(None, 
        lambda: Flow.from_client_secrets_file(
            "credentials.json",
            scopes=SCOPES,
            redirect_uri=redirect_uri
        )
    )

    # Get auth URL in executor since it makes HTTP requests
    auth_url, state = await loop.run_in_executor(None,
        lambda: flow.authorization_url(
            prompt='consent',
            access_type='offline',
            include_granted_scopes='true'
        )
    )

    # Update MongoDB asynchronously
    await oauth_states_collection.update_one(
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

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    print("Authorization header received")

    token = credentials.credentials
    print(f"Token starts with: {token[:10]}... (length: {len(token)})")

    try:
        # Run JWT decode in executor since it's CPU-bound
        loop = asyncio.get_running_loop()
        payload = await loop.run_in_executor(None,
            lambda: jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        )
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

async def get_dashboard_events(user_id: str):
    """
    Get events for the current day (starting at 00:00am) through the next 3 days.
    Returns events in JSON format with title, date (DD-MM-YYYY), and time.
    """
    # ============ GOOGLE CALENDAR CODE - COMMENTED OUT ============
    # # Check if user has valid OAuth token
    # token_data = oauth_tokens_collection.find_one({"user_id": user_id})
    # if not token_data:
    #     return {"events": [], "error": "AUTH_REQUIRED"}
    # 
    # try:
    #     # Initialize Google Calendar service
    #     creds = Credentials.from_authorized_user_info(token_data["token"], SCOPES)
    #     service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    #     
    #     # Set timezone to Asia/Kuala_Lumpur
    #     tz = pytz.timezone("Asia/Kuala_Lumpur")
    #     now = datetime.now(tz)
    #     
    #     # Set start time to beginning of current day (00:00am)
    #     start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
    #     
    #     # Set end time to end of the day 3 days from now (23:59:59)
    #     end_time = start_time + timedelta(days=4) - timedelta(seconds=1)
    #     
    #     # Fetch events from Google Calendar
    #     events_result = service.events().list(
    #         calendarId='primary',
    #         timeMin=start_time.isoformat(),
    #         timeMax=end_time.isoformat(),
    #         singleEvents=True,
    #         orderBy='startTime'
    #     ).execute()
    #     
    #     events = events_result.get("items", [])
    #     
    #     # Format events for dashboard
    #     formatted_events = []
    #     for event in events:
    #         title = event.get("summary", "No Title")
    #         
    #         # Get start date/time information
    #         start_dt_str = event["start"].get("dateTime")
    #         end_dt_str = event["end"].get("dateTime")
    #         start_date_str = event["start"].get("date")
    #         end_date_str = event["end"].get("date")
    #         
    #         # Determine if it's an all-day event
    #         is_all_day = start_date_str is not None
    #         
    #         if is_all_day:
    #             # All-day event
    #             event_date = datetime.strptime(start_date_str, "%Y-%m-%d")
    #             formatted_date = event_date.strftime("%d-%m-%Y")
    #             formatted_time = "All-day"
    #         else:
    #             # Timed event
    #             try:
    #                 start_dt = datetime.fromisoformat(start_dt_str)
    #                 end_dt = datetime.fromisoformat(end_dt_str)
    #                 
    #                 # Format date as DD-MM-YYYY
    #                 formatted_date = start_dt.strftime("%d-%m-%Y")
    #                 
    #                 # Format time as HH:MM - HH:MM
    #                 formatted_time = f"{start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}"
    #             except Exception:
    #                 # Fallback if datetime parsing fails
    #                 formatted_date = "Unknown"
    #                 formatted_time = "All-day"
    #         
    #         formatted_events.append({
    #             "title": title,
    #             "date": formatted_date,
    #             "time": formatted_time
    #         })
    #     
    #     return {"events": formatted_events}
    #     
    # except Exception as e:
    #     print(f"Error fetching dashboard events: {e}")
    #     return {"events": [], "error": str(e)}
    # ============ END GOOGLE CALENDAR CODE ============
    
    # ============ MONGODB IMPLEMENTATION ============
    from db.mongo import db
    calendar_collection = db["calendar"]

    try:
        tz = pytz.timezone("Asia/Kuala_Lumpur")
        now = datetime.now(tz)

        # Define time window (today to +6 days)
        start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_time = start_time + timedelta(days=6) - timedelta(seconds=1)

        # Fetch events matching this window
        cursor = await calendar_collection.find({
            "user_id": user_id,
            "$or": [
                {"start.dateTime": {"$gte": start_time.isoformat(), "$lte": end_time.isoformat()}},
                {"start.date": {"$gte": start_time.date().isoformat(), "$lte": end_time.date().isoformat()}}
            ]
        }).sort("start.dateTime", 1)
        
        events = []
        async for event in cursor:
            events.append(event)

        formatted_events = []

        for event in events:
            title = event.get("summary", "No Title")
            start = event.get("start", {})
            end = event.get("end", {})

            start_dt_str = start.get("dateTime")
            end_dt_str = end.get("dateTime")
            start_date_str = start.get("date")
            end_date_str = end.get("date")

            if start_date_str:
                # All-day event
                event_date = datetime.strptime(start_date_str, "%Y-%m-%d")
                formatted_date = event_date.strftime("%d-%m-%Y")
                formatted_time = "All-day"
            elif start_dt_str:
                # Timed event (parse with dateparser)
                start_dt = dateparser.parse(start_dt_str).astimezone(tz)
                end_dt = dateparser.parse(end_dt_str).astimezone(tz)
                formatted_date = start_dt.strftime("%d-%m-%Y")
                formatted_time = f"{start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}"
            else:
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