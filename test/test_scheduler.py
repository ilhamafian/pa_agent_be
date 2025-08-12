#!/usr/bin/env python3
"""
Test script for scheduler reminder jobs
This allows testing the reminder functionality without WhatsApp integration
"""

import asyncio
import pytz
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
import sys
import os

# Add the project root to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from tools.scheduler import (
    get_events_for_user_on_date, 
    format_combined_reminder,
    get_all_users
)
from tools.task import get_tasks
from utils.utils import decrypt_phone

# Mock WhatsApp message sending
async def mock_send_whatsapp_message(phone_number, message):
    """Mock version of send_whatsapp_message for testing"""
    print(f"\n📱 [MOCK WHATSAPP] Would send to {phone_number}:")
    print("=" * 60)
    print(message)
    print("=" * 60)
    return {"status": "success", "message_id": "mock_12345"}

def mock_get_event_loop():
    """Mock version of get_event_loop"""
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        return asyncio.new_event_loop()

async def test_today_reminder_job():
    """Test the today reminder job logic"""
    print("\n🌅 [TEST] Testing TODAY reminder job...")
    
    try:
        today = datetime.now(pytz.timezone("Asia/Kuala_Lumpur")).date()
        users = get_all_users() or []
        print(f"[TEST] Found {len(users)} users to test")
        
        if not users:
            print("⚠️  No users found in database. Add some test users first.")
            return
        
        for i, user in enumerate(users):
            print(f"\n--- Testing User {i+1}/{len(users)} ---")
            user_id = user.get("user_id")
            nickname = user.get("nickname")
            encrypted_phone = user.get("phone_number")
            
            print(f"User ID: {user_id}")
            print(f"Nickname: {nickname}")
            print(f"Has phone: {bool(encrypted_phone)}")
            
            # Skip user if essential data is missing
            if not user_id or not nickname or not encrypted_phone:
                print(f"❌ Skipping user due to missing data")
                continue
            
            try:
                decrypted_phone = decrypt_phone(encrypted_phone)
                if not decrypted_phone:
                    print(f"❌ Failed to decrypt phone number")
                    continue
                print(f"Phone: {decrypted_phone[:3]}***{decrypted_phone[-3:]}")  # Masked for privacy
            except Exception as decrypt_error:
                print(f"❌ Error decrypting phone: {decrypt_error}")
                continue
            
            # Fetch events for today
            print(f"📅 Fetching events for {today}...")
            events = get_events_for_user_on_date(user_id, today)
            print(f"Found {len(events)} events")
            
            # Fetch pending and in-progress tasks
            print(f"📝 Fetching tasks...")
            try:
                pending_tasks = get_tasks(user_id, status="pending") or []
                in_progress_tasks = get_tasks(user_id, status="in_progress") or []
                all_active_tasks = pending_tasks + in_progress_tasks
                print(f"Found {len(all_active_tasks)} active tasks ({len(pending_tasks)} pending, {len(in_progress_tasks)} in progress)")
            except Exception as task_error:
                print(f"❌ Error fetching tasks: {task_error}")
                all_active_tasks = []
            
            # Generate and "send" message
            if events or all_active_tasks:
                message = format_combined_reminder(events, all_active_tasks, nickname, is_tomorrow=False)
                await mock_send_whatsapp_message(decrypted_phone, message)
                print("✅ Message would be sent successfully")
            else:
                print("ℹ️  No events or active tasks - no message needed")
                
    except Exception as e:
        print(f"🔥 [TEST ERROR] {e}")
        import traceback
        traceback.print_exc()

