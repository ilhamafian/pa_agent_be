"""
Script to run FastAPI server with proper reload configuration
"""
import uvicorn
import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    
    # Run with reload but exclude directories that shouldn't trigger restarts
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=True,
        reload_excludes=[
            "__pycache__",
            "*.pyc",
            "*.pyo",
            "*.log",
            ".env.local",
            ".env",
            "venv/*",
            "test/*",
            ".git/*",
            "*.db",
            "*.sqlite",
        ],
        # Only watch specific directories
        reload_dirs=[".", "routers", "tools", "ai", "db", "utils"],
    )

