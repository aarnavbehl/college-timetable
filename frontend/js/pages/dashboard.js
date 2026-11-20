import { api } from '../api.js';
import { badge, confirmDialog, h, pageHeader, toast, errorList } from '../ui.js';
import { loadSettings, store } from '../store.js';
import { navigate, rerender } from '../router.js';

const LINKS = { branches: '/branches', rooms: '/rooms', sections: '/sections', faculty: '/faculty',
  courses: '/courses', allocations: '/allocations' };

export async function dashboardPage(container) {
  const [stats, check] = await Promise.all([api.get('/stats'), api.get('/validate')]);
  const empty = Object.values(stats.counts).every((n) => n === 0);

  async function loadDemo() {
    const replace = !empty;
    if (replace && !(await confirmDialog('This replaces ALL your current data with the demo college. Continue?', { confirmLabel: 'Replace with demo', danger: true }))) return;
    try {
      await api.post('/demo', { reset: replace });
      toast('Demo college loaded.', 'success');
      await loadSettings();
      rerender();
    } catch (e) { toast(e.message, 'error'); }
  }

  const done = stats.checklist.filter((s) => s.done).length;
  const tt = stats.timetable;
  const status = !tt ? h('p', { class: 'muted' }, 'No timetable yet.')
    : h('div', {},
      h('p', {}, tt.hard === 0 ? badge('No clashes', 'ok') : badge(`${tt.hard} clash(es)`, 'bad'), ' ',
        tt.stale ? badge('Data changed — regenerate', 'warn') : badge('Up to date', 'ok')),
      h('p', { class: 'muted' }, `${tt.sessions} scheduled classes · generated ${new Date(tt.generated_at).toLocaleString()} · preference score ${tt.soft} (lower is better)`));

  container.append(
    pageHeader(`Welcome, ${store.user.college_name}`, 'Set up your college, then generate the timetable.',
      h('button', { class: 'btn', onclick: () => navigate('/import') }, '📥 Import from Excel'),
      h('button', { class: 'btn', onclick: loadDemo }, empty ? 'Load demo college' : 'Reload demo data')),
    h('div', { class: 'cards' }, Object.entries(stats.counts).map(([k, n]) =>
      h('a', { class: 'stat', href: `#${LINKS[k]}` }, h('b', {}, n), h('span', {}, k)))),
    h('div', { class: 'two-col' },
      h('div', { class: 'card' }, h('h3', {}, `Setup checklist (${done}/${stats.checklist.length})`),
        h('ol', { class: 'checklist' }, stats.checklist.map((s) =>
          h('li', { class: s.done ? 'done' : '' }, h('a', { href: `#${LINKS[s.key]}` }, s.label))))),
      h('div', { class: 'card' }, h('h3', {}, 'Timetable'), status,
        check.ok ? h('p', {}, badge('Ready to generate', 'ok')) : errorList(check.errors.slice(0, 4), 'error'),
        check.warnings.length ? errorList(check.warnings, 'warn') : null,
        h('div', { class: 'actions left' },
          h('button', { class: 'btn primary', onclick: () => navigate('/generate') }, 'Generate'),
          tt ? h('button', { class: 'btn', onclick: () => navigate('/timetable') }, 'View timetable') : null))));
}
