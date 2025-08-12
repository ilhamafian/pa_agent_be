#!/usr/bin/env python3
"""
Simple test runner for scheduler functionality
Run this to test your reminder jobs without deployment
"""

import os
import sys
from dotenv import load_dotenv

# Load environment variables and set test mode
load_dotenv()
os.environ["SCHEDULER_TEST_MODE"] = "true"

# Add the project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)
print(f"Project root: {project_root}")  # Debug print

def test_today_reminder():
    """Test today's reminder job"""
    from tools.scheduler import start_scheduler
    
    print("🌅 Testing TODAY's reminder job...")
    print("Setting up scheduler...")
    
    # Import the functions we need after setting test mode
    from tools.scheduler import trigger_today_reminder_manually
    
    # Start scheduler to initialize everything
    start_scheduler()
    
    # Manually trigger the job
    trigger_today_reminder_manually()

def test_tomorrow_reminder():
    """Test tomorrow's reminder job"""
    from tools.scheduler import start_scheduler
    
    print("🌙 Testing TOMORROW's reminder job...")
    print("Setting up scheduler...")
    
    # Import the functions we need after setting test mode
    from tools.scheduler import trigger_tomorrow_reminder_manually
    
    # Start scheduler to initialize everything
    start_scheduler()
    
    # Manually trigger the job
    trigger_tomorrow_reminder_manually()

def test_both():
    """Test both reminder jobs"""
    print("🧪 Testing BOTH reminder jobs...")
    test_today_reminder()
    print("\n" + "="*60 + "\n")
    test_tomorrow_reminder()

if __name__ == "__main__":
    print("📋 Scheduler Test Runner")
    print("This will test your reminder jobs without sending actual WhatsApp messages")
    print("=" * 70)
    
    print("\nChoose test:")
    print("1. Today's reminder (9 AM job)")
    print("2. Tomorrow's reminder (10:20 PM job)")
    print("3. Both")
    print("4. Exit")
    
    choice = input("\nEnter choice (1-4): ").strip()
    
    try:
        if choice == "1":
            test_today_reminder()
        elif choice == "2":
            test_tomorrow_reminder()
        elif choice == "3":
            test_both()
        elif choice == "4":
            print("👋 Goodbye!")
            sys.exit(0)
        else:
            print("❌ Invalid choice")
            sys.exit(1)
            
        print("\n✅ Test completed!")
        
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
