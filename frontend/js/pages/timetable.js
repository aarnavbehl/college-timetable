import { api, download } from '../api.js';
import { renderGrid } from '../grid.js';
import { navigate, rerender } from '../router.js';
import { openExplain } from './explain.js';
import { badge, clear, confirmDialog, emptyState, errorList, h, modal, pageHeader, spinner, toast } from '../ui.js';
import { DAY_NAMES, DAY_SHORT, entityOptions, filterSessions, periodLabel, timeRange, weeklyLoad } from '../utils.js';

const KINDS = [['section', 'Sections'], ['faculty', 'Faculty'], ['room', 'Rooms']];

export async function timetablePage(container) {
  let data = await api.get('/timetable');
  const state = { kind: 'section', id: null };

  if (!data.meta) {
    container.append(pageHeader('Timetable'),
      emptyState('No timetable has been generated yet.', h('button', { class: 'btn primary', onclick: () => navigate('/generate') }, 'Generate one')));
    return;
  }

  const banner = h('div');
  const controls = h('div', { class: 'toolbar' });
  const gridHost = h('div');

  async function reload() { data = await api.get('/timetable'); drawAll(); }

  function drawBanner() {
    clear(banner);
    const m = data.meta;
    if (m.hard > 0) banner.append(h('div', { class: 'banner bad' }, `⚠️ ${m.hard} clash(es) in this timetable (usually after a forced move). Press “Verify” for details.`));
    if (m.stale) banner.append(h('div', { class: 'banner warn' }, 'Your data changed after this timetable was generated. ',
      h('a', { href: '#/generate' }, 'Regenerate it'), ' to include the changes.'));
  }

  function drawControls() {
    clear(controls);
    const options = entityOptions(data.sessions, state.kind);
    if (!options.find((o) => o.id === state.id)) state.id = options[0]?.id ?? null;
    controls.append(
      h('div', { class: 'seg' }, KINDS.map(([k, label]) => h('button', { class: state.kind === k ? 'on' : '', type: 'button',
        onclick: () => { state.kind = k; state.id = null; drawControls(); drawGrid(); } }, label))),
      h('select', { 'aria-label': 'Choose', value: state.id == null ? '' : String(state.id),
        onchange: (e) => { state.id = Number(e.target.value); drawGrid(); } },
      options.map((o) => h('option', { value: String(o.id) }, o.label))));
  }

  function drawGrid() {
    clear(gridHost);
    if (state.id == null) { gridHost.append(emptyState('Nothing to show.')); return; }
    const mine = filterSessions(data.sessions, state.kind, state.id);
    const label = entityOptions(data.sessions, state.kind).find((o) => o.id === state.id)?.label;
    gridHost.append(h('div', { class: 'grid-title' }, h('h3', {}, label), h('span', { class: 'muted' }, `${weeklyLoad(mine)} periods per week`)),
      renderGrid(mine, data.settings, state.kind, openDetails));
  }

  function drawAll() { drawBanner(); drawControls(); drawGrid(); }

  // ----------------------------------------------------------- details & moving
  function openDetails(s) {
    const partners = data.sessions.filter((x) => x.task_key === s.task_key && x.id !== s.id);
    const m = modal(`${s.course_code} — ${s.course_name}`, h('div', {},
      h('dl', { class: 'facts' },
        h('dt', {}, 'Section'), h('dd', {}, s.section + (s.batch ? ` · Batch ${s.batch}` : '')),
        h('dt', {}, 'Faculty'), h('dd', {}, s.faculty),
        h('dt', {}, 'Room'), h('dd', {}, s.room || '— (room was deleted)'),
        h('dt', {}, 'When'), h('dd', {}, `${DAY_NAMES[s.day]}, ${periodLabel(s.start, s.length)} (${timeRange(data.settings.period_times, s.start, s.length)})`),
        h('dt', {}, 'Type'), h('dd', {}, s.course_type === 'LAB' ? badge('Lab', 'lab') : 'Theory')),
      partners.length ? h('div', { class: 'note' }, h('strong', {}, 'Runs at the same time as: '),
        partners.map((p) => `${p.course_code} (${p.section}${p.batch ? ` B${p.batch}` : ''}, ${p.faculty}, ${p.room || 'no room'})`).join(' · '),
        h('br'), 'Moving this class moves all of them together.') : null,
      h('div', { class: 'actions' }, h('button', { class: 'btn', onclick: () => m.close() }, 'Close'),
        h('button', { class: 'btn', onclick: () => { m.close(); openExplain(s, data.settings); } }, 'Why this slot?'),
        h('button', { class: 'btn primary', onclick: () => { m.close(); openMove(s); } }, 'Move to another slot…'))));
  }

  async function openMove(s) {
    const body = h('div', {}, spinner('Checking every slot…'));
    const m = modal(`Move ${s.course_code}`, body, { wide: true });
    let info;
    try { info = await api.get(`/timetable/sessions/${s.id}/options`); } catch (e) { clear(body).append(errorList([e.message])); return; }
    const P = data.settings.periods_per_day;
    const byKey = new Map(info.options.map((o) => [`${o.day}:${o.start}`, o]));

    async function attempt(opt, force) {
      try {
        await api.put(`/timetable/sessions/${s.id}/move`, { day: opt.day, start: opt.start, force });
        m.close();
        toast('Class moved.', 'success');
        await reload();
      } catch (e) {
        if (e.status === 409 && !force && e.detail && e.detail.reasons) {
          const go = await confirmDialog(`That slot clashes (${e.detail.reasons.join(', ')}). Move it anyway?`, { confirmLabel: 'Move anyway', danger: true });
          if (go) await attempt(opt, true);
        } else toast(e.message, 'error');
      }
    }

    const rows = [];
    for (let d = 0; d < data.settings.days_per_week; d += 1) {
      const cells = [h('th', {}, DAY_SHORT[d])];
      for (let p = 1; p <= P; p += 1) {
        const o = byKey.get(`${d}:${p}`);
        if (!o) { cells.push(h('td', {}, h('span', { class: 'slot na', title: 'Not a valid start (outside the day or across lunch)' }, '·'))); continue; }
        const cls = o.current ? 'current' : o.ok ? 'free' : 'busy';
        cells.push(h('td', {}, h('button', { type: 'button', class: `slot ${cls}`, disabled: o.current,
          title: o.current ? 'Current slot' : o.ok ? 'Free — click to move here' : `Clash: ${o.reasons.join(', ')}`,
          onclick: () => attempt(o, false) }, periodLabel(p, info.length))));
      }
      rows.push(h('tr', {}, cells));
    }
    clear(body).append(
      h('p', { class: 'muted' }, 'Each button is where the class would begin. ',
        h('span', { class: 'legend' }, h('i', { class: 'slot free' }), ' free ', h('i', { class: 'slot busy' }), ' clashes ', h('i', { class: 'slot current' }), ' now')),
      h('div', { class: 'table-wrap' }, h('table', { class: 'moves' },
        h('thead', {}, h('tr', {}, h('th'), Array.from({ length: P }, (_, i) => h('th', {}, `P${i + 1}`)))), h('tbody', {}, rows))));
  }

  // ----------------------------------------------------------- toolbar actions
  async function verify() {
    try {
      const r = await api.get('/timetable/check');
      modal('Verification', r.ok
        ? h('p', {}, '✅ Verified independently: no teacher, room or student group is double-booked, no one is scheduled while unavailable, and every lab is in a lab room.')
        : h('div', {}, h('p', {}, `${r.problems.length} problem(s) found:`), errorList(r.problems)));
    } catch (e) { toast(e.message, 'error'); }
  }

  async function clearAll() {
    if (!(await confirmDialog('Delete the generated timetable? Your setup data is not affected.', { confirmLabel: 'Delete timetable' }))) return;
    await api.del('/timetable');
    rerender();
  }

  container.append(pageHeader('Timetable', 'Click any class for details or to move it.',
    h('button', { class: 'btn', onclick: verify }, '✔ Verify'),
    h('button', { class: 'btn', onclick: () => download('/timetable/export.csv', 'timetable.csv').catch((e) => toast(e.message, 'error')) }, '⬇ CSV'),
    h('button', { class: 'btn', onclick: () => window.print() }, '🖨 Print'),
    h('button', { class: 'btn danger-outline', onclick: clearAll }, 'Delete')),
  banner, controls, gridHost);
  drawAll();
}
