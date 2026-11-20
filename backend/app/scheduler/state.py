"""Mutable timetable state with incremental clash counting.

Resources (student atoms, faculty, rooms) are arrays indexed by `day * S + period`
holding how many classes use them. `hard` is the running number of violations:

    double-booked resource cells        (each extra class beyond the first = 1)
  + classes placed in a faculty member's unavailable periods
  + periods a faculty member teaches beyond their max-per-day

so placing/removing a class is O(length) and clashes are known instantly.
"""
from __future__ import annotations

import random

from .problem import Problem


class State:
    __slots__ = ("p", "slot", "rooms", "atom_occ", "fac_occ", "room_occ", "fac_day", "hard")

    def __init__(self, p: Problem):
        self.p = p
        n, T = len(p.tasks), p.T
        self.slot: list[tuple[int, int] | None] = [None] * n
        self.rooms: list[tuple[int, ...] | None] = [None] * n
        self.atom_occ = [[0] * T for _ in range(p.n_atoms)]
        self.fac_occ = [[0] * T for _ in range(p.n_fac)]
        self.room_occ = [[0] * T for _ in range(p.n_rooms)]
        self.fac_day = [[0] * p.days for _ in range(p.n_fac)]
        self.hard = 0

    def copy(self) -> "State":
        c = State.__new__(State)
        c.p = self.p
        c.slot = self.slot[:]
        c.rooms = self.rooms[:]
        c.atom_occ = [r[:] for r in self.atom_occ]
        c.fac_occ = [r[:] for r in self.fac_occ]
        c.room_occ = [r[:] for r in self.room_occ]
        c.fac_day = [r[:] for r in self.fac_day]
        c.hard = self.hard
        return c

    # ------------------------------------------------------------------ place / remove
    def place(self, i: int, day: int, start: int, rooms: tuple[int, ...]) -> None:
        p, t = self.p, self.p.tasks[i]
        base = day * p.S + start
        end = base + t.length
        h = 0
        for a in t.atoms:
            row = self.atom_occ[a]
            for k in range(base, end):
                if row[k]:
                    h += 1
                row[k] += 1
        for f in t.fac:
            row, un = self.fac_occ[f], p.unav[f]
            for k in range(base, end):
                if row[k]:
                    h += 1
                row[k] += 1
                if un[k]:
                    h += 1
            fd, mx = self.fac_day[f], p.fac_max_day[f]
            before = fd[day]
            fd[day] = before + t.length
            h += max(0, before + t.length - mx) - max(0, before - mx)
        for r in rooms:
            row = self.room_occ[r]
            for k in range(base, end):
                if row[k]:
                    h += 1
                row[k] += 1
        self.slot[i] = (day, start)
        self.rooms[i] = rooms
        self.hard += h

    def unplace(self, i: int) -> None:
        p, t = self.p, self.p.tasks[i]
        day, start = self.slot[i]
        base = day * p.S + start
        end = base + t.length
        h = 0
        for a in t.atoms:
            row = self.atom_occ[a]
            for k in range(base, end):
                row[k] -= 1
                if row[k]:
                    h += 1
        for f in t.fac:
            row, un = self.fac_occ[f], p.unav[f]
            for k in range(base, end):
                row[k] -= 1
                if row[k]:
                    h += 1
                if un[k]:
                    h += 1
            fd, mx = self.fac_day[f], p.fac_max_day[f]
            before = fd[day]
            fd[day] = before - t.length
            h += max(0, before - mx) - max(0, before - t.length - mx)
        for r in self.rooms[i]:
            row = self.room_occ[r]
            for k in range(base, end):
                row[k] -= 1
                if row[k]:
                    h += 1
        self.slot[i] = None
        self.rooms[i] = None
        self.hard -= h

    # ------------------------------------------------------------------ evaluation
    def eval_slot(self, i: int, day: int, start: int, rng: random.Random):
        """Conflict score of putting *unplaced* task i at (day, start), plus the
        least-conflicted rooms for it (the section's home room wins ties).

        The score counts every existing class it would collide with (standard
        min-conflicts). Guarantees the solver relies on: it is 0 exactly when placing
        adds no violation, and it is never smaller than the resulting rise in `hard`."""
        p, t = self.p, self.p.tasks[i]
        base = day * p.S + start
        end = base + t.length
        c = 0
        for a in t.atoms:
            row = self.atom_occ[a]
            for k in range(base, end):
                c += row[k]
        for f in t.fac:
            row, un = self.fac_occ[f], p.unav[f]
            for k in range(base, end):
                c += row[k] + un[k]
            before, mx = self.fac_day[f][day], p.fac_max_day[f]
            c += max(0, before + t.length - mx) - max(0, before - mx)
        chosen: list[int] = []
        for j, opts in enumerate(t.room_opts):
            best_c, ties = 1 << 30, []
            for r in opts:
                if r in chosen:
                    continue
                row = self.room_occ[r]
                rc = 0
                for k in range(base, end):
                    rc += row[k]
                if rc < best_c:
                    best_c, ties = rc, [r]
                elif rc == best_c:
                    ties.append(r)
            if not ties:                       # fewer rooms than simultaneous members
                ties, best_c = [opts[0]], t.length
            pick = t.home[j] if t.home[j] in ties else rng.choice(ties)
            chosen.append(pick)
            c += best_c
        return c, tuple(chosen)

    def conflicts_of(self, i: int) -> int:
        """Violations that placed task i is currently involved in."""
        p, t = self.p, self.p.tasks[i]
        day, start = self.slot[i]
        base = day * p.S + start
        end = base + t.length
        c = 0
        for a in t.atoms:
            row = self.atom_occ[a]
            for k in range(base, end):
                c += row[k] - 1
        for f in t.fac:
            row, un = self.fac_occ[f], p.unav[f]
            for k in range(base, end):
                c += row[k] - 1 + un[k]
            if self.fac_day[f][day] > p.fac_max_day[f]:
                c += 1
        for r in self.rooms[i]:
            row = self.room_occ[r]
            for k in range(base, end):
                c += row[k] - 1
        return c

    def conflicted_tasks(self) -> list[int]:
        return [i for i in range(len(self.slot)) if self.conflicts_of(i) > 0]

    def reasons_at(self, i: int, day: int, start: int) -> list[str]:
        """Why can't *unplaced* task i go at (day, start)? (for the manual-edit UI)"""
        p, t = self.p, self.p.tasks[i]
        base = day * p.S + start
        end = base + t.length
        out = []
        if any(self.atom_occ[a][k] for a in t.atoms for k in range(base, end)):
            out.append("students already have a class")
        for f in t.fac:
            if any(self.fac_occ[f][k] for k in range(base, end)):
                out.append("faculty busy")
                break
        if any(p.unav[f][k] for f in t.fac for k in range(base, end)):
            out.append("faculty unavailable")
        if any(self.fac_day[f][day] + t.length > p.fac_max_day[f] for f in t.fac):
            out.append("faculty daily limit")
        for opts in t.room_opts:
            if all(any(self.room_occ[r][k] for k in range(base, end)) for r in opts):
                out.append("no free room")
                break
        return out


def recompute_hard(state: State) -> int:
    """Brute-force `hard` from the occupancy arrays (used by tests to check the incremental count)."""
    p = state.p
    h = 0
    for rows in (state.atom_occ, state.fac_occ, state.room_occ):
        for row in rows:
            h += sum(c - 1 for c in row if c > 1)
    for f in range(p.n_fac):
        h += sum(p.unav[f][k] * c for k, c in enumerate(state.fac_occ[f]))
        h += sum(max(0, d - p.fac_max_day[f]) for d in state.fac_day[f])
    return h
