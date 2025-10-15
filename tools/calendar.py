from __future__ import print_function
import datetime
import os.path
import pytz
import dateparser
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
# GOOGLE CALENDAR IMPORTS - COMMENTED OUT
# from google.oauth2.credentials import Credentials
# from googleapiclient.discovery import build
# from googleapiclient.errors import HttpError
# from google.auth.exceptions import RefreshError
# from utils.utils import get_auth_url
from db.mongo import db, users_collection  # Import MongoDB connection
from dateutil.relativedelta import relativedelta
from tools.reminder import create_event_reminder
from bson import ObjectId

load_dotenv(dotenv_path=".env.local", override=True)  # Make sure environment variables are loaded

# GOOGLE CALENDAR SCOPES - COMMENTED OUT
# SCOPES = json.loads(os.getenv("SCOPES", "[]"))

# MongoDB Calendar Collection
calendar_collection = db["calendar"]

# Index creation moved to FastAPI lifespan in main.py
# Async index creation function for proper initialization
async def init_calendar_indexes():
    """Initialize calendar collection indexes"""
    try:
        await calendar_collection.create_index("user_id")
        await calendar_collection.create_index([("user_id", 1), ("start_time", 1)])
        print("✅ Created indexes on calendar collection")
    except Exception as e:
        print(f"⚠️ Calendar index creation failed (might already exist): {e}")

class AuthRequiredError(Exception):
    """Legacy exception for Google Calendar auth - kept for compatibility"""
    pass

async def create_event(time: str = None, end_time: str = None, date: str = None, title: str = None, user_id=None, description: str = None) -> dict:
    """Create a calendar event in MongoDB"""
    
    # ============ MONGODB IMPLEMENTATION ============
    if user_id is None:
        raise ValueError("Missing user_id in create_event() call!")
    
    print(f"Creating event for user_id: {user_id}")
    
    tz = pytz.timezone('Asia/Kuala_Lumpur')
    
    if time and end_time:
        # Timed event
        start_datetime = datetime.strptime(f"{date}T{time}:00", "%Y-%m-%dT%H:%M:%S")
        end_datetime = datetime.strptime(f"{date}T{end_time}:00", "%Y-%m-%dT%H:%M:%S")
        # Make timezone aware
        start_datetime = tz.localize(start_datetime)
        end_datetime = tz.localize(end_datetime)
        
        event = {
            'user_id': user_id,
            'summary': title,
            'description': description or "",
            'start': {
                'dateTime': start_datetime.isoformat(),
                'timeZone': 'Asia/Kuala_Lumpur',
            },
            'end': {
                'dateTime': end_datetime.isoformat(),
                'timeZone': 'Asia/Kuala_Lumpur',
            },
            'start_time': start_datetime,  # For efficient querying
            'end_time': end_datetime,
            'is_all_day': False,
            'created_at': datetime.now(tz),
            'updated_at': datetime.now(tz)
        }
    elif time:
         # When end time is not given
        start_datetime = datetime.strptime(f"{date}T{time}:00", "%Y-%m-%dT%H:%M:%S")
        end_datetime = start_datetime + timedelta(hours=1)
        # Make timezone aware
        start_datetime = tz.localize(start_datetime)
        end_datetime = tz.localize(end_datetime)
        
        event = {
            'user_id': user_id,
            'summary': title,
            'description': description or "",
            'start': {
                'dateTime': start_datetime.isoformat(),
                'timeZone': 'Asia/Kuala_Lumpur',
            },
            'end': {
                'dateTime': end_datetime.isoformat(),
                'timeZone': 'Asia/Kuala_Lumpur',
            },
            'start_time': start_datetime,  # For efficient querying
            'end_time': end_datetime,
            'is_all_day': False,
            'created_at': datetime.now(tz),
            'updated_at': datetime.now(tz)
        }
    else:
        # All-day event
        event_date = datetime.strptime(date, "%Y-%m-%d")
        event_date = tz.localize(event_date)
        
        event = {
            'user_id': user_id,
            'summary': title,
            'description': description or "",
            'start': {
                'date': date,
            },
            'end': {
                'date': date,
            },
            'start_time': event_date,  # For efficient querying
            'end_time': event_date + timedelta(days=1) - timedelta(seconds=1),
            'is_all_day': True,
            'created_at': datetime.now(tz),
            'updated_at': datetime.now(tz)
        }
    
    print("Creating event:", event)
    result = await calendar_collection.insert_one(event)
    event['_id'] = str(result.inserted_id)
    event['id'] = str(result.inserted_id)  # For compatibility
    
    print(f"✅ Event created: {event['summary']}")
    
    # Automatically create a reminder 15 minutes before the event (only for timed events)
    if not event.get('is_all_day', False):
        try:
            # Get user's phone number from database
            user_doc = await users_collection.find_one({"_id": ObjectId(user_id)})
            if user_doc and 'phone_number' in user_doc:
                phone_number = user_doc['phone_number']
                # Create event reminder
                await create_event_reminder(
                    event_title=event['summary'],
                    event_start_time=event['start_time'],
                    user_id=user_id,
                    phone_number=phone_number,
                    minutes_before=15
                )
            else:
                print(f"⚠️ Could not create reminder: phone number not found for user {user_id}")
        except Exception as e:
            print(f"⚠️ Failed to create event reminder: {e}")
            # Don't fail the event creation if reminder creation fails
    
    return event

