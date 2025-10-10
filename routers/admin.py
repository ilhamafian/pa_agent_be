import asyncio
import traceback
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from db.mongo import get_all_users as get_all_users_mongo
from utils.utils import send_whatsapp_message, send_whatsapp_template, decrypt_phone

load_dotenv(dotenv_path=".env.local", override=True)

print("[ADMIN ROUTER] Admin router module loaded")

router = APIRouter()

class AnnouncementPayload(BaseModel):
    announcement: str = ""  # Can be empty if using template
    use_template: bool = False  # Set to True to use WhatsApp template (for users outside 24h window)
    template_name: Optional[str] = None  # Template name if use_template=True
    
@router.post("/announcement")
async def announcement(data: AnnouncementPayload):
    print("\n" + "="*80)
    print(f"[ANNOUNCEMENT] Endpoint HIT!")
    print(f"[ANNOUNCEMENT] Parsed data.announcement: {data.announcement}")
    print(f"[ANNOUNCEMENT] Parsed data.use_template: {data.use_template}")
    print(f"[ANNOUNCEMENT] Parsed data.template_name: {data.template_name}")
    print(f"[ANNOUNCEMENT] Raw model dict: {data.model_dump()}")
    print("="*80 + "\n")
    
    try:
        print("[ANNOUNCEMENT] Step 1: Fetching users from MongoDB...")
        # Run the synchronous MongoDB call in a thread pool to avoid blocking
        users = await asyncio.to_thread(get_all_users_mongo)
        print(f"[ANNOUNCEMENT] Step 1 Complete: Retrieved {len(users) if users else 0} users")
        
        if not users:
            print("[ANNOUNCEMENT] No users found, returning early")
            return {"message": "No users found to send announcement to"}
        
        print(f"[ANNOUNCEMENT] Step 2: Preparing to send messages to {len(users)} users...")
        print(f"[ANNOUNCEMENT] Using template: {data.use_template}, Template name: {data.template_name}")
        
        if data.use_template and not data.template_name:
            raise ValueError("template_name is required when use_template=True")
        
        tasks = []
        for idx, user in enumerate(users):
            print(f"[ANNOUNCEMENT] Processing user {idx+1}/{len(users)}: {user.get('user_id', 'NO_ID')}")
            try:
                decrypted_phone = decrypt_phone(user["phone_number"])
                print(f"[ANNOUNCEMENT] User {idx+1} phone decrypted successfully: {decrypted_phone[:5]}****")
                
                if data.use_template:
                    # Use template message (works for all users regardless of 24h window)
                    tasks.append(send_whatsapp_template(decrypted_phone, data.template_name))
                else:
                    # Use free-form message (only works within 24h window)
                    tasks.append(send_whatsapp_message(decrypted_phone, data.announcement))
            except Exception as decrypt_err:
                print(f"[ANNOUNCEMENT] ERROR decrypting phone for user {idx+1}: {decrypt_err}")
        
        print(f"[ANNOUNCEMENT] Step 3: Sending {len(tasks)} WhatsApp messages concurrently...")
        # Run all WhatsApp sends concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        print(f"[ANNOUNCEMENT] Step 3 Complete: Got {len(results)} results")
        
        # Count successes and failures
        successes = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "success")
        failures = len(results) - successes
        
        print(f"[ANNOUNCEMENT] Final Result: {successes} successful, {failures} failed")
        
        # Log errors separately for debugging
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                print(f"[ANNOUNCEMENT] Result {idx+1}: EXCEPTION - {result}")
            elif isinstance(result, dict) and result.get("status") == "error":
                print(f"[ANNOUNCEMENT] Result {idx+1}: ERROR - {result}")
                # Check for specific WhatsApp errors
                if result.get("response_json"):
                    error_info = result["response_json"].get("error", {})
                    if "template" in error_info.get("message", "").lower():
                        print(f"[ANNOUNCEMENT] ⚠️ TEMPLATE ERROR: Template '{data.template_name}' may not exist or is not approved!")
            else:
                print(f"[ANNOUNCEMENT] Result {idx+1}: SUCCESS")

        response = {
            "message": f"Announcement sent to {successes}/{len(users)} users successfully",
            "total_users": len(users),
            "successful": successes,
            "failed": failures
        }
        print(f"[ANNOUNCEMENT] Returning response: {response}")
        return response
        
    except Exception as e:
        print(f"[ANNOUNCEMENT] CRITICAL ERROR in announcement endpoint: {e}")
        print(f"[ANNOUNCEMENT] Error type: {type(e)}")
        print(f"[ANNOUNCEMENT] Full traceback:")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    
@router.get("/total_users")
async def get_total_users():
    try:
        # Run the synchronous MongoDB call in a thread pool to avoid blocking
        users = await asyncio.to_thread(get_all_users_mongo)
        return {"total": len(users)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch users: {e}")