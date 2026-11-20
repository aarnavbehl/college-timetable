import test from 'node:test';
import assert from 'node:assert/strict';
import {
  canSubmit, CONFIRM_WORD, describeDelete, firstTab, importButtonLabel, missingSheets, submitMode, totalsLine,
  visibleRows,
} from '../js/import-utils.js';

const sheet = (key, o = {}) => ({ key, name: key[0].toUpperCase() + key.slice(1), present: true, errors: [], rows: [],
  counts: { new: 0, skipped: 0, rejected: 0, total: 0 }, ...o });
const preview = (totals, o = {}) => ({
  totals: { rows: 10, new: 10, skipped: 0, rejected: 0, ...totals }, has_errors: false, can_import: true,
  can_import_strict: true, sheets: [sheet('branches')], ...o,
});

test('cannot submit without a preview or with an empty workbook', () => {
  assert.equal(canSubmit({ preview: null }).ok, false);
  assert.match(canSubmit({ preview: null }).reason, /Choose a file/);
  assert.match(canSubmit({ preview: preview({ rows: 0, new: 0 }) }).reason, /no data rows/);
});

test('a clean file can be imported', () => {
  assert.deepEqual(canSubmit({ preview: preview({}) }), { ok: true, reason: '' });
});

test('everything already existing means there is nothing to import', () => {
  const v = canSubmit({ preview: preview({ new: 0, skipped: 10 }) });
  assert.equal(v.ok, false);
  assert.match(v.reason, /Nothing new/);
});

test('rejected rows block a strict import but not a partial one', () => {
  const p = preview({ new: 7, rejected: 3 }, { has_errors: true, can_import_strict: false });
  assert.equal(canSubmit({ preview: p }).ok, false);
  assert.match(canSubmit({ preview: p }).reason, /Import only the valid rows/);
  assert.equal(canSubmit({ preview: p, partial: true }).ok, true);
});

test('partial import still needs at least one valid row', () => {
  const p = preview({ new: 0, rejected: 3 }, { has_errors: true, can_import_strict: false });
  assert.equal(canSubmit({ preview: p, partial: true }).ok, false);
});

test('replace needs a clean file AND the typed confirmation word', () => {
  const clean = preview({});
  assert.match(canSubmit({ preview: clean, replace: true }).reason, new RegExp(CONFIRM_WORD));
  assert.equal(canSubmit({ preview: clean, replace: true, typed: 'replace' }).ok, false);   // case matters
  assert.equal(canSubmit({ preview: clean, replace: true, typed: ' REPLACE ' }).ok, true);
  const dirty = preview({ new: 7, rejected: 3 }, { has_errors: true, can_import_strict: false });
  assert.equal(canSubmit({ preview: dirty, replace: true, typed: 'REPLACE', partial: true }).ok, false);
  assert.match(canSubmit({ preview: dirty, replace: true, typed: 'REPLACE' }).reason, /no problems/);
});

test('replace is always imported strictly', () => {
  assert.equal(submitMode({ partial: true, replace: true }), 'strict');
  assert.equal(submitMode({ partial: true }), 'partial');
  assert.equal(submitMode({}), 'strict');
});

test('labels and summaries', () => {
  assert.equal(totalsLine({ new: 5, skipped: 2, rejected: 1 }), '5 new · 2 skipped · 1 rejected');
  assert.equal(importButtonLabel(preview({ new: 1 })), 'Import 1 record');
  assert.equal(importButtonLabel(preview({ new: 7 }, { has_errors: true }), { partial: true }), 'Import 7 records (valid rows only)');
  assert.match(importButtonLabel(preview({ new: 4 }), { replace: true }), /Replace everything and import 4 records/);
});

test('describeDelete lists only what exists, with correct plurals', () => {
  assert.equal(describeDelete({ sections: 5, faculty: 1, branches: 0 }), '5 sections and 1 faculty member');
  assert.equal(describeDelete({ branches: 3, rooms: 10, timetable_classes: 101 }),
    '3 branches, 10 rooms and 101 scheduled classes of the timetable');
  assert.equal(describeDelete({ rooms: 1 }), '1 room');
  assert.match(describeDelete({}), /nothing/);
});

test('tab helpers pick the sheet that needs attention', () => {
  const p = preview({}, { sheets: [sheet('branches'), sheet('rooms', { counts: { rejected: 2, new: 0, skipped: 0, total: 2 } }),
    sheet('faculty', { present: false })] });
  assert.equal(firstTab(p), 'rooms');
  assert.deepEqual(missingSheets(p), ['Faculty']);
  assert.equal(firstTab(preview({}, { sheets: [sheet('sections')] })), 'sections');
});

test('visibleRows can show only skipped/rejected rows', () => {
  const s = sheet('rooms', { rows: [{ status: 'new' }, { status: 'skip' }, { status: 'error' }] });
  assert.equal(visibleRows(s, false).length, 3);
  assert.deepEqual(visibleRows(s, true).map((r) => r.status), ['skip', 'error']);
});
