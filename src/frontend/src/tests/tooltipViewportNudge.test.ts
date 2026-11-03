import { describe, it, expect } from 'vitest';

import { getTooltipViewportNudge } from '../utils/tooltipViewportNudge';

const box = (left: number, top: number, width: number, height: number) => ({
  left,
  top,
  width,
  height,
  right: left + width,
  bottom: top + height,
});

describe('getTooltipViewportNudge', () => {
  it('leaves a tooltip that fits where it is', () => {
    expect(getTooltipViewportNudge(box(100, 100, 80, 30), 1600, 900)).toEqual({ x: 0, y: 0 });
  });

  // The two measurements Firefox took of the "Stop download" tooltip in #1646,
  // before and after the nudge, in a 1600x900 window.
  it('moves a tooltip that runs past the right edge back inside', () => {
    const rect = box(1536.2166748046875, 185, 67.566680908203125, 35.5);
    const nudge = getTooltipViewportNudge(rect, 1600, 900);
    expect(nudge.x).toBeCloseTo(1594 - rect.right, 10);
    expect(nudge.y).toBe(0);
  });

  it('leaves a tooltip that measures a fraction of a pixel past the edge', () => {
    const rect = box(1526.433349609375, 185, 67.56666564941406, 35.5);
    expect(rect.right).toBeGreaterThan(1594);
    expect(getTooltipViewportNudge(rect, 1600, 900)).toEqual({ x: 0, y: 0 });
  });

  it('still moves a tooltip that is half a pixel over', () => {
    expect(getTooltipViewportNudge(box(1514, 100, 80.5, 30), 1600, 900)).toEqual({
      x: -0.5,
      y: 0,
    });
  });

  it('moves a tooltip past the left or top edge back inside', () => {
    expect(getTooltipViewportNudge(box(-20, -10, 80, 30), 1600, 900)).toEqual({ x: 26, y: 16 });
  });

  it('moves a tooltip past the bottom edge up', () => {
    expect(getTooltipViewportNudge(box(100, 880, 80, 30), 1600, 900)).toEqual({ x: 0, y: -16 });
  });

  it('pins a tooltip wider than the viewport to the left edge', () => {
    expect(getTooltipViewportNudge(box(40, 100, 400, 30), 390, 900)).toEqual({ x: -34, y: 0 });
  });
});
