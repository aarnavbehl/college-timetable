// Pure helpers (no DOM, no network) so they can be unit-tested with `node --test`.

export const DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
export const DAY_SHORT = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/** Stable pastel colour for a key such as a course code. */
export function colorFor(key) {
  let hash = 0;
  for (const ch of String(key)) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  const hue = hash % 360;
  return { bg: `hsl(${hue} 70% 91%)`, border: `hsl(${hue} 50% 45%)`, text: `hsl(${hue} 55% 22%)` };
}

const KEYS = { section: 'section_id', faculty: 'faculty_id', room: 'room_id' };
const LABELS = { section: 'section', faculty: 'faculty', room: 'room' };

/** Distinct sections / faculty / rooms that appear in the timetable, sorted by name. */
export function entityOptions(sessions, kind) {
  const seen = new Map();
  for (const s of sessions) {
    const id = s[KEYS[kind]];
    if (id == null || seen.has(id)) continue;
    seen.set(id, { id, label: s[LABELS[kind]] });
  }
  return [...seen.values()].sort((a, b) => a.label.localeCompare(b.label, undefined, { numeric: true }));
}

export function filterSessions(sessions, kind, id) {
  return sessions.filter((s) => s[KEYS[kind]] === id);
}

/**
 * Group one day's sessions into display blocks. Sessions of the same task (e.g. two lab
 * batches running together) share a block. Overlapping sessions that are NOT the same task
 * (only possible after a forced move) are merged into one block flagged `conflict`.
 */
export function groupDay(daySessions) {
  const sorted = [...daySessions].sort((a, b) => a.start - b.start || a.length - b.length);
  const groups = [];
  for (const s of sorted) {
    const last = groups[groups.length - 1];
    if (last && s.start < last.start + last.length) {
      last.length = Math.max(last.length, s.start + s.length - last.start);
      last.items.push(s);
      if (!last.items.every((i) => i.task_key === s.task_key)) last.conflict = true;
    } else {
      groups.push({ start: s.start, length: s.length, items: [s], conflict: false });
    }
  }
  return groups;
}

/** "09:00–09:50" for a class starting at `start` (1-based) lasting `length` periods. */
export function timeRange(times, start, length) {
  const first = times[start - 1];
  const last = times[start + length - 2];
  return first && last ? `${first[0]}–${last[1]}` : '';
}

export function periodLabel(start, length) {
  return length > 1 ? `P${start}–${start + length - 1}` : `P${start}`;
}

/** Periods per week each entity teaches / attends, e.g. for the load summary. */
export function weeklyLoad(sessions) {
  return sessions.reduce((sum, s) => sum + s.length, 0);
}

/** Turn solver reason codes into a short sentence. */
export function reasonText(reasons) {
  return reasons && reasons.length ? reasons.join(', ') : 'free';
}

/** Pydantic/FastAPI error bodies come in several shapes; produce one readable string. */
export function formatError(detail) {
  if (detail == null) return 'Something went wrong.';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => {
      const field = Array.isArray(d.loc) ? d.loc.filter((x) => x !== 'body').join('.') : '';
      return field ? `${field}: ${d.msg}` : d.msg;
    }).join('; ');
  }
  if (typeof detail === 'object') {
    const parts = [detail.message];
    if (detail.errors) parts.push(detail.errors.join(' '));
    if (detail.reasons && detail.reasons.length) parts.push(`(${detail.reasons.join(', ')})`);
    return parts.filter(Boolean).join(' ');
  }
  return String(detail);
}

export function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** Monday=0 ... Sunday=6 for a YYYY-MM-DD string (timezone independent). */
export function weekdayOf(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7;
}
