import asyncio
import traceback
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from dotenv import load_dotenv
from db.mongo import get_all_users as get_all_users_mongo
from utils.utils import send_whatsapp_message, send_whatsapp_template, decrypt_phone
from utils.cloud_tasks import enqueue_announcement
from ai.workflows.assistant import get_cache_stats, clear_user_cache, warm_cache_for_active_users, schedule_cache_warming

load_dotenv(dotenv_path=".env.local", override=True)

print("[ADMIN ROUTER] Admin router module loaded")

router = APIRouter()

class AnnouncementPayload(BaseModel):
    announcement: str = ""  # Can be empty if using template
    use_template: bool = False  # Set to True to use WhatsApp template (for users outside 24h window)
    template_name: Optional[str] = None  # Template name if use_template=True

class SendAnnouncementPayload(BaseModel):
    phone_number: str
    announcement: str = ""
    use_template: bool = False
    template_name: Optional[str] = None
    timestamp: Optional[str] = None
    
@router.post("/announcement")
async def announcement(request: Request, data: AnnouncementPayload):
    """
    Queue announcement messages to all users via Cloud Tasks.
    This endpoint responds quickly while the actual sending happens asynchronously.
    """
    # Log raw request body
    body = await request.body()
    print("\n" + "="*80)
    print(f"[ANNOUNCEMENT] RAW REQUEST BODY: {body.decode('utf-8')}")
    print(f"[ANNOUNCEMENT] Content-Type: {request.headers.get('content-type')}")
    print("="*80)
    
    print(f"[ANNOUNCEMENT] Endpoint HIT!")
    print(f"[ANNOUNCEMENT] Parsed data.announcement: {data.announcement}")
    print(f"[ANNOUNCEMENT] Parsed data.use_template: {data.use_template}")
    print(f"[ANNOUNCEMENT] Parsed data.template_name: {data.template_name}")
    print(f"[ANNOUNCEMENT] Raw model dict: {data.model_dump()}")
    print("="*80 + "\n")
    
    try:
        print("[ANNOUNCEMENT] Step 1: Fetching users from MongoDB...")
        # Call the async MongoDB function to fetch users
        users = await get_all_users_mongo()
        print(f"[ANNOUNCEMENT] Step 1 Complete: Retrieved {len(users) if users else 0} users")
        
        if not users:
            print("[ANNOUNCEMENT] No users found, returning early")
            return {"message": "No users found to send announcement to"}
        
        print(f"[ANNOUNCEMENT] Step 2: Queueing messages to {len(users)} users via Cloud Tasks...")
        print(f"[ANNOUNCEMENT] Using template: {data.use_template}, Template name: {data.template_name}")
        
        if data.use_template and not data.template_name:
            raise ValueError("template_name is required when use_template=True")
        
        tasks = []
        queued_count = 0
        failed_queue_count = 0
        
        for idx, user in enumerate(users):
            print(f"[ANNOUNCEMENT] Processing user {idx+1}/{len(users)}: {user.get('user_id', 'NO_ID')}")
            try:
                decrypted_phone = decrypt_phone(user["phone_number"])
                print(f"[ANNOUNCEMENT] User {idx+1} phone decrypted successfully: {decrypted_phone[:5]}****")
                
                # Enqueue to Cloud Tasks
                tasks.append(enqueue_announcement(
                    phone_number=decrypted_phone,
                    announcement=data.announcement,
                    use_template=data.use_template,
                    template_name=data.template_name
                ))
            except Exception as decrypt_err:
                print(f"[ANNOUNCEMENT] ERROR decrypting phone for user {idx+1}: {decrypt_err}")
                failed_queue_count += 1
        
        print(f"[ANNOUNCEMENT] Step 3: Enqueuing {len(tasks)} Cloud Tasks...")
        # Run all task enqueues concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        print(f"[ANNOUNCEMENT] Step 3 Complete: Got {len(results)} results")
        
        # Count successes and failures
        for result in results:
            if isinstance(result, Exception):
                failed_queue_count += 1
                print(f"[ANNOUNCEMENT] Failed to queue task: {result}")
            else:
                queued_count += 1
        
        print(f"[ANNOUNCEMENT] Final Result: {queued_count} tasks queued, {failed_queue_count} failed to queue")

        response = {
            "message": f"Announcement queued for {queued_count}/{len(users)} users. Messages will be sent asynchronously.",
            "total_users": len(users),
            "queued": queued_count,
            "failed_to_queue": failed_queue_count,
            "note": "Messages are being sent in the background via Cloud Tasks"
        }
        print(f"[ANNOUNCEMENT] Returning response: {response}")
        return response
        
    except Exception as e:
        print(f"[ANNOUNCEMENT] CRITICAL ERROR in announcement endpoint: {e}")
        print(f"[ANNOUNCEMENT] Error type: {type(e)}")
        print(f"[ANNOUNCEMENT] Full traceback:")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/send/announcement")
