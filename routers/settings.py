# app/user.py
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from typing import List
from dotenv import load_dotenv
from datetime import datetime, timedelta
from jose import jwt
from bson import ObjectId
from db.mongo import client
from utils.utils import hash_data

load_dotenv(dotenv_path=".env.local", override=True)

router = APIRouter()

# MongoDB setup
db = client["oauth_db"]
users_collection = db["users"]
settings_collection = db["settings"]

class UserIdPayload(BaseModel):
    user_id: str

class UpdateProfilePayload(BaseModel):
    user_id: str
    name: str
    language: str

# Pydantic model for daily briefing structure
class DailyBriefingPayload(BaseModel):
    enabled: bool
    time: int  # e.g., 1930

# Pydantic model for update request
class UpdateNotificationsPayload(BaseModel):
    user_id: str
    daily_briefing: DailyBriefingPayload

@router.get("/get_settings_info")
async def settings(user_id: str = Query(...)):
    try:
        oid = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ObjectId format")

    user = users_collection.find_one({"_id": oid})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Convert ObjectId to string
    user["_id"] = str(user["_id"])

    settings = settings_collection.find_one({"user_id": user_id})
    if not settings:
        raise HTTPException(status_code=404, detail="Settings not found")
    
    print(settings)

    user_settings = {
        "user_id": user["_id"],
        "name": user["nickname"],
        "email": user["email"],
        "language": user["language"],
        "daily_briefing": settings["settings"]["daily_briefing"], 
    }
    
    return user_settings

@router.post("/update_profile")
async def update_profile(data: UpdateProfilePayload):
    user_id = data.user_id
    print(f"Updating profile for user: {user_id}")

    try:
        oid = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ObjectId format")

    update_data = {
        "nickname": data.name,
        "language": data.language
    }

    result = users_collection.update_one({"_id": oid}, {"$set": update_data})

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")

    if result.modified_count == 0:
        return {"message": "No changes made"}

    return {"message": "Profile updated successfully"}

@router.post("/update_notifications")
async def update_notifications(data: UpdateNotificationsPayload):
    user_id = data.user_id
    print(f"Updating notifications for user: {user_id}")

    # Ensure valid ObjectId
    try:
        ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ObjectId format")

    # Update only the daily_briefing section
    result = settings_collection.update_one(
        {"user_id": user_id},
        {"$set": {"settings.daily_briefing": data.daily_briefing.dict()}}
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Settings not found")
    
    if result.modified_count == 0:
        return {"message": "No changes made"}

    return {"message": "Notifications updated successfully"}