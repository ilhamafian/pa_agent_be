import json
import os.path
import pytz
import dateparser
import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from db.mongo import oauth_tokens_collection
from utils.utils import send_whatsapp_message, get_event_loop
from bson import ObjectId
from db.mongo import client

# Load environment variables first
load_dotenv()

db = client["oauth_db"]  # Using same database as existing oauth collections
reminders_collection = db["reminders"]

SCOPES = json.loads(os.getenv("SCOPES", "[]"))

class AuthRequiredError(Exception):
    pass

def create_event_reminder(event_title: str, minutes_before: int = 30, user_id=None, phone_number=None, event_date: str = None, event_time: str = None) -> dict:
    """
    Creates a reminder for an existing calendar event.
    
    Args:
        event_title: Title of the calendar event to remind about
        minutes_before: How many minutes before the event to send reminder (default: 30)
        user_id: User ID for authentication and reminder delivery
        event_date: Date of the event (YYYY-MM-DD format)
        event_time: Time of the event (HH:MM format)
    """
    
    if user_id is None:
        raise ValueError("Missing user_id in create_event_reminder() call!")
    
    # Check if user has authentication
    token_data = oauth_tokens_collection.find_one({"user_id": user_id})
    if not token_data:
        raise AuthRequiredError("AUTH_REQUIRED")
    
    # Calculate reminder time
    if event_date and event_time:
        event_datetime_str = f"{event_date}T{event_time}:00"
        event_datetime = datetime.fromisoformat(event_datetime_str)
        # Convert to Kuala Lumpur timezone
        tz = pytz.timezone("Asia/Kuala_Lumpur")
        event_datetime = tz.localize(event_datetime)
    else:
        # If no specific time provided, we'll need to find the event in their calendar
        creds = Credentials.from_authorized_user_info(token_data["token"], SCOPES)
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        
        # Search for the event in their calendar (next 30 days)
        now = datetime.now(pytz.timezone("Asia/Kuala_Lumpur"))
        time_max = now + timedelta(days=30)
        
        events_result = service.events().list(
            calendarId='primary',
            timeMin=now.isoformat(),
            timeMax=time_max.isoformat(),
            q=event_title,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get("items", [])
        if not events:
            return {"status": "error", "message": f"Could not find event '{event_title}' in your calendar. Please specify the date and time manually."}
        
        # Use the first matching event
        event = events[0]
        start = event["start"].get("dateTime", event["start"].get("date"))
        
        try:
            event_datetime = datetime.fromisoformat(start)
            if not event_datetime.tzinfo:
                tz = pytz.timezone("Asia/Kuala_Lumpur")
                event_datetime = tz.localize(event_datetime)
        except ValueError:
            return {"status": "error", "message": f"Found event '{event_title}' but it appears to be an all-day event. Please specify a specific time for the reminder."}
    
    # Calculate reminder time
    reminder_time = event_datetime - timedelta(minutes=minutes_before)
    
    # Store reminder in database
    reminder_data = {
        "user_id": user_id,
        "phone_number": phone_number,
        "type": "event_reminder",
        "event_title": event_title,
        "event_datetime": event_datetime,
        "reminder_time": reminder_time,
        "minutes_before": minutes_before,
        "message": f"⏰ Reminder: '{event_title}' starts in {minutes_before} minutes!",
        "status": "scheduled",
        "created_at": datetime.now(pytz.timezone("Asia/Kuala_Lumpur"))
    }
    
    result = reminders_collection.insert_one(reminder_data)
    reminder_id = str(result.inserted_id)
    
    # Schedule the reminder
    from tools.scheduler import scheduler
    scheduler.add_job(
        send_reminder,
        'date',
        run_date=reminder_time,
        args=[reminder_id],
        id=f"reminder_{reminder_id}",
        misfire_grace_time=300  # 5 minutes grace time
    )
    
    return {
        "status": "success",
        "message": f"✅ Reminder created! You'll be notified {minutes_before} minutes before '{event_title}' on {event_datetime.strftime('%B %d, %Y at %I:%M %p')}",
        "reminder_id": reminder_id,
        "reminder_time": reminder_time.strftime('%Y-%m-%d %H:%M:%S %Z')
    }

def create_custom_reminder(message: str, remind_in: str, user_id=None, phone_number=None) -> dict:
    """
    Creates a custom reminder for a specific time.
    
    Args:
        message: The reminder message to send
        remind_in: Natural language time (e.g., "3 hours", "tomorrow at 9am", "in 30 minutes")
        user_id: User ID for reminder delivery
    """
    
    if user_id is None:
        raise ValueError("Missing user_id in create_custom_reminder() call!")
    
    # Parse the time
    tz = pytz.timezone("Asia/Kuala_Lumpur")
    now = datetime.now(tz)
    
    # Preprocess the remind_in text to normalize common phrases
    remind_in_lower = remind_in.lower().strip()
    
    # Handle common variations
    if remind_in_lower.startswith("the next "):
        remind_in_normalized = remind_in_lower.replace("the next ", "in ")
    elif remind_in_lower.startswith("next "):
        remind_in_normalized = remind_in_lower.replace("next ", "in ")
    else:
        remind_in_normalized = remind_in_lower
    
    # Replace "mins" with "minutes"
    if "mins" in remind_in_normalized and "minutes" not in remind_in_normalized:
        remind_in_normalized = remind_in_normalized.replace("mins", "minutes")
    
    # Handle special cases
    if remind_in_normalized == "in hour":
        remind_in_normalized = "in 1 hour"
    elif remind_in_normalized == "in minute":
        remind_in_normalized = "in 1 minute"
    
    # Ensure "in" prefix for relative times
    if remind_in_normalized.endswith(" minutes") or remind_in_normalized.endswith(" hours") or remind_in_normalized.endswith(" days"):
        if not remind_in_normalized.startswith("in "):
            remind_in_normalized = "in " + remind_in_normalized
    
    print(f"[DEBUG] Original time input: '{remind_in}'")
    print(f"[DEBUG] Normalized time input: '{remind_in_normalized}'")
    
    # Try to parse the natural language time
    # First try with PREFER_DATES_FROM: "current_period" to prefer today for times like "6pm"
    reminder_time = dateparser.parse(
        remind_in_normalized,
        settings={
            "TIMEZONE": "Asia/Kuala_Lumpur",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "RELATIVE_BASE": now,
            "PREFER_DATES_FROM": "current_period"
        }
    )
    
    # If the parsed time is in the past, try again with "future" preference
    if reminder_time and reminder_time <= now:
        print(f"[DEBUG] Time is in past, trying with future preference: {reminder_time}")
        reminder_time = dateparser.parse(
            remind_in_normalized,
            settings={
                "TIMEZONE": "Asia/Kuala_Lumpur",
                "RETURN_AS_TIMEZONE_AWARE": True,
                "RELATIVE_BASE": now,
                "PREFER_DATES_FROM": "future"
            }
        )
    
    print(f"[DEBUG] Parsed time: {reminder_time}")
    print(f"[DEBUG] Current time: {now}")
    
    if not reminder_time:
        # Try the original input if normalization didn't work
        reminder_time = dateparser.parse(
            remind_in,
            settings={
                "TIMEZONE": "Asia/Kuala_Lumpur",
                "RETURN_AS_TIMEZONE_AWARE": True,
                "RELATIVE_BASE": now,
                "PREFER_DATES_FROM": "current_period"
            }
        )
        
        # If the parsed time is in the past, try again with "future" preference  
        if reminder_time and reminder_time <= now:
            print(f"[DEBUG] Fallback time is in past, trying with future preference: {reminder_time}")
            reminder_time = dateparser.parse(
                remind_in,
                settings={
                    "TIMEZONE": "Asia/Kuala_Lumpur",
                    "RETURN_AS_TIMEZONE_AWARE": True,
                    "RELATIVE_BASE": now,
                    "PREFER_DATES_FROM": "future"
                }
            )
        
        print(f"[DEBUG] Fallback parsed time: {reminder_time}")
    
    if not reminder_time:
        return {"status": "error", "message": f"❌ Sorry, I couldn't understand the time '{remind_in}'. Please try something like '3 hours', 'tomorrow at 9am', or 'in 30 minutes'."}
    
    # Add a small buffer to ensure the time is in the future (to handle millisecond precision issues)
    if reminder_time <= now + timedelta(seconds=1):
        print(f"[DEBUG] Time not in future - reminder_time: {reminder_time}, now: {now}")
        return {"status": "error", "message": f"❌ The reminder time must be in the future. I interpreted '{remind_in}' as {reminder_time.strftime('%Y-%m-%d %H:%M:%S %Z')}, but the current time is {now.strftime('%Y-%m-%d %H:%M:%S %Z')}."}
    
    # Store reminder in database
    reminder_data = {
        "user_id": user_id,
        "phone_number": phone_number,
        "type": "custom_reminder",
        "message": f"⏰ Reminder: {message}",
        "reminder_time": reminder_time,
        "original_time_input": remind_in,
        "normalized_time_input": remind_in_normalized,
        "status": "scheduled",
        "created_at": now
    }
    
    result = reminders_collection.insert_one(reminder_data)
    reminder_id = str(result.inserted_id)
    
    # Schedule the reminder
    from tools.scheduler import scheduler
    scheduler.add_job(
        send_reminder,
        'date',
        run_date=reminder_time,
        args=[reminder_id],
        id=f"reminder_{reminder_id}",
        misfire_grace_time=300  # 5 minutes grace time
    )
    
    time_until = reminder_time - now
    if time_until.days > 0:
        time_str = f"{time_until.days} days and {time_until.seconds // 3600} hours"
    elif time_until.seconds >= 3600:
        hours = time_until.seconds // 3600
        minutes = (time_until.seconds % 3600) // 60
        time_str = f"{hours} hours and {minutes} minutes"
    else:
        minutes = time_until.seconds // 60
        time_str = f"{minutes} minutes"
    
    return {
        "status": "success",
        "message": f"✅ Reminder set! I'll remind you about '{message}' in {time_str} ({reminder_time.strftime('%B %d, %Y at %I:%M %p')})",
        "reminder_id": reminder_id,
        "reminder_time": reminder_time.strftime('%Y-%m-%d %H:%M:%S %Z')
    }

def send_reminder(reminder_id: str):
    """
    Sends a reminder message to the user.
    This function is called by the scheduler when a reminder is due.
    """
    try:
        # Get reminder data
        reminder = reminders_collection.find_one({"_id": ObjectId(reminder_id)})
        if not reminder:
            print(f"[REMINDER ERROR] Reminder {reminder_id} not found in database")
            return
        
        phone_number = reminder["phone_number"]
        message = reminder["message"]
        
        print(f"[REMINDER] Sending reminder to user {phone_number}: {message}")
        
        # Send WhatsApp message
        loop = get_event_loop()
        if loop:
            asyncio.run_coroutine_threadsafe(
                send_whatsapp_message(phone_number, message),
                loop
            )
        
        # Mark reminder as sent
        reminders_collection.update_one(
            {"_id": ObjectId(reminder_id)},
            {"$set": {"status": "sent", "sent_at": datetime.now(pytz.timezone("Asia/Kuala_Lumpur"))}}
        )
        
        print(f"[REMINDER] Successfully sent reminder {reminder_id}")
        
    except Exception as e:
        print(f"[REMINDER ERROR] Failed to send reminder {reminder_id}: {e}")
        # Mark reminder as failed
        reminders_collection.update_one(
            {"_id": ObjectId(reminder_id)},
            {"$set": {"status": "failed", "error": str(e)}}
        )

def list_reminders(user_id=None) -> dict:
    """
    Lists all scheduled reminders for a user.
    """
    if user_id is None:
        raise ValueError("Missing user_id in list_reminders() call!")
    
    now = datetime.now(pytz.timezone("Asia/Kuala_Lumpur"))
    
    # Get all active reminders for the user
    reminders = list(reminders_collection.find({
        "user_id": user_id,
        "status": "scheduled",
        "reminder_time": {"$gte": now}
    }).sort("reminder_time", 1))
    
    if not reminders:
        return {"status": "success", "message": "📅 You have no scheduled reminders."}
    
    lines = ["📅 Your scheduled reminders:\n"]
    for i, reminder in enumerate(reminders, 1):
        reminder_time = reminder["reminder_time"]
        time_until = reminder_time - now
        
        if reminder["type"] == "event_reminder":
            event_title = reminder["event_title"]
            minutes_before = reminder["minutes_before"]
            desc = f"Remind {minutes_before} min before '{event_title}'"
        else:
            desc = reminder["message"].replace("⏰ Reminder: ", "")
        
        if time_until.days > 0:
            time_str = f"in {time_until.days}d {time_until.seconds // 3600}h"
        elif time_until.seconds >= 3600:
            time_str = f"in {time_until.seconds // 3600}h {(time_until.seconds % 3600) // 60}m"
        else:
            time_str = f"in {time_until.seconds // 60}m"
        
        lines.append(f"{i}. {desc} ({time_str})")
    
    return {"status": "success", "message": "\n".join(lines)}

# Tool definitions for the AI
create_event_reminder_tool = {
    "type": "function",
    "function": {
        "name": "create_event_reminder",
        "description": "Creates a reminder for an existing calendar event. The reminder will be sent before the event starts.",
        "parameters": {
            "type": "object",
            "properties": {
                "event_title": {
                    "type": "string",
                    "description": "Title of the calendar event to remind about"
                },
                "minutes_before": {
                    "type": "integer",
                    "description": "How many minutes before the event to send the reminder (default: 30)",
                    "default": 30
                },
                "event_date": {
                    "type": "string",
                    "description": "Date of the event (YYYY-MM-DD format). Optional if event can be found by title."
                },
                "event_time": {
                    "type": "string",
                    "description": "Time of the event (HH:MM format). Optional if event can be found by title."
                }
            },
            "required": ["event_title"]
        }
    }
}

create_custom_reminder_tool = {
    "type": "function",
    "function": {
        "name": "create_custom_reminder",
        "description": "Creates a custom reminder for any message at a specific time in the future.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The reminder message to send to the user"
                },
                "remind_in": {
                    "type": "string",
                    "description": "When to send the reminder in natural language. IMPORTANT: Pass the user's original time expression, do NOT convert to specific dates. Examples: 'in 30 minutes', '3 hours', 'tomorrow at 9am', '6pm', '30 minutes'. Common user phrases like 'the next 30 mins' should be extracted as '30 minutes', 'next hour' as '1 hour', etc. DO NOT use date formats like '2025-08-26 18:00'."
                }
            },
            "required": ["message", "remind_in"]
        }
    }
}

list_reminders_tool = {
    "type": "function",
    "function": {
        "name": "list_reminders",
        "description": "Lists all scheduled reminders for the user.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
} 