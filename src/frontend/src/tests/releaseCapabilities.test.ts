import { describe, expect, it } from 'vitest';

import { canUseManualReleaseQuery } from '../utils/releaseCapabilities';

describe('ReleaseModal manual query capability', () => {
  it('is limited to administrators', () => {
    expect(canUseManualReleaseQuery(true)).toBe(true);
    expect(canUseManualReleaseQuery(false)).toBe(false);
  });
});
