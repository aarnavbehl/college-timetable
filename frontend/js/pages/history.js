// Change history of the saved timetable, with safe (re-validated) undo.
import { api } from '../api.js';
import { kindBadge, formatWhen } from '../simulation-utils.js';
import { rerender } from '../router.js';
import { badge, confirmDialog, dataTable, emptyState, errorList, h, modal, pageHeader, toast } from '../ui.js';

function details(e) {
  const d = e.details || {};
  if (e.kind === 'manual_edit') {
    return h('div', {}, d.forced ? h('p', {}, badge('Forced: this created clashes', 'bad')) : null,
      dataTable([{ label: 'Class', value: (c) => c.class }, { label: 'From', value: (c) => c.from }, { label: 'To', value: (c) => c.to }], d.changes));
  }
  if (e.kind === 'generated') {
    return h('ul', { class: 'plain' }, h('li', {}, `${d.classes} scheduled classes, ${d.hard} hard violation(s), soft score ${d.soft}, ${d.seconds}s of search`),
      h('li', {}, d.before_classes ? `Replaced a timetable of ${d.before_classes} classes (it can be restored by undoing this).` : 'There was no timetable before.'));
  }
  if (e.kind === 'cleared') return h('p', {}, `The timetable of ${d.before_classes} classes was deleted. Undo brings it back exactly.`);
  if (e.kind === 'simulation') {
    return h('div', {}, h('p', {}, d.data_changes ? badge('Also changed your data', 'warn') : badge('Timetable only', 'ok'), ` ${d.classes} classes in the new timetable.`),
      d.changes && d.changes.length ? h('ul', { class: 'plain' }, d.changes.map((c) => h('li', {}, c))) : null);
  }
  if (e.kind === 'undo') return h('p', {}, `Reversed history entry #${d.undid}. Violations after: ${d.violations_after}.`);
  return null;
}

export async function historyPage(container) {
  const data = await api.get('/history?limit=100');
  container.append(pageHeader('History & undo', 'Every generation, manual edit and applied simulation, with who and when.'),
    h('div', { class: 'note' }, data.actor_note, ' Undo re-checks the restored timetable with the independent verifier and refuses if it would make things worse.'));
  if (!data.entries.length) {
    container.append(emptyState('No changes recorded yet. Generate a timetable or move a class and it will appear here.'));
    return;
  }

  async function undo(e) {
    const lines = ['Restore the timetable to how it was before this change?', e.summary];
    if (e.kind !== 'manual_edit') lines.push('The current timetable will be replaced.');
    if (!(await confirmDialog(lines, { confirmLabel: 'Undo this change', danger: true }))) return;
    try {
      const r = await api.post(`/history/${e.id}/undo`, { confirm: true });
      toast(`Undone. ${r.violations_after ? `${r.violations_after} violation(s) remain from earlier edits.` : 'The restored timetable is verified clean.'}`, 'success');
      rerender();
    } catch (err) {
      const d = err.detail && typeof err.detail === 'object' && !Array.isArray(err.detail) ? err.detail : null;
      modal('Could not undo', h('div', {}, errorList([d ? d.message : err.message, ...(d && d.errors ? d.errors : [])])));
    }
  }

  container.append(h('div', { class: 'history' }, data.entries.map((e) => {
    const k = kindBadge(e.kind);
    return h('div', { class: `card history-item${e.undone ? ' undone' : ''}` },
      h('div', { class: 'history-head' },
        h('div', {}, badge(k.label, k.tone), e.undone ? [' ', badge('Undone', 'warn')] : null, ' ', h('strong', {}, e.summary)),
        h('div', { class: 'history-meta muted' }, h('span', { title: 'Server time, shown in your local time zone' }, formatWhen(e.created_at)), ' · ', e.actor.email)),
      h('details', { class: 'details-list' }, h('summary', {}, 'Details'), details(e)),
      h('div', { class: 'actions left' },
        h('button', { class: 'btn small', disabled: !e.can_undo, title: e.can_undo ? 'Undo this change' : e.undo_reason, onclick: () => undo(e) }, '↶ Undo'),
        !e.can_undo && !e.undone && e.kind !== 'undo' ? h('small', { class: 'muted' }, e.undo_reason) : null));
  })));
}
