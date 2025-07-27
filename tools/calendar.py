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
from db import oauth_states_collection, oauth_tokens_collection
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

    service = build('calendar', 'v3', credentials=creds)

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

def get_events(natural_range="today", user_id=None): 
    print("Entered get_events")
    print(f"[DEBUG] natural_range input: {natural_range}")
    print(f"[DEBUG] user_id: {user_id}")

    token_data = oauth_tokens_collection.find_one({"user_id": user_id})
    if not token_data:
        raise AuthRequiredError("AUTH_REQUIRED")

    creds = Credentials.from_authorized_user_info(token_data["token"], SCOPES)
    service = build("calendar", "v3", credentials=creds)

    tz = pytz.timezone("Asia/Kuala_Lumpur")
    now = datetime.now(tz)
    print(f"[DEBUG] current time: {now}")

    natural_range = natural_range.strip().lower()
    
   # Try parsing the range naturally (e.g., "tomorrow", "next week")
    date_range = dateparser.parse(
        natural_range,
        settings={"TIMEZONE": "Asia/Kuala_Lumpur", "RETURN_AS_TIMEZONE_AWARE": True}
    )
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
        start_time = date_range
        end_time = start_time + timedelta(days=1)
        print(f"[DEBUG] Single day match -> start: {start_time}, end: {end_time}")

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
        start = event["start"].get("dateTime", event["start"].get("date"))
        end = event["end"].get("dateTime", event["end"].get("date"))

        print(f"[DEBUG] Event raw -> title: {title}, start: {start}, end: {end}")

        try:
            start_dt = datetime.fromisoformat(start)
            end_dt = datetime.fromisoformat(end)
            time_range = f"{start_dt.strftime('%a %-I:%M%p')} until {end_dt.strftime('%-I:%M%p')}"
        except ValueError:
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