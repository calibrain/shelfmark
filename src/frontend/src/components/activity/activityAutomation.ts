// Fork: scripts and the automatic passes act through the admin account and mark what they did with
// an "[auto] " admin note. The cards say "automatically" for those, so a person is not shown as
// the one who declined or approved something they never saw.

const AUTO_PREFIX = /^\s*\[auto\]\s*/i;

export const isAutomaticNote = (note: string | null | undefined): boolean =>
  typeof note === 'string' && AUTO_PREFIX.test(note);

/** The note without its "[auto] " marker. */
export const stripAutomaticMarker = (note: string): string => note.replace(AUTO_PREFIX, '').trim();

interface RetryFields {
  status?: string;
  admin_note?: string | null;
  last_failure_reason?: string | null;
}

/**
 * A pending request the automation is retrying by itself (reopened after a failed or interrupted
 * download), as opposed to one it searched and found nothing for. Nothing for a person to do.
 */
export const isRetryingAutomatically = (record: RetryFields | null | undefined): boolean => {
  if (!record || record.status !== 'pending') {
    return false;
  }
  const note = (record.admin_note ?? '').trim().toLowerCase();
  const reason = (record.last_failure_reason ?? '').trim().toLowerCase();
  return (
    note.startsWith('[auto] retry') ||
    reason.startsWith('retrying') ||
    reason.startsWith('interrupted by a restart')
  );
};
