// App shell: sign-in gate, sidebar navigation and the hash router.
import { api, session, setUnauthorizedHandler } from './api.js';
import { currentPath, navigate, setRerender } from './router.js';
import { loadSettings, store } from './store.js';
import { clear, errorList, h, spinner } from './ui.js';
import { renderAuth } from './pages/auth.js';
import { dashboardPage } from './pages/dashboard.js';
import { generatePage } from './pages/generate.js';
import { importPage } from './pages/import.js';
import { analyticsPage } from './pages/analytics.js';
import { historyPage } from './pages/history.js';
import { simulationsPage } from './pages/simulations.js';
import { allocationsPage, branchesPage, coursesPage, facultyPage, roomsPage, sectionsPage } from './pages/resources.js';
import { settingsPage } from './pages/settings.js';
import { substitutionsPage } from './pages/substitutions.js';
import { timetablePage } from './pages/timetable.js';

const NAV = [
  ['Overview', [['/', 'Dashboard', '🏠']]],
  ['Set up your college', [['/import', 'Import from Excel', '📥'], ['/settings', 'Week & periods', '⚙️'], ['/branches', 'Branches', '🏛️'], ['/rooms', 'Rooms & labs', '🚪'],
    ['/sections', 'Sections', '👥'], ['/faculty', 'Faculty', '🎓'], ['/courses', 'Courses', '📚'], ['/allocations', 'Allocations', '🔗']]],
  ['Timetable', [['/generate', 'Generate', '🧬'], ['/timetable', 'View & edit', '📅'], ['/analytics', 'Analytics', '📊'], ['/simulations', 'What-if simulations', '🧪'], ['/history', 'History & undo', '🕘'], ['/substitutions', 'Substitutions', '🔁']]],
];
const ROUTES = {
  '/': dashboardPage, '/settings': settingsPage, '/branches': branchesPage, '/rooms': roomsPage, '/sections': sectionsPage,
  '/faculty': facultyPage, '/courses': coursesPage, '/allocations': allocationsPage, '/import': importPage,
  '/generate': generatePage,
  '/timetable': timetablePage, '/analytics': analyticsPage,
  '/simulations': simulationsPage, '/history': historyPage, '/substitutions': substitutionsPage,
};

const app = document.getElementById('app');
let main = null;
let renderId = 0;

function showAuth() {
  clear(app);
  renderAuth(app, () => { navigate('/'); start(); });
}

function shell() {
  clear(app);
  main = h('main', { class: 'content' });
  const nav = h('nav', { class: 'sidebar', id: 'sidebar' },
    h('div', { class: 'brand' }, h('span', { class: 'logo' }, '▦'), ' College Timetable'),
    NAV.map(([title, items]) => h('div', { class: 'nav-group' }, h('h4', {}, title),
      items.map(([path, label, icon]) => h('a', { href: `#${path}`, 'data-path': path, onclick: () => nav.classList.remove('open') }, h('span', {}, icon), ` ${label}`)))),
    h('div', { class: 'nav-foot' }, h('div', { class: 'muted' }, store.user.college_name), h('small', { class: 'muted' }, store.user.email),
      h('button', { class: 'btn small', onclick: () => { session.token = null; store.user = null; showAuth(); } }, 'Sign out')));
  app.append(h('button', { class: 'menu-btn', 'aria-label': 'Menu', onclick: () => nav.classList.toggle('open') }, '☰'), nav, main);
}

async function renderPage() {
  const id = ++renderId;
  const path = currentPath();
  for (const a of document.querySelectorAll('.sidebar a')) a.classList.toggle('active', a.dataset.path === path);
  const page = ROUTES[path];
  if (!page) { clear(main).append(h('p', {}, 'Page not found. '), h('a', { href: '#/' }, 'Go to the dashboard')); return; }
  clear(main).append(spinner('Loading…'));
  const host = h('div', { class: 'page' });
  try {
    await page(host);
    if (id === renderId) { clear(main).append(host); window.scrollTo(0, 0); }
  } catch (e) {
    if (id === renderId) clear(main).append(h('h2', {}, 'Something went wrong'), errorList([e.message]));
  }
}

async function start() {
  if (!session.token) { showAuth(); return; }
  try {
    store.user = await api.get('/auth/me');
    await loadSettings();
  } catch {
    session.token = null;
    showAuth();
    return;
  }
  shell();
  renderPage();
}

setRerender(() => { if (main) renderPage(); });
setUnauthorizedHandler(() => { store.user = null; showAuth(); });
window.addEventListener('hashchange', () => { if (main && store.user) renderPage(); });
start();
