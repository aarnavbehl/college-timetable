// The six "setup" screens, each configured for the generic CRUD page.
import { api } from '../api.js';
import { crudPage } from '../crud.js';
import { loadSettings } from '../store.js';
import { DAY_SHORT } from '../utils.js';
import { badge, h } from '../ui.js';

const opts = (rows, label) => rows.map((r) => ({ value: r.id, label: label(r) }));
const YEARS = [1, 2, 3, 4, 5, 6].map((y) => ({ value: y, label: `Year ${y}` }));

// ------------------------------------------------------------------ branches
export const branchesPage = crudPage({
  title: 'Branches', noun: 'branch', path: 'branches',
  subtitle: 'Departments such as CSE, ECE or Mechanical.',
  columns: () => [{ label: 'Code', value: (r) => h('strong', {}, r.code) }, { label: 'Name', value: (r) => r.name }],
  fields: () => [
    { name: 'code', label: 'Code', type: 'text', required: true, maxlength: 12, placeholder: 'CSE' },
    { name: 'name', label: 'Name', type: 'text', required: true, placeholder: 'Computer Science & Engineering' },
  ],
  deleteMessage: (r) => `Delete branch ${r.code}? (Branches that still have sections, courses or faculty cannot be deleted.)`,
});

// ------------------------------------------------------------------ rooms
export const roomsPage = crudPage({
  title: 'Rooms', noun: 'room', path: 'rooms',
  subtitle: 'Classrooms and labs. Labs host lab courses; classrooms host everything else.',
  columns: () => [
    { label: 'Name', value: (r) => h('strong', {}, r.name) },
    { label: 'Type', value: (r) => badge(r.room_type === 'LAB' ? 'Lab' : 'Classroom', r.room_type === 'LAB' ? 'lab' : '') },
    { label: 'Capacity', value: (r) => r.capacity, class: 'num' },
    { label: 'Status', value: (r) => (r.is_active === false ? badge('Closed', 'bad') : badge('Open', 'ok')) },
  ],
  fields: () => [
    { name: 'name', label: 'Room name', type: 'text', required: true, placeholder: 'C101' },
    { name: 'room_type', label: 'Type', type: 'select', options: [{ value: 'CLASSROOM', label: 'Classroom' }, { value: 'LAB', label: 'Lab' }] },
    { name: 'capacity', label: 'Capacity (students)', type: 'number', required: true, min: 1, max: 2000 },
    { name: 'is_active', label: 'Status', type: 'select', boolean: true, options: [{ value: 'true', label: 'Open (scheduled)' }, { value: 'false', label: 'Closed (not scheduled)' }],
      help: 'A closed room is ignored when generating timetables. Regenerate afterwards.' },
  ],
  defaults: () => ({ room_type: 'CLASSROOM', capacity: 60, is_active: true }),
  filters: () => [{ name: 'type', label: 'types', options: [{ value: 'CLASSROOM', label: 'Classrooms' }, { value: 'LAB', label: 'Labs' }], match: (r, v) => r.room_type === v }],
  deleteMessage: (r) => `Delete room ${r.name}? Sections using it as a home room will lose it.`,
});

// ------------------------------------------------------------------ sections
export const sectionsPage = crudPage({
  title: 'Sections', noun: 'section', path: 'sections',
  subtitle: 'A group of students: branch + year + section letter, e.g. CSE · Year 2 · A.',
  help: 'Lab batches: if a section splits into batches for labs (e.g. 2 batches of 30), set it here. A home room is where its theory classes happen.',
  context: async () => ({ branches: await api.get('/branches'), rooms: await api.get('/rooms') }),
  columns: () => [
    { label: 'Section', value: (r) => h('strong', {}, r.label) },
    { label: 'Students', value: (r) => r.strength, class: 'num' },
    { label: 'Lab batches', value: (r) => r.batches, class: 'num' },
    { label: 'Home room', value: (r) => r.home_room || '—' },
  ],
  fields: (ctx) => [
    { name: 'branch_id', label: 'Branch', type: 'select', number: true, required: true, options: opts(ctx.branches, (b) => `${b.code} — ${b.name}`) },
    { name: 'year', label: 'Year', type: 'select', number: true, options: YEARS },
    { name: 'name', label: 'Section', type: 'text', required: true, maxlength: 10, placeholder: 'A' },
    { name: 'strength', label: 'Number of students', type: 'number', required: true, min: 1, max: 1000 },
    { name: 'batches', label: 'Lab batches', type: 'number', required: true, min: 1, max: 6, help: '1 = the section never splits for labs.' },
    { name: 'home_room_id', label: 'Home room', type: 'select', number: true, optional: true, placeholder: '— no fixed room —',
      options: opts(ctx.rooms.filter((r) => r.room_type === 'CLASSROOM'), (r) => `${r.name} (${r.capacity})`) },
  ],
  defaults: (ctx) => ({ branch_id: ctx.branches[0]?.id, year: 1, strength: 60, batches: 1 }),
  filters: (ctx) => [{ name: 'branch', label: 'branches', options: opts(ctx.branches, (b) => b.code), match: (r, v) => String(r.branch_id) === v }],
  deleteMessage: (r) => `Delete ${r.label}? Its course allocations and timetable entries will be deleted too.`,
});

