from __future__ import print_function
import datetime
import os.path
import pytz
import dateparser
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from db.mongo import oauth_tokens_collection
from dateutil.relativedelta import relativedelta

load_dotenv()  # Make sure environment variables are loaded

# If modifying these SCOPES, delete the file token.json.
SCOPES = json.loads(os.getenv("SCOPES", "[]"))

class AuthRequiredError(Exception):
    pass

def create_event(time: str = None, end_time: str = None, date: str = None, title: str = None, user_id=None, description: str = None) -> dict:
    token_data = oauth_tokens_collection.find_one({"user_id": user_id})
    print("Looking for user_id:", user_id, type(user_id))

    if user_id is None:
        raise ValueError("Missing user_id in create_event() call!")
    print("Token data: ", token_data)

    if not token_data:
        raise AuthRequiredError("AUTH_REQUIRED")

    creds = Credentials(
        token=token_data["token"]["token"],
        refresh_token=token_data["token"]["refresh_token"],
        token_uri=token_data["token"]["token_uri"],
        client_id=token_data["token"]["client_id"],
        client_secret=token_data["token"]["client_secret"],
        scopes=token_data["token"]["scopes"],
    )

    service = build('calendar', 'v3', credentials=creds, cache_discovery=False)

    if time and end_time:
        # Timed event
        start_datetime = f"{date}T{time}:00"
        end_datetime = f"{date}T{end_time}:00"
        event = {
            'summary': title,
            'description': description or "",  # Include description if provided
            'start': {
                'dateTime': start_datetime,
                'timeZone': 'Asia/Kuala_Lumpur',
            },
            'end': {
                'dateTime': end_datetime,
                'timeZone': 'Asia/Kuala_Lumpur',
            },
        }
    else:
        # All-day event
        event = {
            'summary': title,
            'description': description or "",  # Include description if provided
            'start': {
                'date': date,
            },
            'end': {
                'date': date,
            },
        }

    print("In event:", event)
    event = service.events().insert(calendarId='primary', body=event).execute()
    print('Event created:', event.get('htmlLink'))
    return event

def update_event(user_id=None, original_title=None, new_title=None, new_date=None, new_start_time=None, new_end_time=None, new_description=None):
    """
    Update an existing Google Calendar event by searching it with original_title (and optionally date).
    You can update title, date, time, and description.

    Parameters:
    - user_id: user identifier for OAuth token lookup (required)
    - original_title: the title of the event to find (required)
    - new_title: new title to update to (optional)
    - new_date: new date in YYYY-MM-DD format (optional)
    - new_start_time: new start time in HH:MM 24-hour format (optional)
    - new_end_time: new end time in HH:MM 24-hour format (optional)
    - new_description: new description (optional)
    """
    if user_id is None or original_title is None:
        raise ValueError("user_id and original_title are required")

    token_data = oauth_tokens_collection.find_one({"user_id": user_id})
    if not token_data:
        raise AuthRequiredError("AUTH_REQUIRED")

    creds = Credentials.from_authorized_user_info(token_data["token"], SCOPES)
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    # Define a time window to search for the event, e.g., +/- 7 days around new_date or today if new_date is None
    tz = pytz.timezone("Asia/Kuala_Lumpur")
    if new_date:
        start_search = datetime.strptime(new_date, "%Y-%m-%d").replace(tzinfo=tz) - timedelta(days=7)
        end_search = datetime.strptime(new_date, "%Y-%m-%d").replace(tzinfo=tz) + timedelta(days=7)
    else:
        now = datetime.now(tz)
        start_search = now - timedelta(days=7)
        end_search = now + timedelta(days=7)

    events_result = service.events().list(
        calendarId='primary',
        timeMin=start_search.isoformat(),
        timeMax=end_search.isoformat(),
        q=original_title,
        singleEvents=True,
        orderBy='startTime'
    ).execute()

    events = events_result.get("items", [])
    if not events:
        return f"❌ No event found with title '{original_title}' in the search range."

    # For demo, pick the first matched event (you can enhance by matching exact title or other criteria)
    event = events[0]

    # Update fields if provided
    if new_title:
        event['summary'] = new_title
    if new_description is not None:
        event['description'] = new_description

    # If new date/time provided, update start and end accordingly
    if new_date and new_start_time and new_end_time:
        start_datetime = f"{new_date}T{new_start_time}:00"
        end_datetime = f"{new_date}T{new_end_time}:00"
        event['start'] = {'dateTime': start_datetime, 'timeZone': 'Asia/Kuala_Lumpur'}
        event['end'] = {'dateTime': end_datetime, 'timeZone': 'Asia/Kuala_Lumpur'}
    elif new_date:
        # If only date, treat as all-day event
        event['start'] = {'date': new_date}
        event['end'] = {'date': new_date}

    updated_event = service.events().update(
        calendarId='primary',
        eventId=event['id'],
        body=event
    ).execute()

    return f"✅ Event updated: {updated_event.get('htmlLink')}"


def get_events(natural_range="today", user_id=None): 
    print("Entered get_events")
    print(f"[DEBUG] natural_range input: {natural_range}")
    print(f"[DEBUG] user_id: {user_id}")

    token_data = oauth_tokens_collection.find_one({"user_id": user_id})
    if not token_data:
        raise AuthRequiredError("AUTH_REQUIRED")

    creds = Credentials.from_authorized_user_info(token_data["token"], SCOPES)
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

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

    # Check if input looks like a month (e.g., "july 2025")
    month_keywords = [
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december"
    ]
    is_month = any(m in natural_range for m in month_keywords)

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

    # Fetch events
    print(f"[DEBUG] Fetching events from {start_time.isoformat()} to {end_time.isoformat()}")
    events_result = service.events().list(
        calendarId='primary',
        timeMin=start_time.isoformat(),
        timeMax=end_time.isoformat(),
        singleEvents=True,
        orderBy='startTime'
    ).execute()

    events = events_result.get("items", [])
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
                "user_id": {
                    "type": "string",
                    "description": "User identifier for OAuth token lookup"
                },
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
            "required": ["user_id", "original_title"]
        }
    }
}


get_events_tool = {
    "type": "function",
    "function": {
        "name": "get_events",
        "description": "Fetches Google Calendar events using a natural language time range.",
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