import json
import os.path
import pytz
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from dotenv import load_dotenv
from db.mongo import get_all_users, oauth_tokens_collection
from utils.utils import send_whatsapp_message

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

    lines = [f"📅 Events for {date.strftime('%A, %B %d')}:\n"]
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

def start_scheduler():
    def daily_reminder_job():
        print("\n[REMINDER JOB] Starting daily reminder job...")
        tomorrow = (datetime.now(pytz.timezone("Asia/Kuala_Lumpur")) + timedelta(days=1)).date()
        users = get_all_users()
        print(f"[REMINDER JOB] Checking events for {len(users)} users on {tomorrow}")

        for user in users:
            user_id = user.get("user_id")
            print(f"[REMINDER JOB] Fetching events for user_id: {user_id}")
            events = get_events_for_user_on_date(user_id, tomorrow)
            if events:
                message = format_event_reminder(events, tomorrow)
                print(f"[REMINDER JOB] Sending message to user {user_id}:")
                print(message)
                send_whatsapp_message(user_id, message)
            else:
                print(f"[REMINDER JOB] No events to notify for user {user_id}.")

    scheduler.add_job(daily_reminder_job, 'cron', hour=19, minute=00)
    scheduler.start()
    print("\n✅ Scheduler started and daily reminder job registered at 7PM daily.")
