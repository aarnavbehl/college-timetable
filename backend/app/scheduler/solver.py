"""Memetic genetic algorithm for college timetables.

    1. CONSTRUCT  Build several starting timetables greedily: hardest tasks first
                  (labs, electives), each put where it causes the fewest clashes.
    2. REPAIR     Min-conflicts search: repeatedly pick a clashing class and move it
                  to its least-clashing slot/room. This is the "targeted mutation".
    3. EVOLVE     Repeat: pick two good parents, CROSSOVER (each section's weekly
                  schedule comes from one parent or the other), MUTATE (relocate a few
                  classes, preferring clashing ones), REPAIR the child, and let it
                  replace the worst member if it is better.
    4. POLISH     Once there are no clashes, hill-climb (move/swap classes) on the
                  soft preferences: fewer gaps, spread-out subjects, balanced days.

Fitness = hard_violations * 1000 + soft_cost (lower is better; 0 hard = valid).
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass

from .problem import Problem
from .soft import soft_cost
from .state import State

HARD_WEIGHT = 1000


@dataclass
class Solution:
    state: State
    hard: int
    soft: int
    generations: int
    seconds: float

    def sessions(self) -> list[dict]:
        """One row per member of every task: what gets stored in the database."""
        p, st = self.state.p, self.state
        out = []
        for t in p.tasks:
            day, start = st.slot[t.index]
            for j, alloc_id in enumerate(t.alloc_ids):
                out.append({
                    "allocation_id": alloc_id, "task_key": t.key, "day": day, "start": start,
                    "length": t.length, "room_id": p.room_ids[st.rooms[t.index][j]],
                })
        return out


def _fitness(st: State) -> int:
    return st.hard * HARD_WEIGHT + soft_cost(st.p, st.slot)


# --------------------------------------------------------------------------- operators
def construct(p: Problem, rng: random.Random, noise: float) -> State:
    st = State(p)
    order = sorted(range(len(p.tasks)),
                   key=lambda i: (-(p.tasks[i].length * len(p.tasks[i].alloc_ids)),
                                  rng.random() * noise))
    for i in order:
        _place_best(st, i, rng)
    return st


def _place_best(st: State, i: int, rng: random.Random, random_walk: float = 0.0) -> None:
    t = st.p.tasks[i]
    if random_walk and rng.random() < random_walk:
        d, s = rng.choice(t.starts)
        c, rooms = st.eval_slot(i, d, s, rng)
        st.place(i, d, s, rooms)
        return
    best_c, best = 1 << 30, []
    for d, s in t.starts:
        c, rooms = st.eval_slot(i, d, s, rng)
        if c < best_c:
            best_c, best = c, [(d, s, rooms)]
        elif c == best_c:
            best.append((d, s, rooms))
    d, s, rooms = rng.choice(best)
    st.place(i, d, s, rooms)


def repair(st: State, rng: random.Random, deadline: float, max_steps: int, locked=frozenset()) -> None:
    """Min-conflicts: move clashing classes to their least-clashing slot until none clash.
    Tasks in `locked` (pinned by the user) are never moved."""
    steps = 0
    while st.hard > 0 and steps < max_steps:
        if steps % 16 == 0 and time.monotonic() > deadline:
            return
        bad = [i for i in st.conflicted_tasks() if i not in locked]
        if not bad:
            return
        i = rng.choice(bad)
        st.unplace(i)
        _place_best(st, i, rng, random_walk=0.04)
        steps += 1


def crossover(a: State, b: State, rng: random.Random) -> State:
    p = a.p
    take_a = [rng.random() < 0.5 for _ in range(p.n_sections)]
    child = State(p)
    for t in p.tasks:
        parent = a if take_a[t.sections[0]] else b
        d, s = parent.slot[t.index]
        child.place(t.index, d, s, parent.rooms[t.index])
    return child


def mutate(st: State, rng: random.Random, count: int) -> None:
    n = len(st.p.tasks)
    pool = st.conflicted_tasks() if st.hard else []
    for _ in range(count):
        i = rng.choice(pool) if pool and rng.random() < 0.7 else rng.randrange(n)
        if st.slot[i] is None:
            continue
        st.unplace(i)
        d, s = rng.choice(st.p.tasks[i].starts)
        _, rooms = st.eval_slot(i, d, s, rng)
        st.place(i, d, s, rooms)


def polish(st: State, rng: random.Random, deadline: float, locked=frozenset()) -> None:
    """Hill-climb on soft cost without ever creating a hard clash (never moves `locked` tasks)."""
    if st.hard:
        return
    p = st.p
    n = len(p.tasks)
    cur = soft_cost(p, st.slot)
    stale = 0
    while cur > 0 and stale < 4000 and time.monotonic() < deadline:
        i = rng.randrange(n)
        if i in locked:
            stale += 1
            continue
        if rng.random() < 0.5:                                   # move one class
            old, old_rooms = st.slot[i], st.rooms[i]
            st.unplace(i)
            d, s = rng.choice(p.tasks[i].starts)
            c, rooms = st.eval_slot(i, d, s, rng)
            if c:
                st.place(i, *old, old_rooms)
                stale += 1
                continue
            st.place(i, d, s, rooms)
            new = soft_cost(p, st.slot)
            if new <= cur:
                stale = stale + 1 if new == cur else 0
                cur = new
            else:
                st.unplace(i)
                st.place(i, *old, old_rooms)
                stale += 1
        else:                                                    # swap two classes
            j = rng.randrange(n)
            ti, tj = p.tasks[i], p.tasks[j]
            if i == j or j in locked or ti.length != tj.length or st.slot[i] == st.slot[j]:
                stale += 1
                continue
            if st.slot[j] not in ti.starts or st.slot[i] not in tj.starts:
                stale += 1
                continue
            oi, oj, ri, rj = st.slot[i], st.slot[j], st.rooms[i], st.rooms[j]
            st.unplace(i)
            st.unplace(j)
            ci, rooms_i = st.eval_slot(i, *oj, rng)
            if ci == 0:
                st.place(i, *oj, rooms_i)
                cj, rooms_j = st.eval_slot(j, *oi, rng)
                if cj == 0:
                    st.place(j, *oi, rooms_j)
                    new = soft_cost(p, st.slot)
                    if new <= cur:
                        stale = stale + 1 if new == cur else 0
                        cur = new
                        continue
                    st.unplace(j)
                st.unplace(i)
            st.place(i, *oi, ri)                                 # revert
            st.place(j, *oj, rj)
            stale += 1


# --------------------------------------------------------------------------- driver
def solve(p: Problem, *, time_limit: float = 15.0, seed: int | None = None, population: int = 8,
          max_generations: int = 3000, patience: int = 250) -> Solution:
    """Search for a clash-free, pleasant timetable within `time_limit` seconds."""
    t0 = time.monotonic()
    rng = random.Random(seed)
    ga_deadline = t0 + time_limit * 0.8
    deadline = t0 + time_limit
    n = len(p.tasks)
    repair_steps = max(200, 6 * n)

    pop: list[tuple[int, State]] = []
    for k in range(population):
        st = construct(p, rng, noise=0.0 if k == 0 else 1.0)
        repair(st, rng, ga_deadline, repair_steps)
        pop.append((_fitness(st), st))
        if st.hard == 0 and time.monotonic() > t0 + time_limit * 0.3 and k >= 1:
            break
    best_fit, best = min(pop, key=lambda x: x[0])
    last_improve = 0
    gen = 0

    def pick() -> State:
        return min(rng.sample(pop, min(3, len(pop))), key=lambda x: x[0])[1]

    while gen < max_generations and time.monotonic() < ga_deadline and best_fit > 0:
        if best.hard == 0 and gen - last_improve >= patience:
            break
        parent_a, parent_b = pick(), pick()
        child = crossover(parent_a, parent_b, rng) if rng.random() < 0.8 and parent_a is not parent_b \
            else parent_a.copy()
        mutate(child, rng, rng.choice((1, 1, 2, 3)))
        repair(child, rng, ga_deadline, repair_steps // 2)
        fit = _fitness(child)
        worst = max(range(len(pop)), key=lambda x: pop[x][0])
        if fit <= pop[worst][0]:
            pop[worst] = (fit, child)
        if fit < best_fit:
            best_fit, best, last_improve = fit, child, gen
        gen += 1

    if best.hard:                                      # last chance: keep repairing the best one
        best = best.copy()
        repair(best, rng, deadline, 20 * n)
    polish(best, rng, deadline)
    return Solution(best, best.hard, soft_cost(p, best.slot), gen, time.monotonic() - t0)


def state_from_sessions(p: Problem, rows: list[dict]) -> State:
    """Rebuild a State from stored session rows (for manual edits / checks).

    Raises ValueError if the stored sessions don't match the current data."""
    by_key: dict[str, list[dict]] = {}
    for r in rows:
        by_key.setdefault(r["task_key"], []).append(r)
    st = State(p)
    room_index = {rid: i for i, rid in enumerate(p.room_ids)}
    for t in p.tasks:
        got = by_key.get(t.key)
        if not got or len(got) != len(t.alloc_ids):
            raise ValueError("The saved timetable is out of date: regenerate it.")
        by_alloc = {r["allocation_id"]: r for r in got}
        if set(by_alloc) != set(t.alloc_ids):
            raise ValueError("The saved timetable is out of date: regenerate it.")
        first = got[0]
        rooms = []
        for j, a in enumerate(t.alloc_ids):
            rid = by_alloc[a]["room_id"]
            rooms.append(room_index[rid] if rid in room_index else t.room_opts[j][0])
        st.place(t.index, first["day"], first["start"], tuple(rooms))
    return st
