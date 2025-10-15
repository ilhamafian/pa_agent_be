"""
Cloud Tasks utility functions for scheduling recurring and one-time tasks.
"""
import os
import json
from datetime import datetime, time, timedelta
from google.cloud import tasks_v2
import pytz
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env.local", override=True)

# Check if running in Cloud Run
IN_CLOUD_RUN = bool(os.getenv("K_SERVICE"))  # K_SERVICE is automatically set in Cloud Run


async def schedule_daily_task(endpoint_url: str, task_name: str, hour: int, minute: int, timezone_str: str = "Asia/Kuala_Lumpur", request_body: dict = None):
    """
    Schedule a recurring daily task using Cloud Tasks.
    
    Since Cloud Tasks doesn't natively support recurring tasks, this function
    schedules the next occurrence. The endpoint itself should reschedule
    the next occurrence after completion.
    
    Args:
        endpoint_url: The full URL of the endpoint to call
        task_name: Unique name for the task
        hour: Hour of the day (0-23) in the specified timezone
        minute: Minute of the hour (0-59)
        timezone_str: Timezone for scheduling (default: Asia/Kuala_Lumpur)
        request_body: Optional custom request body dict (e.g., {"user_id": "123"})
    
    Returns:
        Task response from Cloud Tasks
    """
    client = tasks_v2.CloudTasksAsyncClient()
    
    project = os.getenv("GOOGLE_PROJECT_ID")
    queue = os.getenv("QUEUE_ID")
    location = os.getenv("QUEUE_LOCATION")
    
    # Construct queue path
    parent = client.queue_path(project, location, queue)
    
    # Calculate next occurrence
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)
    
    # Create datetime for today at the specified time
    target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    # If the time has already passed today, schedule for tomorrow
    if target_time <= now:
        target_time = target_time + timedelta(days=1)
    
    # Convert to UTC for Cloud Tasks
    target_time_utc = target_time.astimezone(pytz.UTC)
    
    # Use custom request body or default
    body_data = request_body if request_body else {"scheduled": True}
    
    # Build task
    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": endpoint_url,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body_data).encode(),
        },
        "schedule_time": target_time_utc
    }
    
    try:
        # Try to create the task
        response = await client.create_task(request={"parent": parent, "task": task})
        print(f"✅ Daily task '{task_name}' scheduled for {target_time.strftime('%Y-%m-%d %H:%M:%S %Z')} — Task: {response.name}")
        return response
    except Exception as e:
        # If task already exists, delete and recreate
        if "ALREADY_EXISTS" in str(e):
            print(f"⚠️ Task '{task_name}' already exists, deleting and recreating...")
            try:
                await client.delete_task(name=task["name"])
                print(f"✅ Deleted existing task '{task_name}'")
                # Recreate without the name field to let Cloud Tasks generate a new one
                task_without_name = {
                    "http_request": task["http_request"],
                    "schedule_time": task["schedule_time"]
                }
                response = await client.create_task(request={"parent": parent, "task": task_without_name})
                print(f"✅ Daily task '{task_name}' rescheduled for {target_time.strftime('%Y-%m-%d %H:%M:%S %Z')} — Task: {response.name}")
                return response
            except Exception as delete_error:
                print(f"❌ Failed to delete and recreate task '{task_name}': {delete_error}")
                raise
        else:
            print(f"❌ Failed to schedule daily task '{task_name}': {e}")
            raise