async def update_event(user_id=None, original_title=None, new_title=None, new_date=None, new_start_time=None, new_end_time=None, new_description=None):
    """
    Update an existing calendar event by searching it with original_title (and optionally date).
    You can update title, date, time, and description.

    Parameters:
    - user_id: user identifier (required)
    - original_title: the title of the event to find (required)
    - new_title: new title to update to (optional)
    - new_date: new date in YYYY-MM-DD format (optional)
    - new_start_time: new start time in HH:MM 24-hour format (optional)
    - new_end_time: new end time in HH:MM 24-hour format (optional)
    - new_description: new description (optional)
    """
    
    # ============ GOOGLE CALENDAR CODE - COMMENTED OUT ============
    # if user_id is None or original_title is None:
    #     raise ValueError("user_id and original_title are required")
    # 
    # token_data = oauth_tokens_collection.find_one({"user_id": user_id})
    # if not token_data:
    #     raise AuthRequiredError("AUTH_REQUIRED")
    # 
    # try:
    #     creds = Credentials.from_authorized_user_info(token_data["token"], SCOPES)
    #     service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    # 
    #     tz = pytz.timezone("Asia/Kuala_Lumpur")
    #     if new_date:
    #         start_search = datetime.strptime(new_date, "%Y-%m-%d").replace(tzinfo=tz) - timedelta(days=7)
    #         end_search = datetime.strptime(new_date, "%Y-%m-%d").replace(tzinfo=tz) + timedelta(days=7)
    #     else:
    #         now = datetime.now(tz)
    #         start_search = now - timedelta(days=7)
    #         end_search = now + timedelta(days=7)
    # 
    #     events_result = service.events().list(
    #         calendarId='primary',
    #         timeMin=start_search.isoformat(),
    #         timeMax=end_search.isoformat(),
    #         q=original_title,
    #         singleEvents=True,
    #         orderBy='startTime'
    #     ).execute()
    # 
    #     events = events_result.get("items", [])
    #     if not events:
    #         return f"❌ No event found with title '{original_title}' in the search range."
    # 
    #     event = events[0]
    # 
    #     if new_title:
    #         event['summary'] = new_title
    #     if new_description is not None:
    #         event['description'] = new_description
    # 
    #     if new_date and new_start_time and new_end_time:
    #         start_datetime = f"{new_date}T{new_start_time}:00"
    #         end_datetime = f"{new_date}T{new_end_time}:00"
    #         event['start'] = {'dateTime': start_datetime, 'timeZone': 'Asia/Kuala_Lumpur'}
    #         event['end'] = {'dateTime': end_datetime, 'timeZone': 'Asia/Kuala_Lumpur'}
    #     elif new_date:
    #         event['start'] = {'date': new_date}
    #         event['end'] = {'date': new_date}
    # 
    #     updated_event = service.events().update(
    #         calendarId='primary',
    #         eventId=event['id'],
    #         body=event
    #     ).execute()
    # 
    #     def format_datetime(dt):
    #         if 'dateTime' in dt:
    #             dt_obj = datetime.fromisoformat(dt['dateTime'])
    #             return dt_obj.strftime("%Y-%m-%d %H:%M")
    #         elif 'date' in dt:
    #             return dt['date']
    #         else:
    #             return "Unknown"
    # 
    #     title = updated_event.get('summary', 'No Title')
    #     start_str = format_datetime(updated_event.get('start', {}))
    #     end_str = format_datetime(updated_event.get('end', {}))
    #     time_str = f"{start_str} to {end_str}" if start_str != end_str else start_str
    #     link = updated_event.get('htmlLink', 'No Link')
    # 
    #     return (
    #         f"✅ Event Updated\n\n"
    #         f"Title: {title}\n"
    #         f"Date & Time: {time_str}\n"
    #         f"Link: {link}"
    #     )
    # 
    # except (RefreshError, HttpError) as e:
    #     print(f"[DEBUG] Token error in update_event: {e}")
    #     if "invalid_grant" in str(e) or "Token has been expired or revoked" in str(e):
    #         raise AuthRequiredError("AUTH_REQUIRED")
    #     else:
    #         raise e
    # except Exception as e:
    #     print(f"[DEBUG] Unexpected error in update_event: {e}")
    #     if "invalid_grant" in str(e) or "Token has been expired or revoked" in str(e):
    #         raise AuthRequiredError("AUTH_REQUIRED")
    #     else:
    #         raise e
    # ============ END GOOGLE CALENDAR CODE ============
    
    # ============ MONGODB IMPLEMENTATION ============
    if user_id is None or original_title is None:
        raise ValueError("user_id and original_title are required")
    
    # Find the event using case-insensitive search
    event = await calendar_collection.find_one({
        "user_id": user_id,
        "summary": {"$regex": f"^{original_title}$", "$options": "i"}
    })
    
    if not event:
        return f"❌ No event found with title '{original_title}'."
    
    # Build update dict
    tz = pytz.timezone('Asia/Kuala_Lumpur')
    update_data = {"updated_at": datetime.now(tz)}
    
    if new_title:
        update_data['summary'] = new_title
    
    if new_description is not None:
        update_data['description'] = new_description
    
    # Update date/time if provided
    if new_date and new_start_time and new_end_time:
        start_datetime = tz.localize(datetime.strptime(f"{new_date}T{new_start_time}:00", "%Y-%m-%dT%H:%M:%S"))
        end_datetime = tz.localize(datetime.strptime(f"{new_date}T{new_end_time}:00", "%Y-%m-%dT%H:%M:%S"))
        
        update_data['start'] = {'dateTime': start_datetime.isoformat(), 'timeZone': 'Asia/Kuala_Lumpur'}
        update_data['end'] = {'dateTime': end_datetime.isoformat(), 'timeZone': 'Asia/Kuala_Lumpur'}
        update_data['start_time'] = start_datetime
        update_data['end_time'] = end_datetime
        update_data['is_all_day'] = False
    elif new_date:
        # All-day event
        update_data['start'] = {'date': new_date}
        update_data['end'] = {'date': new_date}
        event_date = tz.localize(datetime.strptime(new_date, "%Y-%m-%d"))
        update_data['start_time'] = event_date
        update_data['end_time'] = event_date + timedelta(days=1) - timedelta(seconds=1)
        update_data['is_all_day'] = True
    
    # Perform update
    await calendar_collection.update_one(
        {"_id": event["_id"]},
        {"$set": update_data}
    )
    
    # Fetch updated event
    updated_event = await calendar_collection.find_one({"_id": event["_id"]})
    
    # Format response
    def format_datetime(dt):
        if 'dateTime' in dt:
            dt_obj = datetime.fromisoformat(dt['dateTime'])
            return dt_obj.strftime("%Y-%m-%d %H:%M")
        elif 'date' in dt:
            return dt['date']
        else:
            return "Unknown"
    
    title = updated_event.get('summary', 'No Title')
    start_str = format_datetime(updated_event.get('start', {}))
    end_str = format_datetime(updated_event.get('end', {}))
    time_str = f"{start_str} to {end_str}" if start_str != end_str else start_str
    
    return (
        f"✅ Event Updated\n\n"
        f"Title: {title}\n"
        f"Date & Time: {time_str}"
    )


