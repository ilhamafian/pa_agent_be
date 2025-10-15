import json
import os
import os.path
import pytz
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from db.mongo import db  # Added db import for calendar collection

load_dotenv(dotenv_path=".env.local", override=True)

# MongoDB calendar collection
calendar_collection = db["calendar"]

now = datetime.now(ZoneInfo("Asia/Kuala_Lumpur"))
today_str = now.strftime("%Y-%m-%d")
tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")

async def get_events_for_user_on_date(user_id, target_date):
    """
    Fetch events for a user on a specific date from MongoDB.
    
    Returns:
        tuple: (events_list, token_expired_flag)
        - events_list: List of events (empty if none or error)
        - token_expired_flag: Boolean indicating if token is expired (always False for MongoDB)
    """
    
    # ============ MONGODB IMPLEMENTATION ============
    print("\n[EVENTS FETCH] user_id:", user_id)
    print("[EVENTS FETCH] target_date:", target_date)
    
    try:
        tz = pytz.timezone("Asia/Kuala_Lumpur")
        start_time = tz.localize(datetime.combine(target_date, datetime.min.time()))
        end_time = tz.localize(datetime.combine(target_date, datetime.max.time()))
        
        # Query MongoDB for events within the date range
        cursor = await calendar_collection.find({
            "user_id": user_id,
            "start_time": {"$gte": start_time, "$lte": end_time}
        }).sort("start_time", 1)  # Sort by start_time ascending
        
        events = []
        async for event in cursor:
            events.append(event)
        
        print(f"[EVENTS FETCH] {len(events)} events fetched from MongoDB.")
        
        # Convert MongoDB events to match Google Calendar format for compatibility
        formatted_events = []
        for event in events:
            formatted_event = {
                "summary": event.get("summary", "No Title"),
                "start": event.get("start", {}),
                "end": event.get("end", {}),
                "description": event.get("description", "")
            }
            formatted_events.append(formatted_event)
        
        return formatted_events, False  # Success, no token issues (MongoDB doesn't use tokens)
    
    except Exception as e:
        print(f"[ERROR] Failed to fetch events from MongoDB for user {user_id}: {e}")
        return [], False

async def format_event_reminder(events, date):
    if not events:
        return f"📅 You have no events on {date.strftime('%A, %B %d')}."

    lines = [f"📅 Upcoming events on {date.strftime('%A, %B %d')}:\n"]
    for event in events:
        title = event.get("summary", "No Title")
        start = event["start"].get("dateTime", event["start"].get("date"))
        end = event["end"].get("dateTime", event["end"].get("date"))

        try:
            start_dt = datetime.fromisoformat(start)
            end_dt = datetime.fromisoformat(end)
            time_range = f"{start_dt.strftime('%-I:%M%p')} - {end_dt.strftime('%-I:%M%p')}"
        except ValueError:
            time_range = "All-day"

        lines.append(f"• {title} ({time_range})")

    return "\n".join(lines)

async def format_task_reminder(tasks):
    """Format pending and in-progress tasks for daily reminder"""
    if not tasks:
        return ""
    
    lines = ["📝 Your pending tasks:\n"]
    for task in tasks:
        title = task.get("title", "No Title")
        status = task.get("status", "pending")
        priority = task.get("priority", "medium")
        
        # Priority emojis
        priority_emoji = "🔴" if priority == "high" else "🟡" if priority == "medium" else "🟢"
        
        # Status emojis
        status_emoji = "⏳" if status == "in_progress" else "📋"
        
        status_text = "In Progress" if status == "in_progress" else "Pending"
        lines.append(f"{status_emoji} {priority_emoji} {title} ({status_text})")
    
    return "\n".join(lines)

async def format_combined_reminder(events, tasks, nickname, is_tomorrow=True):
    """Combine events and tasks into a comprehensive daily reminder"""
    lines = []
    
    # Add greeting based on time of day
    if is_tomorrow:
        lines.append(f"Hi {nickname}! Your day is wrapped up! Here's what's coming up for tomorrow:\n")
    else:
        lines.append(f"Good morning {nickname}! Here's what you have planned for today:\n")
    
    # Add events section
    if events:
        event_header = "📅 *Tomorrow's Events:*" if is_tomorrow else "📅 *Today's Events:*"
        lines.append(event_header)
        for event in events:
            title = event.get("summary", "No Title")
            start = event["start"].get("dateTime", event["start"].get("date"))
            end = event["end"].get("dateTime", event["end"].get("date"))

            try:
                start_dt = datetime.fromisoformat(start)
                end_dt = datetime.fromisoformat(end)
                time_range = f"{start_dt.strftime('%-I:%M%p')} - {end_dt.strftime('%-I:%M%p')}"
            except ValueError:
                time_range = "All-day"

            lines.append(f"• {title} ({time_range})")
        lines.append("")  # Empty line for spacing
    
    # Add tasks section
    if tasks:
        lines.append(f"📝 *Tasks to Focus On:*")
        for task in tasks:
            title = task.get("title", "No Title")
            status = task.get("status", "pending")
            priority = task.get("priority", "medium")
            
            # Priority emojis
            priority_emoji = "🔴" if priority == "high" else "🟡" if priority == "medium" else "🟢"
            
            # Status emojis
            # status_emoji = "⏳" if status == "in_progress" else "📋"
            
            status_text = "In Progress" if status == "in_progress" else "Pending"
            # lines.append(f"{status_emoji} {priority_emoji} {title} ({status_text})")
            lines.append(f"{priority_emoji} {title} ({status_text})")
    
    # Add motivational footer
    if events or tasks:
        footer_message = "\nHave a productive day!" if is_tomorrow else "\nLet's make today productive!"
        lines.append(footer_message)
    else:
        if is_tomorrow:
            lines.append("🎉 You have a free day with no scheduled events or pending tasks!")
        else:
            lines.append("🎉 You have a free day today with no scheduled events or pending tasks!")
    
    return "\n".join(lines)

async def start_scheduler():
    """
    Initialize Cloud Tasks for daily reminders.
    Schedules recurring tasks for today and tomorrow reminders.
    """
    from utils.cloud_tasks import schedule_daily_task
    
    app_url = os.getenv("APP_URL")
    
    # Schedule daily reminders using Cloud Tasks
    try:
        # Schedule today's reminder at 8:30 AM
        today_url = f"{app_url}/reminder/daily/today"
        schedule_daily_task(
            endpoint_url=today_url,
            hour=8,
            minute=30,
            timezone_str="Asia/Kuala_Lumpur"
        )
        
        # Schedule tomorrow's reminder at 7:30 PM
        tomorrow_url = f"{app_url}/reminder/daily/tomorrow"
        schedule_daily_task(
            endpoint_url=tomorrow_url,
            hour=21,
            minute=27,
            timezone_str="Asia/Kuala_Lumpur"
        )
        
        print("\n✅ Cloud Tasks scheduler initialized with:")
        print("   • Today's reminder at 8:30 AM")
        print("   • Tomorrow's reminder at 7:30 PM")
    except Exception as e:
        print(f"❌ Failed to schedule daily tasks: {e}")
        raise
    
    print("✅ Cloud Tasks scheduler initialized successfully")