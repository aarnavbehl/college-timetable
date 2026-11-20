// "Why this slot?": shows what the saved timetable lets us say about one class (see services/explain.py).
import { api } from '../api.js';
import { deltaClass, deltaLabel } from '../analytics-utils.js';
import { badge, clear, errorList, h, modal, spinner } from '../ui.js';
import { DAY_NAMES, periodLabel, timeRange } from '../utils.js';

export async function openExplain(session, settings) {
  const body = h('div', {}, spinner('Re-checking every constraint for this class…'));
  modal(`Why this slot? ${session.course_code} · ${session.section}${session.batch ? ` batch ${session.batch}` : ''}`, body, { wide: true });
  let e;
  try {
    e = await api.get(`/timetable/sessions/${session.id}/explain`);
  } catch (err) {
    clear(body).append(errorList([err.message]));
    return;
  }
  const times = settings.period_times;
  const slotText = (d, s, len) => `${DAY_NAMES[d]}, ${periodLabel(s, len)} (${timeRange(times, s, len)})`;
  const a = e.assignment;
  let showAll = false;
  const altHost = h('div');

  function drawAlternatives() {
    clear(altHost);
    const slots = e.alternatives.slots.filter((x) => !x.current);
    // by default: the best feasible slots PLUS a few blocked ones, so the reasons are visible straight away
    const feasible = slots.filter((x) => x.feasible);
    const blocked = slots.filter((x) => !x.feasible);
    const rows = showAll ? slots : [...feasible.slice(0, 6), ...blocked.slice(0, 6)];
    altHost.append(h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
      h('thead', {}, h('tr', {}, ['Slot', 'Possible?', 'Soft-score change', 'Details'].map((t) => h('th', {}, t)))),
      h('tbody', {}, rows.map((x) => h('tr', { class: x.feasible ? '' : 'row-error' },
        h('td', {}, slotText(x.day, x.start, a.length)),
        h('td', {}, x.feasible ? badge('Yes', 'ok') : badge('No', 'bad')),
        h('td', { class: x.feasible ? `delta ${deltaClass(x.delta)}` : 'muted' }, x.feasible ? deltaLabel(x.delta) : '—'),
        h('td', {}, x.feasible
          ? h('small', { class: 'muted' }, x.rooms.length ? `Rooms: ${x.rooms.join(', ')}` : '')
          : h('small', { class: 'row-msg bad' }, `Rejected: ${x.reasons.join('; ')}`))))))));
    if (slots.length > rows.length || showAll) {
      altHost.append(h('button', { class: 'btn small', onclick: () => { showAll = !showAll; drawAlternatives(); } },
        showAll ? 'Show fewer' : `Show all ${slots.length} slots`));
    }
  }
  drawAlternatives();

  const sm = e.alternatives.summary;
  clear(body).append(h('div', { class: 'explain' },
    h('div', { class: 'note' }, h('strong', {}, 'Assigned: '), slotText(a.day, a.start, a.length),
      h('ul', { class: 'plain' }, e.members.map((m) => h('li', {}, `${m.course_code} for ${m.section}${m.batch ? ` batch ${m.batch}` : ''} (${m.students} students) — ${m.faculty}, ${m.room || 'no room'}${m.room_capacity ? ` (capacity ${m.room_capacity})` : ''}`)))),
    e.warnings.length ? errorList(e.warnings, 'warn') : null,

    h('h3', {}, 'Hard constraints ', e.hard.all_satisfied ? badge('all satisfied', 'ok') : badge('NOT all satisfied', 'bad')),
    h('ul', { class: 'checks' }, e.hard.constraints.map((c) => h('li', { class: c.satisfied ? 'pass' : 'fail' },
      h('span', { class: 'mark' }, c.satisfied ? '✓' : '✗'), h('span', {}, h('strong', {}, c.label), h('br'), h('small', { class: 'muted' }, c.evidence))))),

    h('h3', {}, 'Soft preferences ', badge('quality, not rules', '')),
    e.soft.marginal.length
      ? h('ul', { class: 'plain' }, e.soft.marginal.map((m) => h('li', { class: deltaClass(m.cost) === 'good' ? 'delta good' : 'delta bad' }, m.text)))
      : null,
    h('p', { class: 'muted' }, e.soft.note, ` Timetable soft score: ${e.soft.total} (lower is better).`),

    h('h3', {}, 'Other slots this class could take'),
    h('p', {}, `${sm.feasible_alternatives} of ${sm.legal_slots - 1} other legal slots are possible without a clash: `,
      `${sm.better} would score better, ${sm.equal} the same, ${sm.worse} worse. `,
      `${sm.infeasible_alternatives} are blocked (reasons below).`),
    altHost,

    h('h3', {}, 'Rooms at this time'),
    e.rooms.map((blk) => h('div', {}, h('strong', {}, blk.class),
      h('div', { class: 'chips' }, blk.options.map((o) => h('span', { class: `chip ${o.status}`, title: o.occupied_by.length ? `Used by ${o.occupied_by.join(', ')}` : '' },
        `${o.name} (${o.capacity})${o.home_room ? ' · home' : ''} — ${o.status}`))))),
    h('details', { class: 'details-list' }, h('summary', {}, 'How was this worked out?'), h('p', { class: 'muted' }, e.method))));
}
