// What-if simulations: change a COPY of your college, see the verified result next to the saved timetable,
// and only then (after an explicit confirmation) apply it. Running a simulation never touches saved data.
import { api } from '../api.js';
import { currentQuery, navigate, rerender } from '../router.js';
import { loadSettings } from '../store.js';
import { badge, buildForm, clear, confirmDialog, dataTable, emptyState, errorList, h, modal, pageHeader, spinner, toast } from '../ui.js';
import {
  applyMessage, CHANGE_TYPES, canApply, deltaText, describeChange, diffAvailability, metricText, nextRef, placementSummary,
  formatWhen, slotText, statusInfo, VERDICT_CLASS, WEIGHT_FIELDS,
} from '../simulation-utils.js';
import { DAY_NAMES, DAY_SHORT } from '../utils.js';

export async function simulationsPage(container) {
  const id = currentQuery().get('id');
  if (id) return editor(container, id);
  return list(container);
}

// ------------------------------------------------------------------ list
async function list(container) {
  const items = await api.get('/scenarios');
  container.append(
    pageHeader('What-if simulations', 'Try a change on a copy of your college and compare the result with your saved timetable before deciding.',
      h('button', { class: 'btn primary', onclick: () => navigate('/simulations?id=new') }, '+ New scenario')),
    h('div', { class: 'note' }, 'Simulations never change your saved timetable or data. Only “Apply” does, and only after you confirm. Every candidate is checked by the independent verifier.'));
  if (!items.length) {
    container.append(emptyState('No scenarios yet. Examples: a teacher becomes unavailable on Mondays, a lab is closed for repairs, a new section is introduced.',
      h('button', { class: 'btn primary', onclick: () => navigate('/simulations?id=new') }, 'Create your first scenario')));
    return;
  }
  container.append(dataTable([
    { label: 'Scenario', value: (s) => h('a', { href: `#/simulations?id=${s.id}` }, h('strong', {}, s.name)) },
    { label: 'Status', value: (s) => { const i = statusInfo(s.status, s.outdated); return badge(i.label, i.kind); } },
    { label: 'Changes', value: (s) => s.change_count, class: 'num' },
    { label: 'Result', value: (s) => (!s.headline ? '—' : s.headline.hard_violations == null ? 'No candidate: see diagnostics'
      : `${s.headline.hard_violations} violation(s) · ${s.headline.moved} class(es) moved`) },
    { label: 'Last run', value: (s) => formatWhen(s.run_at) },
  ], items, {
    rowActions: (s) => [
      h('button', { class: 'btn small', onclick: () => navigate(`/simulations?id=${s.id}`) }, 'Open'),
      h('button', { class: 'btn small danger-outline', onclick: async () => {
        if (!(await confirmDialog(`Delete the scenario “${s.name}”? Your saved timetable is not affected.`))) return;
        await api.del(`/scenarios/${s.id}`);
        rerender();
      } }, 'Delete'),
    ],
  }));
}

// ------------------------------------------------------------------ availability picker
function makePicker(settings, current) {
  const selected = new Set(current.map(([d, p]) => `${d}:${p}`));
  const grid = h('div', { class: 'avail', style: { gridTemplateColumns: `auto repeat(${settings.periods_per_day}, 1fr)` } },
    h('span'), Array.from({ length: settings.periods_per_day }, (_, p) => h('span', { class: 'avail-head' }, `P${p + 1}`)),
    Array.from({ length: settings.days_per_week }, (_, d) => [h('span', { class: 'avail-head' }, DAY_SHORT[d]),
      Array.from({ length: settings.periods_per_day }, (_, p) => {
        const key = `${d}:${p + 1}`;
        const cell = h('button', { type: 'button', class: `avail-cell${selected.has(key) ? ' off' : ''}`, title: 'Click to toggle',
          onclick: () => { selected.has(key) ? selected.delete(key) : selected.add(key); cell.classList.toggle('off'); } });
        return cell;
      })]));
  return { el: h('div', {}, grid, h('small', { class: 'help' }, 'Red = unavailable. It starts as the person’s current availability; toggle what should change.')),
    get: () => [...selected].map((k) => k.split(':').map(Number)) };
}

