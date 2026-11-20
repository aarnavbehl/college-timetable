// Pure helpers for the what-if simulation and history screens (no DOM, no network): unit-tested with node --test.
import { DAY_NAMES, DAY_SHORT } from './utils.js';

export const CHANGE_TYPES = [
  { type: 'faculty_availability', label: 'A faculty member’s availability changes', saves: true,
    help: 'Click slots to make them unavailable (or available again). Red = unavailable.' },
  { type: 'faculty_limits', label: 'A faculty member’s workload limits change', saves: true,
    help: 'Change the maximum periods per day and/or per week.' },
  { type: 'room_closed', label: 'A room becomes unavailable', saves: true,
    help: 'The room is not scheduled any more (the whole week).' },
  { type: 'add_section', label: 'Introduce a new section', saves: true,
    help: 'Then add allocations for it using “Allocate a course to a section”.' },
  { type: 'add_course', label: 'Introduce a new course', saves: true,
    help: 'Then allocate it to one or more sections.' },
  { type: 'add_allocation', label: 'Allocate a course to a section', saves: true,
    help: 'Who teaches which course to which section (including sections/courses added in this scenario).' },
  { type: 'soft_weights', label: 'Change a soft preference', saves: false,
    help: 'How strongly the scheduler avoids gaps, long runs and so on. Weights are not stored; applying keeps only the resulting timetable.' },
  { type: 'pin_class', label: 'Fix a class’s time or room', saves: false,
    help: 'Keeps that class exactly where you say; the rest adjusts around it.' },
];

export const WEIGHT_FIELDS = [
  ['section_gap', 'Gaps in a section’s day'], ['late_start', 'Late first class'], ['faculty_gap', 'Gaps in a teacher’s day'],
  ['same_day_repeat', 'Same subject twice in a day'], ['long_run', 'Teacher teaching > 3 periods in a row'], ['unbalanced', 'Uneven days'],
];

const STATUS = {
  draft: ['Not run yet', 'sync'], ready: ['Verified candidate', 'ok'], invalid: ['Candidate has violations', 'bad'],
  infeasible: ['Cannot be scheduled', 'bad'], applied: ['Applied', 'ok'],
};

export function statusInfo(status, outdated = false) {
  if (outdated && (status === 'ready' || status === 'invalid')) return { label: 'Out of date: run again', kind: 'warn' };
  const [label, kind] = STATUS[status] || [status, ''];
  return { label, kind };
}

/** Which slots to make unavailable / available again, given the current set and the user's final selection. */
export function diffAvailability(current, selected) {
  const key = ([d, p]) => `${d}:${p}`;
  const cur = new Map(current.map((x) => [key(x), x]));
  const sel = new Map(selected.map((x) => [key(x), x]));
  const sorted = (items) => items.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  return {
    add: sorted([...sel].filter(([k]) => !cur.has(k)).map(([, v]) => [...v])),
    remove: sorted([...cur].filter(([k]) => !sel.has(k)).map(([, v]) => [...v])),
  };
}

/** Next unused temporary reference like "new:s1" for scenario-created sections ('s') or courses ('c'). */
export function nextRef(changes, prefix) {
  const used = new Set(changes.map((c) => c.ref).filter(Boolean));
  let n = 1;
  while (used.has(`new:${prefix}${n}`)) n += 1;
  return `new:${prefix}${n}`;
}

/** May a scenario be applied right now? Mirrors the server (which re-checks everything). */
export function canApply(sc) {
  if (!sc) return { ok: false, reason: 'Save and run the scenario first.' };
  if (sc.status === 'applied') return { ok: false, reason: 'This scenario was already applied.' };
  if (sc.status === 'draft') return { ok: false, reason: 'Run the simulation first.' };
  if (sc.outdated) return { ok: false, reason: 'Your data or timetable changed after this simulation. Run it again.' };
  if (sc.status !== 'ready') return { ok: false, reason: 'Only a verified candidate (no hard violations) can be applied.' };
  return { ok: true, reason: '' };
}

/** The warning shown in the confirmation: what exactly applying changes and whether it can be undone. */
export function applyMessage(sc) {
  const data = sc.result && sc.result.data_changes;
  const lines = ['This replaces your saved timetable with the simulated candidate.'];
  if (data) {
    lines.push('It also permanently changes your data:', ...sc.result.changes.map((c) => `• ${c}`));
    lines.push('Because it changes data, it cannot be undone automatically.');
  } else {
    lines.push('It changes no setup data. You can undo it from History & undo.');
  }
  return lines;
}

