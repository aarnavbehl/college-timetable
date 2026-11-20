from fastapi import APIRouter

from . import auth, college, history, imports, insights, resources, scenarios, substitutions, timetable

api_router = APIRouter()
for module in (auth, college, resources, imports, timetable, insights, scenarios, history, substitutions):
    api_router.include_router(module.router)