// ------------------------------------------------------------------ editor
async function editor(container, idParam) {
  const [settings, faculty, rooms, sections, courses, branches, tt] = await Promise.all([
    loadSettings(), api.get('/faculty'), api.get('/rooms'), api.get('/sections'), api.get('/courses'), api.get('/branches'), api.get('/timetable')]);
  container.append(pageHeader('What-if simulation', null, h('a', { class: 'btn', href: '#/simulations' }, '← All scenarios')));
  if (!tt.meta) {
    container.append(emptyState('A simulation compares against your saved timetable, and there is none yet.',
      h('button', { class: 'btn primary', onclick: () => navigate('/generate') }, 'Generate a timetable first')));
    return;
  }
  const ctx = { settings, faculty, rooms: rooms.filter((r) => r.is_active !== false), sections, courses, branches, sessions: tt.sessions };
  const state = { sc: null, name: 'New scenario', description: '', strategy: 'adjust', time_limit: 10, changes: [], dirty: idParam === 'new', error: null, busy: '', showAll: false };
  if (idParam !== 'new') {
    state.sc = await api.get(`/scenarios/${idParam}`);
    Object.assign(state, { name: state.sc.name, description: state.sc.description || '', strategy: state.sc.strategy,
      time_limit: state.sc.time_limit, changes: state.sc.changes });
  }
  const root = h('div');
  container.append(root);
  const locked = () => state.sc && state.sc.status === 'applied';

  const describe = (c) => {
    if (c.type === 'pin_class') {
      const s = ctx.sessions.find((x) => x.id === c.session_id);
      const name = s ? `${s.course_code} (${s.section}${s.batch ? ` b${s.batch}` : ''})` : `class #${c.session_id}`;
      return `Pin ${name}: ${[c.day != null ? `fixed at ${DAY_NAMES[c.day]} P${c.start}` : '', c.room_id ? `in room ${(ctx.rooms.find((r) => r.id === c.room_id) || {}).name}` : ''].filter(Boolean).join(', ')}`;
    }
    return describeChange(c, ctx);
  };

  // ---- persistence
  async function save() {
    const body = { name: state.name.trim() || 'Untitled scenario', description: state.description.trim() || null,
      strategy: state.strategy, time_limit: state.time_limit, changes: state.changes };
    state.sc = state.sc ? await api.put(`/scenarios/${state.sc.id}`, body) : await api.post('/scenarios', body);
    state.changes = state.sc.changes;
    state.dirty = false;
    if (idParam === 'new') history.replaceState(null, '', `#/simulations?id=${state.sc.id}`);
    idParam = String(state.sc.id);
  }
  async function guard(fn, busyText) {
    state.error = null;
    state.busy = busyText;
    draw();
    try { await fn(); } catch (e) {
      const d = e.detail && typeof e.detail === 'object' && !Array.isArray(e.detail) ? e.detail : null;
      state.error = d ? [d.message, ...(d.errors || [])] : [e.message];
    }
    state.busy = '';
    draw();
  }
  const saveOnly = () => guard(async () => { await save(); toast('Scenario saved.', 'success'); }, 'Saving…');
  const saveAndRun = () => guard(async () => {
    if (!state.changes.length) throw new Error('Add at least one change first.');
    if (state.dirty || !state.sc) await save();
    state.sc = await api.post(`/scenarios/${state.sc.id}/run`);
    toast(state.sc.status === 'ready' ? 'Simulation finished: verified candidate.' : 'Simulation finished: see the diagnostics.', state.sc.status === 'ready' ? 'success' : 'error');
  }, `Simulating… (up to ${state.time_limit} s)`);

  async function apply() {
    const verdict = canApply(state.sc);
    if (!verdict.ok) return;
    if (!(await confirmDialog(applyMessage(state.sc), { confirmLabel: 'Apply to my saved timetable', danger: true }))) return;
    await guard(async () => {
      await api.post(`/scenarios/${state.sc.id}/apply`, { confirm: true });
      toast('Scenario applied. Your saved timetable now matches the candidate.', 'success');
      navigate('/timetable');
    }, 'Applying…');
  }

  // ---- drawing
  function draw() {
    clear(root);
    if (state.busy) { root.append(spinner(state.busy)); return; }
    const info = state.sc ? statusInfo(state.sc.status, state.sc.outdated) : { label: 'Not saved', kind: 'sync' };
    const disabled = locked();
    const nameInput = h('input', { type: 'text', value: state.name, maxlength: 80, disabled, 'aria-label': 'Scenario name',
      oninput: (e) => { state.name = e.target.value; state.dirty = true; } });
    const descInput = h('input', { type: 'text', value: state.description, maxlength: 300, disabled, placeholder: 'Optional note',
      oninput: (e) => { state.description = e.target.value; state.dirty = true; } });
    const strategy = h('select', { value: state.strategy, disabled, 'aria-label': 'Strategy',
      onchange: (e) => { state.strategy = e.target.value; state.dirty = true; draw(); } },
    h('option', { value: 'adjust' }, 'Adjust my current timetable (changes as few classes as possible)'),
    h('option', { value: 'rebuild' }, 'Rebuild from scratch (full optimisation, may move many classes)'));
    const limit = h('select', { value: String(state.time_limit), disabled, 'aria-label': 'Time limit',
      onchange: (e) => { state.time_limit = Number(e.target.value); state.dirty = true; } },
    [5, 10, 20, 30, 60].map((v) => h('option', { value: String(v) }, `${v} s`)));

    root.append(h('div', {},          // wrapped so h() skips the nulls (native append() would print the text "null")
      state.error ? h('div', { class: 'card' }, errorList(state.error)) : null,
      h('div', { class: 'card' },
        h('h3', {}, 'Scenario ', badge(info.label, info.kind)),
        h('div', { class: 'form' },
          h('label', { class: 'field' }, h('span', { class: 'label' }, 'Name'), nameInput),
          h('label', { class: 'field' }, h('span', { class: 'label' }, 'Note'), descInput),
          h('label', { class: 'field' }, h('span', { class: 'label' }, 'How should the candidate be produced?'), strategy,
            h('small', { class: 'help' }, state.strategy === 'adjust'
              ? 'Starts from your saved timetable and repairs only what the changes break. If that is not enough it falls back to a full rebuild and tells you.'
              : 'Runs the full genetic algorithm again from scratch.')),
          h('label', { class: 'field' }, h('span', { class: 'label' }, 'Search time limit'), limit))),
      h('div', { class: 'card' },
        h('h3', {}, 'Changes to simulate'),
        state.changes.length
          ? h('ul', { class: 'change-list' }, state.changes.map((c, i) => h('li', {}, h('span', {}, describe(c)),
            disabled ? null : h('button', { class: 'btn small danger-outline', onclick: () => { state.changes.splice(i, 1); state.dirty = true; draw(); } }, 'Remove'))))
          : h('p', { class: 'muted' }, 'No changes yet. Add one below.'),
        disabled ? null : h('div', { class: 'actions left' },
          h('button', { class: 'btn', onclick: openAddChange }, '+ Add a change'),
          h('button', { class: 'btn', disabled: !state.dirty, onclick: saveOnly }, 'Save'),
          h('button', { class: 'btn primary', disabled: !state.changes.length, onclick: saveAndRun }, '▶ Run simulation'))),
      state.sc && state.sc.result ? resultCard() : null));
  }

  function resultCard() {
    const sc = state.sc;
    const r = sc.result;
    const verdict = canApply(sc);
    const items = r.placement_changes ? r.placement_changes.items : [];
    const shown = state.showAll ? items : items.slice(0, 25);
    const banner = sc.status === 'applied' ? h('div', { class: 'banner ok' }, 'This scenario was applied.')
      : sc.outdated ? h('div', { class: 'banner warn' }, 'Your data or saved timetable changed after this simulation, so the result below is out of date. Run it again.')
        : r.status === 'ready' ? h('div', { class: 'banner ok' }, '✔ Candidate verified by the independent verifier: no hard-constraint violations.')
          : r.status === 'invalid' ? h('div', { class: 'banner bad' }, `✖ The candidate is NOT valid: ${r.verification.violations} hard-constraint violation(s). It cannot be applied.`)
            : h('div', { class: 'banner bad' }, '✖ This scenario cannot be scheduled. Nothing can be applied.');
    return h('div', { class: 'card' },
      h('h3', {}, 'Result ', sc.run_at ? h('small', { class: 'muted' }, `run ${formatWhen(sc.run_at)}`) : null),
      banner,
      r.strategy_used ? h('p', { class: 'muted' }, `Strategy used: ${r.strategy_used} · ${r.seconds}s`, ...(r.notes || []).map((n) => h('span', {}, h('br'), n))) : null,
      r.diagnostics && r.diagnostics.length ? h('div', {}, h('h4', {}, 'Diagnostics'), errorList(r.diagnostics, 'error')) : null,
      r.comparison ? h('div', {}, h('h4', {}, 'Current timetable vs candidate'),
        dataTable([
          { label: 'Metric', value: (c) => h('span', {}, c.label, ' ', badge(c.kind === 'hard' ? 'hard' : c.kind === 'soft' ? 'soft' : 'info', c.kind === 'hard' ? 'bad' : c.kind === 'soft' ? '' : 'sync')) },
          { label: 'Current', value: (c) => metricText(c, c.baseline), class: 'num' },
          { label: 'Candidate', value: (c) => h('strong', {}, metricText(c, c.candidate)), class: 'num' },
          { label: 'Change', value: (c) => h('span', { class: `delta ${VERDICT_CLASS[c.verdict]}` }, deltaText(c), c.verdict === 'better' ? ' ✓' : c.verdict === 'worse' ? ' ✗' : ''), class: 'num' },
        ], r.comparison)) : null,
      r.soft_under_scenario_weights ? h('p', {}, h('strong', {}, 'Soft cost under your new weights: '),
        `current ${r.soft_under_scenario_weights.baseline} → candidate ${r.soft_under_scenario_weights.candidate} (lower is better)`) : null,
      r.placement_changes ? h('div', {}, h('h4', {}, 'Class placements'), h('p', {}, placementSummary(r.placement_changes.summary)),
        items.length ? h('div', {}, dataTable([
          { label: 'What', value: (i) => badge({ moved: 'Moved', room_changed: 'Room changed', added: 'Added', removed: 'Removed' }[i.change], i.change === 'removed' ? 'bad' : i.change === 'added' ? 'ok' : 'warn') },
          { label: 'Class', value: (i) => (i.to || i.from).class },
          { label: 'Faculty', value: (i) => (i.to || i.from).faculty },
          { label: 'From', value: (i) => (i.from ? `${slotText(i.from.day, i.from.start)} · ${i.from.room || 'no room'}` : '—') },
          { label: 'To', value: (i) => (i.to ? `${slotText(i.to.day, i.to.start)} · ${i.to.room || 'no room'}` : '—') },
        ], shown), items.length > 25 ? h('button', { class: 'btn small', onclick: () => { state.showAll = !state.showAll; draw(); } }, state.showAll ? 'Show fewer' : `Show all ${items.length}`) : null)
          : h('p', { class: 'muted' }, 'No class had to move.')) : null,
      sc.status === 'applied' ? null : h('div', { class: 'danger-zone apply-zone' },
        h('p', {}, h('strong', {}, 'Apply this candidate'), ' replaces your saved timetable',
          r.data_changes ? ' and permanently saves the data changes above.' : '. No setup data changes.'),
        h('div', { class: 'actions left' }, h('button', { class: 'btn danger', disabled: !verdict.ok, onclick: apply }, 'Apply to my saved timetable…')),
        verdict.ok ? null : h('p', { class: 'muted' }, verdict.reason)));
  }

  // ---- adding changes
  function openAddChange() {
    const holder = h('div');
    const help = h('p', { class: 'muted' });
    const typeSel = h('select', { 'aria-label': 'Type of change', onchange: () => render(typeSel.value) },
      CHANGE_TYPES.map((t) => h('option', { value: t.type }, t.label)));
    const m = modal('Add a change', h('div', {}, h('label', { class: 'field' }, h('span', { class: 'label' }, 'What changes?'), typeSel), help, holder), { wide: true });
    function render(type) {
      clear(holder);
      help.textContent = CHANGE_TYPES.find((t) => t.type === type).help;
      const spec = SPECS[type]();
      const form = buildForm(spec.fields, spec.defaults || {}, {
        submitLabel: 'Add to scenario', onCancel: () => m.close(),
        onSubmit: async (values) => { state.changes.push(spec.build(values)); state.dirty = true; m.close(); draw(); },
      });
      holder.append(form.el);
    }
    render(typeSel.value);
  }

  const opts = (rows, label) => rows.map((r) => ({ value: r.id, label: label(r) }));
  const facOptions = () => opts(ctx.faculty, (f) => `${f.name} (${f.code})`);
  const SPECS = {
    faculty_availability() {
      let picker;
      const wrap = h('div');
      const rebuild = (fid) => { const f = ctx.faculty.find((x) => x.id === fid); picker = makePicker(ctx.settings, f ? f.unavailable : []); clear(wrap).append(picker.el); };
      return {
        fields: [
          { name: 'faculty_id', label: 'Faculty member', type: 'select', number: true, options: facOptions(), onChange: (control) => rebuild(Number(control.value)) },
          { name: 'slots', label: 'Availability', type: 'custom', render: () => { rebuild(ctx.faculty[0] && ctx.faculty[0].id); return { el: wrap, get: () => picker.get() }; } }],
        defaults: { faculty_id: ctx.faculty[0] && ctx.faculty[0].id },
        build: (v) => {
          const current = (ctx.faculty.find((f) => f.id === v.faculty_id) || {}).unavailable || [];
          const d = diffAvailability(current, v.slots);
          if (!d.add.length && !d.remove.length) throw new Error('Toggle at least one slot to change.');
          return { type: 'faculty_availability', faculty_id: v.faculty_id, add_unavailable: d.add, remove_unavailable: d.remove };
        },
      };
    },
    faculty_limits: () => ({
      fields: [{ name: 'faculty_id', label: 'Faculty member', type: 'select', number: true, options: facOptions() },
        { name: 'max_per_day', label: 'Max periods per day', type: 'number', optional: true, min: 1, max: 12, placeholder: 'unchanged' },
        { name: 'max_per_week', label: 'Max periods per week', type: 'number', optional: true, min: 1, max: 84, placeholder: 'unchanged' }],
      defaults: { faculty_id: ctx.faculty[0] && ctx.faculty[0].id },
      build: (v) => {
        if (v.max_per_day == null && v.max_per_week == null) throw new Error('Set a daily and/or weekly maximum.');
        return { type: 'faculty_limits', faculty_id: v.faculty_id, max_per_day: v.max_per_day, max_per_week: v.max_per_week };
      },
    }),
    room_closed: () => ({
      fields: [{ name: 'room_id', label: 'Room', type: 'select', number: true, options: opts(ctx.rooms, (r) => `${r.name} (${r.room_type === 'LAB' ? 'lab' : 'classroom'}, ${r.capacity})`) }],
      defaults: { room_id: ctx.rooms[0] && ctx.rooms[0].id },
      build: (v) => ({ type: 'room_closed', room_id: v.room_id }),
    }),
    add_section: () => ({
      fields: [{ name: 'branch_id', label: 'Branch', type: 'select', number: true, options: opts(ctx.branches, (b) => `${b.code} — ${b.name}`) },
        { name: 'year', label: 'Year', type: 'number', required: true, min: 1, max: 6 },
        { name: 'name', label: 'Section', type: 'text', required: true, maxlength: 10, placeholder: 'C' },
        { name: 'strength', label: 'Number of students', type: 'number', required: true, min: 1, max: 1000 },
        { name: 'batches', label: 'Lab batches', type: 'number', required: true, min: 1, max: 6 },
        { name: 'home_room_id', label: 'Home room', type: 'select', number: true, optional: true, placeholder: '— none —',
          options: opts(ctx.rooms.filter((r) => r.room_type === 'CLASSROOM'), (r) => `${r.name} (${r.capacity})`) }],
      defaults: { branch_id: ctx.branches[0] && ctx.branches[0].id, year: 2, strength: 60, batches: 1 },
      build: (v) => ({ type: 'add_section', ref: nextRef(state.changes, 's'), branch_id: v.branch_id, year: v.year, name: v.name, strength: v.strength,
        batches: v.batches, home_room_id: v.home_room_id, label: `${(ctx.branches.find((b) => b.id === v.branch_id) || {}).code} Y${v.year}-${v.name}` }),
    }),
    add_course: () => ({
      fields: [{ name: 'code', label: 'Course code', type: 'text', required: true, maxlength: 20, placeholder: 'MA301' },
        { name: 'name', label: 'Course name', type: 'text', required: true },
        { name: 'course_type', label: 'Type', type: 'select', options: [{ value: 'THEORY', label: 'Theory' }, { value: 'LAB', label: 'Lab' }] },
        { name: 'hours_per_week', label: 'Hours (periods) per week', type: 'number', required: true, min: 1, max: 30 },
        { name: 'session_length', label: 'Periods per session', type: 'number', required: true, min: 1, max: 6 },
        { name: 'branch_id', label: 'Department', type: 'select', number: true, optional: true, placeholder: '— none —', options: opts(ctx.branches, (b) => b.code) }],
      defaults: { course_type: 'THEORY', hours_per_week: 3, session_length: 1 },
      build: (v) => ({ type: 'add_course', ref: nextRef(state.changes, 'c'), code: v.code, name: v.name, course_type: v.course_type,
        hours_per_week: v.hours_per_week, session_length: v.session_length, branch_id: v.branch_id }),
    }),
    add_allocation() {
      const secOptions = [...opts(ctx.sections, (s) => `${s.label} (${s.strength})`),
        ...state.changes.filter((c) => c.type === 'add_section').map((c) => ({ value: c.ref, label: `${c.label || c.name} (new in this scenario)` }))];
      const courseOptions = [...opts(ctx.courses, (c) => `${c.code} — ${c.name}`),
        ...state.changes.filter((c) => c.type === 'add_course').map((c) => ({ value: c.ref, label: `${c.code} — ${c.name} (new in this scenario)` }))];
      const parse = (x) => (/^\d+$/.test(String(x)) ? Number(x) : x);
      return {
        fields: [{ name: 'section_id', label: 'Section', type: 'select', options: secOptions },
          { name: 'course_id', label: 'Course', type: 'select', options: courseOptions },
          { name: 'faculty_id', label: 'Faculty', type: 'select', number: true, options: facOptions() },
          { name: 'hours_per_week', label: 'Hours per week', type: 'number', optional: true, min: 1, max: 30, placeholder: 'course default' },
          { name: 'batch', label: 'Lab batch', type: 'number', optional: true, min: 1, max: 6, placeholder: 'whole section' },
          { name: 'sync_group', label: 'Sync group', type: 'text', optional: true, maxlength: 40 },
          { name: 'students', label: 'Students enrolled', type: 'number', optional: true, min: 1, max: 1000 }],
        defaults: { faculty_id: ctx.faculty[0] && ctx.faculty[0].id },
        build: (v) => ({ type: 'add_allocation', section_id: parse(v.section_id), course_id: parse(v.course_id), faculty_id: v.faculty_id,
          hours_per_week: v.hours_per_week, batch: v.batch, sync_group: v.sync_group, students: v.students }),
      };
    },
    soft_weights: () => ({
      fields: [...WEIGHT_FIELDS.map(([k, label]) => ({ name: k, label: `${label} (0 = don't care)`, type: 'number', optional: true, min: 0, max: 20, placeholder: 'unchanged' })),
        { name: 'max_run', label: 'Periods in a row before it counts as long', type: 'number', optional: true, min: 1, max: 12, placeholder: 'unchanged' }],
      build: (v) => {
        const given = Object.fromEntries(Object.entries(v).filter(([, x]) => x != null));
        if (!Object.keys(given).length) throw new Error('Set at least one weight.');
        return { type: 'soft_weights', ...given };
      },
    }),
    pin_class: () => ({
      fields: [{ name: 'session_id', label: 'Class', type: 'select', number: true,
        options: ctx.sessions.map((s) => ({ value: s.id, label: `${s.course_code} · ${s.section}${s.batch ? ` b${s.batch}` : ''} — ${slotText(s.day, s.start)}, ${s.room || 'no room'}` })) },
      { name: 'day', label: 'Fix on day', type: 'select', number: true, optional: true, placeholder: '— keep current time —',
        options: Array.from({ length: ctx.settings.days_per_week }, (_, d) => ({ value: d, label: DAY_NAMES[d] })) },
      { name: 'start', label: 'Starting period', type: 'select', number: true, optional: true, placeholder: '—',
        options: Array.from({ length: ctx.settings.periods_per_day }, (_, p) => ({ value: p + 1, label: `Period ${p + 1}` })) },
      { name: 'room_id', label: 'Fix in room', type: 'select', number: true, optional: true, placeholder: '— keep current room —',
        options: opts(ctx.rooms, (r) => `${r.name} (${r.capacity})`) }],
      defaults: { session_id: ctx.sessions[0] && ctx.sessions[0].id },
      build: (v) => {
        if ((v.day == null) !== (v.start == null)) throw new Error('Choose both a day and a starting period, or neither.');
        if (v.day == null && v.room_id == null) throw new Error('Pin a time and/or a room.');
        return { type: 'pin_class', session_id: v.session_id, day: v.day, start: v.start, room_id: v.room_id };
      },
    }),
  };

  draw();
}
