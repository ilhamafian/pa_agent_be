from fastapi import APIRouter, Request
from db.mongo import reminders_collection, get_all_users, db
from utils.utils import send_whatsapp_message, get_event_loop, decrypt_phone
from bson import ObjectId
from datetime import datetime, timedelta
import pytz
import asyncio

router = APIRouter()

# MongoDB calendar collection
calendar_collection = db["calendar"]

@router.post("/reminder/send")
async def reminder_consumer(request: Request):
    data = await request.json()
    reminder_id = data.get("reminder_id")

    reminder = await reminders_collection.find_one({"_id": ObjectId(reminder_id)})
    if not reminder:
        return {"status": "error", "message": "Reminder not found"}

    phone_number = reminder["phone_number"]
    message = reminder["message"]

    print(f"[TASK REMINDER] Sending reminder: {message} to {phone_number}")

    loop = get_event_loop()
    asyncio.run_coroutine_threadsafe(send_whatsapp_message(phone_number, message), loop)

    await reminders_collection.update_one(
        {"_id": ObjectId(reminder_id)},
        {"$set": {"status": "sent", "sent_at": datetime.now(pytz.timezone("Asia/Kuala_Lumpur"))}}
    )

    return {"status": "success"}

@router.post("/reminder/daily/today")
async def today_reminder_handler(request: Request):
    """
    HTTP endpoint handler for today's morning reminder job.
    This is triggered by Google Cloud Tasks at 8:30 AM daily.
    """
    try:
        print("\n[TODAY REMINDER JOB] Starting morning reminder job...")
        today = datetime.now(pytz.timezone("Asia/Kuala_Lumpur")).date()
        users = await get_all_users() or []
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
            
            # Import functions from scheduler module
            from tools.scheduler import get_events_for_user_on_date, format_combined_reminder
            from tools.task import get_tasks
            
            # Fetch events for today
            events, token_expired = await get_events_for_user_on_date(user_id, today)
            print(f"[TODAY REMINDER JOB] Found {len(events)} events for user {user_id}, token_expired: {token_expired}")
            
            # Fetch pending and in-progress tasks
            try:
                pending_tasks = await get_tasks(user_id, status="pending") or []
                in_progress_tasks = await get_tasks(user_id, status="in_progress") or []
                all_active_tasks = pending_tasks + in_progress_tasks
                print(f"[TODAY REMINDER JOB] Found {len(all_active_tasks)} active tasks for user {user_id}")
            except Exception as task_error:
                print(f"[TODAY REMINDER JOB] Error fetching tasks for user {user_id}: {task_error}")
                all_active_tasks = []
            
            # Send combined reminder if there are events or tasks
            if events or all_active_tasks:
                message = await format_combined_reminder(events, all_active_tasks, nickname, is_tomorrow=False)
                print(f"[TODAY REMINDER JOB] Sending combined reminder to user {user_id}:")
                
                loop = get_event_loop()
                if loop:
                    try:
                        future = asyncio.run_coroutine_threadsafe(
                            send_whatsapp_message(decrypted_phone, message),
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
        
        # Reschedule for tomorrow
        try:
            import os
            from utils.cloud_tasks import reschedule_daily_task_for_next_day
            app_url = os.getenv("APP_URL")
            today_url = f"{app_url}/reminder/daily/today"
            reschedule_daily_task_for_next_day(
                endpoint_url=today_url,
                task_name="daily-today-reminder",
                hour=8,
                minute=30,
                timezone_str="Asia/Kuala_Lumpur"
            )
            print("✅ Today reminder rescheduled for tomorrow")
        except Exception as reschedule_error:
            print(f"⚠️ Failed to reschedule today reminder: {reschedule_error}")
        
        return {"status": "success", "users_processed": len(users)}
    except Exception as e:
        print(f"🔥 [TODAY REMINDER JOB ERROR] {e}")
        return {"status": "error", "message": str(e)}

@router.post("/reminder/daily/tomorrow")
async def tomorrow_reminder_handler(request: Request):
    """
    HTTP endpoint handler for tomorrow's evening reminder job.
    This is triggered by Google Cloud Tasks at 7:30 PM daily.
    """
    try:
        print("\n[TOMORROW REMINDER JOB] Starting daily reminder job...")
        tomorrow = (datetime.now(pytz.timezone("Asia/Kuala_Lumpur")) + timedelta(days=1)).date()
        users = await get_all_users() or []
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
            
            # Import functions from scheduler module
            from tools.scheduler import get_events_for_user_on_date, format_combined_reminder
            from tools.task import get_tasks
            
            # Fetch events for tomorrow
            events, token_expired = await get_events_for_user_on_date(user_id, tomorrow)
            print(f"[TOMORROW REMINDER JOB] Found {len(events)} events for user {user_id}, token_expired: {token_expired}")
            
            # Fetch pending and in-progress tasks
            try:
                pending_tasks = await get_tasks(user_id, status="pending") or []
                in_progress_tasks = await get_tasks(user_id, status="in_progress") or []
                all_active_tasks = pending_tasks + in_progress_tasks
                print(f"[TOMORROW REMINDER JOB] Found {len(all_active_tasks)} active tasks for user {user_id}")
            except Exception as task_error:
                print(f"[TOMORROW REMINDER JOB] Error fetching tasks for user {user_id}: {task_error}")
                all_active_tasks = []
            
            # Send combined reminder if there are events or tasks
            if events or all_active_tasks:
                message = await format_combined_reminder(events, all_active_tasks, nickname, is_tomorrow=True)
                print(f"[TOMORROW REMINDER JOB] Sending combined reminder to user {user_id}:")
                
                loop = get_event_loop()
                print(f"[TOMORROW REMINDER JOB] Event loop status: {loop is not None}, running: {loop.is_running() if loop else 'N/A'}")
                
                if loop:
                    try:
                        print(f"[TOMORROW REMINDER JOB] About to send combined reminder to {decrypted_phone[:5]}...")
                        future = asyncio.run_coroutine_threadsafe(
                            send_whatsapp_message(decrypted_phone, message),
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
        
        # Reschedule for tomorrow
        try:
            import os
            from utils.cloud_tasks import reschedule_daily_task_for_next_day
            app_url = os.getenv("APP_URL")
            tomorrow_url = f"{app_url}/reminder/daily/tomorrow"
            reschedule_daily_task_for_next_day(
                endpoint_url=tomorrow_url,
                task_name="daily-tomorrow-reminder",
                hour=19,
                minute=30,
                timezone_str="Asia/Kuala_Lumpur"
            )
            print("✅ Tomorrow reminder rescheduled for next day")
        except Exception as reschedule_error:
            print(f"⚠️ Failed to reschedule tomorrow reminder: {reschedule_error}")
        
        return {"status": "success", "users_processed": len(users)}
    except Exception as e:
        print(f"🔥 [TOMORROW REMINDER JOB ERROR] {e}")
        return {"status": "error", "message": str(e)}