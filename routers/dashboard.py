from datetime import datetime
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from db.mongo import client
from utils.utils import get_current_user, get_dashboard_events

db = client["oauth_db"]
tasks_collection = db["task_list"]
bugs_collection = db["bugs"]

class BugPayload(BaseModel):
    user_id: str
    title: str
    description: str

router = APIRouter()

@router.get("/get_dashboard_info")
async def dashboard(current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    tasks_doc = await tasks_collection.find_one({"user_id": user_id})

    tasks_list = tasks_doc["tasks"] if tasks_doc and "tasks" in tasks_doc else []
    
    # Get dashboard events for the next 4 days (today + next 3 days)
    events_data = get_dashboard_events(user_id)
    events_list = events_data.get("events", [])

    return {
        "events": events_list,
        "tasks": tasks_list
    }

@router.post("/report_bug")
async def report_bug(bug: BugPayload):
    await bugs_collection.insert_one({
        "user_id": bug.user_id,
        "title": bug.title,
        "description": bug.description,
        "created_at": datetime.now()
    })
    return {"message": "Bug reported successfully"}