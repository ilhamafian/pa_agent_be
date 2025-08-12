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
from utils.utils import decrypt_phone, send_whatsapp_message, get_event_loop
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
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

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

def format_combined_reminder(events, tasks, nickname, is_tomorrow=True):
    """Combine events and tasks into a comprehensive daily reminder"""
    lines = []
    
    # Add greeting based on time of day
    if is_tomorrow:
        lines.append(f"Hi {nickname}! Your day is wrapped up! Here's what's coming up for tomorrow:\n")
    else:
        lines.append(f"Good morning {nickname}! Here's what you have planned for today:\n")
    
    # Add events section
    if events:
        event_header = "📅 **Tomorrow's Events:**" if is_tomorrow else "📅 **Today's Events:**"
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
        lines.append(f"📝 **Tasks to Focus On:**")
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
        footer_message = "\n Have a productive day!" if is_tomorrow else "\n Let's make today productive!"
        lines.append(footer_message)
    else:
        if is_tomorrow:
            lines.append("🎉 You have a free day with no scheduled events or pending tasks!")
        else:
            lines.append("🎉 You have a free day today with no scheduled events or pending tasks!")
    
    return "\n".join(lines)

def start_scheduler():
    def today_reminder_job():
        try:
            print("\n[TODAY REMINDER JOB] Starting morning reminder job...")
            today = datetime.now(pytz.timezone("Asia/Kuala_Lumpur")).date()
            users = get_all_users() or []
            print(f"[TODAY REMINDER JOB] Checking events and tasks for {len(users)} users on {today}")

            for user in users:
                user_id = user.get("user_id")
                nickname = user.get("nickname")
                encrypted_phone = user.get("phone_number")
                decrypted_phone = decrypt_phone(encrypted_phone)
                print(f"[TODAY REMINDER JOB] Fetching data for user_id: {user_id}")
                
                # Fetch events for today
                events = get_events_for_user_on_date(user_id, today)
                print(f"[TODAY REMINDER JOB] Found {len(events)} events for user {user_id}")
                
                # Fetch pending and in-progress tasks
                try:
                    pending_tasks = get_tasks(user_id, status="pending") or []
                    in_progress_tasks = get_tasks(user_id, status="in_progress") or []
                    all_active_tasks = pending_tasks + in_progress_tasks
                    print(f"[TODAY REMINDER JOB] Found {len(all_active_tasks)} active tasks for user {user_id}")
                except Exception as task_error:
                    print(f"[TODAY REMINDER JOB] Error fetching tasks for user {user_id}: {task_error}")
                    all_active_tasks = []
                
                # Send combined reminder if there are events or tasks
                if events or all_active_tasks:
                    message = format_combined_reminder(events, all_active_tasks, nickname, is_tomorrow=False)
                    print(f"[TODAY REMINDER JOB] Sending combined reminder to user {user_id}:")
                    print(message)
                    loop = get_event_loop()
                    if loop:
                        asyncio.run_coroutine_threadsafe(
                            send_whatsapp_message(decrypted_phone, message),
                            loop
                        )
                else:
                    print(f"[TODAY REMINDER JOB] No events or active tasks to notify for user {user_id}.")
        except Exception as e:
            print(f"🔥 [TODAY REMINDER JOB ERROR] {e}")

    def tomorrow_reminder_job():
        try:
            print("\n[TOMORROW REMINDER JOB] Starting daily reminder job...")
            tomorrow = (datetime.now(pytz.timezone("Asia/Kuala_Lumpur")) + timedelta(days=1)).date()
            users = get_all_users() or []
            print(f"[TOMORROW REMINDER JOB] Checking events and tasks for {len(users)} users on {tomorrow}")

            for user in users:
                user_id = user.get("user_id")
                nickname = user.get("nickname")
                encrypted_phone = user.get("phone_number")
                decrypted_phone = decrypt_phone(encrypted_phone)
                print(f"[TOMORROW REMINDER JOB] Fetching data for user_id: {user_id}")
                
                # Fetch events for tomorrow
                events = get_events_for_user_on_date(user_id, tomorrow)
                print(f"[TOMORROW REMINDER JOB] Found {len(events)} events for user {user_id}")
                
                # Fetch pending and in-progress tasks
                try:
                    pending_tasks = get_tasks(user_id, status="pending") or []
                    in_progress_tasks = get_tasks(user_id, status="in_progress") or []
                    all_active_tasks = pending_tasks + in_progress_tasks
                    print(f"[TOMORROW REMINDER JOB] Found {len(all_active_tasks)} active tasks for user {user_id}")
                except Exception as task_error:
                    print(f"[TOMORROW REMINDER JOB] Error fetching tasks for user {user_id}: {task_error}")
                    all_active_tasks = []
                
                # Send combined reminder if there are events or tasks
                if events or all_active_tasks:
                    message = format_combined_reminder(events, all_active_tasks, nickname, is_tomorrow=True)
                    print(f"[TOMORROW REMINDER JOB] Sending combined reminder to user {user_id}:")
                    print(message)
                    loop = get_event_loop()
                    if loop:
                        asyncio.run_coroutine_threadsafe(
                            send_whatsapp_message(decrypted_phone, message),
                            loop
                        )
                else:
                    print(f"[TOMORROW REMINDER JOB] No events or active tasks to notify for user {user_id}.")
        except Exception as e:
            print(f"🔥 [TOMORROW REMINDER JOB ERROR] {e}")

    # Schedule today's reminder at 9:00 AM
    scheduler.add_job(today_reminder_job, 'cron', hour=9, minute=0)
    # Schedule tomorrow's reminder at 10:40 PM
    scheduler.add_job(tomorrow_reminder_job, 'cron', hour=22, minute=20)
    scheduler.start()
    print("\n✅ Scheduler started with:")
    print("   • Today's reminder at 9:00 AM")
    print("   • Tomorrow's reminder at 10:40 PM")
