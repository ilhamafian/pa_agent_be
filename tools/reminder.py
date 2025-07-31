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
from pymongo import MongoClient
from apscheduler.schedulers.background import BackgroundScheduler
from utils.utils import send_whatsapp_message, event_loop
from bson import ObjectId

load_dotenv()

# MongoDB setup for reminders
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client["oauth_db"]  # Using same database as existing oauth collections
reminders_collection = db["reminders"]

SCOPES = json.loads(os.getenv("SCOPES", "[]"))

class AuthRequiredError(Exception):
    pass

def create_event_reminder(event_title: str, minutes_before: int = 30, user_id=None, event_date: str = None, event_time: str = None) -> dict:
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
        service = build("calendar", "v3", credentials=creds)
        
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

def create_custom_reminder(message: str, remind_in: str, user_id=None) -> dict:
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
    
    # Try to parse the natural language time
    reminder_time = dateparser.parse(
        remind_in,
        settings={
            "TIMEZONE": "Asia/Kuala_Lumpur",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "RELATIVE_BASE": now
        }
    )
    
    if not reminder_time:
        return {"status": "error", "message": f"❌ Sorry, I couldn't understand the time '{remind_in}'. Please try something like '3 hours', 'tomorrow at 9am', or 'in 30 minutes'."}
    
    if reminder_time <= now:
        return {"status": "error", "message": "❌ The reminder time must be in the future."}
    
    # Store reminder in database
    reminder_data = {
        "user_id": user_id,
        "type": "custom_reminder",
        "message": f"⏰ Reminder: {message}",
        "reminder_time": reminder_time,
        "original_time_input": remind_in,
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
        
        user_id = reminder["user_id"]
        message = reminder["message"]
        
        print(f"[REMINDER] Sending reminder to user {user_id}: {message}")
        
        # Send WhatsApp message
        asyncio.run_coroutine_threadsafe(
            send_whatsapp_message(user_id, message),
            event_loop
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
                    "description": "When to send the reminder in natural language (e.g., '3 hours', 'tomorrow at 9am', 'in 30 minutes', '2 days from now')"
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