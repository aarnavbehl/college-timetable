import test from 'node:test';
import assert from 'node:assert/strict';
import {
  applyMessage, canApply, deltaText, describeChange, diffAvailability, kindBadge, metricText, nextRef, parseUtc,
  placementSummary, slotText, statusInfo, VERDICT_CLASS,
} from '../js/simulation-utils.js';

test('diffAvailability computes exactly what the user toggled', () => {
  const current = [[4, 5], [4, 6], [4, 7]];
  assert.deepEqual(diffAvailability(current, current), { add: [], remove: [] });
  assert.deepEqual(diffAvailability(current, [[4, 5], [4, 6], [4, 7], [0, 1], [0, 2]]), { add: [[0, 1], [0, 2]], remove: [] });
  assert.deepEqual(diffAvailability(current, [[4, 5]]), { add: [], remove: [[4, 6], [4, 7]] });
  assert.deepEqual(diffAvailability([], [[2, 3], [0, 1]]), { add: [[0, 1], [2, 3]], remove: [] });     // sorted
  assert.deepEqual(diffAvailability([[1, 1]], [[2, 2]]), { add: [[2, 2]], remove: [[1, 1]] });
});

test('nextRef gives unused temporary references', () => {
  assert.equal(nextRef([], 's'), 'new:s1');
  assert.equal(nextRef([{ ref: 'new:s1' }, { ref: 'new:s2' }, { ref: 'new:c1' }], 's'), 'new:s3');
  assert.equal(nextRef([{ ref: 'new:s1' }, { type: 'x' }], 'c'), 'new:c1');
});

test('statusInfo labels each state and flags out-of-date results', () => {
  assert.equal(statusInfo('ready').label, 'Verified candidate');
  assert.equal(statusInfo('infeasible').kind, 'bad');
  assert.deepEqual(statusInfo('ready', true), { label: 'Out of date: run again', kind: 'warn' });
  assert.equal(statusInfo('applied', true).label, 'Applied');               // applied is never "out of date"
  assert.equal(statusInfo('draft', true).label, 'Not run yet');
});

test('canApply only allows a fresh, verified, unapplied candidate', () => {
  assert.equal(canApply(null).ok, false);
  assert.equal(canApply({ status: 'draft' }).ok, false);
  assert.equal(canApply({ status: 'invalid' }).ok, false);
  assert.equal(canApply({ status: 'infeasible' }).ok, false);
  assert.equal(canApply({ status: 'applied' }).ok, false);
  assert.match(canApply({ status: 'ready', outdated: true }).reason, /Run it again/);
  assert.deepEqual(canApply({ status: 'ready', outdated: false }), { ok: true, reason: '' });
});

test('applyMessage is honest about data changes and undo', () => {
  const pure = applyMessage({ result: { data_changes: false, changes: [] } });
  assert.ok(pure.some((l) => /can undo it/.test(l)));
  const data = applyMessage({ result: { data_changes: true, changes: ['Dr X: unavailable Mon P1', 'Room C1 is closed'] } });
  assert.ok(data.includes('• Dr X: unavailable Mon P1') && data.includes('• Room C1 is closed'));
  assert.ok(data.some((l) => /cannot be undone automatically/.test(l)));
});

test('metric and delta formatting', () => {
  assert.equal(metricText({ key: 'room_utilization' }, 22.456), '22.5%');
  assert.equal(metricText({ key: 'quality_score' }, 93.4), '93.4');
  assert.equal(metricText({ key: 'quality_score' }, null), '—');
  assert.equal(metricText({ key: 'hard_violations' }, 0), '0');
  assert.equal(deltaText({ key: 'soft_cost', delta: 25 }), '+25');
  assert.equal(deltaText({ key: 'soft_cost', delta: -3 }), '−3');
  assert.equal(deltaText({ key: 'soft_cost', delta: 0 }), 'no change');
  assert.equal(deltaText({ key: 'quality_score', delta: null }), '—');
  assert.equal(deltaText({ key: 'room_utilization', delta: 1.25 }), '+1.3 pts');
  assert.deepEqual([VERDICT_CLASS.better, VERDICT_CLASS.worse, VERDICT_CLASS.info], ['good', 'bad', '']);
});

test('placementSummary and slotText', () => {
  assert.equal(placementSummary({ moved: 4, room_changed: 1, added: 0, removed: 0, unchanged: 109 }), '4 moved · 1 room change · 0 added · 0 removed · 109 unchanged');
  assert.equal(slotText(0, 3), 'Mon P3');
  assert.equal(slotText(4, 7), 'Fri P7');
});

test('describeChange words every change type from lookups', () => {
  const ctx = { faculty: [{ id: 1, name: 'Dr A' }], rooms: [{ id: 2, name: 'C101' }], sections: [{ id: 3, label: 'CSE Y2-A' }], courses: [{ id: 4, code: 'CS201' }] };
  assert.equal(describeChange({ type: 'faculty_availability', faculty_id: 1, add_unavailable: [[0, 1], [0, 2]], remove_unavailable: [[4, 5]] }, ctx),
    'Dr A: unavailable Mon P1, Mon P2; available again Fri P5');
  assert.equal(describeChange({ type: 'faculty_limits', faculty_id: 1, max_per_day: 4, max_per_week: null }, ctx), 'Dr A: max 4/day');
  assert.equal(describeChange({ type: 'room_closed', room_id: 2 }, ctx), 'Room C101 is closed');
  assert.equal(describeChange({ type: 'add_allocation', section_id: 3, course_id: 4, faculty_id: 1, batch: 2 }, ctx), 'Dr A teaches CS201 to CSE Y2-A batch 2');
  assert.equal(describeChange({ type: 'add_allocation', section_id: 'new:s1', course_id: 'new:c1', faculty_id: 1 }, ctx), 'Dr A teaches new:c1 to new:s1');
  assert.equal(describeChange({ type: 'pin_class', session_id: 9, day: 1, start: 2, room_id: 2 }, ctx), 'Pin class #9 at Tue P2 in C101');
  assert.match(describeChange({ type: 'soft_weights', section_gap: 5 }, ctx), /section gap = 5/);
});

test('history helpers: kind badges and timestamps are read as UTC, never local', () => {
  assert.deepEqual(kindBadge('manual_edit'), { label: 'Manual edit', tone: '' });
  assert.equal(kindBadge('undo').tone, 'warn');
  assert.equal(kindBadge('mystery').label, 'mystery');
  assert.equal(parseUtc('2026-10-21T08:21:29Z').toISOString(), '2026-10-21T08:21:29.000Z');
  assert.equal(parseUtc('2026-10-21T08:21:29.144853').toISOString(), '2026-10-21T08:21:29.144Z');     // zone-less => UTC
  assert.equal(parseUtc('2026-10-21T08:21:29+05:30').toISOString(), '2026-10-21T02:51:29.000Z');
  assert.equal(parseUtc(null), null);
});
