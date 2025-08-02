# app/user.py

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
import hashlib
from datetime import datetime
import pytz
from db.mongo import client
from utils.utils import hash_data

router = APIRouter()

# MongoDB setup
db = client["oauth_db"]
users_collection = db["users"]

class Metadata(BaseModel):
    q1: List[str]
    q2: str
    q3: str
    q4: str

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

@router.post("/user_onboarding")
async def create_user(data: UserPayload):
    print(f"Received user: {data}")
    
    try:
        # Hash PIN and phone_number
        hashed_pin = hash_data(str(data.PIN))
        hashed_phone = hash_data(str(data.phone_number))
        
        # Get current timestamp
        tz = pytz.timezone("Asia/Kuala_Lumpur")
        now = datetime.now(tz)
        
        # Prepare user document for MongoDB
        user_doc = {
            "PIN": hashed_pin,
            "phone_number": hashed_phone,
            "nickname": data.nickname,
            "email": data.email,
            "language": data.language,
            "metadata": {
                "q1": data.metadata.q1,
                "q2": data.metadata.q2,
                "q3": data.metadata.q3,
                "q4": data.metadata.q4
            },
            "created_at": now,
            "updated_at": now
        }
        
        # Check if user already exists (by hashed phone number)
        existing_user = users_collection.find_one({"phone_number": hashed_phone})
        if existing_user:
            raise HTTPException(status_code=400, detail="User with this phone number already exists")
        
        # Insert user into MongoDB
        result = users_collection.insert_one(user_doc)
        
        print(f"User created with ID: {result.inserted_id}")
        
        return {
            "message": "User created successfully",
            "user_id": str(result.inserted_id),
            "nickname": data.nickname,
            "email": data.email
        }
        
    except Exception as e:
        print(f"Error creating user: {e}")
        raise HTTPException(status_code=500, detail="Failed to create user")

@router.post("/user_login")
async def login_user(data: UserLoginPayload):
    print(f"Received login attempt for phone: {data.phone_number}")
    
    try:
        # Hash PIN and phone_number
        hashed_pin = hash_data(str(data.PIN))
        hashed_phone = hash_data(str(data.phone_number))
        
        # Find user by hashed phone number
        user = users_collection.find_one({"phone_number": hashed_phone})
        
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
        return {
            "message": "Login successful",
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
     