// ------------------------------------------------------------------ faculty
/** Grid of day x period toggles; selected cells are "not available". */
function availabilityField(settings) {
  return (value) => {
    const blocked = new Set((value || []).map(([d, p]) => `${d}:${p}`));
    const cells = [];
    const grid = h('div', { class: 'avail', style: { gridTemplateColumns: `auto repeat(${settings.periods_per_day}, 1fr)` } },
      h('span'), Array.from({ length: settings.periods_per_day }, (_, p) => h('span', { class: 'avail-head' }, `P${p + 1}`)),
      Array.from({ length: settings.days_per_week }, (_, d) => [
        h('span', { class: 'avail-head' }, DAY_SHORT[d]),
        Array.from({ length: settings.periods_per_day }, (_, p) => {
          const key = `${d}:${p + 1}`;
          const cell = h('button', { type: 'button', class: `avail-cell${blocked.has(key) ? ' off' : ''}`, title: 'Click to toggle',
            onclick: () => { blocked.has(key) ? blocked.delete(key) : blocked.add(key); cell.classList.toggle('off'); } });
          cells.push(cell);
          return cell;
        }),
      ]));
    return {
      el: h('div', {}, grid, h('small', { class: 'help' }, 'Red cells = not available (e.g. visiting faculty). The scheduler will never place a class there.')),
      get: () => [...blocked].map((k) => k.split(':').map(Number)),
    };
  };
}

export const facultyPage = crudPage({
  title: 'Faculty', noun: 'faculty member', path: 'faculty',
  subtitle: 'Lecturers and professors, with their workload limits and unavailable times.',
  wideForm: true,
  context: async () => ({ branches: await api.get('/branches'), settings: await loadSettings() }),
  columns: () => [
    { label: 'Code', value: (r) => h('strong', {}, r.code) },
    { label: 'Name', value: (r) => r.name },
    { label: 'Department', value: (r) => r.branch_code || '—' },
    { label: 'Max / day', value: (r) => r.max_per_day, class: 'num' },
    { label: 'Max / week', value: (r) => r.max_per_week, class: 'num' },
    { label: 'Blocked', value: (r) => (r.unavailable.length ? badge(`${r.unavailable.length} periods`, 'warn') : '—') },
  ],
  fields: (ctx) => [
    { name: 'name', label: 'Full name', type: 'text', required: true, placeholder: 'Dr. Anita Sharma' },
    { name: 'code', label: 'Short code', type: 'text', required: true, maxlength: 12, placeholder: 'AS' },
    { name: 'branch_id', label: 'Department', type: 'select', number: true, optional: true, placeholder: '— none —', options: opts(ctx.branches, (b) => b.code) },
    { name: 'max_per_day', label: 'Max periods per day', type: 'number', required: true, min: 1, max: 12 },
    { name: 'max_per_week', label: 'Max periods per week', type: 'number', required: true, min: 1, max: 84 },
    { name: 'unavailable', label: 'Not available at', type: 'custom', render: availabilityField(ctx.settings) },
  ],
  defaults: () => ({ max_per_day: 5, max_per_week: 20, unavailable: [] }),
  filters: (ctx) => [{ name: 'branch', label: 'departments', options: opts(ctx.branches, (b) => b.code), match: (r, v) => String(r.branch_id) === v }],
  deleteMessage: (r) => `Delete ${r.name}? (Faculty who still have allocations cannot be deleted.)`,
});

