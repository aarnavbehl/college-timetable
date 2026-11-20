// One generic "list + add/edit/delete" page, configured per resource.
import { api } from './api.js';
import { clear, confirmDialog, dataTable, emptyState, formModal, h, pageHeader, toast } from './ui.js';

/**
 * cfg: {
 *   title, subtitle, path, noun, columns(ctx) -> [{label, value(row), class}],
 *   fields(ctx, row|null) -> form fields, defaults(ctx) -> values for "Add",
 *   context() -> async extra data (lookups), filters(ctx) -> [{name,label,options,match(row,val)}],
 *   deleteMessage(row), help
 * }
 */
export function crudPage(cfg) {
  return async function render(container) {
    const state = { rows: [], ctx: {}, search: '', filters: {} };
    const body = h('div');
    const addBtn = h('button', { class: 'btn primary', onclick: () => openForm(null) }, `+ Add ${cfg.noun}`);
    container.append(pageHeader(cfg.title, cfg.subtitle, addBtn));
    if (cfg.help) container.append(h('div', { class: 'note' }, cfg.help));
    container.append(body);

    async function load() {
      const [rows, ctx] = await Promise.all([api.get(`/${cfg.path}`), cfg.context ? cfg.context() : {}]);
      state.rows = rows;
      state.ctx = ctx;
      draw();
    }

    function visibleRows() {
      const q = state.search.toLowerCase();
      const filters = cfg.filters ? cfg.filters(state.ctx) : [];
      return state.rows.filter((row) => {
        if (q && !JSON.stringify(Object.values(row)).toLowerCase().includes(q)) return false;
        return filters.every((f) => !state.filters[f.name] || f.match(row, state.filters[f.name]));
      });
    }

    function draw() {
      clear(body);
      const filters = cfg.filters ? cfg.filters(state.ctx) : [];
      body.append(h('div', { class: 'toolbar' },
        h('input', { class: 'search', type: 'search', placeholder: 'Search…', value: state.search,
          oninput: (e) => { state.search = e.target.value; drawTable(); } }),
        filters.map((f) => h('select', { 'aria-label': f.label, value: state.filters[f.name] || '',
          onchange: (e) => { state.filters[f.name] = e.target.value; drawTable(); } },
        h('option', { value: '' }, `All ${f.label}`), f.options.map((o) => h('option', { value: String(o.value) }, o.label)))),
        h('span', { class: 'muted count' }, `${state.rows.length} total`)));
      body.append(h('div', { id: 'crud-table' }));
      drawTable();
    }

    function drawTable() {
      const holder = body.querySelector('#crud-table');
      if (!holder) return;
      clear(holder);
      const rows = visibleRows();
      if (!state.rows.length) {
        holder.append(emptyState(`No ${cfg.title.toLowerCase()} yet.`, h('button', { class: 'btn primary', onclick: () => openForm(null) }, `Add the first ${cfg.noun}`)));
        return;
      }
      if (!rows.length) { holder.append(emptyState('Nothing matches your search or filter.')); return; }
      holder.append(dataTable(cfg.columns(state.ctx), rows, {
        rowActions: (row) => [
          h('button', { class: 'btn small', onclick: () => openForm(row) }, 'Edit'),
          h('button', { class: 'btn small danger-outline', onclick: () => remove(row) }, 'Delete'),
        ],
      }));
    }

    function openForm(row) {
      formModal({
        title: row ? `Edit ${cfg.noun}` : `Add ${cfg.noun}`,
        fields: cfg.fields(state.ctx, row),
        values: row || (cfg.defaults ? cfg.defaults(state.ctx) : {}),
        submitLabel: row ? 'Save changes' : `Add ${cfg.noun}`,
        wide: cfg.wideForm,
        onSubmit: async (values) => {
          if (row) await api.put(`/${cfg.path}/${row.id}`, values); else await api.post(`/${cfg.path}`, values);
          toast(`${cfg.noun[0].toUpperCase()}${cfg.noun.slice(1)} ${row ? 'updated' : 'added'}`, 'success');
          await load();
        },
      });
    }

    async function remove(row) {
      const ok = await confirmDialog(cfg.deleteMessage ? cfg.deleteMessage(row) : `Delete this ${cfg.noun}?`);
      if (!ok) return;
      try {
        await api.del(`/${cfg.path}/${row.id}`);
        toast(`${cfg.noun[0].toUpperCase()}${cfg.noun.slice(1)} deleted`, 'success');
        await load();
      } catch (err) { toast(err.message, 'error'); }
    }

    await load();
  };
}

