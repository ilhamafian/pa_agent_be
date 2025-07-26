from __future__ import print_function
import datetime
import os.path
import pytz
import dateparser
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow, Flow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from db import oauth_states_collection, oauth_tokens_collection
from dateutil.relativedelta import relativedelta


# If modifying these SCOPES, delete the file token.json.
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events"
]

class AuthRequiredError(Exception):
    pass

def get_auth_url(user_id):
    print("Entered get_auth_url")
    flow = Flow.from_client_secrets_file(
        "credentials.json",
        scopes=SCOPES,
        redirect_uri="https://1bb8ed3755d1.ngrok-free.app/auth/callback"  # 🔁 You handle this below
    )

    print("In flow: ", flow);

    auth_url, state = flow.authorization_url(
        prompt='consent',
        access_type='offline',
        include_granted_scopes='true'
    )

    # You should save the `state` with the user_id (in memory or DB)
    # Example:
    oauth_states_collection.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "state": state,
                "created_at": datetime.now()
            }
        },
        upsert=True
    )

    return auth_url

def create_event(time: str = None, end_time: str = None, date: str = None, title: str = None, user_id=None, description: str = None, location: str =None) -> dict:
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
    token_data = oauth_tokens_collection.find_one({"user_id": user_id})

    if not token_data:
        raise AuthRequiredError("AUTH_REQUIRED")

    creds = Credentials.from_authorized_user_info(token_data["token"], SCOPES)
    service = build("calendar", "v3", credentials=creds)

    tz = pytz.timezone("Asia/Kuala_Lumpur")
    now = datetime.now(tz)

    natural_range = natural_range.strip().lower()
    
    # Try parsing "september", "july 2025", etc.
    month_match = dateparser.parse(f"1 {natural_range}", settings={"TIMEZONE": "Asia/Kuala_Lumpur", "RETURN_AS_TIMEZONE_AWARE": True})
    
    if month_match:
        start_time = month_match.replace(day=1)
        end_time = (start_time + relativedelta(months=1)) - timedelta(seconds=1)
    
    elif " to " in natural_range or " until " in natural_range:
        parts = natural_range.split(" to ") if " to " in natural_range else natural_range.split(" until ")
        start = dateparser.parse(parts[0], settings={"TIMEZONE": "Asia/Kuala_Lumpur", "RETURN_AS_TIMEZONE_AWARE": True})
        end = dateparser.parse(parts[1], settings={"TIMEZONE": "Asia/Kuala_Lumpur", "RETURN_AS_TIMEZONE_AWARE": True})
        if not start or not end:
            return "❌ Sorry, I couldn't understand that date range."
        start_time = start
        end_time = end + timedelta(hours=23, minutes=59)
    
    else:
        date_range = dateparser.parse(natural_range, settings={"TIMEZONE": "Asia/Kuala_Lumpur", "RETURN_AS_TIMEZONE_AWARE": True})
        if not date_range:
            return "❌ Sorry, I couldn't understand that time."
        start_time = date_range
        end_time = start_time + timedelta(days=1)

    # Fetch events
    events_result = service.events().list(
        calendarId='primary',
        timeMin=start_time.isoformat(),
        timeMax=end_time.isoformat(),
        singleEvents=True,
        orderBy='startTime'
    ).execute()

    events = events_result.get("items", [])

    if not events:
        return f"📅 You have no events for {natural_range}."

    # Format the events
    reply_lines = [f"📅 Events for '{natural_range}':"]
    for event in events:
        title = event.get("summary", "No Title")
        start = event["start"].get("dateTime", event["start"].get("date"))
        end = event["end"].get("dateTime", event["end"].get("date"))

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
        "description": "Retrieve events from your Google Calendar using natural language time ranges.",
        "parameters": {
            "type": "object",
            "properties": {
                "natural_range": {
                    "type": "string",
                    "description": (
                        "The time range to fetch events for. Accepts natural language such as "
                        "'today', 'tomorrow', 'this weekend', 'Friday', 'next 3 days', "
                        "or a specific range like 'July 10 to July 14'."
                    )
                }
            },
            "required": ["natural_range"]
        }
    }
}