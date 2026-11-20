"""Soft preferences. These never make a timetable invalid; they make it nicer.

`soft_counts` measures how many *units* of each preference a timetable has (see weights.COMPONENTS);
`soft_cost` is the weighted total the solver minimises; `soft_breakdown` shows the parts.
All three share one measuring function, so they can never disagree.
"""
from __future__ import annotations

from .problem import Problem
from .weights import COMPONENTS, DEFAULT_WEIGHTS, SoftWeights

# kept for backwards compatibility with older imports / docs
W_SECTION_GAP = DEFAULT_WEIGHTS.section_gap
W_SECTION_LATE_START = DEFAULT_WEIGHTS.late_start
W_FACULTY_GAP = DEFAULT_WEIGHTS.faculty_gap
W_SAME_DAY_REPEAT = DEFAULT_WEIGHTS.same_day_repeat
W_LONG_RUN = DEFAULT_WEIGHTS.long_run
W_UNBALANCED = DEFAULT_WEIGHTS.unbalanced
MAX_RUN = DEFAULT_WEIGHTS.max_run

KEYS = tuple(COMPONENTS)


def _popcount(x: int) -> int:
    return x.bit_count()


def soft_counts(p: Problem, slot, max_run: int | None = None) -> dict[str, int]:
    """Units of each soft preference in the placement `slot` (list of (day, start) or None per task)."""
    max_run = p.weights.max_run if max_run is None else max_run
    D = p.days
    sec_mask = [[0] * D for _ in range(p.n_sections)]
    fac_mask = [[0] * D for _ in range(p.n_fac)]
    repeats: dict[tuple[int, int], int] = {}
    for t in p.tasks:
        pos = slot[t.index]
        if pos is None:
            continue
        d, s = pos
        m = ((1 << t.length) - 1) << s
        for sec in t.sections:
            sec_mask[sec][d] |= m
        for f in t.fac:
            fac_mask[f][d] |= m
        if t.theory:
            for a in t.alloc_ids:
                repeats[(a, d)] = repeats.get((a, d), 0) + 1

    section_gap = late_start = unbalanced = faculty_gap = long_run = same_day_repeat = 0
    for sec in range(p.n_sections):
        total = p.section_total[sec]
        cap = -(-total // D) + 1 if total else 0
        for d in range(D):
            m = sec_mask[sec][d]
            if not m:
                continue
            first = (m & -m).bit_length() - 1
            last = m.bit_length() - 1
            n = _popcount(m)
            section_gap += last - first + 1 - n
            late_start += first - 1
            unbalanced += max(0, n - cap)
    for f in range(p.n_fac):
        for d in range(D):
            m = fac_mask[f][d]
            if not m:
                continue
            first = (m & -m).bit_length() - 1
            last = m.bit_length() - 1
            faculty_gap += last - first + 1 - _popcount(m)
            run = 0
            for k in range(first, last + 1):
                if m >> k & 1:
                    run += 1
                    if run > max_run:
                        long_run += 1
                else:
                    run = 0
    for n in repeats.values():
        if n > 1:
            same_day_repeat += n - 1
    return {"section_gap": section_gap, "late_start": late_start, "faculty_gap": faculty_gap,
            "same_day_repeat": same_day_repeat, "long_run": long_run, "unbalanced": unbalanced}


def weighted(counts: dict[str, int], w: SoftWeights) -> int:
    return sum(counts[k] * getattr(w, k) for k in KEYS)


def soft_cost(p: Problem, slot) -> int:
    """The number the solver minimises (units x the problem's weights)."""
    return weighted(soft_counts(p, slot), p.weights)


def soft_breakdown(p: Problem, slot, weights: SoftWeights | None = None) -> dict[str, dict]:
    """{component: {label, count, weight, cost}} plus a 'total' entry; sum of costs == soft_cost."""
    w = weights or p.weights
    counts = soft_counts(p, slot, w.max_run)
    out = {k: {"label": COMPONENTS[k][0], "count": counts[k], "weight": getattr(w, k),
               "cost": counts[k] * getattr(w, k)} for k in KEYS}
    out["total"] = {"label": "Total", "count": sum(counts.values()), "weight": None,
                    "cost": sum(v["cost"] for v in out.values())}
    return out
