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
import os
from google.cloud import tasks_v2
import pytz
import asyncio
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables first
load_dotenv(dotenv_path=".env.local", override=True)

db_name = os.environ.get("DB_NAME")
db = client[db_name]  # Using same database as existing oauth collections
reminders_collection = db["reminders"]

SCOPES = json.loads(os.getenv("SCOPES", "[]"))

class AuthRequiredError(Exception):
    pass

async def create_custom_reminder(message: str, remind_in: str, user_id=None, phone_number=None) -> dict:
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
    
    # Try to parse the natural language time with different strategies
    parsing_strategies = [
        # Strategy 1: Default settings
        {
            "TIMEZONE": "Asia/Kuala_Lumpur",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "RELATIVE_BASE": now,
        },
        # Strategy 2: Prefer current period (today)
        {
            "TIMEZONE": "Asia/Kuala_Lumpur", 
            "RETURN_AS_TIMEZONE_AWARE": True,
            "RELATIVE_BASE": now,
            "PREFER_DATES_FROM": "current_period"
        },
        # Strategy 3: Prefer future
        {
            "TIMEZONE": "Asia/Kuala_Lumpur",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "RELATIVE_BASE": now,
            "PREFER_DATES_FROM": "future"
        }
    ]
    
    reminder_time = None
    for i, settings in enumerate(parsing_strategies):
        print(f"[DEBUG] Trying parsing strategy {i+1} with settings: {settings}")
        reminder_time = dateparser.parse(remind_in_normalized, settings=settings)
        print(f"[DEBUG] Strategy {i+1} result: {reminder_time}")
        
        if reminder_time:
            # If we got a result but it's in the past, continue to next strategy
            if reminder_time <= now:
                print(f"[DEBUG] Strategy {i+1} time is in past, trying next strategy")
                continue
            else:
                print(f"[DEBUG] Strategy {i+1} successful - time is in future")
                break
    
    # If normalized input didn't work, try the original input
    if not reminder_time:
        print(f"[DEBUG] Normalized input failed, trying original input: '{remind_in}'")
        for i, settings in enumerate(parsing_strategies):
            print(f"[DEBUG] Trying original input with strategy {i+1}")
            reminder_time = dateparser.parse(remind_in, settings=settings)
            print(f"[DEBUG] Original input strategy {i+1} result: {reminder_time}")
            
            if reminder_time:
                if reminder_time <= now:
                    print(f"[DEBUG] Original input strategy {i+1} time is in past, trying next")
                    continue
                else:
                    print(f"[DEBUG] Original input strategy {i+1} successful")
                    break
    
    # Manual fallback for common time patterns that dateparser might miss
    if not reminder_time:
        print(f"[DEBUG] All dateparser strategies failed, trying manual parsing")
        import re
        
        # Try to match common time patterns like "6pm", "6:30pm", "18:00", etc.
        time_pattern = r'^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$'
        match = re.match(time_pattern, remind_in.lower().strip())
        
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2)) if match.group(2) else 0
            am_pm = match.group(3)
            
            # Convert to 24-hour format
            if am_pm == 'pm' and hour != 12:
                hour += 12
            elif am_pm == 'am' and hour == 12:
                hour = 0
                
            print(f"[DEBUG] Manual parsing - hour: {hour}, minute: {minute}")
            
            # Create datetime for today at the specified time
            try:
                reminder_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                print(f"[DEBUG] Manual parsing result (today): {reminder_time}")
                
                # If it's in the past, try tomorrow
                if reminder_time <= now:
                    reminder_time = reminder_time + timedelta(days=1)
                    print(f"[DEBUG] Time was in past, using tomorrow: {reminder_time}")
                    
            except ValueError as e:
                print(f"[DEBUG] Manual parsing failed: {e}")
                reminder_time = None
    
    print(f"[DEBUG] Final parsed time: {reminder_time}")
    print(f"[DEBUG] Current time: {now}")
    
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
    
    result = await reminders_collection.insert_one(reminder_data)
    reminder_id = str(result.inserted_id)

    # Instead of guna send_reminder, move send_reminder jadi consumer

    enqueue_reminder_task(reminder_id, reminder_time)
    
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

