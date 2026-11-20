import { api } from '../api.js';
import { badge, clear, confirmDialog, emptyState, errorList, h, pageHeader, spinner, toast } from '../ui.js';
import { loadSettings } from '../store.js';
import { DAY_NAMES, periodLabel, timeRange, todayIso } from '../utils.js';

export async function substitutionsPage(container) {
  const [faculty, settings, plans] = await Promise.all([api.get('/faculty'), loadSettings(), api.get('/substitutions')]);
  const picked = new Set();
  const dateInput = h('input', { type: 'date', value: todayIso() });
  const resultHost = h('div');
  const plansHost = h('div');
  let current = null;

  const list = h('div', { class: 'checklist-box' }, faculty.map((f) => h('label', { class: 'check' },
    h('input', { type: 'checkbox', onchange: (e) => { e.target.checked ? picked.add(f.id) : picked.delete(f.id); } }),
    ` ${f.name} `, h('small', { class: 'muted' }, f.code))));

  async function generate() {
    if (!picked.size) { toast('Tick at least one absent faculty member.', 'error'); return; }
    clear(resultHost).append(spinner('Finding substitutes…'));
    try {
      current = await api.post('/substitutions/generate', { date: dateInput.value, absent_faculty_ids: [...picked] });
      drawResult();
    } catch (e) { clear(resultHost).append(errorList([e.message])); }
  }

  function drawResult() {
    clear(resultHost);
    if (!current) return;
    if (current.message) { resultHost.append(h('div', { class: 'note' }, current.message)); return; }
    if (!current.rows.length) { resultHost.append(emptyState(`The selected faculty have no classes on ${current.day_name}.`)); return; }
    resultHost.append(h('div', { class: 'card' },
      h('h3', {}, `Substitution chart — ${current.day_name} ${current.date}`),
      current.unfilled ? h('p', {}, badge(`${current.unfilled} class(es) have no free substitute`, 'bad')) : h('p', {}, badge('Every class is covered', 'ok')),
      h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
        h('thead', {}, h('tr', {}, ['Period', 'Section', 'Course', 'Room', 'Absent', 'Substitute'].map((t) => h('th', {}, t)))),
        h('tbody', {}, current.rows.map((r) => h('tr', {},
          h('td', {}, h('strong', {}, periodLabel(r.start, r.length)), h('br'), h('small', { class: 'muted' }, timeRange(settings.period_times, r.start, r.length))),
          h('td', {}, r.section + (r.batch ? ` · B${r.batch}` : '')),
          h('td', {}, `${r.course_code} · ${r.course_name}`),
          h('td', {}, r.room || '—'),
          h('td', {}, r.absent),
          h('td', {}, r.substitute ? h('strong', {}, r.substitute) : badge('No free teacher', 'bad'))))))),
      h('div', { class: 'actions left' },
        h('button', { class: 'btn primary', onclick: save }, 'Save this plan'),
        h('button', { class: 'btn', onclick: () => window.print() }, '🖨 Print'))));
  }

  async function save() {
    await api.post('/substitutions', { date: current.date, absent_faculty_ids: [...picked], rows: current.rows });
    toast('Plan saved.', 'success');
    drawPlans(await api.get('/substitutions'));
  }

  function drawPlans(items) {
    clear(plansHost);
    if (!items.length) { plansHost.append(emptyState('No saved plans yet.')); return; }
    const names = new Map(faculty.map((f) => [f.id, f.name]));
    plansHost.append(h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
      h('thead', {}, h('tr', {}, ['Date', 'Absent', 'Classes covered', ''].map((t) => h('th', {}, t)))),
      h('tbody', {}, items.map((p) => h('tr', {},
        h('td', {}, h('strong', {}, p.date)),
        h('td', {}, p.absent_faculty_ids.map((id) => names.get(id) || `#${id}`).join(', ')),
        h('td', { class: 'num' }, `${p.rows.filter((r) => r.substitute_id).length}/${p.rows.length}`),
        h('td', { class: 'actions-col' },
          h('button', { class: 'btn small', onclick: () => {
            current = { date: p.date, day_name: DAY_NAMES[(new Date(`${p.date}T00:00:00Z`).getUTCDay() + 6) % 7], rows: p.rows,
              unfilled: p.rows.filter((r) => !r.substitute_id).length, message: '' };
            drawResult();
            window.scrollTo({ top: 0, behavior: 'smooth' });
          } }, 'View'),
          h('button', { class: 'btn small danger-outline', onclick: async () => {
            if (!(await confirmDialog(`Delete the plan for ${p.date}?`))) return;
            await api.del(`/substitutions/${p.id}`);
            drawPlans(await api.get('/substitutions'));
          } }, 'Delete'))))))));
  }

  container.append(pageHeader('Substitutions', 'Pick the day and the absent faculty; free colleagues are assigned class by class.'),
    h('div', { class: 'two-col' },
      h('div', { class: 'card' }, h('label', { class: 'field' }, h('span', { class: 'label' }, 'Date'), dateInput),
        h('span', { class: 'label' }, 'Absent faculty'), list,
        h('div', { class: 'actions left' }, h('button', { class: 'btn primary', onclick: generate }, 'Find substitutes'))),
      h('div', {}, resultHost, h('div', { class: 'note' }, 'A substitute must be free for the whole class (including 3-period labs), not unavailable, and under their daily limit. Same-department colleagues with the lightest day come first.'))),
    h('h3', { class: 'section-title' }, 'Saved plans'), plansHost);
  drawPlans(plans);
}
