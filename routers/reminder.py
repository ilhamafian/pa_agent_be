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

@router.post("/reminder/daily/today/user")
async def today_reminder_user_handler(request: Request):
    """
    HTTP endpoint handler for processing today's reminder for a single user.
    This is triggered by Cloud Tasks (dispatched from the main scheduler).
    """
    try:
        data = await request.json()
        user_id = data.get("user_id")
        
        if not user_id:
            return {"status": "error", "message": "user_id is required"}
        
        print(f"\n[TODAY USER REMINDER] Processing user: {user_id}")
        
        # Import database function
        from db.mongo import db
        users_collection = db["users"]
        
        # Fetch user data
        user = await users_collection.find_one({"_id": ObjectId(user_id)})
        if not user:
            print(f"[TODAY USER REMINDER] User {user_id} not found")
            return {"status": "error", "message": "User not found"}
        
        nickname = user.get("nickname")
        encrypted_phone = user.get("phone_number")
        
        # Skip user if essential data is missing
        if not nickname or not encrypted_phone:
            print(f"[TODAY USER REMINDER] Skipping user due to missing data: nickname={nickname}, phone={bool(encrypted_phone)}")
            return {"status": "error", "message": "Missing user data"}
        
        try:
            decrypted_phone = decrypt_phone(encrypted_phone)
            if not decrypted_phone:
                print(f"[TODAY USER REMINDER] Failed to decrypt phone number for user {user_id}")
                return {"status": "error", "message": "Failed to decrypt phone"}
        except Exception as decrypt_error:
            print(f"[TODAY USER REMINDER] Error decrypting phone for user {user_id}: {decrypt_error}")
            return {"status": "error", "message": str(decrypt_error)}
        
        # Get today's date
        today = datetime.now(pytz.timezone("Asia/Kuala_Lumpur")).date()
        
        # Import functions from scheduler module
        from tools.scheduler import get_events_for_user_on_date, format_combined_reminder
        from tools.task import get_tasks
        
        # Fetch events for today
        events, token_expired = await get_events_for_user_on_date(user_id, today)
        print(f"[TODAY USER REMINDER] Found {len(events)} events for user {user_id}, token_expired: {token_expired}")
        
        # Fetch pending and in-progress tasks
        try:
            pending_tasks = await get_tasks(user_id, status="pending") or []
            in_progress_tasks = await get_tasks(user_id, status="in_progress") or []
            all_active_tasks = pending_tasks + in_progress_tasks
            print(f"[TODAY USER REMINDER] Found {len(all_active_tasks)} active tasks for user {user_id}")
        except Exception as task_error:
            print(f"[TODAY USER REMINDER] Error fetching tasks for user {user_id}: {task_error}")
            all_active_tasks = []
        
        # Send combined reminder if there are events or tasks
        if events or all_active_tasks:
            message = await format_combined_reminder(events, all_active_tasks, nickname, is_tomorrow=False)
            print(f"[TODAY USER REMINDER] Sending combined reminder to user {user_id}")
            
            loop = get_event_loop()
            if loop:
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        send_whatsapp_message(decrypted_phone, message),
                        loop
                    )
                    result = future.result(timeout=30)
                    print(f"[TODAY USER REMINDER] Combined reminder sent successfully to user {user_id}")
                except Exception as send_error:
                    print(f"[TODAY USER REMINDER] Error sending combined reminder to user {user_id}: {send_error}")
            else:
                print(f"[TODAY USER REMINDER] No event loop available for user {user_id}")
        else:
            print(f"[TODAY USER REMINDER] No events or active tasks to notify for user {user_id}")
        
        # Reschedule for tomorrow (recurring task)
        try:
            import os
            from utils.cloud_tasks import schedule_daily_task
            app_url = os.getenv("APP_URL")
            today_url = f"{app_url}/reminder/daily/today/user"
            
            await schedule_daily_task(
                endpoint_url=today_url,
                task_name=f"today-reminder-{user_id}",
                hour=8,
                minute=30,
                timezone_str="Asia/Kuala_Lumpur",
                request_body={"user_id": user_id}
            )
            print(f"[TODAY USER REMINDER] Rescheduled next occurrence for user {user_id}")
        except Exception as reschedule_error:
            print(f"[TODAY USER REMINDER] Error rescheduling task for user {user_id}: {reschedule_error}")
        
        return {"status": "success", "user_id": user_id, "message_sent": bool(events or all_active_tasks)}
            
    except Exception as e:
        print(f"🔥 [TODAY USER REMINDER ERROR] {e}")
        import traceback
        print(f"[TODAY USER REMINDER] Full traceback: {traceback.format_exc()}")
        return {"status": "error", "message": str(e)}