async def get_events(natural_range="today", user_id=None): 
    """Fetch calendar events from MongoDB using natural language time range"""
    print("Entered get_events")
    print(f"[DEBUG] natural_range input: {natural_range}")
    print(f"[DEBUG] user_id: {user_id}")
    
    # ============ GOOGLE CALENDAR CODE - COMMENTED OUT ============
    # token_data = oauth_tokens_collection.find_one({"user_id": user_id})
    # if not token_data:
    #     raise AuthRequiredError("AUTH_REQUIRED")
    # 
    # try:
    #     creds = Credentials.from_authorized_user_info(token_data["token"], SCOPES)
    #     service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    #     
    #     # ... rest of Google Calendar API code ...
    #     
    #     events_result = service.events().list(
    #         calendarId='primary',
    #         timeMin=start_time.isoformat(),
    #         timeMax=end_time.isoformat(),
    #         singleEvents=True,
    #         orderBy='startTime'
    #     ).execute()
    #     events = events_result.get("items", [])
    # 
    # except (RefreshError, HttpError) as e:
    #     print(f"[DEBUG] Token error: {e}")
    #     if "invalid_grant" in str(e) or "Token has been expired or revoked" in str(e):
    #         auth_url = get_auth_url(user_id)
    #         return (
    #             f"🔐 Oops! It seems like you haven't given me access to your calendar yet. "
    #             f"Please authorize access through this link:\n{auth_url}\n\n"
    #             f"Alternatively, you can manage your external app integration through your dashboard:\n"
    #             f"https://lofy-assistant.vercel.app/dashboard/integration"
    #         )
    #     else:
    #         raise e
    # except Exception as e:
    #     print(f"[DEBUG] Unexpected error in get_events: {e}")
    #     if "invalid_grant" in str(e) or "Token has been expired or revoked" in str(e):
    #         auth_url = get_auth_url(user_id)
    #         return (
    #             f"🔐 Oops! It seems like you haven't given me access to your calendar yet. "
    #             f"Please authorize access through this link:\n{auth_url}\n\n"
    #             f"Alternatively, you can manage your external app integration through your dashboard:\n"
    #             f"https://lofy-assistant.vercel.app/dashboard/integration"
    #         )
    #     else:
    #         raise e
    # ============ END GOOGLE CALENDAR CODE ============
    
    # ============ MONGODB IMPLEMENTATION ============
    if user_id is None:
        raise ValueError("Missing user_id in get_events() call!")
    
    tz = pytz.timezone("Asia/Kuala_Lumpur")
    now = datetime.now(tz)
    print(f"[DEBUG] current time: {now}")

    natural_range = natural_range.strip().lower()
    
    # Try parsing the range naturally (e.g., "tomorrow", "next week", weekday names)
    date_range = dateparser.parse(
        natural_range,
        settings={
            "TIMEZONE": "Asia/Kuala_Lumpur",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
        }
    )
    # If a weekday parsed to a past date relative to now, bump to next week
    if date_range and date_range < now:
        date_range = date_range + timedelta(days=7)
    print(f"[DEBUG] Parsed date_range: {date_range}")

    # Check if input looks like a month (e.g., "july 2025") but not a specific date
    month_keywords = [
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december"
    ]
    # Only consider it a month request if:
    # 1. Contains a month name AND
    # 2. Does NOT contain day indicators (numbers 1-31, "st", "nd", "rd", "th")
    has_month = any(m in natural_range for m in month_keywords)
    has_day_indicator = any(str(i) in natural_range for i in range(1, 32)) or any(suffix in natural_range for suffix in ["st", "nd", "rd", "th"])
    is_month = has_month and not has_day_indicator

    if is_month and date_range:
        start_time = date_range.replace(day=1)
        end_time = (start_time + relativedelta(months=1)) - timedelta(seconds=1)
        print(f"[DEBUG] Detected month -> start: {start_time}, end: {end_time}")

    elif " to " in natural_range or " until " in natural_range:
        parts = natural_range.split(" to ") if " to " in natural_range else natural_range.split(" until ")
        parts = [p.strip() for p in parts]
        start = dateparser.parse(parts[0], settings={"TIMEZONE": "Asia/Kuala_Lumpur", "RETURN_AS_TIMEZONE_AWARE": True})
        end = dateparser.parse(parts[1], settings={"TIMEZONE": "Asia/Kuala_Lumpur", "RETURN_AS_TIMEZONE_AWARE": True})
        if not start or not end:
            return "❌ Sorry, I couldn't understand that date range."
        start_time = start
        end_time = end + timedelta(hours=23, minutes=59)
        print(f"[DEBUG] Range match -> start: {start_time}, end: {end_time}")

    elif date_range:
        # Normalize to full-day window in Asia/Kuala_Lumpur
        start_time = date_range.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        end_time = start_time + timedelta(days=1) - timedelta(seconds=1)
        print(f"[DEBUG] Single day match (normalized) -> start: {start_time}, end: {end_time}")

    else:
        print(f"[DEBUG] Failed to parse: {natural_range}")
        return "❌ Sorry, I couldn't understand that time."

    # Fetch events from MongoDB
    print(f"[DEBUG] Fetching events from {start_time.isoformat()} to {end_time.isoformat()}")
    events = list(await calendar_collection.find({
        "user_id": user_id,
        "start_time": {"$gte": start_time, "$lte": end_time}
    }).sort("start_time", 1))  # Sort by start_time ascending
    
    print(f"[DEBUG] Number of events fetched: {len(events)}")

    if not events:
        return f"📅 You have no events for {natural_range}."

    # Format the events
    reply_lines = [f"📅 Events for '{natural_range}':"]
    for event in events:
        title = event.get("summary", "No Title")
        start_dt_str = event["start"].get("dateTime")
        end_dt_str = event["end"].get("dateTime")
        all_day_start = event["start"].get("date") is not None
        all_day_end = event["end"].get("date") is not None

        print(f"[DEBUG] Event raw -> title: {title}, start: {start_dt_str or event['start'].get('date')}, end: {end_dt_str or event['end'].get('date')}")

        if all_day_start or all_day_end:
            time_range = "All-day"
        else:
            try:
                start_dt = datetime.fromisoformat(start_dt_str)
                end_dt = datetime.fromisoformat(end_dt_str)
                time_range = f"{start_dt.strftime('%a %-I:%M%p')} until {end_dt.strftime('%-I:%M%p')}"
            except Exception:
                time_range = "All-day"

        reply_lines.append(f"{title} - {time_range}")

    return "\n".join(reply_lines)


