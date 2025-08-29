import json
import os.path
import pytz
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.exceptions import RefreshError
from dotenv import load_dotenv
from db.mongo import get_all_users, oauth_tokens_collection
from utils.utils import decrypt_phone, send_whatsapp_message, get_event_loop, get_auth_url
from tools.task import get_tasks

load_dotenv()

# Test mode configuration
TEST_MODE = os.getenv("SCHEDULER_TEST_MODE", "false").lower() == "true"

async def mock_send_whatsapp_message(phone_number, message):
    """Mock version of send_whatsapp_message for testing"""
    print(f"\n📱 [TEST MODE] Would send WhatsApp to {phone_number}:")
    print("=" * 60)
    print(message)
    print("=" * 60)
    return {"status": "mock_success", "message_id": "test_12345"}

SCOPES = json.loads(os.getenv("SCOPES", "[]"))

scheduler = BackgroundScheduler(timezone="Asia/Kuala_Lumpur")
now = datetime.now(ZoneInfo("Asia/Kuala_Lumpur"))
today_str = now.strftime("%Y-%m-%d")
tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")

def get_events_for_user_on_date(user_id, target_date):
    """
    Fetch events for a user on a specific date.
    
    Returns:
        tuple: (events_list, token_expired_flag)
        - events_list: List of events (empty if none or error)
        - token_expired_flag: Boolean indicating if token is expired
    """
    print("\n[EVENTS FETCH] user_id:", user_id)
    print("[EVENTS FETCH] target_date:", target_date)
    token_data = oauth_tokens_collection.find_one({"user_id": user_id})
    if not token_data:
        print("[EVENTS FETCH] No token data found for user.")
        return [], False  # No token data is not the same as expired token

    try:
        creds = Credentials.from_authorized_user_info(token_data["token"], SCOPES)
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)

        tz = pytz.timezone("Asia/Kuala_Lumpur")
        start_time = tz.localize(datetime.combine(target_date, datetime.min.time()))
        end_time = tz.localize(datetime.combine(target_date, datetime.max.time()))

        events_result = service.events().list(
            calendarId='primary',
            timeMin=start_time.isoformat(),
            timeMax=end_time.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get("items", [])
        print(f"[EVENTS FETCH] {len(events)} events fetched.")
        return events, False  # Success, no token issues
    
    except (RefreshError, HttpError) as e:
        # Handle expired or invalid tokens
        print(f"[ERROR] Token error for user {user_id}: {e}")
        if "invalid_grant" in str(e) or "Token has been expired or revoked" in str(e):
            print(f"[EVENTS FETCH] Token expired for user {user_id}.")
            return [], True  # Return empty events and token expired flag
        else:
            # Re-raise if it's a different type of error
            print(f"[ERROR] Non-token related error for user {user_id}: {e}")
            return [], False
    except Exception as e:
        # Handle any other unexpected errors
        print(f"[ERROR] Unexpected error fetching events for user {user_id}: {e}")
        # Check if the error message contains token expiration indicators
        if "invalid_grant" in str(e) or "Token has been expired or revoked" in str(e):
            print(f"[EVENTS FETCH] Token expired for user {user_id}.")
            return [], True  # Return empty events and token expired flag
        else:
            print(f"[ERROR] Failed to fetch events for user {user_id}: {e}")
            return [], False

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

def format_token_renewal_message(user_id):
    """Format the second message with renewal link"""
    auth_url = get_auth_url(user_id)
    message = (
        f"Tap here to update: {auth_url}\n"
        f"Thanks! 😊"
    )
    return message

def format_combined_reminder_with_expired_token(tasks, nickname, is_tomorrow=True):
    """Format reminder when token is expired but still show tasks"""
    lines = []
    
    # Add greeting based on time of day
    if is_tomorrow:
        lines.append(f"Hi {nickname}! Your day is wrapped up! Here's what's coming up for tomorrow:\n")
    else:
        lines.append(f"Good morning {nickname}! Here's what you have planned for today:\n")
    
    # Add events section with token expiration notice
    if is_tomorrow:
        lines.append("📅 *Tomorrow's Events:*")
        lines.append("It seems like your calendar token has expired. To keep getting your daily events brief, please update your calendar token.")
    else:
        lines.append("📅 *Today's Events:*")
        lines.append("It seems like your calendar token has expired. To keep getting your daily events brief, please update your calendar token.")
    lines.append("It's a quick 30-second step.")
    lines.append("")  # Empty line for spacing
    
    # Add tasks section
    if tasks:
        lines.append("📝 *Tasks to Focus On:*")
        for task in tasks:
            title = task.get("title", "No Title")
            status = task.get("status", "pending")
            priority = task.get("priority", "medium")
            
            # Priority emojis
            priority_emoji = "🔴" if priority == "high" else "🟡" if priority == "medium" else "🟢"
            
            status_text = "In Progress" if status == "in_progress" else "Pending"
            lines.append(f"{priority_emoji} {title} ({status_text})")
    
    # Add motivational footer
    footer_message = "\nHave a productive day!" if is_tomorrow else "\nLet's make today productive!"
    lines.append(footer_message)
    
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
                
                # Skip user if essential data is missing
                if not user_id or not nickname or not encrypted_phone:
                    print(f"[TODAY REMINDER JOB] Skipping user due to missing data: user_id={user_id}, nickname={nickname}, phone={bool(encrypted_phone)}")
                    continue
                
                try:
                    decrypted_phone = decrypt_phone(encrypted_phone)
                    if not decrypted_phone:
                        print(f"[TODAY REMINDER JOB] Skipping user {user_id} - failed to decrypt phone number")
                        continue
                except Exception as decrypt_error:
                    print(f"[TODAY REMINDER JOB] Error decrypting phone for user {user_id}: {decrypt_error}")
                    continue
                
                print(f"[TODAY REMINDER JOB] Fetching data for user_id: {user_id}")
                
                # Fetch events for today
                events, token_expired = get_events_for_user_on_date(user_id, today)
                print(f"[TODAY REMINDER JOB] Found {len(events)} events for user {user_id}, token_expired: {token_expired}")
                
                # Fetch pending and in-progress tasks
                try:
                    pending_tasks = get_tasks(user_id, status="pending") or []
                    in_progress_tasks = get_tasks(user_id, status="in_progress") or []
                    all_active_tasks = pending_tasks + in_progress_tasks
                    print(f"[TODAY REMINDER JOB] Found {len(all_active_tasks)} active tasks for user {user_id}")
                except Exception as task_error:
                    print(f"[TODAY REMINDER JOB] Error fetching tasks for user {user_id}: {task_error}")
                    all_active_tasks = []
                
                # Choose send function based on test mode
                send_func = mock_send_whatsapp_message if TEST_MODE else send_whatsapp_message
                loop = get_event_loop()
                
                # If token is expired, send combined message with token notice + tasks
                if token_expired:
                    # First message: Combined reminder with token expiration notice and tasks
                    message1 = format_combined_reminder_with_expired_token(all_active_tasks, nickname, is_tomorrow=False)
                    # Second message: Renewal link
                    message2 = format_token_renewal_message(user_id)
                    
                    print(f"[TODAY REMINDER JOB] Sending token expiration reminder with tasks to user {user_id}")
                    
                    if not TEST_MODE:
                        print("Message 1:", message1)
                        print("Message 2:", message2)
                    
                    if loop:
                        try:
                            # Send first message
                            print(f"[TODAY REMINDER JOB] About to send message 1 to {decrypted_phone[:5]}...")
                            future1 = asyncio.run_coroutine_threadsafe(
                                send_func(decrypted_phone, message1),
                                loop
                            )
                            result1 = future1.result(timeout=30)
                            print(f"[TODAY REMINDER JOB] Message 1 sent successfully: {result1}")
                            
                            # Wait 5 seconds between messages to avoid spam detection
                            import time
                            time.sleep(5)
                            
                            # Send second message
                            print(f"[TODAY REMINDER JOB] About to send message 2 to {decrypted_phone[:5]}...")
                            future2 = asyncio.run_coroutine_threadsafe(
                                send_func(decrypted_phone, message2),
                                loop
                            )
                            result2 = future2.result(timeout=30)
                            print(f"[TODAY REMINDER JOB] Message 2 sent successfully: {result2}")
                            
                        except Exception as send_error:
                            print(f"[TODAY REMINDER JOB] Error sending messages to user {user_id}: {send_error}")
                    else:
                        print(f"[TODAY REMINDER JOB] No event loop available for user {user_id}")
                
                # Normal case: token not expired, send regular combined reminder
                elif events or all_active_tasks:
                    message = format_combined_reminder(events, all_active_tasks, nickname, is_tomorrow=False)
                    print(f"[TODAY REMINDER JOB] Sending combined reminder to user {user_id}:")
                    if not TEST_MODE:
                        print(message)
                    
                    if loop:
                        try:
                            future = asyncio.run_coroutine_threadsafe(
                                send_func(decrypted_phone, message),
                                loop
                            )
                            result = future.result(timeout=30)
                            print(f"[TODAY REMINDER JOB] Combined reminder sent successfully to user {user_id}: {result}")
                        except Exception as send_error:
                            print(f"[TODAY REMINDER JOB] Error sending combined reminder to user {user_id}: {send_error}")
                    else:
                        print(f"[TODAY REMINDER JOB] No event loop available for user {user_id}")
                else:
                    print(f"[TODAY REMINDER JOB] No events or active tasks to notify for user {user_id}.")
                
                print(f"[TODAY REMINDER JOB] Completed processing for user {user_id}")
            
            print(f"[TODAY REMINDER JOB] Finished processing all {len(users)} users")
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
                
                print(f"[TOMORROW REMINDER JOB] Processing user: {user_id}")
                
                # Skip user if essential data is missing
                if not user_id or not nickname or not encrypted_phone:
                    print(f"[TOMORROW REMINDER JOB] Skipping user due to missing data: user_id={user_id}, nickname={nickname}, phone={bool(encrypted_phone)}")
                    continue
                
                try:
                    decrypted_phone = decrypt_phone(encrypted_phone)
                    if not decrypted_phone:
                        print(f"[TOMORROW REMINDER JOB] Skipping user {user_id} - failed to decrypt phone number")
                        continue
                except Exception as decrypt_error:
                    print(f"[TOMORROW REMINDER JOB] Error decrypting phone for user {user_id}: {decrypt_error}")
                    continue
                
                print(f"[TOMORROW REMINDER JOB] Fetching data for user_id: {user_id}")
                
                # Fetch events for tomorrow
                events, token_expired = get_events_for_user_on_date(user_id, tomorrow)
                print(f"[TOMORROW REMINDER JOB] Found {len(events)} events for user {user_id}, token_expired: {token_expired}")
                
                # Fetch pending and in-progress tasks
                try:
                    pending_tasks = get_tasks(user_id, status="pending") or []
                    in_progress_tasks = get_tasks(user_id, status="in_progress") or []
                    all_active_tasks = pending_tasks + in_progress_tasks
                    print(f"[TOMORROW REMINDER JOB] Found {len(all_active_tasks)} active tasks for user {user_id}")
                except Exception as task_error:
                    print(f"[TOMORROW REMINDER JOB] Error fetching tasks for user {user_id}: {task_error}")
                    all_active_tasks = []
                
                # Choose send function based on test mode
                send_func = mock_send_whatsapp_message if TEST_MODE else send_whatsapp_message
                loop = get_event_loop()
                print(f"[TOMORROW REMINDER JOB] Event loop status: {loop is not None}, running: {loop.is_running() if loop else 'N/A'}")
                
                # If token is expired, send combined message with token notice + tasks
                if token_expired:
                    # First message: Combined reminder with token expiration notice and tasks
                    message1 = format_combined_reminder_with_expired_token(all_active_tasks, nickname, is_tomorrow=True)
                    # Second message: Renewal link
                    message2 = format_token_renewal_message(user_id)
                    
                    print(f"[TOMORROW REMINDER JOB] Sending token expiration reminder with tasks to user {user_id}")
                    
                    if not TEST_MODE:
                        print("Message 1:", message1)
                        print("Message 2:", message2)
                    
                    if loop:
                        try:
                            # Send first message
                            print(f"[TOMORROW REMINDER JOB] About to send message 1 to {decrypted_phone[:5]}...")
                            future1 = asyncio.run_coroutine_threadsafe(
                                send_func(decrypted_phone, message1),
                                loop
                            )
                            result1 = future1.result(timeout=30)
                            print(f"[TOMORROW REMINDER JOB] Message 1 sent successfully: {result1}")
                            
                            # Wait 5 seconds between messages to avoid spam detection
                            import time
                            time.sleep(5)
                            
                            # Send second message
                            print(f"[TOMORROW REMINDER JOB] About to send message 2 to {decrypted_phone[:5]}...")
                            future2 = asyncio.run_coroutine_threadsafe(
                                send_func(decrypted_phone, message2),
                                loop
                            )
                            result2 = future2.result(timeout=30)
                            print(f"[TOMORROW REMINDER JOB] Message 2 sent successfully: {result2}")
                            
                        except asyncio.TimeoutError:
                            print(f"[TOMORROW REMINDER JOB] Timeout error sending messages to user {user_id}")
                        except Exception as send_error:
                            print(f"[TOMORROW REMINDER JOB] Error sending messages to user {user_id}")
                            print(f"[TOMORROW REMINDER JOB] Error type: {type(send_error).__name__}")
                            print(f"[TOMORROW REMINDER JOB] Error details: {str(send_error)}")
                            import traceback
                            print(f"[TOMORROW REMINDER JOB] Full traceback: {traceback.format_exc()}")
                    else:
                        print(f"[TOMORROW REMINDER JOB] No event loop available for user {user_id}")
                
                # Normal case: token not expired, send regular combined reminder
                elif events or all_active_tasks:
                    message = format_combined_reminder(events, all_active_tasks, nickname, is_tomorrow=True)
                    print(f"[TOMORROW REMINDER JOB] Sending combined reminder to user {user_id}:")
                    if not TEST_MODE:
                        print(message)
                    
                    if loop:
                        try:
                            print(f"[TOMORROW REMINDER JOB] About to send combined reminder to {decrypted_phone[:5]}...")
                            future = asyncio.run_coroutine_threadsafe(
                                send_func(decrypted_phone, message),
                                loop
                            )
                            print(f"[TOMORROW REMINDER JOB] Combined reminder coroutine submitted, waiting for result...")
                            result = future.result(timeout=30)
                            print(f"[TOMORROW REMINDER JOB] Received combined reminder result: {result}")
                            print(f"[TOMORROW REMINDER JOB] Combined reminder sent successfully to user {user_id}")
                            if result and result.get("message_id"):
                                print(f"[TOMORROW REMINDER JOB] WhatsApp Message ID: {result['message_id']}")
                        except asyncio.TimeoutError:
                            print(f"[TOMORROW REMINDER JOB] Timeout error: Combined reminder sending took longer than 30 seconds for user {user_id}")
                        except Exception as send_error:
                            print(f"[TOMORROW REMINDER JOB] Error sending combined reminder to user {user_id}")
                            print(f"[TOMORROW REMINDER JOB] Error type: {type(send_error).__name__}")
                            print(f"[TOMORROW REMINDER JOB] Error details: {str(send_error)}")
                            print(f"[TOMORROW REMINDER JOB] Error repr: {repr(send_error)}")
                            import traceback
                            print(f"[TOMORROW REMINDER JOB] Full traceback: {traceback.format_exc()}")
                    else:
                        print(f"[TOMORROW REMINDER JOB] No event loop available for user {user_id}")
                else:
                    print(f"[TOMORROW REMINDER JOB] No events or active tasks to notify for user {user_id}.")
                
                print(f"[TOMORROW REMINDER JOB] Completed processing for user {user_id}")
            
            print(f"[TOMORROW REMINDER JOB] Finished processing all {len(users)} users")
        except Exception as e:
            print(f"🔥 [TOMORROW REMINDER JOB ERROR] {e}")

    # Schedule today's reminder at 9:00 AM
    scheduler.add_job(today_reminder_job, 'cron', hour=8, minute=30)
    # Schedule tomorrow's reminder at 10:40 PM
    scheduler.add_job(tomorrow_reminder_job, 'cron', hour=19, minute=30)
    scheduler.start()
    print("\n✅ Scheduler started with:")
    print("   • Today's reminder at 8:30 AM")
    print("   • Tomorrow's reminder at 7:30 PM")
    if TEST_MODE:
        print("   🧪 RUNNING IN TEST MODE - WhatsApp messages will be mocked")

def trigger_today_reminder_manually():
    """Manually trigger today's reminder for testing"""
    print("\n🔧 [MANUAL TRIGGER] Running today's reminder job manually...")
    # Get the inner function and call it directly
    scheduler_jobs = scheduler.get_jobs()
    for job in scheduler_jobs:
        if 'today_reminder_job' in str(job.func):
            job.func()
            break
    else:
        print("❌ Today reminder job not found in scheduler")

def trigger_tomorrow_reminder_manually():
    """Manually trigger tomorrow's reminder for testing"""
    print("\n🔧 [MANUAL TRIGGER] Running tomorrow's reminder job manually...")
    # Get the inner function and call it directly
    scheduler_jobs = scheduler.get_jobs()
    for job in scheduler_jobs:
        if 'tomorrow_reminder_job' in str(job.func):
            job.func()
            break
    else:
        print("❌ Tomorrow reminder job not found in scheduler")