@router.post("/reminder/daily/tomorrow/user")
async def tomorrow_reminder_user_handler(request: Request):
    """
    HTTP endpoint handler for processing tomorrow's reminder for a single user.
    This is triggered by Cloud Tasks (dispatched from the main scheduler).
    """
    try:
        data = await request.json()
        user_id = data.get("user_id")
        
        if not user_id:
            return {"status": "error", "message": "user_id is required"}
        
        print(f"\n[TOMORROW USER REMINDER] Processing user: {user_id}")
        
        # Import database function
        from db.mongo import db
        users_collection = db["users"]
        
        # Fetch user data
        user = await users_collection.find_one({"_id": ObjectId(user_id)})
        if not user:
            print(f"[TOMORROW USER REMINDER] User {user_id} not found")
            return {"status": "error", "message": "User not found"}
        
        nickname = user.get("nickname")
        encrypted_phone = user.get("phone_number")
        
        # Skip user if essential data is missing
        if not nickname or not encrypted_phone:
            print(f"[TOMORROW USER REMINDER] Skipping user due to missing data: nickname={nickname}, phone={bool(encrypted_phone)}")
            return {"status": "error", "message": "Missing user data"}
        
        try:
            decrypted_phone = decrypt_phone(encrypted_phone)
            if not decrypted_phone:
                print(f"[TOMORROW USER REMINDER] Failed to decrypt phone number for user {user_id}")
                return {"status": "error", "message": "Failed to decrypt phone"}
        except Exception as decrypt_error:
            print(f"[TOMORROW USER REMINDER] Error decrypting phone for user {user_id}: {decrypt_error}")
            return {"status": "error", "message": str(decrypt_error)}
        
        # Get tomorrow's date
        tomorrow = (datetime.now(pytz.timezone("Asia/Kuala_Lumpur")) + timedelta(days=1)).date()
        
        # Import functions from scheduler module
        from tools.scheduler import get_events_for_user_on_date, format_combined_reminder
        from tools.task import get_tasks
        
        # Fetch events for tomorrow
        events, token_expired = await get_events_for_user_on_date(user_id, tomorrow)
        print(f"[TOMORROW USER REMINDER] Found {len(events)} events for user {user_id}, token_expired: {token_expired}")
        
        # Fetch pending and in-progress tasks
        try:
            pending_tasks = await get_tasks(user_id, status="pending") or []
            in_progress_tasks = await get_tasks(user_id, status="in_progress") or []
            all_active_tasks = pending_tasks + in_progress_tasks
            print(f"[TOMORROW USER REMINDER] Found {len(all_active_tasks)} active tasks for user {user_id}")
        except Exception as task_error:
            print(f"[TOMORROW USER REMINDER] Error fetching tasks for user {user_id}: {task_error}")
            all_active_tasks = []
        
        # Send combined reminder if there are events or tasks
        if events or all_active_tasks:
            message = await format_combined_reminder(events, all_active_tasks, nickname, is_tomorrow=True)
            print(f"[TOMORROW USER REMINDER] Sending combined reminder to user {user_id}")
            
            loop = get_event_loop()
            if loop:
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        send_whatsapp_message(decrypted_phone, message),
                        loop
                    )
                    result = future.result(timeout=30)
                    print(f"[TOMORROW USER REMINDER] Combined reminder sent successfully to user {user_id}")
                    if result and result.get("message_id"):
                        print(f"[TOMORROW USER REMINDER] WhatsApp Message ID: {result['message_id']}")
                except asyncio.TimeoutError:
                    print(f"[TOMORROW USER REMINDER] Timeout error: Combined reminder sending took longer than 30 seconds")
                except Exception as send_error:
                    print(f"[TOMORROW USER REMINDER] Error sending combined reminder: {type(send_error).__name__} - {str(send_error)}")
                    import traceback
                    print(f"[TOMORROW USER REMINDER] Full traceback: {traceback.format_exc()}")
            else:
                print(f"[TOMORROW USER REMINDER] No event loop available for user {user_id}")
        else:
            print(f"[TOMORROW USER REMINDER] No events or active tasks to notify for user {user_id}")
        
        # Reschedule for next day (recurring task)
        try:
            import os
            from utils.cloud_tasks import schedule_daily_task
            app_url = os.getenv("APP_URL")
            tomorrow_url = f"{app_url}/reminder/daily/tomorrow/user"
            
            await schedule_daily_task(
                endpoint_url=tomorrow_url,
                task_name=f"tomorrow-reminder-{user_id}",
                hour=19,
                minute=30,
                timezone_str="Asia/Kuala_Lumpur",
                request_body={"user_id": user_id}
            )
            print(f"[TOMORROW USER REMINDER] Rescheduled next occurrence for user {user_id}")
        except Exception as reschedule_error:
            print(f"[TOMORROW USER REMINDER] Error rescheduling task for user {user_id}: {reschedule_error}")
        
        return {"status": "success", "user_id": user_id, "message_sent": bool(events or all_active_tasks)}
            
    except Exception as e:
        print(f"🔥 [TOMORROW USER REMINDER ERROR] {e}")
        import traceback
        print(f"[TOMORROW USER REMINDER] Full traceback: {traceback.format_exc()}")
        return {"status": "error", "message": str(e)}
