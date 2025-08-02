from datetime import datetime
import pytz
import uuid
from db.mongo import client

# Task collection setup
db = client["oauth_db"]
task_list_collection = db["task_list"]

class AuthRequiredError(Exception):
    pass

def create_task(title: str = None, priority: str = "medium", user_id=None, description: str = None) -> dict:
    if user_id is None:
        raise ValueError("Missing user_id in create_task() call!")
    
    print("Creating task for user_id:", user_id, type(user_id))
    
    # Validate priority
    valid_priorities = ["high", "medium", "low"]
    if priority not in valid_priorities:
        priority = "medium"  # Default fallback
    
    # Create task object
    tz = pytz.timezone("Asia/Kuala_Lumpur")
    now = datetime.now(tz)
    
    task = {
        'task_id': str(uuid.uuid4()),  # Unique task ID
        'title': title,
        'description': description or "",
        'priority': priority,
        'status': 'pending',
        'created_at': now
    }
    
    print("Task object:", task)
    
    # Update user document - add task to tasks array
    result = task_list_collection.update_one(
        {"user_id": user_id},
        {
            "$push": {"tasks": task},
            "$set": {"updated_at": now}
        },
        upsert=True  # Create document if it doesn't exist
    )
    
    print(f'Task added to user {user_id} - Modified: {result.modified_count}, Upserted: {result.upserted_id}')
    return task

def get_tasks(user_id: str, status: str = None, priority: str = None) -> list:
    """Get all tasks for a user, optionally filtered by status and/or priority"""
    if user_id is None:
        raise ValueError("Missing user_id in get_tasks() call!")
    
    print("Getting tasks for user_id:", user_id)
    
    # Find user document
    user_doc = task_list_collection.find_one({"user_id": user_id})
    
    if not user_doc or "tasks" not in user_doc:
        print(f"No tasks found for user {user_id}")
        return []
    
    tasks = user_doc["tasks"]
    
    # Filter by status if provided
    if status:
        tasks = [task for task in tasks if task.get("status") == status]
    
    # Filter by priority if provided
    if priority:
        tasks = [task for task in tasks if task.get("priority") == priority]
    
    print(f"Found {len(tasks)} tasks for user {user_id}")
    return tasks

def update_task_status(task_id: str, status: str, user_id: str) -> dict:
    """Update the status of a specific task"""
    print(f"Updating task {task_id} status to {status} for user {user_id}")
    
    # Validate status
    valid_statuses = ["pending", "in_progress", "completed"]
    if status not in valid_statuses:
        print(f"Invalid status: {status}. Valid options: {valid_statuses}")
        return None
    
    tz = pytz.timezone("Asia/Kuala_Lumpur")
    now = datetime.now(tz)
    
    # Update specific task in the array
    result = task_list_collection.update_one(
        {"user_id": user_id, "tasks.task_id": task_id},
        {
            "$set": {
                "tasks.$.status": status,
                "tasks.$.updated_at": now,
                "updated_at": now
            }
        }
    )
    
    if result.modified_count > 0:
        print(f"Task {task_id} updated successfully")
        # Return the updated task
        user_doc = task_list_collection.find_one({"user_id": user_id})
        updated_task = next((task for task in user_doc["tasks"] if task["task_id"] == task_id), None)
        return updated_task
    else:
        print(f"Task {task_id} not found or update failed")
        return None

create_task_tool = {
    "type": "function",
    "function": {
        "name": "create_task",
        "description": "Creates a task in the todo list with priority level.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Title of the task"
                },
                "priority": {
                    "type": "string",
                    "description": "Priority level: 'high', 'medium', or 'low' (default: medium)"
                },
                "description": {
                    "type": "string",
                    "description": "Optional additional details about the task"
                }
            },
            "required": ["title"]
        }
    }
}

get_tasks_tool = {
    "type": "function",
    "function": {
        "name": "get_tasks",
        "description": "Gets all tasks for a user, optionally filtered by status and/or priority.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Optional status filter: 'pending', 'in_progress', or 'completed'"
                },
                "priority": {
                    "type": "string",
                    "description": "Optional priority filter: 'high', 'medium', or 'low'"
                }
            },
            "required": []
        }
    }
}

update_task_status_tool = {
    "type": "function",
    "function": {
        "name": "update_task_status",
        "description": "Updates the status of a specific task.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The unique task_id of the task to update"
                },
                "status": {
                    "type": "string",
                    "description": "New status: 'pending', 'in_progress', or 'completed'"
                }
            },
            "required": ["task_id", "status"]
        }
    }
}
