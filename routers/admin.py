import asyncio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from db.mongo import get_all_users as get_all_users_mongo
from utils.utils import send_whatsapp_message, decrypt_phone

load_dotenv(dotenv_path=".env.local", override=True)

router = APIRouter()

class AnnouncementPayload(BaseModel):
    announcement: str
    
@router.post("/announcement")
async def announcement(data: AnnouncementPayload):
    print(f"Received announcement: {data.announcement}")
    try:
        users = get_all_users_mongo()
        
        tasks = []
        for user in users:
            decrypted_phone = decrypt_phone(user["phone_number"])
            tasks.append(send_whatsapp_message(decrypted_phone, data.announcement))
        
        # Run all WhatsApp sends concurrently
        await asyncio.gather(*tasks)

        return {"message": f"Announcement sent to {len(users)} users successfully"}
    except Exception as e:
        print(f"Error sending announcement: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
@router.get("/total_users")
async def get_total_users():
    try:
        users = get_all_users_mongo()
        return {"total": len(users)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch users: {e}")