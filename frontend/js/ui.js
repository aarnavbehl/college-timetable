// DOM helpers. Everything is built with createElement/textContent, never innerHTML,
// so user-entered names can't inject markup.

export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  let value;
  for (const [key, val] of Object.entries(props || {})) {
    if (val == null || val === false) continue;
    if (key === 'class') el.className = val;
    else if (key === 'style' && typeof val === 'object') Object.assign(el.style, val);
    else if (key === 'value') value = val;
    else if (key.startsWith('on') && typeof val === 'function') el.addEventListener(key.slice(2).toLowerCase(), val);
    else if (key === 'disabled' || key === 'checked' || key === 'selected') el[key] = val;
    else el.setAttribute(key, val === true ? '' : val);
  }
  append(el, children);
  if (value !== undefined) el.value = value;     // after options exist, so <select> works
  return el;
}

export function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function clear(el) {
  el.replaceChildren();
  return el;
}

// ------------------------------------------------------------------ toasts / modals
export function toast(message, kind = 'info') {
  const box = document.getElementById('toasts');
  const el = h('div', { class: `toast ${kind}` }, message);
  box.append(el);
  setTimeout(() => el.remove(), kind === 'error' ? 7000 : 3500);
}

export function modal(title, content, { wide = false } = {}) {
  const overlay = h('div', { class: 'overlay' });
  const close = () => { overlay.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  const dialog = h('div', { class: `dialog${wide ? ' wide' : ''}`, role: 'dialog', 'aria-label': title },
    h('div', { class: 'dialog-head' }, h('h3', {}, title), h('button', { class: 'icon', 'aria-label': 'Close', onclick: close }, '×')),
    h('div', { class: 'dialog-body' }, content));
  overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) close(); });
  overlay.append(dialog);
  document.body.append(overlay);
  document.addEventListener('keydown', onKey);
  return { close, el: dialog };
}

export function confirmDialog(message, { confirmLabel = 'Delete', danger = true } = {}) {
  return new Promise((resolve) => {
    const done = (v) => { m.close(); resolve(v); };
    const m = modal('Please confirm', h('div', {},
      Array.isArray(message) ? message.map((line) => h('p', {}, line)) : h('p', {}, message),
      h('div', { class: 'actions' },
        h('button', { class: 'btn', onclick: () => done(false) }, 'Cancel'),
        h('button', { class: `btn ${danger ? 'danger' : 'primary'}`, onclick: () => done(true) }, confirmLabel))));
  });
}

// ------------------------------------------------------------------ forms
/**
 * fields: [{name, label, type: text|number|select|custom, required, optional, options,
 *           number, min, max, help, placeholder, list, render}]
 * Returns {el, values()}. `custom` fields supply render(value, allValues) -> {el, get}.
 */
export function buildForm(fields, initial = {}, { onSubmit, submitLabel = 'Save', onCancel } = {}) {
  const getters = {};
  const rows = fields.map((f) => {
    let control;
    const val = initial[f.name];
    if (f.type === 'custom') {
      const made = f.render(val, initial);
      control = made.el;
      getters[f.name] = made.get;
    } else if (f.type === 'select') {
      control = h('select', { name: f.name, required: f.required },
        f.optional ? h('option', { value: '' }, f.placeholder || '— none —') : null,
        f.options.map((o) => h('option', { value: String(o.value) }, o.label)));
      control.value = val == null ? (f.optional ? '' : String(f.options[0]?.value ?? '')) : String(val);
      getters[f.name] = () => (f.boolean ? control.value === 'true'
        : control.value === '' ? null : (f.number ? Number(control.value) : control.value));
    } else {
      const isNum = f.type === 'number';
      control = h('input', {
        name: f.name, type: isNum ? 'number' : 'text', required: f.required, min: f.min, max: f.max,
        step: isNum ? 1 : undefined, placeholder: f.placeholder, list: f.list, autocomplete: 'off',
        maxlength: f.maxlength, value: val == null ? '' : val,
      });
      getters[f.name] = () => {
        const v = control.value.trim();
        if (isNum) return v === '' ? null : Number(v);
        return v === '' && f.optional ? null : v;
      };
    }
    f.onChange && control.addEventListener('change', () => f.onChange(control, getters));
    return h('label', { class: 'field' }, h('span', { class: 'label' }, f.label, f.required ? h('b', { class: 'req' }, ' *') : null),
      control, f.help ? h('small', { class: 'help' }, f.help) : null);
  });
  const error = h('div', { class: 'form-error', hidden: true });
  const el = h('form', { class: 'form', novalidate: false }, rows, error,
    onSubmit ? h('div', { class: 'actions' },
      onCancel ? h('button', { type: 'button', class: 'btn', onclick: onCancel }, 'Cancel') : null,
      h('button', { type: 'submit', class: 'btn primary' }, submitLabel)) : null);
  const values = () => Object.fromEntries(Object.entries(getters).map(([k, g]) => [k, g()]));
  if (onSubmit) {
    el.addEventListener('submit', async (e) => {
      e.preventDefault();
      error.hidden = true;
      const btn = el.querySelector('button[type=submit]');
      btn.disabled = true;
      try { await onSubmit(values()); } catch (err) { error.textContent = err.message; error.hidden = false; } finally { btn.disabled = false; }
    });
  }
  return { el, values };
}

export function formModal({ title, fields, values = {}, submitLabel, onSubmit, wide }) {
  let m;
  const form = buildForm(fields, values, {
    submitLabel, onCancel: () => m.close(),
    onSubmit: async (v) => { await onSubmit(v); m.close(); },
  });
  m = modal(title, form.el, { wide });
  const first = form.el.querySelector('input,select');
  if (first) first.focus();
  return m;
}

// ------------------------------------------------------------------ tables & misc
export function dataTable(columns, rows, { rowActions } = {}) {
  return h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
    h('thead', {}, h('tr', {}, columns.map((c) => h('th', {}, c.label)), rowActions ? h('th', { class: 'actions-col' }) : null)),
    h('tbody', {}, rows.map((row) => h('tr', {},
      columns.map((c) => h('td', { class: c.class }, c.value(row))),
      rowActions ? h('td', { class: 'actions-col' }, rowActions(row)) : null)))));
}

export function badge(text, kind = '') { return h('span', { class: `badge ${kind}` }, text); }

export function pageHeader(title, subtitle, ...actions) {
  return h('div', { class: 'page-head' }, h('div', {}, h('h2', {}, title), subtitle ? h('p', { class: 'muted' }, subtitle) : null),
    h('div', { class: 'page-actions' }, actions));
}

export function spinner(text = 'Working…') { return h('div', { class: 'spinner-row' }, h('span', { class: 'spinner' }), text); }

export function emptyState(text, action) { return h('div', { class: 'empty' }, h('p', {}, text), action || null); }

export function errorList(errors, kind = 'error') {
  return h('ul', { class: `messages ${kind}` }, errors.map((e) => h('li', {}, e)));
}