async def test_tomorrow_reminder_job():
    """Test the tomorrow reminder job logic"""
    print("\n🌙 [TEST] Testing TOMORROW reminder job...")
    
    try:
        tomorrow = (datetime.now(pytz.timezone("Asia/Kuala_Lumpur")) + timedelta(days=1)).date()
        users = get_all_users() or []
        print(f"[TEST] Found {len(users)} users to test")
        
        if not users:
            print("⚠️  No users found in database. Add some test users first.")
            return
        
        for i, user in enumerate(users):
            print(f"\n--- Testing User {i+1}/{len(users)} ---")
            user_id = user.get("user_id")
            nickname = user.get("nickname")
            encrypted_phone = user.get("phone_number")
            
            print(f"User ID: {user_id}")
            print(f"Nickname: {nickname}")
            print(f"Has phone: {bool(encrypted_phone)}")
            
            # Skip user if essential data is missing
            if not user_id or not nickname or not encrypted_phone:
                print(f"❌ Skipping user due to missing data")
                continue
            
            try:
                decrypted_phone = decrypt_phone(encrypted_phone)
                if not decrypted_phone:
                    print(f"❌ Failed to decrypt phone number")
                    continue
                print(f"Phone: {decrypted_phone[:3]}***{decrypted_phone[-3:]}")  # Masked for privacy
            except Exception as decrypt_error:
                print(f"❌ Error decrypting phone: {decrypt_error}")
                continue
            
            # Fetch events for tomorrow
            print(f"📅 Fetching events for {tomorrow}...")
            events = get_events_for_user_on_date(user_id, tomorrow)
            print(f"Found {len(events)} events")
            
            # Fetch pending and in-progress tasks
            print(f"📝 Fetching tasks...")
            try:
                pending_tasks = get_tasks(user_id, status="pending") or []
                in_progress_tasks = get_tasks(user_id, status="in_progress") or []
                all_active_tasks = pending_tasks + in_progress_tasks
                print(f"Found {len(all_active_tasks)} active tasks ({len(pending_tasks)} pending, {len(in_progress_tasks)} in progress)")
            except Exception as task_error:
                print(f"❌ Error fetching tasks: {task_error}")
                all_active_tasks = []
            
            # Generate and "send" message
            if events or all_active_tasks:
                message = format_combined_reminder(events, all_active_tasks, nickname, is_tomorrow=True)
                await mock_send_whatsapp_message(decrypted_phone, message)
                print("✅ Message would be sent successfully")
            else:
                print("ℹ️  No events or active tasks - no message needed")
                
    except Exception as e:
        print(f"🔥 [TEST ERROR] {e}")
        import traceback
        traceback.print_exc()

def test_format_combined_reminder():
    """Test the message formatting with sample data"""
    print("\n📝 [TEST] Testing message formatting...")
    
    # Sample events data
    sample_events = [
        {
            "summary": "Team Meeting",
            "start": {"dateTime": "2025-01-13T09:00:00+08:00"},
            "end": {"dateTime": "2025-01-13T10:00:00+08:00"}
        },
        {
            "summary": "Lunch with Client",
            "start": {"dateTime": "2025-01-13T12:30:00+08:00"},
            "end": {"dateTime": "2025-01-13T13:30:00+08:00"}
        }
    ]
    
    # Sample tasks data
    sample_tasks = [
        {
            "title": "Complete project proposal",
            "status": "pending",
            "priority": "high"
        },
        {
            "title": "Review code changes",
            "status": "in_progress",
            "priority": "medium"
        },
        {
            "title": "Update documentation",
            "status": "pending",
            "priority": "low"
        }
    ]
    
    print("🌅 Today's reminder format:")
    today_message = format_combined_reminder(sample_events, sample_tasks, "John", is_tomorrow=False)
    print("=" * 60)
    print(today_message)
    print("=" * 60)
    
    print("\n🌙 Tomorrow's reminder format:")
    tomorrow_message = format_combined_reminder(sample_events, sample_tasks, "John", is_tomorrow=True)
    print("=" * 60)
    print(tomorrow_message)
    print("=" * 60)
    
    print("\n📭 Empty data format:")
    empty_message = format_combined_reminder([], [], "John", is_tomorrow=False)
    print("=" * 60)
    print(empty_message)
    print("=" * 60)

async def run_all_tests():
    """Run all tests"""
    print("🧪 Starting Scheduler Tests")
    print("=" * 80)
    
    # Test message formatting first (no database required)
    test_format_combined_reminder()
    
    # Test actual job logic (requires database)
    try:
        await test_today_reminder_job()
        await test_tomorrow_reminder_job()
    except Exception as e:
        print(f"❌ Database tests failed: {e}")
        print("Make sure your database is running and has test users")
    
    print("\n✅ All tests completed!")

def run_quick_format_test():
    """Quick test that doesn't require database"""
    print("🚀 Running quick format test (no database required)...")
    test_format_combined_reminder()

if __name__ == "__main__":
    print("Scheduler Test Script")
    print("1. Quick format test (no database)")
    print("2. Full test (requires database)")
    
    choice = input("Choose test (1 or 2): ").strip()
    
    if choice == "1":
        run_quick_format_test()
    elif choice == "2":
        asyncio.run(run_all_tests())
    else:
        print("Invalid choice. Running quick test...")
        run_quick_format_test()
