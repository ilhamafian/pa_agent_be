from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, Query
from bson import ObjectId
from db.mongo import client
from utils.utils import get_auth_url, get_current_user

load_dotenv(dotenv_path=".env.local", override=True)

router = APIRouter()

db = client["oauth_db"]
integrations_collection = db["integrations"]

@router.get("/get_integrations")
async def get_integrations(user_id: str = Query(...)):
    integrations = await integrations_collection.find_one({"user_id": user_id})
    if not integrations:
        raise HTTPException(status_code=404, detail="Integrations not found")
    
    # Convert ObjectId to string
    integrations["_id"] = str(integrations["_id"])
    data = integrations["integrations"]
    return data

@router.get("/google_auth_url")
async def google_auth_url(current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    auth_url = get_auth_url(user_id)
    return {"auth_url": auth_url}