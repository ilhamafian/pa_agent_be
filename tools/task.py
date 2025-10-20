from datetime import datetime
import pytz
import uuid
from db.mongo import client
from dotenv import load_dotenv
import os

# Task collection setup
load_dotenv(dotenv_path=".env.local", override=True)

db_name = os.environ.get("DB_NAME")
db = client[db_name]
task_list_collection = db["tasks"]

class AuthRequiredError(Exception):
    pass

async def create_task(title: str = None, priority: str = "medium", user_id=None, description: str = None) -> dict:
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
    result = await task_list_collection.update_one(
        {"user_id": user_id},
        {
            "$push": {"tasks": task},
            "$set": {"updated_at": now}
        },
        upsert=True  # Create document if it doesn't exist
    )
    
    print(f'Task added to user {user_id} - Modified: {result.modified_count}, Upserted: {result.upserted_id}')
    return task

async def get_tasks(user_id: str, status: str = None, priority: str = None) -> list:
    """Get all tasks for a user, optionally filtered by status and/or priority.
    If status is 'completed', return only the latest 5 completed tasks.
    If no status is given, return all tasks but limit completed ones to latest 5.
    """
    
    print("Getting tasks for user_id:", user_id)
    
    user_doc = await task_list_collection.find_one({"user_id": user_id})
    
    if not user_doc or "tasks" not in user_doc:
        print(f"No tasks found for user {user_id}")
        return []
    
    tasks = user_doc["tasks"]

    # Apply priority filter first if given
    if priority:
        tasks = [task for task in tasks if task.get("priority") == priority]

    if status:
        # Filter and possibly limit completed tasks
        tasks = [task for task in tasks if task.get("status") == status]
        if status == "completed":
            tasks.sort(key=lambda task: task.get("updated_at") or task.get("created_at") or "", reverse=True)
            tasks = tasks[:5]
    else:
        # Group tasks by status
        pending = [task for task in tasks if task.get("status") == "pending"]
        in_progress = [task for task in tasks if task.get("status") == "in_progress"]
        completed = [task for task in tasks if task.get("status") == "completed"]
        
        # Limit completed tasks to latest 5
        completed.sort(key=lambda task: task.get("updated_at") or task.get("created_at") or "", reverse=True)
        completed = completed[:5]

        tasks = pending + in_progress + completed

    print(f"Returning {len(tasks)} tasks for user {user_id}")
    return tasks


async def update_task_status(task_id: str = None, task_title: str = None, status: str = None, user_id: str = None) -> dict:
    """Update the status of a specific task by task_id or task_title"""
    print(f"Updating task status to {status} for user {user_id}")
    
    # Validate status
    valid_statuses = ["pending", "in_progress", "completed"]
    if status not in valid_statuses:
        print(f"Invalid status: {status}. Valid options: {valid_statuses}")
        return None
    
    if user_id is None:
        raise ValueError("Missing user_id in update_task_status() call!")
    
    # Find user document first
    user_doc = await task_list_collection.find_one({"user_id": user_id})
    if not user_doc or "tasks" not in user_doc:
        print(f"No tasks found for user {user_id}")
        return None
    
    # Find the task to update
    task_to_update = None
    task_index = None
    
    for i, task in enumerate(user_doc["tasks"]):
        if task_id and task.get("task_id") == task_id:
            task_to_update = task
            task_index = i
            break
        elif task_title and task_title.lower() in task.get("title", "").lower():
            task_to_update = task
            task_index = i
            break
    
    if not task_to_update:
        print(f"Task not found for user {user_id}")
        return None
    
    tz = pytz.timezone("Asia/Kuala_Lumpur")
    now = datetime.now(tz)
    
    # Update specific task in the array using array index
    result = await task_list_collection.update_one(
        {"user_id": user_id},
        {
            "$set": {
                f"tasks.{task_index}.status": status,
                f"tasks.{task_index}.updated_at": now,
                "updated_at": now
            }
        }
    )
    
    if result.modified_count > 0:
        print(f"Task updated successfully")
        # Return the updated task
        task_to_update["status"] = status
        task_to_update["updated_at"] = now
        return task_to_update
    else:
        print(f"Task update failed")
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
        "description": "Updates the status of a specific task by title or ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_title": {
                    "type": "string",
                    "description": "The title or partial title of the task to update"
                },
                "status": {
                    "type": "string",
                    "description": "New status: 'pending', 'in_progress', or 'completed'"
                }
            },
            "required": ["task_title", "status"]
        }
    }
}
