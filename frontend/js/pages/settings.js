import { api } from '../api.js';
import { buildForm, h, pageHeader, toast } from '../ui.js';
import { loadSettings, store } from '../store.js';

export async function settingsPage(container) {
  const s = await loadSettings();
  const preview = h('div', { class: 'preview' });

  const fields = [
    { name: 'days_per_week', label: 'Teaching days per week', type: 'number', required: true, min: 1, max: 7, help: '5 = Monday–Friday, 6 = including Saturday.' },
    { name: 'periods_per_day', label: 'Periods per day', type: 'number', required: true, min: 1, max: 12 },
    { name: 'start_time', label: 'First period starts at', type: 'text', required: true, placeholder: '09:00', help: '24-hour HH:MM' },
    { name: 'period_minutes', label: 'Minutes per period', type: 'number', required: true, min: 20, max: 180 },
    { name: 'lunch_after', label: 'Lunch break after period', type: 'number', required: true, min: 0, max: 11, help: '0 = no lunch break. Lab blocks never span the break.' },
    { name: 'lunch_minutes', label: 'Lunch length (minutes)', type: 'number', required: true, min: 0, max: 180 },
  ];
  const form = buildForm(fields, s, {
    submitLabel: 'Save settings',
    onSubmit: async (v) => {
      store.settings = await api.put('/settings', v);
      toast('Settings saved. Regenerate the timetable to apply them.', 'success');
      drawPreview(store.settings);
    },
  });

  function drawPreview(cfg) {
    preview.replaceChildren(h('h4', {}, 'Your day'),
      h('ol', { class: 'day-preview' }, cfg.period_times.flatMap((t, i) => [
        h('li', {}, h('strong', {}, `Period ${i + 1}`), ` ${t[0]}–${t[1]}`),
        cfg.lunch_after === i + 1 ? h('li', { class: 'lunch-row' }, `Lunch break (${cfg.lunch_minutes} min)`) : null,
      ])));
  }
  drawPreview(s);

  container.append(pageHeader('Settings', 'The shape of your teaching week. Change this before adding faculty availability.'),
    h('div', { class: 'two-col' }, h('div', { class: 'card' }, form.el), h('div', { class: 'card' }, preview)));
}
