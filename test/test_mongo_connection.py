"""
Test script to diagnose MongoDB connection and query issues
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from db.mongo import get_all_users, client
import time

def test_connection():
    """Test basic MongoDB connection"""
    print("\n=== Testing MongoDB Connection ===")
    try:
        start = time.time()
        result = client.admin.command('ping')
        elapsed = time.time() - start
        print(f"✅ Ping successful in {elapsed:.3f}s: {result}")
        return True
    except Exception as e:
        print(f"❌ Ping failed: {e}")
        return False

def test_user_count():
    """Test getting user count without fetching all data"""
    print("\n=== Testing User Count ===")
    try:
        from db.mongo import users_collection
        start = time.time()
        count = users_collection.count_documents({})
        elapsed = time.time() - start
        print(f"✅ Total users: {count} (query took {elapsed:.3f}s)")
        return count
    except Exception as e:
        print(f"❌ Count failed: {e}")
        return None

def test_get_all_users():
    """Test fetching all users"""
    print("\n=== Testing get_all_users() ===")
    start = time.time()
    try:
        users = get_all_users()
        elapsed = time.time() - start
        print(f"✅ Successfully fetched {len(users)} users in {elapsed:.3f}s")
        
        # Show sample user structure
        if users:
            sample = users[0]
            print(f"\nSample user fields: {list(sample.keys())}")
            print(f"Fields should only be: _id, phone_number, user_id")
        
        return users
    except Exception as e:
        elapsed = time.time() - start
        print(f"❌ get_all_users failed after {elapsed:.3f}s: {e}")
        import traceback
        traceback.print_exc()
        return None

def main():
    print("="*80)
    print("MongoDB Connection Diagnostic Tool")
    print("="*80)
    
    # Test 1: Connection
    if not test_connection():
        print("\n⚠️ Cannot proceed - MongoDB connection failed")
        return
    
    # Test 2: Count users
    count = test_user_count()
    if count is None:
        print("\n⚠️ Cannot proceed - User count failed")
        return
    
    # Test 3: Fetch all users
    print(f"\n⚠️ About to fetch {count} users from database...")
    input("Press Enter to continue...")
    
    users = test_get_all_users()
    
    print("\n" + "="*80)
    if users:
        print(f"✅ All tests passed! Successfully fetched {len(users)} users")
    else:
        print("❌ Test failed - see errors above")
    print("="*80)

if __name__ == "__main__":
    main()

