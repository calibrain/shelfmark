// Fork: how long a finished download took and how long ago it ended, as short text for the end of
// a card's meta line ("took 4m · 2h ago"). Text only, so the cards stay as quiet as they are.

const SECOND = 1000;
const MINUTE = 60 * SECOND;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** "45s", "4m", "1h 20m", "3d": the two most useful units, never more. */
export const formatDuration = (ms: number): string => {
  if (!Number.isFinite(ms) || ms < 0) {
    return '';
  }
  if (ms < MINUTE) {
    return `${Math.max(Math.round(ms / SECOND), 1)}s`;
  }
  if (ms < HOUR) {
    return `${Math.round(ms / MINUTE)}m`;
  }
  if (ms < DAY) {
    const hours = Math.floor(ms / HOUR);
    const minutes = Math.round((ms % HOUR) / MINUTE);
    return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
  }
  return `${Math.floor(ms / DAY)}d`;
};

/** "just now", "12m ago", "3h ago", "2d ago". */
export const formatAgo = (ms: number): string => {
  if (!Number.isFinite(ms) || ms < 0) {
    return '';
  }
  if (ms < MINUTE) {
    return 'just now';
  }
  if (ms < HOUR) {
    return `${Math.floor(ms / MINUTE)}m ago`;
  }
  if (ms < DAY) {
    return `${Math.floor(ms / HOUR)}h ago`;
  }
  return `${Math.floor(ms / DAY)}d ago`;
};

interface TimingInput {
  /** When it started, epoch ms. 0 or missing when unknown. */
  startedMs?: number;
  /** When it ended, epoch ms. 0 or missing when unknown. */
  finishedMs?: number;
  /** Say how long it took (a completed download); an error or cancel only says how long ago. */
  showDuration: boolean;
  /** Lead the ago text, e.g. "delivered" for a request. */
  agoPrefix?: string;
  now?: number;
}

/** The text for the end of the meta line, or undefined when nothing is known. */
export const buildTimingText = ({
  startedMs = 0,
  finishedMs = 0,
  showDuration,
  agoPrefix,
  now = Date.now(),
}: TimingInput): string | undefined => {
  if (finishedMs <= 0) {
    return undefined;
  }
  const parts: string[] = [];
  if (showDuration && startedMs > 0 && finishedMs >= startedMs) {
    const took = formatDuration(finishedMs - startedMs);
    if (took) {
      parts.push(`took ${took}`);
    }
  }
  const ago = formatAgo(now - finishedMs);
  if (ago) {
    parts.push(agoPrefix ? `${agoPrefix} ${ago}` : ago);
  }
  return parts.length > 0 ? parts.join(' · ') : undefined;
};
