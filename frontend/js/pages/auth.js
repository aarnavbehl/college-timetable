import { api, session } from '../api.js';
import { buildForm, h } from '../ui.js';
import { store } from '../store.js';

/** Sign-in / register screen. Calls onDone() once signed in. */
export function renderAuth(container, onDone) {
  let mode = 'login';
  const holder = h('div');
  container.append(h('div', { class: 'auth-wrap' }, h('div', { class: 'auth-card' },
    h('div', { class: 'brand big' }, h('span', { class: 'logo' }, '▦'), ' College Timetable'),
    h('p', { class: 'muted' }, 'Generate clash-free timetables for your departments, labs and faculty.'),
    holder)));

  function draw() {
    holder.replaceChildren();
    const login = mode === 'login';
    const fields = [
      !login && { name: 'college_name', label: 'College name', type: 'text', required: true, placeholder: 'Demo Institute of Technology' },
      { name: 'email', label: 'Email', type: 'text', required: true, placeholder: 'admin@college.edu' },
      { name: 'password', label: 'Password', type: 'text', required: true, help: login ? null : 'At least 6 characters.' },
    ].filter(Boolean);
    const form = buildForm(fields, {}, {
      submitLabel: login ? 'Sign in' : 'Create college account',
      onSubmit: async (v) => {
        const res = await api.post(login ? '/auth/login' : '/auth/register', v);
        session.token = res.token;
        store.user = res.user;
        onDone();
      },
    });
    form.el.querySelector('[name=password]').type = 'password';
    holder.append(form.el, h('p', { class: 'switch' },
      login ? 'New college? ' : 'Already have an account? ',
      h('a', { href: '#', onclick: (e) => { e.preventDefault(); mode = login ? 'register' : 'login'; draw(); } },
        login ? 'Create an account' : 'Sign in')));
  }
  draw();
}
