import test from 'node:test';
import assert from 'node:assert/strict';
import { barWidth, deltaClass, deltaLabel, heatColor, matrixMax, num, pct, plural, scoreLabel } from '../js/analytics-utils.js';

test('heatColor: empty cells are transparent, intensity rises with value and is capped', () => {
  assert.equal(heatColor(0, 10), 'transparent');
  assert.equal(heatColor(5, 0), 'transparent');
  const light = heatColor(1, 10), dark = heatColor(10, 10), over = heatColor(99, 10);
  assert.notEqual(light, dark);
  assert.equal(dark, over);                                   // capped at max
  assert.match(dark, /^hsl\(215 75% 46%\)$/);
});

test('pct and num handle missing values', () => {
  assert.equal(pct(12.345), '12.3%');
  assert.equal(pct(50, 0), '50%');
  assert.equal(pct(null), '—');
  assert.equal(pct(undefined), '—');
  assert.equal(num(1234567), '1,234,567');
  assert.equal(num(null), '—');
  assert.equal(num(2.456, 1), '2.5');
});

test('scoreLabel bands and the "no score" case', () => {
  assert.equal(scoreLabel(null), 'Not available');
  assert.deepEqual([95, 80, 65, 10].map(scoreLabel), ['Excellent', 'Good', 'Fair', 'Needs work']);
  assert.equal(scoreLabel(90), 'Excellent');
  assert.equal(scoreLabel(75), 'Good');
});

test('barWidth is proportional, never negative, has a visible minimum and caps at 100', () => {
  assert.equal(barWidth(0, 10), 0);
  assert.equal(barWidth(-3, 10), 0);
  assert.equal(barWidth(5, 10), 50);
  assert.equal(barWidth(0.01, 10), 2);
  assert.equal(barWidth(50, 10), 100);
  assert.equal(barWidth(5, 0), 0);
});

test('deltaLabel treats LOWER cost as better', () => {
  assert.equal(deltaLabel(-3), '−3 (better)');
  assert.equal(deltaLabel(2), '+2 (worse)');
  assert.equal(deltaLabel(0), 'no change');
  assert.equal(deltaClass(-1), 'good');
  assert.equal(deltaClass(1), 'bad');
  assert.equal(deltaClass(0), '');
});

test('matrixMax and plural', () => {
  assert.equal(matrixMax([[1, 5], [3, 2]]), 5);
  assert.equal(matrixMax([[]]), 0);
  assert.equal(plural(1, 'class', 'classes'), '1 class');
  assert.equal(plural(3, 'class', 'classes'), '3 classes');
  assert.equal(plural(2, 'room'), '2 rooms');
});
