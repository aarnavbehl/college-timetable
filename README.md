# College Timetable Scheduler

Generate clash-free timetables for a college: **branches, years and sections, faculty, classrooms and labs, 3-hour lab blocks, lab-batch rotations, electives, free periods and substitute teachers.**

**Python (FastAPI + SQLite) backend · JavaScript frontend (no build step) · one command to run.**

## Quick start

You need **Python 3.10+**. Nothing else (no database server, no Node, no `npm install`).

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Open **http://localhost:8000**, create a college account, then click **Load demo college** on the dashboard and **Generate**. API docs are at http://localhost:8000/docs.

> In Cursor: *File → Open Folder* on this folder, open the integrated terminal (Ctrl+\`), and run the commands above. On Mac/Linux use `python3` if `python` isn't found.

### Take the 5-minute tour
1. **Dashboard → Load demo college.** A small engineering college appears: 3 branches, 5 sections, 14 faculty, 10 rooms, 46 allocations.
2. **Generate** (15 s is plenty; it stops early when nothing improves).
3. **View & edit.** Switch between *Sections / Faculty / Rooms*. Click a class → **Move to another slot…** shows every slot, green if free, red if it would clash (hover for the reason, e.g. "faculty busy"). Press **✔ Verify** to re-check the whole timetable with an independent checker.
4. **Substitutions.** Pick a date and absent faculty; free colleagues are assigned class by class.
5. Export **CSV** or **Print** any view.

## Entering your own college

Go through **Set up your college** in the sidebar, top to bottom:

| Page | What you enter |
|---|---|
| **Week & periods** | Days per week (5 or 6), periods per day, start time, period length, lunch break position |
| **Branches** | Departments: CSE, ECE, Mechanical… |
| **Rooms & labs** | Name, type (classroom / lab) and capacity |
| **Sections** | Branch + year + letter, number of students, number of *lab batches*, optional home room |
| **Faculty** | Name, department, max periods per day / week, and a clickable grid of times they are **not available** |
| **Courses** | Code, type (theory / lab), hours per week, periods per session (labs are usually 3) |
| **Allocations** | *Who teaches what to whom*: faculty + course + section (+ optional batch) |

Prefer a spreadsheet? Skip typing and use **[Importing from Excel](#importing-from-excel)**.

### Two concepts that make colleges different from schools
**Lab batches and rotations.** A section of 60 splits into 2 batches of 30 for labs. In a rotation, batch 1 does *Data Structures Lab* while batch 2 does *DBMS Lab* at the same time, then they swap. Model it with **sync groups**: give the allocations that must run together the same *Sync group* label.

| Section | Batch | Course | Faculty | Sync group |
|---|---|---|---|---|
| CSE Y2-A | 1 | DS Lab | Dr. Sharma | `CSE2A-L1` |
| CSE Y2-A | 2 | DBMS Lab | Dr. Krishnan | `CSE2A-L1` |
| CSE Y2-A | 1 | DBMS Lab | Dr. Krishnan | `CSE2A-L2` |
| CSE Y2-A | 2 | DS Lab | Dr. Sharma | `CSE2A-L2` |

**Electives.** Students choose between options that run in the same slot. Give each option the same sync group and set *Students enrolled* so a right-sized room is chosen. See `CSE3A-E1` in the demo.

Rules the scheduler enforces: every member of a sync group has the same sessions/week and length, uses a different faculty member and a different room, and the whole group moves together.

The **Generate** page runs a pre-flight check and tells you in plain English if something is impossible (a faculty member over their weekly maximum, a section with more periods than the week has, no lab big enough, a 5-period session that can't avoid lunch…).

## Importing from Excel

Instead of typing everything in, load your college from a spreadsheet: **sidebar → Import from Excel** (or the *Import from Excel* button on the dashboard).

1. **Download a template.** *Blank template* (headers, dropdowns and an Instructions sheet) or *Example* (the demo college as a workbook, handy to see every column in use).
2. **Fill it in** and save as `.xlsx` (up to 5 MB, up to 5,000 rows per sheet). You do not need every sheet.
3. **Choose the file.** You get a **preview**: for every sheet, each row is marked *New*, *Skipped* or *Rejected*, with the exact reason and Excel row number. **Nothing is saved yet.**
4. **Import.** You get a summary of records **imported, skipped and rejected**, plus a check of whether the data can be scheduled.

### Template format

Sheet names must be exactly **Branches, Rooms, Sections, Faculty, Courses, Allocations** (capitals and spaces don't matter). The first row of each sheet holds the headers; column order doesn't matter, capitals/spaces/underscores don't matter (`Max Per Day` = `max_per_day`), and unknown columns are ignored with a warning. Other sheets (like *Instructions*) are ignored. Use plain values, not formulas.

Sheets are imported in this order, so a row can refer to records from an earlier sheet **or to records that already exist in the app**:
**Branches → Rooms → Sections → Faculty → Courses → Allocations**.

**Branches**

| Column | Required | Format | Notes |
|---|---|---|---|
| Code | yes | text, ≤ 12 chars | Unique key, e.g. `CSE` |
| Name | yes | text, ≤ 120 chars | |

**Rooms**

| Column | Required | Format | Notes |
|---|---|---|---|
| Name | yes | text, ≤ 60 chars | Unique key, e.g. `C101` |
| Type | yes | `Classroom` or `Lab` | |
| Capacity | yes | whole number 1–2000 | |

**Sections**

| Column | Required | Format | Default / notes |
|---|---|---|---|
| Branch | yes | a Branch **Code** | must exist in the file or the app |
| Year | yes | whole number 1–6 | |
| Section | yes | text, ≤ 10 chars | e.g. `A`. Branch + Year + Section is the unique key |
| Students | yes | whole number 1–1000 | alias: `Strength` |
| Batches | no | whole number 1–6 | `1`. Lab batches the section splits into |
| Home Room | no | a Room **Name** | must be a *Classroom*, not a lab |

**Faculty**

| Column | Required | Format | Default / notes |
|---|---|---|---|
| Code | yes | text, ≤ 12 chars | Unique key, e.g. `AS` |
| Name | yes | text, ≤ 120 chars | |
| Department | no | a Branch **Code** | aliases: `Dept`, `Branch` |
| Max Per Day | no | whole number 1–12 | `5` |
| Max Per Week | no | whole number 1–84 | `20` |
| Unavailable | no | `Day:periods; Day:periods` | see below |

*Unavailable* examples: `Fri:5-7` (Friday periods 5, 6, 7) · `Mon:1,2; Fri:5-7` · `Sat:1-3`. Days are `Mon Tue Wed Thu Fri Sat Sun` (or full names); periods are numbers or ranges. They must fit your week (set on **Week & periods**): a Saturday entry is rejected on a 5-day week.

**Courses**

| Column | Required | Format | Default / notes |
|---|---|---|---|
| Code | yes | text, ≤ 20 chars | Unique key, e.g. `CS201` |
| Name | yes | text, ≤ 120 chars | |
| Department | no | a Branch **Code** | aliases: `Dept`, `Branch` |
| Type | yes | `Theory` or `Lab` | |
| Hours Per Week | yes | whole number 1–30 | alias: `Hours` |
| Session Length | no | whole number 1–6 | `1` for Theory, `3` for Lab. Hours Per Week must be a multiple of it |

**Allocations** (who teaches what to whom)

| Column | Required | Format | Default / notes |
|---|---|---|---|
| Branch | yes | a Branch **Code** | together with Year and Section it names the section |
| Year | yes | whole number | |
| Section | yes | text | the section letter |
| Course | yes | a Course **Code** | |
| Faculty | yes | a Faculty **Code** | |
| Hours Per Week | no | whole number 1–30 | the course's hours. Must be a multiple of its session length |
| Batch | no | whole number 1–6 | blank = whole section; cannot exceed the section's Batches |
| Sync Group | no | text, ≤ 40 chars | rows with the same label run at the same time (lab rotations, electives) |
| Students Enrolled | no | whole number 1–1000 | for electives: decides the room size |

Numbers may be real Excel numbers (`60`), whole-number decimals (`60.0`) or text (`"60"`). `60.5`, `abc` and `TRUE` are rejected. Codes typed as numbers (`101`) are read as text.

### Safety rules

* **Existing data is never modified or deleted** by a normal import. A row whose key already exists (same branch/faculty/course code, room name, section, or section + course + batch) is **skipped**; the preview notes if the file's values differ. Matching ignores capitals (`cse` = `CSE`) and only looks at your own college's data.
* The same key twice **inside the file** rejects the later row (`Duplicate of row 2`).
* **Strict mode (default)**: if *any* row is rejected, **nothing** is imported. Fix the file and upload again.
* **Import only the valid rows** (tick the box): valid rows are saved and the rest rejected. A row that depends on a rejected row (a section whose branch was rejected) is rejected too.
* **Replace all existing data** deletes your setup and timetable before importing. It needs the file to have **no problems at all**, and you must type `REPLACE`; the server enforces both. The delete and the import happen in **one transaction**, so if anything fails, your old data stays exactly as it was.
* The file is validated again on the server when you import, so the preview can never be bypassed. A failure part-way through rolls back everything it did.
* Importing marks an existing timetable as *out of date*; regenerate it afterwards. Run **Generate**: the pre-flight check explains anything unschedulable (e.g. a teacher allocated more periods than their weekly maximum), and the import summary already shows those problems.

### Scripting it

```bash
TOKEN=...   # from POST /api/auth/login
curl -H "Authorization: Bearer $TOKEN" -o template.xlsx localhost:8000/api/import/template
curl -H "Authorization: Bearer $TOKEN" -F file=@college.xlsx -F replace_existing=false localhost:8000/api/import/preview
curl -H "Authorization: Bearer $TOKEN" -F file=@college.xlsx -F mode=strict localhost:8000/api/import/commit
```
`mode` is `strict` or `partial`; replace is `-F replace_existing=true -F confirm_replace=REPLACE`.

### Common messages

| Message | Fix |
|---|---|
| `Missing required column(s): Capacity` | add the column (header text must match) |
| `Branch 'MECH' was not found (add it to the Branches sheet or create it first)` | add the branch row, or fix the code |
| `Capacity: must be a whole number (got 'abc')` | use a plain whole number |
| `Hours Per Week (4) must be a multiple of the session length (3)` | labs default to 3-period sessions; set *Session Length* |
| `Unavailable: Sat is outside your 5-day week` | change the week on *Week & periods*, or fix the entry |
| `Batch 3 does not exist: CSE Y2-A has 2 batch(es)` | raise *Batches* on the Sections sheet |
| `Already exists — skipped` | harmless; the existing record is kept |

## Analytics, explanations, simulation and history

Four tools sit on top of the generated timetable. All of them use the real saved data; none invents numbers.

### Analytics (sidebar → *Analytics*)
Quality metrics computed from the saved timetable. The hard-constraint numbers come from the **independent verifier**, re-run on every page load, so the page never claims a timetable is valid without checking.

| Section | What it shows |
|---|---|
| Banner | ✔ verified clean, or ✖ invalid with the counts |
| Headline cards | scheduled / required classes, unscheduled classes, hard violations, room utilization, faculty load spread, section gaps |
| **Hard constraints** | faculty conflicts, room conflicts, student-section conflicts, other violations (unavailable teacher, daily limit, wrong/small room, illegal placement), example messages, list of unscheduled classes |
| **Quality score** | gauge + the six components with their weights and the numbers behind each |
| Charts | classes and teaching hours by day, room utilization per room/type, faculty workload histogram and per-faculty bars, section gaps by day/section |
| Heat maps | rooms in use and faculty teaching per day × period (and a conflict map if any exist) |
| Glossary | a plain-language definition of every metric, labelled *Hard*, *Soft* or *Information* |

**Quality score (deterministic, documented).** `score = (30·compactness + 10·early_start + 20·subject_spread + 15·faculty_continuity + 15·workload_balance + 10·day_balance) / 100`, each component 0–100 (100 = nothing to penalise); formulas are in `backend/app/services/analytics.py` and shown in the UI. **It is withheld (shown as “—”) whenever there is any hard violation or unscheduled class**: a pretty but invalid timetable never gets a score. The soft-preference units are checked against the solver's own cost in the tests.

### “Why this slot?” (timetable → click a class → *Why this slot?*)
The solver is a randomised search and does **not** log its decisions, so nothing here pretends to know what it “thought”. Instead it re-checks the saved timetable with the solver's own constraint checks and scoring, on an in-memory copy, and shows:
* **Hard constraints** satisfied (or violated) with evidence: students free, faculty free and available, daily limit, room type, capacity, room free, lunch/day limits, lab-rotation partners;
* **Soft preferences**: the effect of this class being exactly here, found by re-scoring the timetable with and without it (negative = helps);
* **Every other legal slot**: possible or not, the real reason it is blocked (the same reasons the editor gives), and the exact change in the whole timetable's soft score if the class moved there;
* **Rooms** at that time (free, busy and by whom, home room), and **warnings/trade-offs** (e.g. “3 slots would score better; the solver does not guarantee an optimum”).
The tests check the truth of these claims: doing what an explanation says changes the saved score by exactly the predicted amount, and rejection reasons equal what the editor reports. Explaining is read-only.

### What-if simulations (sidebar → *What-if simulations*)
Try a change on a **copy** first.
1. *+ New scenario* → add one or more changes: a faculty member's availability or limits change; a room becomes unavailable; a new section, new course, or new allocation; a different preference weight; fix a class's time and/or room.
2. *▶ Run simulation* builds a candidate with the existing scheduler: **Adjust my current timetable** (default: keeps every class where it is and repairs only what the change breaks; falls back to a full rebuild and says so) or **Rebuild from scratch**.
3. The candidate is **verified by the independent verifier**, then compared with your saved timetable: hard violations, unscheduled classes, quality score, soft cost, section gaps, workload spread, room utilization, and a list of every class that moves, changes room, or is added.
4. If the changes make scheduling impossible, or the candidate still has violations, you get diagnostics and **Apply stays disabled**.
5. *Apply to my saved timetable…* asks for confirmation (it lists any permanent data changes), re-verifies the candidate, applies the data changes and the new timetable in **one transaction** (verifying what is actually in the database before committing), and refuses if your data or the saved timetable changed since the run.
Running, editing and deleting scenarios never touches your saved data or timetable (tested by comparing complete snapshots before and after).

### History & undo (sidebar → *History & undo*)
Every generation, manual move, deletion and applied simulation is recorded **in the same transaction as the change itself**, with the server's real timestamp, the college account that made it, and exact before/after values. This app has one login per college, so “who” is that account; no other identity is invented.
* A **manual move** can be undone if those classes are still exactly where the edit left them (edits to different classes can be undone in any order; later edits to the same class must be undone first).
* A **generation, deletion or simulation** can be undone only if it is the newest un-undone change and the timetable still matches the state it produced.
* Every undo is **re-verified** by the independent verifier and refused if it would introduce new hard violations (for example because data changed since). Undo is atomic and itself logged; there is no redo.
* A simulation that also changed faculty/room/section/course data is shown as **not undoable** (with the reason) because those data changes would need reverting by hand.
* The newest 200 entries are kept.

### Closed rooms
Rooms now have an **Open/Closed** status (Rooms page). Closed rooms are ignored by the scheduler. This is also what a “room becomes unavailable” simulation changes when applied.

## Database changes (no migration tool is needed)
The project creates its schema with `create_all()`. On start-up it now also:
* creates two **new tables**, `scenarios` and `change_log`;
* adds one column, `rooms.is_active` (default *open*), with a guarded `ALTER TABLE` that is a no-op once the column exists (tested on a database created by the previous version; the SQLite path is tested, the PostgreSQL DDL path is not).
Existing data is untouched. No new Python or JavaScript dependencies were added.

## How the scheduler works

A **memetic genetic algorithm** (`backend/app/scheduler/`):

1. **Construct** several starting timetables greedily, hardest tasks first (labs, electives).
2. **Repair** each by *min-conflicts*: repeatedly move a clashing class to its least-clashing slot and room (the "targeted mutation").
3. **Evolve:** pick good parents, **crossover** (each section's weekly schedule comes from one parent or the other), **mutate**, repair the child, keep it if it beats the worst member.
4. **Polish** once clash-free: hill-climb by moving/swapping classes to improve the preferences.

Clashes are tracked **incrementally** (placing or removing a class is O(its length)), which is what makes it fast.

| Hard rules (must hold) | Soft preferences (nice to have) |
|---|---|
| No faculty, room or student group in two places at once | Few gaps in a section's day, start at period 1 |
| Faculty never scheduled while unavailable | Spread a subject across days (no same-day repeats) |
| Faculty daily maximum respected | Faculty gaps and long runs (>3 in a row) avoided |
| Labs in lab rooms, rooms big enough for the group | Balanced load across days |
| Multi-period sessions never span the lunch break | Theory classes in the section's home room |

Measured on the included tests: the demo college (101 classes) solves clash-free in 2–3 s; a synthetic 16-section college (352 classes) in ~11 s; a 32-section college (704 classes) in ~27 s. The **Verify** button (and the test suite) re-checks a result with `scheduler/check.py`, which deliberately shares no code with the solver.

## Project layout

```
backend/
  run.py                  start the app
  app/main.py             FastAPI app: API under /api, frontend at /
  app/models.py           database tables (SQLAlchemy)
  app/routers/            auth, settings/stats/demo, CRUD, Excel import, timetable, insights (analytics, explain),
                          scenarios, history, substitutions
  app/services/           dataset loader, demo college, Excel import + template, substitutions, CSV,
                          analytics, explain (Why this slot?), scenarios (what-if), history (+ undo)
  app/scheduler/
    weights.py            soft-preference weights + definitions
    adjust.py             warm-start repair + pinned classes (used by simulations)
    problem.py            data model + all validation messages
    state.py              incremental clash tracking
    soft.py               preference scoring
    solver.py             the genetic algorithm
    check.py              independent verifier
  tests/                  139 tests (pytest)
frontend/
  index.html, css/        no build step
  js/                     plain ES modules: api, ui helpers, CRUD factory, grid, import page, pages
  tests/                  34 unit tests for the pure logic (node --test)
```

## Demonstration script (college project viva, about 10 minutes)

Prepare: `python run.py`, open http://localhost:8000, register a college.

1. **Setup (1 min).** Dashboard → *Load demo college*. Say: “3 departments, 5 sections, 14 faculty, labs with batch rotations and an elective, a visiting teacher blocked on Friday afternoons.” Or *Import from Excel → Example* to show the spreadsheet route.
2. **Generate (1 min).** *Generate* → 15 s. Say: “A memetic genetic algorithm: greedy start, min-conflicts repair, crossover/mutation, then preference polishing.” Open *View & edit* and show Sections, Faculty and Rooms views.
3. **Analytics (2 min).** *Analytics*. Point at the green banner (“verified just now by the independent verifier”), hard vs soft, the quality score and its breakdown (hover a bar), the heat maps, and the glossary. Say: “If there were any violation, the score would disappear.”
4. **Prove the verifier is independent (1 min).** In *View & edit* click a class → *Move to another slot…* → pick a **red** slot → *Move anyway*. Reload *Analytics*: red banner, conflict heat map lights up, score withheld.
5. **Undo (1 min).** *History & undo* → the forced move is listed with before/after values and time → *Undo*. Analytics is green again. Say: “Undo is re-verified and atomic.”
6. **Why this slot? (1.5 min).** Click a class → *Why this slot?*. Show the hard-constraint evidence, the soft effect, other slots with real rejection reasons, and the honest note about the solver. Click a lab class to show the rotation partners.
7. **What-if (2.5 min).** *What-if simulations* → new scenario → *A faculty member's availability* → Dr. Neha Bansal, click all Monday slots → *Run simulation*. Show: “verified by the independent verifier”, the comparison table, and that only a handful of classes moved. Open *View & edit* (nothing changed). Back in the scenario: *Apply…* → read the confirmation → apply. Show that her Monday is now empty, and that History lists it as *not undoable because it changed data*.
8. **Impossible case (30 s).** New scenario making one teacher unavailable all week → diagnostics explain why and **Apply is disabled**.
9. **Wrap-up.** Mention the tests: independent verification, simulation isolation, atomic apply/undo, 139 backend + 34 frontend unit tests + browser flows.

## Configuration (all optional)

Copy `backend/.env.example` to `backend/.env` to change the port, database or login lifetime. By default it uses a SQLite file (`backend/college_timetable.db`) and generates a signing key on first run (`backend/.secret_key`). Any SQLAlchemy URL works for `DATABASE_URL` (e.g. PostgreSQL after `pip install psycopg[binary]`).

## Tests

```bash
cd backend && pip install -r requirements-dev.txt && pytest      # 139 tests, ~2.5 min
cd frontend && npm test                                          # needs Node 22+, no dependencies
```

## Troubleshooting

* **"Cannot reach the server"** in the browser: `python run.py` is not running in a terminal.
* **Generate says "Fix these problems first"**: read the list, each line names the faculty member / section / course to change.
* **"Generated, but some clashes remain"**: the data is too tight (e.g. one lab room for many sections). Add a room, free up a faculty member, or raise the time limit; the **Verify** button lists exactly what clashes.
* **Port in use**: set `PORT=8001` in `backend/.env`.
* **Reset everything**: stop the app and delete `backend/college_timetable.db`.

## Known limitations

* **Explanations** re-derive validity and score effects from the saved timetable; they cannot say why the randomised search happened to choose a slot (it keeps no such record).
* **Simulations** support whole-week room closures only (not “closed on Tuesday”), adding sections/courses/allocations but not removing them, and changing preference weights only for the simulation (weights are not stored, so applying keeps just the resulting timetable). Pinned classes need the “adjust” strategy. A run is synchronous (up to 60 s).
* **Undo** has no redo; simulations that changed data cannot be undone automatically; history keeps 200 entries; “who” is the college account.
* **Quality score** is a documented heuristic for comparing timetables of the same college, not an industry standard.
* **Time zones:** the API sends UTC timestamps; the browser shows them in local time.
* Excel import only **adds** records; it never updates existing ones (edit those on their pages, or use *Replace all*). Formulas must have been calculated and saved by Excel; use plain values.
* Password reset by email is not included (change password via the API: `POST /api/auth/change-password`).
* Every teaching day has the same number of periods (no half-day Saturdays).
* Generation runs inside the web request (max 120 s) and the server is single-user-scale, which is fine for one college, not for hosting many at once.
* Rooms are matched by type and capacity only (a "Networks lab" is not distinguished from any other lab).
* The UI is tested end to end in a simulated browser (jsdom) against the real server, not in Chrome/Firefox, so small visual issues are possible. Please report them.
