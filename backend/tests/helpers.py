"""Synthetic colleges for tests/benchmarks."""
from __future__ import annotations

import random

from app.scheduler.problem import (AllocRec, Config, CourseRec, Dataset, FacultyRec, RoomRec,
                                   SectionRec)


def big_college(branches=4, years=4, sections_per_year=1, seed=1, periods=7, days=5) -> Dataset:
    """branches x years x sections sections; each has 6 theory courses (20 h) + a 2-batch lab rotation."""
    rng = random.Random(seed)
    turn = [0]

    def next_fac(pool):          # round-robin keeps every teacher's load balanced
        turn[0] += 1
        return pool[turn[0] % len(pool)]

    cfg = Config(days, periods, lunch_after=4)
    n_sections = branches * years * sections_per_year
    rooms = [RoomRec(i + 1, f"R{i + 1}", "CLASSROOM", 70) for i in range(n_sections + 2)]
    rooms += [RoomRec(1000 + i, f"L{i + 1}", "LAB", 30) for i in range(max(4, n_sections // 2))]
    courses, faculty, sections, allocs = [], [], [], []
    cid = fid = sid = aid = 0
    lab_a = CourseRec(10_000, "LABA", "Lab A", "LAB", 3, 3)
    lab_b = CourseRec(10_001, "LABB", "Lab B", "LAB", 3, 3)
    courses += [lab_a, lab_b]
    for b in range(branches):
        pool = []
        for k in range(10 + 4 * years * sections_per_year):
            fid += 1
            faculty.append(FacultyRec(fid, f"Fac {b}-{k}", f"F{fid}", 6, 22, b + 1))
            pool.append(fid)
        for y in range(1, years + 1):
            for s in range(sections_per_year):
                sid += 1
                sections.append(SectionRec(sid, f"B{b}Y{y}{chr(65 + s)}", b + 1, y, 60, 2, sid))
                for hours in (4, 4, 3, 3, 3, 3):
                    cid += 1
                    courses.append(CourseRec(cid, f"C{cid}", f"Course {cid}", "THEORY", hours, 1, b + 1))
                    aid += 1
                    allocs.append(AllocRec(aid, sid, cid, next_fac(pool)))
                fa, fb = next_fac(pool), next_fac(pool)
                for batch, (lab, fac, grp) in ((1, (lab_a, fa, "L1")), (2, (lab_b, fb, "L1")),
                                               (1, (lab_b, fb, "L2")), (2, (lab_a, fa, "L2"))):
                    aid += 1
                    allocs.append(AllocRec(aid, sid, lab.id, fac, None, batch, f"S{sid}-{grp}"))
    return Dataset(cfg, rooms, sections, courses, faculty, allocs)
