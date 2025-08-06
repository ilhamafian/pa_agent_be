import json
import os.path
import pytz
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from dotenv import load_dotenv
from db.mongo import get_all_users, oauth_tokens_collection
from utils.utils import send_whatsapp_message, event_loop
from tools.task import get_tasks

load_dotenv()

SCOPES = json.loads(os.getenv("SCOPES", "[]"))

scheduler = BackgroundScheduler(timezone="Asia/Kuala_Lumpur")
now = datetime.now(ZoneInfo("Asia/Kuala_Lumpur"))
today_str = now.strftime("%Y-%m-%d")
tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")

def get_events_for_user_on_date(user_id, target_date):
    print("\n[EVENTS FETCH] user_id:", user_id)
    print("[EVENTS FETCH] target_date:", target_date)
    token_data = oauth_tokens_collection.find_one({"user_id": user_id})
    if not token_data:
        print("[EVENTS FETCH] No token data found for user.")
        return []

    creds = Credentials.from_authorized_user_info(token_data["token"], SCOPES)
    service = build("calendar", "v3", credentials=creds)

    tz = pytz.timezone("Asia/Kuala_Lumpur")
    start_time = tz.localize(datetime.combine(target_date, datetime.min.time()))
    end_time = tz.localize(datetime.combine(target_date, datetime.max.time()))

    try:
        events_result = service.events().list(
            calendarId='primary',
            timeMin=start_time.isoformat(),
            timeMax=end_time.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get("items", [])
        print(f"[EVENTS FETCH] {len(events)} events fetched.")
        return events
    except Exception as e:
        print(f"[ERROR] Failed to fetch events for user {user_id}: {e}")
        return []

def format_event_reminder(events, date):
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

def format_task_reminder(tasks):
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

def format_combined_reminder(events, tasks, date):
    """Combine events and tasks into a comprehensive daily reminder"""
    lines = []
    
    # Add greeting
    lines.append(f"🌅 Good morning! Here's what's coming up for {date.strftime('%A, %B %d')}:\n")
    
    # Add events section
    if events:
        lines.append(f"📅 **Upcoming Events:**")
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
        lines.append(f"📝 **Tasks to Focus On:**")
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
    
    # Add motivational footer
    if events or tasks:
        lines.append("\n✨ Have a productive day!")
    else:
        lines.append("🎉 You have a free day with no scheduled events or pending tasks!")
    
    return "\n".join(lines)

def start_scheduler():
    def daily_reminder_job():
        try:
            print("\n[REMINDER JOB] Starting daily reminder job...")
            tomorrow = (datetime.now(pytz.timezone("Asia/Kuala_Lumpur")) + timedelta(days=1)).date()
            users = get_all_users() or []
            print(f"[REMINDER JOB] Checking events and tasks for {len(users)} users on {tomorrow}")

            for user in users:
                user_id = user.get("user_id")
                print(f"[REMINDER JOB] Fetching data for user_id: {user_id}")
                
                # Fetch events for tomorrow
                events = get_events_for_user_on_date(user_id, tomorrow)
                print(f"[REMINDER JOB] Found {len(events)} events for user {user_id}")
                
                # Fetch pending and in-progress tasks
                try:
                    pending_tasks = get_tasks(user_id, status="pending") or []
                    in_progress_tasks = get_tasks(user_id, status="in_progress") or []
                    all_active_tasks = pending_tasks + in_progress_tasks
                    print(f"[REMINDER JOB] Found {len(all_active_tasks)} active tasks for user {user_id}")
                except Exception as task_error:
                    print(f"[REMINDER JOB] Error fetching tasks for user {user_id}: {task_error}")
                    all_active_tasks = []
                
                # Send combined reminder if there are events or tasks
                if events or all_active_tasks:
                    message = format_combined_reminder(events, all_active_tasks, tomorrow)
                    print(f"[REMINDER JOB] Sending combined reminder to user {user_id}:")
                    print(message)
                    asyncio.run_coroutine_threadsafe(
                        send_whatsapp_message(user_id, message),
                        event_loop
                    )
                else:
                    print(f"[REMINDER JOB] No events or active tasks to notify for user {user_id}.")
        except Exception as e:
            print(f"🔥 [REMINDER JOB ERROR] {e}")

    scheduler.add_job(daily_reminder_job, 'cron', hour=19, minute=30)
    scheduler.start()
    print("\n✅ Scheduler started and daily reminder job registered at 7:30 PM daily.")
