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
from db.mongo import oauth_states_collection, oauth_tokens_collection, client
from cryptography.fernet import Fernet

load_dotenv()  # Make sure environment variables are loaded

SCOPES = json.loads(os.getenv("SCOPES", "[]"))
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
APP_URL = os.getenv("APP_URL")
SECRET_KEY = os.getenv("TOKEN_SECRET_KEY")
ALGORITHM = "HS256"
fernet = Fernet(os.getenv("PHONE_ENCRYPTION_KEY"))

# MongoDB setup for WhatsApp tokens
db = client["oauth_db"]
whatsapp_token_collection = db["whatsapp_token"]

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

def get_whatsapp_token() -> str:
    """Get the current WhatsApp token from database or environment"""
    try:
        # First try to get from database
        token_doc = whatsapp_token_collection.find_one()
        if token_doc and token_doc.get("whatsapp_token"):
            print("🔍 Using WhatsApp token from database")
            return token_doc["whatsapp_token"]
        
        # Fallback to environment variable
        print("🔍 Using WhatsApp token from environment variable")
        return WHATSAPP_TOKEN
    except Exception as e:
        print(f"❌ Error getting WhatsApp token: {e}")
        return WHATSAPP_TOKEN

def save_whatsapp_token(token: str) -> bool:
    """Save WhatsApp token to database for persistence"""
    try:
        # Update the existing document or create a new one
        # Since user has only one document, we'll update it directly
        result = whatsapp_token_collection.update_one(
            {},  # Match any document (since there should be only one)
            {
                "$set": {
                    "whatsapp_token": token,
                    "updated_at": datetime.now()
                }
            },
            upsert=True  # Create document if it doesn't exist
        )
        print("✅ WhatsApp token saved to database")
        return True
    except Exception as e:
        print(f"❌ Error saving WhatsApp token: {e}")
        return False

async def refresh_whatsapp_token():
    """
    Refresh WhatsApp access token using app credentials
    """
    try:
        # Get app credentials from environment
        app_id = os.getenv("WHATSAPP_APP_ID")
        app_secret = os.getenv("WHATSAPP_APP_SECRET")
        
        print(f"🔍 Checking environment variables...")
        print(f"🔍 WHATSAPP_APP_ID: {'SET' if app_id else 'NOT SET'}")
        print(f"🔍 WHATSAPP_APP_SECRET: {'SET' if app_secret else 'NOT SET'}")
        
        if not app_id or not app_secret:
            print("❌ WhatsApp App ID or App Secret not found in environment variables")
            print("❌ Please add WHATSAPP_APP_ID and WHATSAPP_APP_SECRET to your .env file")
            print("❌ For now, you can manually get a new token from Facebook Developer Console")
            return None
            
        # Request new access token
        url = "https://graph.facebook.com/oauth/access_token"
        params = {
            "grant_type": "client_credentials",
            "client_id": app_id,
            "client_secret": app_secret
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params)
            
            if response.status_code == 200:
                token_data = response.json()
                new_token = token_data.get("access_token")
                print(f"✅ WhatsApp token refreshed successfully")
                return new_token
            else:
                print(f"❌ Failed to refresh WhatsApp token: {response.status_code} - {response.text}")
                return None
                
    except Exception as e:
        print(f"❌ Error refreshing WhatsApp token: {e}")
        return None

async def send_whatsapp_message(recipient_id: str, message: str):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    
    # Get current token from database or environment
    current_token = get_whatsapp_token()
    
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
            
            # Check for token expiration (401 error)
            if response.status_code == 401:
                print(f"🔍 Detected 401 error, checking for token expiration...")
                
                # Try to parse JSON if not already parsed
                if not response_json:
                    try:
                        response_json = response.json()
                        print(f"✅ Parsed error response JSON: {response_json}")
                    except Exception as json_error:
                        print(f"❌ Failed to parse JSON: {json_error}")
                        response_json = None
                
                # Check if it's a token expiration error
                is_token_expired = False
                if response_json and response_json.get("error"):
                    error_code = response_json["error"].get("code")
                    if error_code == 190:  # OAuthException - token expired
                        is_token_expired = True
                        print(f"🔍 Detected token expiration (code 190)")
                elif "Session has expired" in response_text or "access token" in response_text.lower():
                    is_token_expired = True
                    print(f"🔍 Detected token expiration from response text")
                
                if is_token_expired:
                    print("🔄 WhatsApp token expired, attempting to refresh...")
                    new_token = await refresh_whatsapp_token()
                    
                    if new_token:
                        # Save the new token to database for persistence
                        save_whatsapp_token(new_token)
                        
                        # Update the global token and retry the request
                        global WHATSAPP_TOKEN
                        WHATSAPP_TOKEN = new_token
                        headers["Authorization"] = f"Bearer {new_token}"
                        
                        print("🔄 Retrying WhatsApp message with refreshed token...")
                        retry_response = await client.post(url, json=data, headers=headers)
                        
                        if retry_response.status_code == 200:
                            retry_json = retry_response.json()
                            result = {
                                "status": "success", 
                                "status_code": retry_response.status_code, 
                                "response_json": retry_json,
                                "message_id": retry_json.get("messages", [{}])[0].get("id") if retry_json else None,
                                "token_refreshed": True
                            }
                            print(f"✅ WhatsApp message sent successfully after token refresh")
                            return result
                        else:
                            print(f"❌ Failed to send message even after token refresh: {retry_response.status_code}")
                            return {
                                "status": "error", 
                                "status_code": retry_response.status_code, 
                                "response_text": retry_response.text[:500],
                                "response_json": retry_response.json() if retry_response.headers.get("content-type", "").startswith("application/json") else None,
                                "token_refresh_attempted": True
                            }
                    else:
                        print("❌ Failed to refresh WhatsApp token")
                        return {
                            "status": "error", 
                            "status_code": response.status_code, 
                            "response_text": response_text[:500],
                            "response_json": response_json,
                            "token_refresh_failed": True
                        }
            
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
                print(f"WhatsApp function returning error result: {result}")
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

def update_whatsapp_token_manual(new_token: str) -> dict:
    """
    Manually update WhatsApp token in database and environment
    Use this when automatic refresh fails or for immediate token updates
    """
    try:
        # Save to database
        if save_whatsapp_token(new_token):
            # Update global variable for current session
            global WHATSAPP_TOKEN
            WHATSAPP_TOKEN = new_token
            
            print("✅ WhatsApp token updated successfully")
            return {
                "status": "success",
                "message": "WhatsApp token updated successfully",
                "token_saved_to_db": True
            }
        else:
            return {
                "status": "error",
                "message": "Failed to save token to database"
            }
    except Exception as e:
        print(f"❌ Error updating WhatsApp token manually: {e}")
        return {
            "status": "error",
            "message": f"Failed to update token: {str(e)}"
        }