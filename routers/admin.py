import asyncio
import traceback
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from db.mongo import get_all_users as get_all_users_mongo
from utils.utils import send_whatsapp_message, send_whatsapp_template, decrypt_phone

load_dotenv(dotenv_path=".env.local", override=True)

print("[ADMIN ROUTER] Admin router module loaded")

router = APIRouter()

class AnnouncementPayload(BaseModel):
    announcement: str
    use_template: bool = False  # Set to True to use WhatsApp template (for users outside 24h window)
    template_name: str = None  # Template name if use_template=True
    
@router.post("/announcement")
async def announcement(data: AnnouncementPayload):
    print("\n" + "="*80)
    print(f"[ANNOUNCEMENT] Endpoint HIT! Received announcement: {data.announcement}")
    print(f"[ANNOUNCEMENT] Request data type: {type(data)}")
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
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                print(f"[ANNOUNCEMENT] Result {idx+1}: EXCEPTION - {result}")
            else:
                print(f"[ANNOUNCEMENT] Result {idx+1}: {result}")

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