// ------------------------------------------------------------------ courses
export const coursesPage = crudPage({
  title: 'Courses', noun: 'course', path: 'courses',
  subtitle: 'Subjects and labs. Labs usually run as one block of 2–3 consecutive periods.',
  context: async () => ({ branches: await api.get('/branches') }),
  columns: () => [
    { label: 'Code', value: (r) => h('strong', {}, r.code) },
    { label: 'Name', value: (r) => r.name },
    { label: 'Dept', value: (r) => r.branch_code || '—' },
    { label: 'Type', value: (r) => badge(r.course_type === 'LAB' ? 'Lab' : 'Theory', r.course_type === 'LAB' ? 'lab' : '') },
    { label: 'Hours / week', value: (r) => r.hours_per_week, class: 'num' },
    { label: 'Session', value: (r) => `${r.session_length} period${r.session_length > 1 ? 's' : ''}`, class: 'num' },
  ],
  fields: (ctx) => [
    { name: 'code', label: 'Course code', type: 'text', required: true, maxlength: 20, placeholder: 'CS201' },
    { name: 'name', label: 'Course name', type: 'text', required: true, placeholder: 'Data Structures' },
    { name: 'branch_id', label: 'Department', type: 'select', number: true, optional: true, placeholder: '— none / shared —', options: opts(ctx.branches, (b) => b.code) },
    { name: 'course_type', label: 'Type', type: 'select', options: [{ value: 'THEORY', label: 'Theory (classroom)' }, { value: 'LAB', label: 'Lab (lab room)' }],
      onChange: (control, getters) => {
        const form = control.closest('form');
        const len = form.querySelector('[name=session_length]');
        const hrs = form.querySelector('[name=hours_per_week]');
        if (control.value === 'LAB' && len.value === '1') { len.value = '3'; if (hrs.value === '' || Number(hrs.value) < 3) hrs.value = '3'; }
        if (control.value === 'THEORY' && len.value !== '1') len.value = '1';
      } },
    { name: 'hours_per_week', label: 'Hours (periods) per week', type: 'number', required: true, min: 1, max: 30 },
    { name: 'session_length', label: 'Periods per session', type: 'number', required: true, min: 1, max: 6, help: 'Consecutive periods in one sitting. Hours per week must be a multiple of this.' },
  ],
  defaults: () => ({ course_type: 'THEORY', hours_per_week: 4, session_length: 1 }),
  filters: () => [{ name: 'type', label: 'types', options: [{ value: 'THEORY', label: 'Theory' }, { value: 'LAB', label: 'Labs' }], match: (r, v) => r.course_type === v }],
  deleteMessage: (r) => `Delete course ${r.code}? (Courses that are still allocated cannot be deleted.)`,
});

// ------------------------------------------------------------------ allocations
export const allocationsPage = crudPage({
  title: 'Allocations', noun: 'allocation', path: 'allocations',
  subtitle: 'Who teaches what to whom: faculty + course + section (optionally one lab batch).',
  help: 'Batch: leave empty for the whole section, or pick a lab batch. Sync group: allocations with the same label run at the same time — use it for lab rotations (batch 1 does Lab A while batch 2 does Lab B) and elective choices.',
  wideForm: true,
  context: async () => {
    const [sections, courses, faculty, allocations] = await Promise.all([
      api.get('/sections'), api.get('/courses'), api.get('/faculty'), api.get('/allocations')]);
    return { sections, courses, faculty, groups: [...new Set(allocations.map((a) => a.sync_group).filter(Boolean))] };
  },
  columns: () => [
    { label: 'Section', value: (r) => h('strong', {}, r.section_label) },
    { label: 'Batch', value: (r) => (r.batch ? `Batch ${r.batch}` : 'Whole') },
    { label: 'Course', value: (r) => `${r.course_code} · ${r.course_name}` },
    { label: 'Faculty', value: (r) => r.faculty_name },
    { label: 'Hours', value: (r) => r.effective_hours, class: 'num' },
    { label: 'Sync group', value: (r) => (r.sync_group ? badge(r.sync_group, 'sync') : '—') },
    { label: 'Students', value: (r) => r.students || '—', class: 'num' },
  ],
  fields: (ctx) => [
    { name: 'section_id', label: 'Section', type: 'select', number: true, required: true, options: opts(ctx.sections, (s) => `${s.label} (${s.strength})`) },
    { name: 'course_id', label: 'Course', type: 'select', number: true, required: true, options: opts(ctx.courses, (c) => `${c.code} — ${c.name} (${c.course_type === 'LAB' ? 'lab' : 'theory'})`) },
    { name: 'faculty_id', label: 'Faculty', type: 'select', number: true, required: true, options: opts(ctx.faculty, (f) => `${f.name} (${f.code})`) },
    { name: 'batch', label: 'Lab batch', type: 'number', optional: true, min: 1, max: 6, placeholder: 'empty = whole section' },
    { name: 'hours_per_week', label: 'Hours per week', type: 'number', optional: true, min: 1, max: 30, placeholder: "empty = course's default" },
    { name: 'sync_group', label: 'Sync group', type: 'text', optional: true, list: 'sync-groups', placeholder: 'e.g. CSE3A-ELEC1', maxlength: 40,
      help: 'Same label = same time slot.' },
    { name: 'students', label: 'Students enrolled', type: 'number', optional: true, min: 1, max: 1000, placeholder: 'empty = whole section / batch', help: 'For electives: how many choose this option (decides the room size).' },
  ],
  filters: (ctx) => [
    { name: 'section', label: 'sections', options: opts(ctx.sections, (s) => s.label), match: (r, v) => String(r.section_id) === v },
    { name: 'faculty', label: 'faculty', options: opts(ctx.faculty, (f) => f.code), match: (r, v) => String(r.faculty_id) === v },
  ],
  deleteMessage: (r) => `Remove ${r.course_code} for ${r.section_label}${r.batch ? ` batch ${r.batch}` : ''}?`,
});
