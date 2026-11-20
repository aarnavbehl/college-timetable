import { api } from '../api.js';
import { badge, buildForm, errorList, h, pageHeader, spinner, toast } from '../ui.js';
import { navigate } from '../router.js';

export async function generatePage(container) {
  const check = await api.get('/validate');
  const result = h('div');

  const form = buildForm([
    { name: 'time_limit', label: 'Search time (seconds)', type: 'select', number: true,
      options: [5, 10, 15, 30, 60, 120].map((v) => ({ value: v, label: `${v} s${v === 15 ? ' (recommended)' : ''}` })),
      help: 'The scheduler stops early once nothing improves. Larger colleges benefit from more time.' },
  ], { time_limit: 15 }, {
    submitLabel: 'Generate timetable',
    onSubmit: async (v) => {
      result.replaceChildren(spinner('Evolving timetables… this can take up to the time you chose.'));
      try {
        const r = await api.post('/timetable/generate', { time_limit: v.time_limit });
        result.replaceChildren(h('div', { class: 'card' },
          h('h3', {}, r.ok ? '✅ Timetable generated — no clashes' : '⚠️ Generated, but some clashes remain'),
          h('p', {}, `${r.classes} classes (${r.sessions} sessions) · ${r.generations} generations · ${r.seconds}s · preference score ${r.soft}`),
          r.ok ? null : h('div', {}, h('p', {}, 'The data may be too tight for a clash-free solution. Details:'), errorList(r.problems.length ? r.problems : [`${r.hard} remaining violation(s)`])),
          r.warnings.length ? errorList(r.warnings, 'warn') : null,
          h('div', { class: 'actions left' }, h('button', { class: 'btn primary', onclick: () => navigate('/timetable') }, 'View timetable'))));
        toast(r.ok ? 'Timetable ready' : 'Generated with clashes', r.ok ? 'success' : 'error');
      } catch (e) { result.replaceChildren(errorList([e.message], 'error')); }
    },
  });

  container.append(pageHeader('Generate timetable', 'Uses a genetic algorithm to place every class, lab and elective.'),
    h('div', { class: 'card' },
      h('h3', {}, 'Pre-flight check ', check.ok ? badge('passed', 'ok') : badge('needs attention', 'bad')),
      check.ok ? h('p', { class: 'muted' }, `${check.tasks} classes to place.`) : errorList(check.errors),
      check.warnings.length ? errorList(check.warnings, 'warn') : null),
    check.ok ? h('div', { class: 'card' }, form.el) : h('p', { class: 'muted' }, 'Fix the problems above, then come back.'),
    result);
}
