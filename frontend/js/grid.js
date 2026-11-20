// Renders one weekly timetable as an HTML table: days down, periods across, lunch column.
import { colorFor, DAY_SHORT, groupDay } from './utils.js';
import { h } from './ui.js';

/**
 * kind: 'section' | 'faculty' | 'room' decides which details are shown in each cell.
 * onPick(session) is called when a class is clicked.
 */
export function renderGrid(sessions, settings, kind, onPick) {
  const P = settings.periods_per_day;
  const D = settings.days_per_week;
  const lunch = settings.lunch_after;
  const head = h('tr', {}, h('th', { class: 'corner' }, 'Day'));
  for (let p = 1; p <= P; p += 1) {
    const t = settings.period_times[p - 1];
    head.append(h('th', {}, h('div', {}, `P${p}`), h('small', {}, `${t[0]}–${t[1]}`)));
    if (lunch === p) head.append(h('th', { class: 'lunch-head' }, 'Lunch'));
  }

  const body = [];
  for (let d = 0; d < D; d += 1) {
    const row = h('tr', {}, h('th', { class: 'day' }, DAY_SHORT[d]));
    const groups = groupDay(sessions.filter((s) => s.day === d));
    let p = 1;
    let lunchDone = false;
    const maybeLunch = (end) => {
      if (lunch && !lunchDone && end === lunch) {
        lunchDone = true;
        if (d === 0) row.append(h('td', { class: 'lunch', rowspan: D }, h('span', {}, 'LUNCH')));
      }
    };
    while (p <= P) {
      const group = groups.find((g) => g.start === p);
      if (group) {
        const crossesLunch = lunch && group.start <= lunch && group.start + group.length - 1 > lunch;
        if (crossesLunch) lunchDone = true;
        row.append(h('td', { colspan: group.length + (crossesLunch ? 1 : 0), class: group.conflict ? 'cell conflict' : 'cell' },
          group.items.map((s) => cellItem(s, kind, onPick))));
        p += group.length;
        maybeLunch(p - 1);
      } else {
        row.append(h('td', { class: 'cell empty' }));
        p += 1;
        maybeLunch(p - 1);
      }
    }
    body.push(row);
  }
  return h('div', { class: 'table-wrap grid-wrap' }, h('table', { class: 'grid' }, h('thead', {}, head), h('tbody', {}, body)));
}

function cellItem(s, kind, onPick) {
  const c = colorFor(s.course_code);
  const lines = {
    section: [s.faculty_code ? `${s.faculty}` : '', s.room || 'no room'],
    faculty: [`${s.section}${s.batch ? ` · Batch ${s.batch}` : ''}`, s.room || 'no room'],
    room: [`${s.section}${s.batch ? ` · Batch ${s.batch}` : ''}`, s.faculty],
  }[kind];
  return h('button', { class: `item${s.course_type === 'LAB' ? ' lab' : ''}`, type: 'button',
    style: { background: c.bg, borderLeftColor: c.border, color: c.text },
    title: `${s.course_name} — click for details`, onclick: () => onPick(s) },
  h('strong', {}, s.course_code, kind === 'section' && s.batch ? h('em', {}, ` B${s.batch}`) : null),
  h('span', {}, lines[0]), h('small', {}, lines[1]));
}

