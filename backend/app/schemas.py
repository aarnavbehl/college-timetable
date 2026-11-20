"""Request bodies (pydantic). Field limits here give the user friendly 422 errors."""
from __future__ import annotations

import re
from datetime import date as _date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RoomType = Literal["CLASSROOM", "LAB"]
CourseType = Literal["THEORY", "LAB"]


class Body(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class RegisterIn(Body):
    college_name: str = Field(min_length=2, max_length=120)
    email: str = Field(max_length=160)
    password: str = Field(min_length=6, max_length=72)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("Enter a valid email address")
        return v


class LoginIn(Body):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class ChangePasswordIn(Body):
    current_password: str
    new_password: str = Field(min_length=6, max_length=72)


class SettingsIn(Body):
    days_per_week: int = Field(ge=1, le=7)
    periods_per_day: int = Field(ge=1, le=12)
    start_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    period_minutes: int = Field(ge=20, le=180)
    lunch_after: int = Field(ge=0, le=11)
    lunch_minutes: int = Field(ge=0, le=180)

    @model_validator(mode="after")
    def _lunch(self):
        if self.lunch_after >= self.periods_per_day:
            raise ValueError("Lunch break must come after a period that is not the last one (use 0 for none)")
        return self


class BranchIn(Body):
    code: str = Field(min_length=1, max_length=12)
    name: str = Field(min_length=1, max_length=120)


class RoomIn(Body):
    name: str = Field(min_length=1, max_length=60)
    room_type: RoomType = "CLASSROOM"
    capacity: int = Field(ge=1, le=2000)
    is_active: bool = True


class SectionIn(Body):
    branch_id: int
    year: int = Field(ge=1, le=6)
    name: str = Field(min_length=1, max_length=10)
    strength: int = Field(ge=1, le=1000)
    batches: int = Field(default=1, ge=1, le=6)
    home_room_id: int | None = None


class FacultyIn(Body):
    name: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=12)
    branch_id: int | None = None
    max_per_day: int = Field(default=5, ge=1, le=12)
    max_per_week: int = Field(default=20, ge=1, le=84)
    unavailable: list[list[int]] = Field(default_factory=list)

    @field_validator("unavailable")
    @classmethod
    def _pairs(cls, v):
        if any(len(x) != 2 for x in v):
            raise ValueError("unavailable must be a list of [day, period] pairs")
        return v


class CourseIn(Body):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=120)
    branch_id: int | None = None
    course_type: CourseType = "THEORY"
    hours_per_week: int = Field(ge=1, le=30)
    session_length: int = Field(default=1, ge=1, le=6)


class AllocationIn(Body):
    section_id: int
    course_id: int
    faculty_id: int
    hours_per_week: int | None = Field(default=None, ge=1, le=30)
    batch: int | None = Field(default=None, ge=1, le=6)
    sync_group: str | None = Field(default=None, max_length=40)
    students: int | None = Field(default=None, ge=1, le=1000)

    @field_validator("sync_group")
    @classmethod
    def _blank(cls, v):
        return v or None


class GenerateIn(Body):
    time_limit: int = Field(default=15, ge=1, le=120)
    seed: int | None = None


class MoveIn(Body):
    day: int = Field(ge=0, le=6)
    start: int = Field(ge=1, le=12)
    force: bool = False


class SubstituteIn(Body):
    date: str
    absent_faculty_ids: list[int] = Field(min_length=1)

    @field_validator("date")
    @classmethod
    def _date(cls, v):
        try:
            _date.fromisoformat(v)
        except ValueError:
            raise ValueError("date must look like 2026-01-31")
        return v


class SubstitutionSaveIn(SubstituteIn):
    rows: list[dict] = Field(default_factory=list)


class DemoIn(Body):
    reset: bool = False


class ScenarioIn(Body):
    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=300)
    changes: list[dict] = Field(default_factory=list, max_length=50)
    strategy: Literal["adjust", "rebuild"] = "adjust"
    time_limit: int = Field(default=10, ge=1, le=60)


class ApplyIn(Body):
    confirm: bool = False
