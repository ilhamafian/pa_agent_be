from pymongo import MongoClient
import os

MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)

db = client["oauth_db"]
oauth_states_collection = db["oauth_states"]
oauth_tokens_collection = db["oauth_tokens"]

def get_all_users():
    users = list(oauth_tokens_collection.find({}))
    return users if users else []