def  enqueue_reminder_task(reminder_id: str, reminder_time: datetime):
    client = tasks_v2.CloudTasksClient()

    project = os.getenv("GOOGLE_PROJECT_ID")
    queue = os.getenv("QUEUE_ID")
    location = os.getenv("QUEUE_LOCATION")
    url = os.getenv("REMINDER_HANDLER_URL")

    # Construct queue path
    parent = client.queue_path(project, location, queue)

    # Convert reminder_time to UTC
    reminder_time_utc = reminder_time.astimezone(pytz.UTC)

    # Build task payload
    payload = json.dumps({"reminder_id": reminder_id})

    # Configure scheduled task
    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": url,
            "headers": {"Content-Type": "application/json"},
            "body": payload.encode(),
        },
        "schedule_time": reminder_time_utc
    }

    response = client.create_task(request={"parent": parent, "task": task})
    print(f"✅ Task scheduled for {reminder_time} — ID: {response.name}")

async def create_event_reminder(event_title: str, event_start_time: datetime, user_id: str, phone_number: str, minutes_before: int = 15) -> dict:
    """
    Creates a reminder for a calendar event.
    
    Args:
        event_title: The title of the event
        event_start_time: The start time of the event (timezone-aware datetime)
        user_id: User ID for reminder delivery
        phone_number: User's phone number for WhatsApp delivery
        minutes_before: How many minutes before the event to remind (default: 15)
    
    Returns:
        dict: Result with status and reminder details
    """
    try:
        tz = pytz.timezone("Asia/Kuala_Lumpur")
        now = datetime.now(tz)
        
        # Calculate reminder time (minutes before event)
        reminder_time = event_start_time - timedelta(minutes=minutes_before)
        
        # Only create reminder if it's in the future
        if reminder_time <= now:
            print(f"[EVENT REMINDER] Skipping reminder for '{event_title}' - event is too soon or has passed")
            return {
                "status": "skipped",
                "message": f"Event is less than {minutes_before} minutes away, no reminder created"
            }
        
        # Store reminder in database
        reminder_data = {
            "user_id": user_id,
            "phone_number": phone_number,
            "type": "event_reminder",
            "event_title": event_title,
            "minutes_before": minutes_before,
            "message": f"⏰ Reminder: Your event '{event_title}' starts in {minutes_before} minutes at {event_start_time.strftime('%I:%M %p')}",
            "reminder_time": reminder_time,
            "event_start_time": event_start_time,
            "status": "scheduled",
            "created_at": now
        }
        
        result = await reminders_collection.insert_one(reminder_data)
        reminder_id = str(result.inserted_id)
        
        # Enqueue the reminder task
        enqueue_reminder_task(reminder_id, reminder_time)
        
        print(f"✅ Event reminder created for '{event_title}' at {reminder_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        
        return {
            "status": "success",
            "message": f"Reminder set for {minutes_before} minutes before event",
            "reminder_id": reminder_id,
            "reminder_time": reminder_time.strftime('%Y-%m-%d %H:%M:%S %Z')
        }
        
    except Exception as e:
        print(f"[EVENT REMINDER ERROR] Failed to create reminder for '{event_title}': {e}")
        return {
            "status": "error",
            "message": f"Failed to create reminder: {str(e)}"
        }

async def send_reminder(reminder_id: str):
    """
    Sends a reminder message to the user.
    This function is called by the scheduler when a reminder is due.
    """
    try:
        # Get reminder data
        reminder = await reminders_collection.find_one({"_id": ObjectId(reminder_id)})
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
        await reminders_collection.update_one(
            {"_id": ObjectId(reminder_id)},
            {"$set": {"status": "sent", "sent_at": datetime.now(pytz.timezone("Asia/Kuala_Lumpur"))}}
        )
        
        print(f"[REMINDER] Successfully sent reminder {reminder_id}")
        
    except Exception as e:
        print(f"[REMINDER ERROR] Failed to send reminder {reminder_id}: {e}")
        # Mark reminder as failed
        await reminders_collection.update_one(
            {"_id": ObjectId(reminder_id)},
            {"$set": {"status": "failed", "error": str(e)}}
        )

async def list_reminders(user_id=None) -> dict:
    """
    Lists all scheduled reminders for a user.
    """
    if user_id is None:
        raise ValueError("Missing user_id in list_reminders() call!")
    
    now = datetime.now(pytz.timezone("Asia/Kuala_Lumpur"))
    
    # Get all active reminders for the user
    cursor = reminders_collection.find({
        "user_id": user_id,
        "status": "scheduled",
        "reminder_time": {"$gte": now}
    }).sort("reminder_time", 1)
    reminders = await cursor.to_list(length=None)
    
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