async def delete_event(user_id=None, title=None):
    """Delete a calendar event from MongoDB"""
    if user_id is None or title is None:
        raise ValueError("user_id and title are required")
    
    # Find and delete the event
    result = await calendar_collection.delete_one({
        "user_id": user_id,
        "summary": {"$regex": f"^{title}$", "$options": "i"}
    })
    
    if result.deleted_count > 0:
        return f"✅ Event '{title}' has been deleted."
    else:
        return f"❌ No event found with title '{title}'."


create_event_tool = {
    "type": "function",
    "function": {
        "name": "create_event",
        "description": "Creates a calendar event. If time is not provided, it will be an all-day event.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Title of the event"
                },
                "date": {
                    "type": "string",
                    "description": "Date of the event in YYYY-MM-DD format"
                },
                "time": {
                    "type": "string",
                    "description": "Start time (24-hour format, e.g., 09:00)"
                },
                "end_time": {
                    "type": "string",
                    "description": "End time (24-hour format, e.g., 10:00)"
                },
                "description": {
                    "type": "string",
                    "description": "Optional additional details or notes about the event"
                }
            },
            "required": ["title", "date"]
        }
    }
}

update_event_tool = {
    "type": "function",
    "function": {
        "name": "update_event",
        "description": "Updates an existing calendar event by searching with the original title and optionally date. You can update the title, date, start/end times, and description.",
        "parameters": {
            "type": "object",
            "properties": {
                "original_title": {
                    "type": "string",
                    "description": "The original title of the event to find and update"
                },
                "new_title": {
                    "type": "string",
                    "description": "New title to update the event to"
                },
                "new_date": {
                    "type": "string",
                    "description": "New date of the event in YYYY-MM-DD format"
                },
                "new_start_time": {
                    "type": "string",
                    "description": "New start time (24-hour format, e.g., 09:00)"
                },
                "new_end_time": {
                    "type": "string",
                    "description": "New end time (24-hour format, e.g., 10:00)"
                },
                "new_description": {
                    "type": "string",
                    "description": "New description or notes about the event"
                }
            },
            "required": ["original_title"]
        }
    }
}


get_events_tool = {
    "type": "function",
    "function": {
        "name": "get_events",
        "description": "Fetches calendar events using a natural language time range.",
        "parameters": {
            "type": "object",
            "properties": {
                "natural_range": {
                    "type": "string",
                    "description": (
                        "A natural language description of the time range to fetch events for. "
                        "Examples include: 'today', 'tomorrow', 'next week', 'this weekend', "
                        "'Friday to Sunday', 'August 1st until August 5th', or even 'in 3 days'."
                    )
                },
                
            },
            "required": ["natural_range"]
        }
    }
}

delete_event_tool = {
    "type": "function",
    "function": {
        "name": "delete_event",
        "description": "Deletes a calendar event by searching for its title.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "The title of the event to delete"
                }
            },
            "required": ["title"]
        }
    }
}