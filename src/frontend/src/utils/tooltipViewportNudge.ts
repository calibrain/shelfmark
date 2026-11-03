type MeasuredBox = Pick<DOMRect, 'left' | 'right' | 'top' | 'bottom' | 'width' | 'height'>;

interface ViewportNudge {
  x: number;
  y: number;
}

const VIEWPORT_PADDING = 6;

// Firefox measures a transformed box in single precision, so a tooltip already moved flush
// to the edge can read 0.00001px past it. Moving it by that much changes nothing, the next
// measurement shows the same overflow, and the tooltip's layout effect loops until React
// gives up with error #185 and the page goes blank (#1646).
const SUBPIXEL_TOLERANCE = 0.5;

const nudgeAxis = (start: number, end: number, size: number, viewport: number): number => {
  let delta = 0;
  if (size + VIEWPORT_PADDING * 2 > viewport || start < VIEWPORT_PADDING) {
    delta = VIEWPORT_PADDING - start;
  } else if (end > viewport - VIEWPORT_PADDING) {
    delta = viewport - VIEWPORT_PADDING - end;
  }
  return Math.abs(delta) < SUBPIXEL_TOLERANCE ? 0 : delta;
};

/**
 * How far to move a tooltip so it stays inside the viewport, keeping a small gap
 * from each edge. A tooltip bigger than the viewport is pinned to the top/left edge.
 * Overflow under half a pixel counts as none.
 */
export const getTooltipViewportNudge = (
  rect: MeasuredBox,
  viewportWidth: number,
  viewportHeight: number,
): ViewportNudge => ({
  x: nudgeAxis(rect.left, rect.right, rect.width, viewportWidth),
  y: nudgeAxis(rect.top, rect.bottom, rect.height, viewportHeight),
});
