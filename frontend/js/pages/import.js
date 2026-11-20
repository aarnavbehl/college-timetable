// Import from Excel: download a template, upload a workbook, review a preview, then import.
// The server re-validates the file on import, so this page never has to be trusted.
import { api, download } from '../api.js';
import { navigate } from '../router.js';
import { badge, clear, dataTable, emptyState, errorList, h, pageHeader, spinner, toast } from '../ui.js';
import {
  canSubmit, CONFIRM_WORD, describeDelete, firstTab, importButtonLabel, missingSheets, STATUS, submitMode,
  visibleRows,
} from '../import-utils.js';

const LINKS = { branches: '/branches', rooms: '/rooms', sections: '/sections', faculty: '/faculty',
  courses: '/courses', allocations: '/allocations' };

export async function importPage(container) {
  const state = { file: null, replace: false, partial: false, typed: '', preview: null, tab: null,
    onlyProblems: false, busy: false, error: null, result: null };

  const fileInput = h('input', { type: 'file', accept: '.xlsx', id: 'import-file', 'aria-label': 'Excel file' });
  const replaceBox = h('input', { type: 'checkbox', id: 'import-replace' });
  const dynamic = h('div');

  // ------------------------------------------------------------------ step 1 + 2 (built once)
  const saveFile = (path, name) => () => download(path, name).catch((e) => toast(e.message, 'error'));
  const step1 = h('div', { class: 'card' },
    step(1, 'Get the template'),
    h('p', { class: 'muted' }, 'One Excel workbook with a sheet for each of: Branches, Rooms, Sections, Faculty, Courses and Allocations. '
      + 'Fill in what you need — every sheet is optional, and rows may refer to records you already created in the app.'),
    h('div', { class: 'actions left' },
      h('button', { class: 'btn primary', onclick: saveFile('/import/template', 'college-timetable-template.xlsx') }, '⬇ Blank template'),
      h('button', { class: 'btn', onclick: saveFile('/import/template?example=true', 'college-timetable-example.xlsx') }, '⬇ Example (demo college)')));

  const step2 = h('div', { class: 'card' },
    step(2, 'Choose your file'),
    h('div', { class: 'file-row' }, fileInput),
    h('small', { class: 'help' }, 'Excel .xlsx only, up to 5 MB. Choosing a file shows a preview — nothing is saved yet.'),
    h('label', { class: 'check replace-opt' }, replaceBox,
      h('span', {}, ' ', h('strong', {}, 'Replace all existing data'), ' with this file instead of adding to it ',
        h('small', { class: 'muted' }, '(deletes your current setup and timetable after you confirm)'))));

  fileInput.addEventListener('change', () => {
    state.file = fileInput.files[0] || null;
    state.result = null;
    state.typed = '';
    runPreview();
  });
  replaceBox.addEventListener('change', () => {
    state.replace = replaceBox.checked;
    state.typed = '';
    if (state.file) runPreview(); else draw();
  });

  // ------------------------------------------------------------------ preview
  async function runPreview() {
    state.preview = null;
    state.error = null;
    if (!state.file) { draw(); return; }
    state.busy = true;
    draw();
    try {
      const form = new FormData();
      form.append('file', state.file);
      form.append('replace_existing', String(state.replace));
      state.preview = await api.upload('/import/preview', form);
      state.tab = firstTab(state.preview);
    } catch (e) {
      state.error = e.message;
    }
    state.busy = false;
    draw();
  }

  function draw() {
    clear(dynamic);
    if (state.busy) { dynamic.append(spinner('Reading and checking your workbook…')); return; }
    if (state.result) { dynamic.append(resultCard()); return; }
    if (state.error) { dynamic.append(h('div', { class: 'card' }, errorList([state.error]))); return; }
    if (!state.preview) return;
    dynamic.append(reviewCard(), submitCard());
  }

  // ------------------------------------------------------------------ step 3: review
  function reviewCard() {
    const p = state.preview;
    const t = p.totals;
    const missing = missingSheets(p);
    const host = h('div');

    const tabs = h('div', { class: 'seg sheet-tabs', role: 'tablist' }, p.sheets.filter((s) => s.present).map((s) => {
      const bad = s.counts.rejected > 0 || s.errors.length > 0;
      return h('button', { type: 'button', class: s.key === state.tab ? 'on' : '', role: 'tab', 'data-tab': s.key,
        onclick: () => { state.tab = s.key; drawSheet(); tabs.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.tab === s.key)); } },
      s.name, ' ', h('span', { class: `tab-count${bad ? ' bad' : ''}` }, String(s.counts.total)));
    }));

    function drawSheet() {
      clear(host);
      const cur = p.sheets.find((s) => s.key === state.tab);
      if (!cur) return;
      if (cur.errors.length) host.append(errorList(cur.errors, 'error'));
      if (cur.warnings.length) host.append(errorList(cur.warnings, 'warn'));
      const rows = visibleRows(cur, state.onlyProblems);
      if (!cur.rows.length) { host.append(emptyState(cur.errors.length ? 'This sheet cannot be read until its columns are fixed.' : 'This sheet has no data rows.')); return; }
      if (!rows.length) { host.append(emptyState('No skipped or rejected rows in this sheet.')); return; }
      const columns = [
        { label: 'Row', value: (r) => r.row, class: 'num' },
        { label: 'Status', value: (r) => badge(STATUS[r.status].label, STATUS[r.status].kind) },
        ...cur.columns.map((c, i) => ({ label: c.header + (c.required ? ' *' : ''), value: (r) => r.cells[i] || '' })),
        { label: 'Details', value: (r) => (r.errors.length ? h('span', { class: 'row-msg bad' }, r.errors.join('; '))
          : r.note ? h('span', { class: 'row-msg warn' }, r.note) : '') },
      ];
      const table = dataTable(columns, rows);
      table.classList.add('preview-table');
      table.querySelectorAll('tbody tr').forEach((tr, i) => tr.classList.add(`row-${rows[i].status}`));
      host.append(table);
      if (cur.truncated) host.append(h('p', { class: 'muted' }, `Showing the first ${cur.rows.length} rows of ${cur.counts.total}. Counts above include every row.`));
    }
    drawSheet();

    return h('div', { class: 'card' },
      step(3, 'Review'),
      h('div', { class: 'cards summary-cards' },
        stat('Rows in file', t.rows, ''), stat('New', t.new, 'new'), stat('Skipped (already exist)', t.skipped, 'skip'),
        stat('Rejected', t.rejected, t.rejected ? 'bad' : '')),
      p.ignored_sheets.length ? h('div', { class: 'note' }, `Ignored sheets (not part of the import): ${p.ignored_sheets.join(', ')}.`) : null,
      missing.length && missing.length < p.sheets.length
        ? h('p', { class: 'muted' }, `Not in the file (nothing will change for these): ${missing.join(', ')}.`) : null,
      p.replace_existing ? h('div', { class: 'banner bad' }, 'Replace mode: the numbers above are calculated as if your current data were already deleted.') : null,
      h('div', { class: 'toolbar' }, tabs,
        h('label', { class: 'check' },
          h('input', { type: 'checkbox', onchange: (e) => { state.onlyProblems = e.target.checked; drawSheet(); } }), ' Show only skipped / rejected rows')),
      host);
  }

  // ------------------------------------------------------------------ step 4: import
  function submitCard() {
    const p = state.preview;
    const box = h('div', { class: 'card' });
    const reasonEl = h('p', { class: 'muted' });
    const errHost = h('div');
    const button = h('button', { class: `btn ${state.replace ? 'danger' : 'primary'}`, type: 'button', onclick: submit });
    const typedInput = h('input', { type: 'text', placeholder: CONFIRM_WORD, 'aria-label': `Type ${CONFIRM_WORD} to confirm`,
      autocomplete: 'off', value: state.typed, oninput: (e) => { state.typed = e.target.value; refresh(); } });

    box.append(step(4, state.replace ? 'Replace and import' : 'Import'));
    if (state.replace) {
      box.append(h('div', { class: 'danger-zone' },
        h('p', {}, h('strong', {}, 'This permanently deletes '), describeDelete(p.will_delete), ' from your college, then imports this file. '
          + 'Both happen together: if anything fails, nothing is deleted.'),
        h('label', { class: 'field' }, h('span', { class: 'label' }, `Type ${CONFIRM_WORD} to confirm`), typedInput)));
    } else {
      box.append(h('p', { class: 'muted' }, 'Existing records are never changed or deleted. Rows that already exist are skipped.'));
      if (p.has_errors) {
        box.append(h('label', { class: 'check' },
          h('input', { type: 'checkbox', checked: state.partial, onchange: (e) => { state.partial = e.target.checked; refresh(); } }),
          h('span', {}, ' ', h('strong', {}, 'Import only the valid rows'), ' — rejected rows are left out (rows that depend on a rejected row are rejected too).')));
      }
    }
    box.append(errHost, reasonEl, h('div', { class: 'actions left' }, button));

    function refresh() {
      const verdict = canSubmit({ preview: p, partial: state.partial, replace: state.replace, typed: state.typed });
      button.disabled = !verdict.ok || state.busy;
      button.textContent = importButtonLabel(p, { partial: state.partial, replace: state.replace });
      reasonEl.textContent = verdict.ok ? '' : verdict.reason;
    }

    async function submit() {
      state.busy = true;
      clear(errHost);
      button.disabled = true;
      button.textContent = 'Importing…';
      try {
        const form = new FormData();
        form.append('file', state.file);
        form.append('mode', submitMode({ partial: state.partial, replace: state.replace }));
        form.append('replace_existing', String(state.replace));
        form.append('confirm_replace', state.typed.trim());
        state.result = await api.upload('/import/commit', form);
        state.busy = false;
        toast(state.result.message, 'success');
        draw();
      } catch (e) {
        state.busy = false;
        const d = e.detail && typeof e.detail === 'object' && !Array.isArray(e.detail) ? e.detail : null;
        errHost.append(errorList([d ? d.message : e.message, ...(d && d.errors ? d.errors : [])]));
        refresh();
      }
    }
    refresh();
    return box;
  }

  // ------------------------------------------------------------------ result
  function resultCard() {
    const r = state.result;
    const check = r.scheduling_check;
    return h('div', { class: 'card' },
      step('✓', 'Import complete'),
      h('p', { class: 'result-line' }, h('strong', {}, r.message), r.replaced ? ' Your previous data was replaced.' : ''),
      h('div', { class: 'cards summary-cards' }, stat('Imported', r.imported, 'new'), stat('Skipped', r.skipped, 'skip'),
        stat('Rejected', r.rejected, r.rejected ? 'bad' : '')),
      dataTable([
        { label: 'Sheet', value: (s) => (s.present ? h('a', { href: `#${LINKS[s.key]}` }, s.name) : h('span', { class: 'muted' }, `${s.name} (not in file)`)) },
        { label: 'Imported', value: (s) => s.imported, class: 'num' },
        { label: 'Skipped', value: (s) => s.skipped, class: 'num' },
        { label: 'Rejected', value: (s) => s.rejected, class: 'num' },
      ], r.sheets),
      detailList('Skipped rows', r, 'skipped_rows', (x) => `Row ${x.row}: ${x.reason}`),
      detailList('Rejected rows', r, 'rejected_rows', (x) => `Row ${x.row}: ${x.errors.join('; ')}`),
      h('h4', {}, 'Can this data be scheduled?'),
      check.ok
        ? h('p', {}, badge('Ready to generate', 'ok'), ' ', check.warnings.length ? '' : 'No scheduling problems found.')
        : h('div', {}, h('p', {}, badge('Needs attention', 'bad')), errorList(check.errors)),
      check.warnings.length ? errorList(check.warnings, 'warn') : null,
      h('div', { class: 'actions left' },
        h('button', { class: 'btn primary', onclick: () => navigate('/generate') }, 'Go to Generate'),
        h('button', { class: 'btn', onclick: () => navigate('/') }, 'Dashboard'),
        h('button', { class: 'btn', onclick: reset }, 'Import another file')));
  }

  function detailList(title, r, key, line) {
    const rows = r.sheets.flatMap((s) => s[key].map((x) => `${s.name} · ${line(x)}`));
    if (!rows.length) return null;
    return h('details', { class: 'details-list' }, h('summary', {}, `${title} (${rows.length})`),
      h('ul', {}, rows.map((x) => h('li', {}, x))));
  }

  function reset() {
    Object.assign(state, { file: null, preview: null, result: null, typed: '', error: null, partial: false });
    fileInput.value = '';
    draw();
  }

  container.append(
    pageHeader('Import from Excel', 'Load branches, rooms, sections, faculty, courses and allocations from a spreadsheet.'),
    step1, step2, dynamic);
}

function step(n, title) {
  return h('h3', { class: 'step-title' }, h('span', { class: 'step-num' }, String(n)), ` ${title}`);
}

function stat(label, value, kind) {
  return h('div', { class: `stat ${kind}` }, h('b', {}, value), h('span', {}, label));
}

