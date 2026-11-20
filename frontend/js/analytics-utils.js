// Pure helpers for the analytics / explanation screens (no DOM) so they can be unit-tested.

/** Colour for a heat-map cell: 0 = transparent, max = full intensity of `hue`. */
export function heatColor(value, max, hue = 215) {
  if (!max || !value || value <= 0) return 'transparent';
  const t = Math.min(1, value / max);
  return `hsl(${hue} 75% ${Math.round(94 - 48 * t)}%)`;
}

/** 12.5 -> "12.5%", null/undefined -> "—". */
export function pct(v, digits = 1) {
  if (v == null || Number.isNaN(v)) return '—';
  return `${Number(v.toFixed(digits))}%`;
}

export function num(v, digits = 0) {
  if (v == null || Number.isNaN(v)) return '—';
  return Number(v.toFixed(digits)).toLocaleString('en-US');
}

/** Words for a 0-100 quality score. */
export function scoreLabel(score) {
  if (score == null) return 'Not available';
  if (score >= 90) return 'Excellent';
  if (score >= 75) return 'Good';
  if (score >= 60) return 'Fair';
  return 'Needs work';
}

/** Width (0-100) of a bar for `value` on a scale ending at `max`. */
export function barWidth(value, max) {
  if (!max || value <= 0) return 0;
  return Math.max(2, Math.min(100, (value / max) * 100));
}

/** Signed change in a cost where LOWER is better: "−3 (better)", "+2 (worse)", "no change". */
export function deltaLabel(delta) {
  if (delta === 0) return 'no change';
  return `${delta < 0 ? '−' : '+'}${Math.abs(delta)} (${delta < 0 ? 'better' : 'worse'})`;
}

export function deltaClass(delta) {
  return delta < 0 ? 'good' : delta > 0 ? 'bad' : '';
}

/** Largest value in a matrix (for heat-map scaling). */
export function matrixMax(matrix) {
  return matrix.reduce((m, row) => Math.max(m, ...row), 0);
}

/** "1 class" / "3 classes". */
export function plural(n, one, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}
