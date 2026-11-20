"""Start the app:  python run.py   ->  http://localhost:8000"""
import uvicorn

from app.config import get_settings

if __name__ == "__main__":
    port = get_settings().port
    print(f"\n  College Timetable Scheduler  ->  http://localhost:{port}\n"
          f"  API docs                     ->  http://localhost:{port}/docs\n")
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, reload=True)