const fmt1 = (v) => (v == null ? '—' : String(Number(v.toFixed(1))));
export function metricText(row, value) {
  if (value == null) return '—';
  if (row.key === 'room_utilization') return `${fmt1(value)}%`;
  if (row.key === 'quality_score' || row.key === 'workload_cv') return String(Number(value.toFixed(row.key === 'workload_cv' ? 2 : 1)));
  return String(value);
}

export function deltaText(row) {
  if (row.delta == null) return '—';
  if (row.delta === 0) return 'no change';
  const n = Math.abs(row.key === 'workload_cv' ? Number(row.delta.toFixed(2)) : Number(row.delta.toFixed(1)));
  return `${row.delta > 0 ? '+' : '−'}${n}${row.key === 'room_utilization' ? ' pts' : ''}`;
}

export const VERDICT_CLASS = { better: 'good', worse: 'bad', same: '', info: '' };

export function placementSummary(s) {
  const parts = [`${s.moved} moved`, `${s.room_changed} room change${s.room_changed === 1 ? '' : 's'}`, `${s.added} added`, `${s.removed} removed`];
  return `${parts.join(' · ')} · ${s.unchanged} unchanged`;
}

export function slotText(day, start) {
  return `${DAY_SHORT[day] || DAY_NAMES[day] || '?'} P${start}`;
}

/** Describe a change in words from lookup maps (used for changes that are not saved yet). */
export function describeChange(c, ctx) {
  const fac = (id) => (ctx.faculty.find((f) => f.id === id) || {}).name || `faculty #${id}`;
  const room = (id) => (ctx.rooms.find((r) => r.id === id) || {}).name || `room #${id}`;
  const slots = (pairs) => pairs.map(([d, p]) => slotText(d, p)).join(', ');
  switch (c.type) {
    case 'faculty_availability':
      return `${fac(c.faculty_id)}: ${[c.add_unavailable.length ? `unavailable ${slots(c.add_unavailable)}` : '',
        c.remove_unavailable.length ? `available again ${slots(c.remove_unavailable)}` : ''].filter(Boolean).join('; ')}`;
    case 'faculty_limits':
      return `${fac(c.faculty_id)}: ${[c.max_per_day ? `max ${c.max_per_day}/day` : '', c.max_per_week ? `max ${c.max_per_week}/week` : ''].filter(Boolean).join(', ')}`;
    case 'room_closed': return `Room ${room(c.room_id)} is closed`;
    case 'add_section': return `New section ${c.label || `${c.name} (year ${c.year})`} (${c.strength} students)`;
    case 'add_course': return `New course ${c.code} · ${c.name} (${c.course_type.toLowerCase()}, ${c.hours_per_week} h/week)`;
    case 'add_allocation': {
      const sec = typeof c.section_id === 'string' ? c.section_id : (ctx.sections.find((s) => s.id === c.section_id) || {}).label;
      const course = typeof c.course_id === 'string' ? c.course_id : (ctx.courses.find((x) => x.id === c.course_id) || {}).code;
      return `${fac(c.faculty_id)} teaches ${course} to ${sec}${c.batch ? ` batch ${c.batch}` : ''}`;
    }
    case 'soft_weights': return `Preference weights: ${WEIGHT_FIELDS.filter(([k]) => c[k] != null).map(([k]) => `${k.replace(/_/g, ' ')} = ${c[k]}`).join(', ')}`;
    case 'pin_class': return `Pin class #${c.session_id}${c.day != null ? ` at ${slotText(c.day, c.start)}` : ''}${c.room_id ? ` in ${room(c.room_id)}` : ''}`;
    default: return c.type;
  }
}

// ------------------------------------------------------------------ history
const KIND = {
  generated: ['Generated', 'sync'], manual_edit: ['Manual edit', ''], simulation: ['Simulation applied', 'lab'],
  cleared: ['Deleted', 'bad'], undo: ['Undo', 'warn'],
};
export function kindBadge(kind) {
  const [label, tone] = KIND[kind] || [kind, ''];
  return { label, tone };
}

/** The API sends UTC; make sure a zone-less string is still read as UTC, never as local time. */
export function parseUtc(iso) {
  if (!iso) return null;
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
}

export function formatWhen(iso, locale) {
  const d = parseUtc(iso);
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleString(locale) : '—';
}
