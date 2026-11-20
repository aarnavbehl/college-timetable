import test from 'node:test';
import assert from 'node:assert/strict';
import {
  colorFor, entityOptions, filterSessions, formatError, groupDay, periodLabel, timeRange, weekdayOf,
} from '../js/utils.js';

const S = (o) => ({ day: 0, start: 1, length: 1, task_key: 'A1#0', ...o });

test('colorFor is stable and differs between keys', () => {
  assert.deepEqual(colorFor('CS201'), colorFor('CS201'));
  assert.notEqual(colorFor('CS201').bg, colorFor('EC202').bg);
});

test('entityOptions lists distinct entities sorted naturally', () => {
  const sessions = [
    S({ section_id: 2, section: 'CSE Y10-A' }), S({ section_id: 1, section: 'CSE Y2-A' }),
    S({ section_id: 2, section: 'CSE Y10-A' }), S({ section_id: 3, section: 'ECE Y1-A' }),
  ];
  assert.deepEqual(entityOptions(sessions, 'section').map((o) => o.label), ['CSE Y2-A', 'CSE Y10-A', 'ECE Y1-A']);
  assert.deepEqual(entityOptions([S({ room_id: null, room: null })], 'room'), []);
});

test('filterSessions picks one entity', () => {
  const sessions = [S({ faculty_id: 1 }), S({ faculty_id: 2 }), S({ faculty_id: 1 })];
  assert.equal(filterSessions(sessions, 'faculty', 1).length, 2);
});

test('groupDay merges the batches of one lab rotation into a single block', () => {
  const lab = [S({ start: 5, length: 3, task_key: 'G1#0', batch: 1 }), S({ start: 5, length: 3, task_key: 'G1#0', batch: 2 })];
  const groups = groupDay([S({ start: 1 }), ...lab, S({ start: 2, task_key: 'A2#0' })]);
  assert.deepEqual(groups.map((g) => [g.start, g.length, g.items.length, g.conflict]),
    [[1, 1, 1, false], [2, 1, 1, false], [5, 3, 2, false]]);
});

test('groupDay flags overlapping classes of different tasks as a conflict', () => {
  const groups = groupDay([S({ start: 2, length: 3, task_key: 'A1#0' }), S({ start: 3, task_key: 'A9#0' })]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].conflict, true);
  assert.equal(groups[0].length, 3);
});

test('time and period labels', () => {
  const times = [['09:00', '09:50'], ['09:50', '10:40'], ['10:40', '11:30']];
  assert.equal(timeRange(times, 1, 3), '09:00–11:30');
  assert.equal(timeRange(times, 2, 1), '09:50–10:40');
  assert.equal(periodLabel(5, 3), 'P5–7');
  assert.equal(periodLabel(2, 1), 'P2');
});

test('formatError understands every error shape the API returns', () => {
  assert.equal(formatError('Nope'), 'Nope');
  assert.equal(formatError([{ loc: ['body', 'capacity'], msg: 'must be >= 1' }]), 'capacity: must be >= 1');
  assert.equal(formatError({ message: 'Fix these.', errors: ['A bad.', 'B bad.'] }), 'Fix these. A bad. B bad.');
  assert.equal(formatError({ message: 'Clash', reasons: ['faculty busy'] }), 'Clash (faculty busy)');
});

test('weekdayOf is Monday-based and independent of timezone', () => {
  assert.equal(weekdayOf('2026-11-02'), 0);   // Monday
  assert.equal(weekdayOf('2026-11-07'), 5);   // Saturday
  assert.equal(weekdayOf('2026-11-08'), 6);   // Sunday
});
