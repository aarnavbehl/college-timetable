"""Soft-preference weights and their plain-language definitions (no dependencies, so everything can import it)."""
from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass(frozen=True)
class SoftWeights:
    """Cost per unit of each soft preference. Higher = the solver tries harder to avoid it."""
    section_gap: int = 2        # free period between two classes of a section on one day
    late_start: int = 1         # free period at the start of a section's day
    faculty_gap: int = 1        # free period between two classes of a faculty member on one day
    same_day_repeat: int = 3    # extra single-period session of the same course on one day
    long_run: int = 2           # period beyond `max_run` in a row for a faculty member
    unbalanced: int = 2         # period a section is in class beyond (average load + 1) on a day
    max_run: int = 3            # consecutive periods a faculty member can teach before long_run applies

    def as_dict(self) -> dict[str, int]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


DEFAULT_WEIGHTS = SoftWeights()

# component key -> (label, what one unit means)
COMPONENTS: dict[str, tuple[str, str]] = {
    "section_gap": ("Section gaps", "A free period between two classes of the same section on the same day."),
    "late_start": ("Late starts", "A free period at the start of a section's day (before its first class)."),
    "faculty_gap": ("Faculty gaps", "A free period between two classes of the same faculty member on the same day."),
    "same_day_repeat": ("Same-day repeats", "A second single-period session of the same course for the same section on one day."),
    "long_run": ("Long teaching runs", "A faculty member teaching more than 3 periods in a row (counted per extra period)."),
    "unbalanced": ("Uneven days", "A section having more periods on a day than its weekly average plus one (per extra period)."),
}
