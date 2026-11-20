// Quality analytics: every number comes from /api/analytics (saved timetable + independent verifier).
import { api } from '../api.js';
import { barList, columnChart, gauge, heatmap, metricCard } from '../charts.js';
import { num, pct, plural, scoreLabel } from '../analytics-utils.js';
import { navigate } from '../router.js';
import { badge, dataTable, emptyState, errorList, h, pageHeader } from '../ui.js';

const KIND_BADGE = { hard: ['Hard constraint', 'bad'], soft: ['Soft preference', ''], info: ['Information', 'sync'] };

export async function analyticsPage(container) {
  const a = await api.get('/analytics');
  container.append(pageHeader('Analytics', 'Quality metrics calculated from your saved timetable. Nothing here is estimated.'));
  if (!a.available) {
    container.append(emptyState(a.reason, h('button', { class: 'btn primary', onclick: () => navigate('/generate') }, 'Generate a timetable')));
    return;
  }
  const D = a.definitions;
  const S = a.summary;
  const def = (key) => (D[key] ? D[key].text : '');
  const card = (title, kind, ...children) => h('div', { class: 'card' },
    h('h3', {}, title, ' ', badge(...KIND_BADGE[kind])), children);

  // ------------------------------------------------------------------ verification banner
  const clean = S.hard_violations === 0 && S.unscheduled_classes === 0;
  container.append(h('div', {},
    h('div', { class: `banner ${clean ? 'ok' : 'bad'}` }, clean
      ? '✔ Verified just now by the independent verifier: no hard-constraint violations and every required class is scheduled.'
      : `✖ This timetable is NOT valid: ${plural(S.hard_violations, 'hard-constraint violation')} and ${plural(S.unscheduled_classes, 'unscheduled class', 'unscheduled classes')}.`),
    a.timetable && a.timetable.stale ? h('div', { class: 'banner warn' }, 'Your data changed after this timetable was generated, so these numbers describe the saved timetable against your current data. ',
      h('a', { href: '#/generate' }, 'Regenerate it'), '.') : null));

  // ------------------------------------------------------------------ headline metrics
  container.append(h('div', { class: 'metrics' },
    metricCard({ label: 'Scheduled / required classes', value: `${S.scheduled_classes} / ${S.required_classes}`, sub: `${S.scheduled_periods} of ${S.required_periods} periods`, help: def('required_classes') }),
    metricCard({ label: 'Unscheduled classes', value: S.unscheduled_classes, kind: S.unscheduled_classes ? 'bad' : 'ok', help: def('unscheduled_classes') }),
    metricCard({ label: 'Hard violations', value: S.hard_violations, kind: S.hard_violations ? 'bad' : 'ok', sub: 'must be 0', help: def('hard_violations') }),
    metricCard({ label: 'Room utilization', value: pct(a.rooms.overall_pct), sub: `${a.rooms.used_slots} of ${a.rooms.available_slots} room-periods`, help: def('room_utilization') }),
    metricCard({ label: 'Faculty load spread (CV)', value: num(a.faculty.stats.cv, 2), sub: `${a.faculty.stats.teaching} of ${a.faculty.stats.total_faculty} teaching`, help: def('faculty_workload') }),
    metricCard({ label: 'Section gaps', value: a.gaps.total, kind: 'soft', sub: 'free periods between classes', help: def('gaps') })));

  // ------------------------------------------------------------------ hard constraints
  const hard = a.hard;
  const other = Object.entries(hard.other);
  container.append(card('Hard constraints', 'hard',
    h('p', { class: 'muted' }, 'These must all be zero. They are checked by ', h('strong', {}, 'a verifier that shares no code with the solver'), '.'),
    h('div', { class: 'metrics small' },
      metricCard({ label: 'Faculty conflicts', value: hard.faculty_conflicts, kind: hard.faculty_conflicts ? 'bad' : 'ok', help: def('faculty_conflicts') }),
      metricCard({ label: 'Room conflicts', value: hard.room_conflicts, kind: hard.room_conflicts ? 'bad' : 'ok', help: def('room_conflicts') }),
      metricCard({ label: 'Student-section conflicts', value: hard.section_conflicts, kind: hard.section_conflicts ? 'bad' : 'ok', help: def('section_conflicts') })),
    other.length ? h('p', {}, 'Other violations: ', other.map(([k, n]) => badge(`${k.replace(/_/g, ' ')}: ${n}`, 'bad')).flatMap((b) => [b, ' '])) : null,
    hard.samples.length ? errorList(hard.samples) : null,
    hard.unscheduled.length ? h('div', {}, h('h4', {}, 'Unscheduled classes'),
      dataTable([{ label: 'Section', value: (r) => r.section + (r.batch ? ` · B${r.batch}` : '') },
        { label: 'Course', value: (r) => `${r.course} · ${r.course_name}` }, { label: 'Faculty', value: (r) => r.faculty },
        { label: 'Required', value: (r) => r.required, class: 'num' }, { label: 'Scheduled', value: (r) => r.scheduled, class: 'num' }], hard.unscheduled)) : null));

  // ------------------------------------------------------------------ quality score
  const q = a.quality;
  container.append(card('Quality score', 'soft',
    h('div', { class: 'two-col' },
      h('div', { class: 'score-box' }, gauge(q.score, q.score == null ? 'No score' : scoreLabel(q.score)),
        q.reason ? h('p', { class: 'muted' }, q.reason) : h('p', { class: 'muted' }, 'Higher is better. It summarises the soft preferences only; it never replaces the hard constraints.')),
      h('div', {}, barList(q.components.map((c) => ({ label: `${c.label} (×${c.weight})`, value: c.score, text: `${c.score}`, note: c.detail })), { max: 100 }),
        h('small', { class: 'muted' }, 'Hover a bar for the numbers behind it.'))),
    h('details', { class: 'details-list' }, h('summary', {}, 'How is the score calculated?'), h('p', {}, q.formula),
      h('p', { class: 'muted' }, 'Each component is 0–100 where 100 means nothing to penalise. See the glossary below for each preference.'),
      h('ul', {}, q.components.map((c) => h('li', {}, h('strong', {}, `${c.label}: ${c.score}`), ` — ${c.detail}`)))),
    h('h4', {}, 'Soft preference units found'),
    dataTable([{ label: 'Preference', value: (c) => h('span', { title: c.definition }, c.label) },
      { label: 'Units', value: (c) => c.units, class: 'num' }, { label: 'Weight', value: (c) => c.weight, class: 'num' },
      { label: 'Cost', value: (c) => c.cost, class: 'num' }, { label: 'What it means', value: (c) => h('small', { class: 'muted' }, c.definition) }], a.soft.components),
    h('p', { class: 'muted' }, `Total solver cost: ${a.soft.weighted_total} (lower is better).`)));

  // ------------------------------------------------------------------ charts
  container.append(
    h('div', { class: 'two-col' },
      card('Classes and teaching hours by day', 'info',
        columnChart(a.days.map((d) => ({ label: d.name.slice(0, 3), value: d.classes, text: String(d.classes) }))),
        h('table', { class: 'mini' }, h('tbody', {}, a.days.map((d) => h('tr', {}, h('td', {}, d.name), h('td', { class: 'num' }, plural(d.classes, 'class', 'classes')),
          h('td', { class: 'num' }, `${num(d.hours, 2)} h`), h('td', { class: 'num' }, `${d.faculty_teaching} faculty`)))))),
      card('Room utilization', 'info',
        h('p', {}, h('strong', {}, pct(a.rooms.overall_pct)), ' overall', Object.entries(a.rooms.by_type).map(([t, v]) => ` · ${t === 'LAB' ? 'Labs' : 'Classrooms'} ${pct(v)}`)),
        barList(a.rooms.by_room.map((r) => ({ label: r.name, value: r.pct, text: pct(r.pct, 0), note: `${r.used} of ${r.available} periods`, kind: r.type === 'LAB' ? 'lab' : '' })), { max: 100 }))),
    h('div', { class: 'two-col' },
      card('Faculty workload', 'info',
        h('p', {}, `Average ${num(a.faculty.stats.mean, 1)} periods/week (min ${a.faculty.stats.min}, max ${a.faculty.stats.max}), spread CV ${num(a.faculty.stats.cv, 2)}.`),
        barList(a.faculty.histogram.map((x) => ({ label: x.label, value: x.count, text: plural(x.count, 'faculty', 'faculty'), kind: x.label === 'Over max' ? 'bad' : '' }))),
        h('h4', {}, 'Per faculty member (periods per week vs their maximum)'),
        barList(a.faculty.list.map((f) => ({ label: f.code, value: f.pct_of_max ?? 0, text: `${f.periods}/${f.max_week}`, note: `${f.name}: ${f.sessions} classes on ${f.days_taught} day(s); busiest day ${f.max_day_load} periods (limit ${f.max_day})`, kind: (f.pct_of_max ?? 0) > 100 ? 'bad' : '' })), { max: 100 })),
      card('Gaps in section timetables', 'soft',
        h('p', { class: 'muted' }, def('gaps')),
        columnChart(a.gaps.by_day.map((d) => ({ label: d.name.slice(0, 3), value: d.gaps })), { height: 90 }),
        h('h4', {}, 'By section'),
        a.gaps.by_section.length ? barList(a.gaps.by_section.map((s) => ({ label: s.section, value: s.gaps, text: plural(s.gaps, 'gap'), note: `${s.late_start} late-start period(s)` }))) : h('p', { class: 'muted' }, 'No sections have classes.'))));

  const hm = a.heatmap;
  container.append(h('div', { class: 'two-col' },
    card('Rooms in use (day × period)', 'info', h('p', { class: 'muted' }, `Darker = more of your ${hm.total_rooms} rooms busy. Hover a cell for the percentage.`),
      heatmap({ days: hm.days, periods: hm.periods, values: hm.rooms_in_use, total: hm.total_rooms, unit: 'rooms', hue: 215 })),
    card('Faculty teaching (day × period)', 'info', h('p', { class: 'muted' }, `Darker = more of your ${hm.total_faculty} faculty teaching.`),
      heatmap({ days: hm.days, periods: hm.periods, values: hm.faculty_teaching, total: hm.total_faculty, unit: 'faculty', hue: 150 }))));
  if (hm.conflicts.some((r) => r.some(Boolean))) {
    container.append(card('Conflict heat map', 'hard', h('p', { class: 'muted' }, 'Slots with hard-constraint violations.'),
      heatmap({ days: hm.days, periods: hm.periods, values: hm.conflicts, unit: 'violations', hue: 350 })));
  }

  // ------------------------------------------------------------------ glossary
  container.append(h('details', { class: 'card glossary' }, h('summary', {}, h('strong', {}, 'Glossary: what every metric means')),
    h('dl', {}, Object.values(D).flatMap((d) => [h('dt', {}, d.label, ' ', badge(...KIND_BADGE[d.kind])), h('dd', {}, d.text)]))));
}
