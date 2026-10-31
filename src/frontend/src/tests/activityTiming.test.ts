import { describe, expect, it } from 'vitest';

import { buildTimingText, formatAgo, formatDuration } from '../components/activity/activityTiming';

const SECOND = 1000;
const MINUTE = 60 * SECOND;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

describe('formatDuration', () => {
  it('uses the two most useful units', () => {
    expect(formatDuration(500)).toBe('1s');
    expect(formatDuration(45 * SECOND)).toBe('45s');
    expect(formatDuration(4 * MINUTE)).toBe('4m');
    expect(formatDuration(HOUR + 20 * MINUTE)).toBe('1h 20m');
    expect(formatDuration(3 * HOUR)).toBe('3h');
    expect(formatDuration(3 * DAY + 5 * HOUR)).toBe('3d');
  });

  it('says nothing for a value that makes no sense', () => {
    expect(formatDuration(-1)).toBe('');
    expect(formatDuration(Number.NaN)).toBe('');
  });
});

describe('formatAgo', () => {
  it('reads as a relative time', () => {
    expect(formatAgo(10 * SECOND)).toBe('just now');
    expect(formatAgo(12 * MINUTE)).toBe('12m ago');
    expect(formatAgo(3 * HOUR + 40 * MINUTE)).toBe('3h ago');
    expect(formatAgo(2 * DAY)).toBe('2d ago');
  });

  it('says nothing for a time in the future', () => {
    expect(formatAgo(-5 * MINUTE)).toBe('');
  });
});

describe('buildTimingText', () => {
  const now = 1_000_000_000_000;

  it('says how long a completed download took and how long ago it ended', () => {
    const text = buildTimingText({
      startedMs: now - 2 * HOUR - 4 * MINUTE,
      finishedMs: now - 2 * HOUR,
      showDuration: true,
      now,
    });
    expect(text).toBe('took 4m · 2h ago');
  });

  it('only says how long ago when the duration is not wanted', () => {
    expect(
      buildTimingText({
        startedMs: now - 3 * HOUR,
        finishedMs: now - HOUR,
        showDuration: false,
        now,
      }),
    ).toBe('1h ago');
  });

  it('leads the ago text with a prefix when one is given', () => {
    expect(
      buildTimingText({
        finishedMs: now - 3 * HOUR,
        showDuration: false,
        agoPrefix: 'delivered',
        now,
      }),
    ).toBe('delivered 3h ago');
  });

  it('skips the duration when the start is unknown or after the end', () => {
    expect(buildTimingText({ finishedMs: now - MINUTE * 5, showDuration: true, now })).toBe(
      '5m ago',
    );
    expect(
      buildTimingText({ startedMs: now, finishedMs: now - MINUTE * 5, showDuration: true, now }),
    ).toBe('5m ago');
  });

  it('says nothing without a finish time', () => {
    expect(buildTimingText({ startedMs: now - HOUR, showDuration: true, now })).toBeUndefined();
    expect(buildTimingText({ finishedMs: 0, showDuration: true, now })).toBeUndefined();
  });
});