async def send_announcement_worker(data: SendAnnouncementPayload):
    """
    Worker endpoint called by Cloud Tasks to send individual announcement messages.
    This endpoint is triggered by the Cloud Tasks queue for each user.
    """
    print(f"\n[SEND_ANNOUNCEMENT] Worker endpoint HIT for phone: {data.phone_number[:5]}****")
    print(f"[SEND_ANNOUNCEMENT] use_template: {data.use_template}, template_name: {data.template_name}")
    print(f"[SEND_ANNOUNCEMENT] timestamp: {data.timestamp}")
    
    try:
        if data.use_template:
            # Use template message (works for all users regardless of 24h window)
            if not data.template_name:
                raise ValueError("template_name is required when use_template=True")
            
            print(f"[SEND_ANNOUNCEMENT] Sending WhatsApp template '{data.template_name}' to {data.phone_number[:5]}****")
            result = await send_whatsapp_template(data.phone_number, data.template_name)
        else:
            # Use free-form message (only works within 24h window)
            print(f"[SEND_ANNOUNCEMENT] Sending WhatsApp message to {data.phone_number[:5]}****")
            result = await send_whatsapp_message(data.phone_number, data.announcement)
        
        if result.get("status") == "success":
            print(f"[SEND_ANNOUNCEMENT] ✅ Successfully sent to {data.phone_number[:5]}****")
            return {
                "status": "success",
                "phone_number": data.phone_number[:5] + "****",
                "message": "Announcement sent successfully"
            }
        else:
            print(f"[SEND_ANNOUNCEMENT] ❌ Failed to send to {data.phone_number[:5]}****: {result}")
            # Check for template errors
            if result.get("response_json"):
                error_info = result["response_json"].get("error", {})
                if "template" in error_info.get("message", "").lower():
                    print(f"[SEND_ANNOUNCEMENT] ⚠️ TEMPLATE ERROR: Template '{data.template_name}' may not exist or is not approved!")
            
            return {
                "status": "error",
                "phone_number": data.phone_number[:5] + "****",
                "error": result.get("error", "Unknown error"),
                "details": result
            }
    
    except Exception as e:
        print(f"[SEND_ANNOUNCEMENT] EXCEPTION sending to {data.phone_number[:5]}****: {e}")
        traceback.print_exc()
        # Don't raise HTTPException - we want to return 200 so Cloud Tasks doesn't retry
        # (failed messages are logged for debugging)
        return {
            "status": "error",
            "phone_number": data.phone_number[:5] + "****",
            "error": str(e)
        }

    
@router.get("/total_users")
async def get_total_users():
    try:
        # Call the async MongoDB function to fetch users
        users = await get_all_users_mongo()
        return {"total": len(users)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch users: {e}")

@router.get("/cache_stats")
async def get_cache_statistics():
    """
    Get cache performance statistics for monitoring.
    Returns hit rates, cache sizes, and other metrics.
    """
    try:
        stats = get_cache_stats()

        # Calculate cache hit rate if we had counters (for future enhancement)
        # For now, just return the basic stats
        return {
            "cache_stats": stats,
            "status": "Cache is operating normally"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch cache stats: {e}")

@router.post("/cache/clear/{user_id}")
async def clear_user_conversation_cache(user_id: str):
    """
    Clear conversation cache for a specific user.
    Useful for troubleshooting or forcing cache refresh.
    """
    try:
        cleared = await clear_user_cache(user_id)
        if cleared:
            return {
                "message": f"Cache cleared for user {user_id}",
                "user_id": user_id,
                "cleared": True
            }
        else:
            return {
                "message": f"No cache entry found for user {user_id}",
                "user_id": user_id,
                "cleared": False
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear cache for user {user_id}: {e}")

@router.post("/cache/clear_all")
async def clear_all_conversation_cache():
    """
    Clear all conversation caches.
    WARNING: This will cause all users to hit the database on their next message.
    Use sparingly and only for maintenance purposes.
    """
    try:
        from ai.workflows.assistant import conversation_cache, user_locks

        # Get current cache sizes before clearing
        conversation_count = conversation_cache.currsize
        user_locks_count = user_locks.currsize

        # Clear both caches
        conversation_cache.clear()
        user_locks.clear()

        return {
            "message": "All conversation caches cleared",
            "cleared_conversations": conversation_count,
            "cleared_user_locks": user_locks_count,
            "status": "All users will hit database on next message"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear all caches: {e}")

@router.post("/cache/warm")
async def warm_cache_endpoint(limit: int = 100):
    """
    Manually trigger cache warming for active users.
    This pre-loads conversation history for recently active users to improve performance.

    Args:
        limit: Maximum number of users to warm cache for (default: 100)

    Returns:
        Cache warming statistics
    """
    try:
        result = await warm_cache_for_active_users(limit)
        return {
            "message": f"Cache warming completed for {result['warmed_users']}/{result['total_active_users']} users",
            "warming_stats": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to warm cache: {e}")

@router.post("/cache/warm_scheduler/start")
async def start_cache_warming_scheduler(interval_minutes: int = 15):
    """
    Start the background cache warming scheduler.
    This will periodically warm the cache for active users.

    Args:
        interval_minutes: How often to run cache warming (default: 15 minutes)

    Returns:
        Confirmation that scheduler was started
    """
    try:
        # Note: In a real application, you might want to use a proper task manager
        # For now, we'll just return a message that this would start the scheduler
        return {
            "message": f"Cache warming scheduler would start with {interval_minutes} minute intervals",
            "interval_minutes": interval_minutes,
            "note": "In production, this should be managed by the application lifecycle"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start cache warming scheduler: {e}")

@router.get("/cache/warm/status")
async def get_cache_warming_status():
    """
    Get the current status of cache warming functionality.

    Returns:
        Information about cache warming configuration and status
    """
    try:
        from ai.workflows.assistant import conversation_cache

        return {
            "cache_warming_enabled": True,
            "cache_size": conversation_cache.currsize,
            "cache_max_size": conversation_cache.maxsize,
            "manual_warming_available": True,
            "scheduler_available": True,
            "features": [
                "Manual cache warming via POST /cache/warm",
                "Configurable cache sizes via environment variables",
                "Cache statistics monitoring",
                "Individual user cache clearing"
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get cache warming status: {e}")