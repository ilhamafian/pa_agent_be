# app/user.py
import hashlib
import os
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from typing import List
from dotenv import load_dotenv
from datetime import datetime, timedelta
from jose import jwt
import pytz
from db.mongo import client
from utils.utils import hash_data, encrypt_phone, send_whatsapp_message

load_dotenv(dotenv_path=".env.local", override=True)
SECRET_KEY = os.getenv("TOKEN_SECRET_KEY")
FRONTEND_URL = os.getenv("FRONTEND_URL")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 1 day

with open("ai/prompts/onboarding_guide.txt", "r", encoding="utf-8") as f:
    onboarding_guide_prompt = f.read()

router = APIRouter()

# MongoDB setup
db = client["oauth_db"]
users_collection = db["users"]
waitlist_collection = db["waitlist"]
integrations_collection = db["integrations"]
settings_collection = db["settings"]

class Metadata(BaseModel):
    about_yourself: str
    profession: str
    source: str

class UserPayload(BaseModel):
    PIN: int
    phone_number: int
    nickname: str
    email: str
    language: str
    metadata: Metadata
    
class UserLoginPayload(BaseModel):
    PIN: int
    phone_number: int

class LogoutPayload(BaseModel):
    phone_number: str

class WaitlistPayload(BaseModel):
    phone_number: str

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def _check_phone_number_exists(hashed_phone: str) -> bool:
    """Utility function to check if a hashed phone number exists in the database"""
    user = users_collection.find_one({"hashed_phone_number": hashed_phone})
    return bool(user)

async def send_onboarding_guide(phone_number: int):
    onboarding_url = f"{FRONTEND_URL}/guide"
    formatted_message = onboarding_guide_prompt.format(onboarding_url=onboarding_url)
    await send_whatsapp_message(phone_number, formatted_message)
    return {"message": "Onboarding guide sent successfully"}

@router.post("/user_onboarding")
async def create_user(data: UserPayload):
    print(f"Received user: {data}")

    try:
        # Hash PIN and phone_number
        hashed_pin = hash_data(str(data.PIN))
        encrypted_phone = encrypt_phone(str(data.phone_number))
        hashed_phone = hash_data(str(data.phone_number))

        # Check if user already exists (by hashed phone number)
        if _check_phone_number_exists(hashed_phone):  # pass hashed here!
            raise HTTPException(status_code=400, detail="User with this phone number already exists")

        # Get current timestamp
        tz = pytz.timezone("Asia/Kuala_Lumpur")
        now = datetime.now(tz)

        # Prepare user document for MongoDB
        user_doc = {
            "PIN": hashed_pin,
            "phone_number": encrypted_phone,
            "hashed_phone_number": hashed_phone,
            "nickname": data.nickname,
            "email": data.email,
            "language": data.language,
            "metadata": {
                "about_yourself": data.metadata.about_yourself,
                "profession": data.metadata.profession,
                "source": data.metadata.source,
            },
            "onboarding_completed": False,
            "created_at": now,
            "updated_at": now
        }

        # Insert user into MongoDB
        result = users_collection.insert_one(user_doc)
        user_id_str = str(result.inserted_id)

        token = create_access_token(data={"user_id": user_id_str})
        await send_onboarding_guide(data.phone_number)
        return {
            "token": token,
            "message": "User created successfully",
            "user_id": user_id_str,
            "nickname": data.nickname,
            "email": data.email
        }

    except Exception as e:
        print(f"Error creating user: {e}")
        raise HTTPException(status_code=500, detail="Failed to create user")

@router.post("/login")
async def login_user(data: UserLoginPayload):
    print(f"Received login attempt for phone: {data.phone_number}")
    
    try:
        # Hash PIN and phone_number
        hashed_pin = hash_data(str(data.PIN))
        hashed_phone = hash_data(str(data.phone_number))
        print(f"Hashed phone: {hashed_phone}")
        
        # Find user by hashed phone number
        user = users_collection.find_one({"hashed_phone_number": hashed_phone})
        
        if not user:
            print(f"User not found: {data.phone_number}")
            raise HTTPException(status_code=401, detail="Invalid phone number or PIN")
        
        # Verify PIN
        if user["PIN"] != hashed_pin:
            print(f"Invalid PIN: {user.get('nickname', 'Unknown')}")
            raise HTTPException(status_code=401, detail="Invalid phone number or PIN")
        
        # Update last login timestamp
        tz = pytz.timezone("Asia/Kuala_Lumpur")
        now = datetime.now(tz)
        
        users_collection.update_one(
            {"_id": user["_id"]},
            {"$set": {"last_login": now, "updated_at": now}}
        )
        
        print(f"Successful login for user: {user.get('nickname', 'Unknown')}")
        
        # Return success response (excluding sensitive data)
        token = create_access_token(data={"user_id": str(user["_id"])})
        return {
            "message": "Login successful",
            "token": token,
            "user_id": str(user["_id"]),
            "nickname": user["nickname"],
            "email": user["email"],
            "language": user["language"],
        }
        
    except HTTPException:
        # Re-raise HTTP exceptions (401 errors)
        raise
    except Exception as e:
        print(f"Error during login: {e}")
        raise HTTPException(status_code=500, detail="Login failed")

@router.post("/check_phone_number_exist", status_code=status.HTTP_200_OK)
async def check_phone_number_exist(data: dict):
    try:
        phone_number = data.get("phone_number")
        print(f"Checking phone number: {phone_number}")
        if not phone_number:
            raise HTTPException(status_code=400, detail="Invalid phone number")

        hashed_phone = hash_data(str(phone_number))
        user = users_collection.find_one({"hashed_phone_number": hashed_phone})

        return {"exists": bool(user)}

    except HTTPException:
        raise
    except Exception as e:
        print(f"[ERROR] /check_user_exist: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Something went wrong while checking user existence."
        )

@router.post("/logout")
async def logout(data: LogoutPayload):
    print(f"Logging out user for phone: {data.phone_number}")

    try:
        hashed_phone = hash_data(data.phone_number)
        
        result = users_collection.update_one(
            {"hashed_phone_number": hashed_phone},
            {"$set": {"last_login": None}}
        )

        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="User not found")

        return {"message": "✅ User logged out successfully"}
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error during logout: {e}")
        raise HTTPException(status_code=500, detail="❌ Logout failed")
    
@router.post("/waitlist")
async def waitlist(req: WaitlistPayload):
    phone_number = req.phone_number
    print(f"Adding to waitlist: {phone_number}")

    try:
        waitlist_collection.insert_one({"phone_number": phone_number})
        return {
            "message": "✅ Added to waitlist successfully",
            "phone_number": phone_number
        }
    except Exception as e:
        print(f"Error during waitlist: {e}")
        raise HTTPException(status_code=500, detail="❌ Failed to add to waitlist")
