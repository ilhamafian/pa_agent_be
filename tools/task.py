from datetime import datetime
import pytz
from db.mongo import client

# Task collection setup
db = client["oauth_db"]
task_list_collection = db["task_list"]

class AuthRequiredError(Exception):
    pass

def create_task(title: str = None, date: str = None, user_id=None, description: str = None) -> dict:
    if user_id is None:
        raise ValueError("Missing user_id in create_task() call!")
    
    print("Creating task for user_id:", user_id, type(user_id))
    
    # Create task document
    tz = pytz.timezone("Asia/Kuala_Lumpur")
    now = datetime.now(tz)
    
    task = {
        'title': title,
        'description': description or "",
        'date': date,
        'user_id': user_id,
        'status': 'pending',
        'created_at': now
    }
    
    print("Task document:", task)
    result = task_list_collection.insert_one(task)
    task['_id'] = str(result.inserted_id)
    
    print('Task created with ID:', result.inserted_id)
    return task

create_task_tool = {
    "type": "function",
    "function": {
        "name": "create_task",
        "description": "Creates a task in the todo list.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Title of the task"
                },
                "date": {
                    "type": "string",
                    "description": "Date of the task in YYYY-MM-DD format"
                },
                "description": {
                    "type": "string",
                    "description": "Optional additional details about the task"
                }
            },
            "required": ["title", "date"]
        }
    }
}
