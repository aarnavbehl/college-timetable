// Pure helpers for the Excel import page (no DOM, no network) so they can be unit-tested.

export const CONFIRM_WORD = 'REPLACE';

export const STATUS = {
  new: { label: 'New', kind: 'ok' },
  skip: { label: 'Skipped', kind: 'warn' },
  error: { label: 'Rejected', kind: 'bad' },
};

const DELETE_LABELS = [
  ['branches', 'branch', 'branches'], ['rooms', 'room', 'rooms'], ['sections', 'section', 'sections'],
  ['faculty', 'faculty member', 'faculty members'], ['courses', 'course', 'courses'],
  ['allocations', 'allocation', 'allocations'], ['timetable_classes', 'scheduled class of the timetable', 'scheduled classes of the timetable'],
  ['substitution_plans', 'saved substitution plan', 'saved substitution plans'],
];

/**
 * May the Import button be pressed? Mirrors the server's rules (which re-check everything).
 * Returns { ok, reason } where reason explains a disabled button.
 */
export function canSubmit({ preview, partial = false, replace = false, typed = '' }) {
  if (!preview) return { ok: false, reason: 'Choose a file first.' };
  if (preview.totals.rows === 0) return { ok: false, reason: 'The workbook has no data rows.' };
  if (replace) {
    if (!preview.can_import_strict) {
      return {
        ok: false,
        reason: preview.has_errors
          ? 'Replacing needs a file with no problems. Fix the rejected rows and choose the file again.'
          : 'The file has nothing to import.',
      };
    }
    if (typed.trim() !== CONFIRM_WORD) return { ok: false, reason: `Type ${CONFIRM_WORD} to confirm deleting your existing data.` };
    return { ok: true, reason: '' };
  }
  if (preview.totals.new === 0) return { ok: false, reason: 'Nothing new to import: every row already exists or was rejected.' };
  if (preview.has_errors && !partial) {
    return { ok: false, reason: 'Some rows have problems. Fix the file and choose it again, or tick “Import only the valid rows”.' };
  }
  return { ok: true, reason: '' };
}

/** Replacing always imports strictly (the server enforces this too). */
export function submitMode({ partial = false, replace = false }) {
  return replace || !partial ? 'strict' : 'partial';
}

export function totalsLine(t) {
  return `${t.new} new · ${t.skipped} skipped · ${t.rejected} rejected`;
}

export function importButtonLabel(preview, { partial = false, replace = false } = {}) {
  if (!preview) return 'Import';
  if (replace) return `Replace everything and import ${preview.totals.new} records`;
  const n = preview.totals.new;
  return `Import ${n} record${n === 1 ? '' : 's'}${preview.has_errors && partial ? ' (valid rows only)' : ''}`;
}

/** "5 sections, 14 faculty members and 1 scheduled class of the timetable" for the replace warning. */
export function describeDelete(counts) {
  const parts = DELETE_LABELS
    .filter(([key]) => counts && counts[key] > 0)
    .map(([key, one, many]) => `${counts[key]} ${counts[key] === 1 ? one : many}`);
  if (!parts.length) return 'nothing (your college has no data yet)';
  return parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`;
}

/** Rows to show for a sheet, optionally only those that need attention. */
export function visibleRows(sheet, onlyProblems) {
  return onlyProblems ? sheet.rows.filter((r) => r.status !== 'new') : sheet.rows;
}

/** First sheet worth showing: the first present sheet with problems, else the first present one. */
export function firstTab(preview) {
  const present = preview.sheets.filter((s) => s.present);
  const bad = present.find((s) => s.counts.rejected > 0 || s.errors.length);
  return (bad || present[0] || preview.sheets[0]).key;
}

export function missingSheets(preview) {
  return preview.sheets.filter((s) => !s.present).map((s) => s.name);
}
