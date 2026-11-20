"""'Adjust the current timetable': keep what still works, repair what the changes broke.

Starts from a saved timetable (the baseline), places every class where it was, puts brand-new classes
where they clash least, applies any pinned placements, then runs the solver's min-conflicts repair
(optionally followed by preference polishing). Pinned classes are never moved. Typically only the classes
affected by a change move, which makes the result easy to compare with the baseline.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from .problem import Problem
from .soft import soft_cost
from .solver import Solution, _place_best, polish, repair
from .state import State


@dataclass
class Pins:
    """User-fixed placements. times: task_key -> (day, start); rooms: allocation_id -> room_id."""
    times: dict[str, tuple[int, int]] = field(default_factory=dict)
    rooms: dict[int, int] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.times or self.rooms)


class PinError(ValueError):
    """A pin cannot be honoured (illegal slot, incompatible room, unknown class)."""


def build_warm_state(p: Problem, baseline_rows: list[dict], rng: random.Random, pins: Pins | None = None):
    """Returns (state, locked_task_indexes, notes)."""
    pins = pins or Pins()
    room_index = {rid: i for i, rid in enumerate(p.room_ids)}
    by_key: dict[str, dict[int, dict]] = {}
    for r in baseline_rows:
        by_key.setdefault(r["task_key"], {})[r["allocation_id"]] = r

    st = State(p)
    locked: set[int] = set()
    kept = fresh = 0
    notes: list[str] = []
    tasks_by_key = p.task_by_key()
    for key in pins.times:
        if key not in tasks_by_key:
            raise PinError(f"The class {key} does not exist in the changed scenario.")

    for t in p.tasks:
        i = t.index
        old = by_key.get(t.key)
        slot = None
        if old and set(old) == set(t.alloc_ids):
            first = old[t.alloc_ids[0]]
            if (first["day"], first["start"]) in t.starts:
                slot = (first["day"], first["start"])
        if t.key in pins.times:
            if pins.times[t.key] not in t.starts:
                raise PinError(f"Pinned slot for {t.key} is not a legal start (outside the day or across the lunch break).")
            slot = pins.times[t.key]
        if slot is None:
            fresh += 1
            _place_best(st, i, rng)
            continue
        d, s = slot
        _, auto = st.eval_slot(i, d, s, rng)
        rooms, used = [], set()
        for j, alloc in enumerate(t.alloc_ids):
            wanted = pins.rooms.get(alloc)
            if wanted is not None:
                idx = room_index.get(wanted)
                if idx is None or idx not in t.room_opts[j]:
                    raise PinError("The pinned room is closed or not suitable for that class (wrong type or too small).")
            else:
                prev = old.get(alloc) if old else None
                idx = room_index.get(prev["room_id"]) if prev else None
                if idx is None or idx not in t.room_opts[j]:
                    idx = auto[j]
            if idx in used:                                  # keep rooms distinct within one task
                idx = auto[j]
            used.add(idx)
            rooms.append(idx)
        st.place(i, d, s, tuple(rooms))
        kept += 1
        if t.key in pins.times or any(a in pins.rooms for a in t.alloc_ids):
            locked.add(i)
    notes.append(f"{kept} class(es) started from their saved placement, {fresh} new class(es) were placed fresh, "
                 f"{len(locked)} pinned.")
    return st, locked, notes


def adjust(p: Problem, baseline_rows: list[dict], *, pins: Pins | None = None, time_limit: float = 10.0,
           seed: int | None = 1, optimize_preferences: bool = False) -> tuple[Solution, list[str]]:
    t0 = time.monotonic()
    rng = random.Random(seed)
    st, locked, notes = build_warm_state(p, baseline_rows, rng, pins)
    deadline = t0 + time_limit
    repair(st, rng, deadline, max(400, 12 * len(p.tasks)), frozenset(locked))
    if st.hard == 0 and optimize_preferences:
        polish(st, rng, deadline, frozenset(locked))
    return Solution(st, st.hard, soft_cost(p, st.slot), 0, time.monotonic() - t0), notes
