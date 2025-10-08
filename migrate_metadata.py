from db.mongo import users_collection

def migrate_metadata(doc):
    metadata = doc.get("metadata", {})

    # Step 1: Remove q1 and q3 if they exist
    metadata.pop("q1", None)
    metadata.pop("q3", None)

    # Step 2: Rename q2 → profession
    if "q2" in metadata:
        metadata["profession"] = metadata.pop("q2")

    # Step 3: Rename q4 → source
    if "q4" in metadata:
        metadata["source"] = metadata.pop("q4")

    # Step 4: Add about_yourself if missing
    if "about_yourself" not in metadata:
        metadata["about_yourself"] = ""

    return metadata

# Update all documents
for doc in users_collection.find():
    new_metadata = migrate_metadata(doc)
    users_collection.update_one(
        {"_id": doc["_id"]},
        {
            "$set": {
                "metadata": new_metadata,
                "onboarding_completed": True
            }
        }
    )

print("✅ Migration completed!")