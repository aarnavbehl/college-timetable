// Small dependency-free chart helpers built from plain DOM (bars, heat maps, gauge, metric cards).
import { barWidth, heatColor, matrixMax, num, pct } from './analytics-utils.js';
import { h } from './ui.js';

/** A labelled summary number. kind: '' | 'ok' | 'bad' | 'warn' | 'soft' | 'hard'. */
export function metricCard({ label, value, sub, kind = '', help }) {
  return h('div', { class: `metric ${kind}`, title: help || '' },
    h('span', { class: 'metric-label' }, label, help ? h('span', { class: 'info', 'aria-hidden': 'true' }, ' ⓘ') : null),
    h('b', { class: 'metric-value' }, value),
    sub ? h('small', { class: 'metric-sub' }, sub) : null);
}

/** Horizontal bars. items: [{label, value, text?, note?, kind?}] */
export function barList(items, { max, valueText = (i) => num(i.value) } = {}) {
  const top = max ?? Math.max(0, ...items.map((i) => i.value));
  return h('div', { class: 'bars' }, items.map((i) => h('div', { class: 'bar-row', title: i.note || '' },
    h('span', { class: 'bar-label' }, i.label),
    h('span', { class: 'bar-track' }, h('span', { class: `bar-fill ${i.kind || ''}`, style: { width: `${barWidth(i.value, top)}%` } })),
    h('span', { class: 'bar-value' }, i.text ?? valueText(i)))));
}

/** Vertical columns. items: [{label, value, text?}] */
export function columnChart(items, { max, height = 140 } = {}) {
  const top = max ?? Math.max(0, ...items.map((i) => i.value));
  return h('div', { class: 'columns', style: { height: `${height + 44}px` } }, items.map((i) => h('div', { class: 'column' },
    h('span', { class: 'column-value' }, i.text ?? num(i.value)),
    h('span', { class: 'column-bar', style: { height: `${(barWidth(i.value, top) / 100) * height}px` } }),
    h('span', { class: 'column-label' }, i.label))));
}

/**
 * Day x period heat map. values[d][p]; `total` (optional) turns counts into percentages in the tooltip.
 */
export function heatmap({ days, periods, values, total, hue = 215, unit = '' }) {
  const max = total || matrixMax(values);
  const head = h('tr', {}, h('th'), Array.from({ length: periods }, (_, p) => h('th', {}, `P${p + 1}`)));
  const body = days.map((day, d) => h('tr', {}, h('th', {}, day.slice(0, 3)),
    values[d].map((v) => h('td', {
      style: { background: heatColor(v, max, hue) },
      title: total ? `${v} of ${total} ${unit} (${pct((100 * v) / total, 0)})` : `${v} ${unit}`,
    }, v ? String(v) : ''))));
  return h('div', { class: 'table-wrap' }, h('table', { class: 'heat' }, h('thead', {}, head), h('tbody', {}, body)));
}

/** Circular gauge for a 0-100 score (or a dash when there is none). */
export function gauge(score, caption) {
  const deg = score == null ? 0 : Math.round((score / 100) * 360);
  return h('div', { class: 'gauge-wrap' },
    h('div', { class: `gauge${score == null ? ' empty' : ''}`, style: { '--deg': `${deg}deg` }, role: 'img',
      'aria-label': score == null ? 'No quality score' : `Quality score ${score} out of 100` },
    h('span', {}, score == null ? '—' : String(score))),
    caption ? h('small', { class: 'muted' }, caption) : null);
}
