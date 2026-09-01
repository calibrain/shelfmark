import { describe, it, expect } from 'vitest';

import {
  INITIAL_ENTER_ANIMATION,
  nextEnterAnimation,
  type EnterAnimationState,
} from '../utils/releaseModalEnterAnimation';

describe('nextEnterAnimation', () => {
  it('animates the first session', () => {
    const next = nextEnterAnimation(INITIAL_ENTER_ANIMATION, 'book-1', false);
    expect(next).toEqual({ key: 'book-1', animate: true });
  });

  it('animates the first session in combined mode too', () => {
    const next = nextEnterAnimation(INITIAL_ENTER_ANIMATION, 'book-1', true);
    expect(next).toEqual({ key: 'book-1', animate: true });
  });

  it('does not animate a step transition between combined-mode sessions', () => {
    const current: EnterAnimationState = { key: 'book-1', animate: true };
    expect(nextEnterAnimation(current, 'book-2', true)).toEqual({
      key: 'book-2',
      animate: false,
    });
  });

  it('animates a session swap outside combined mode', () => {
    const current: EnterAnimationState = { key: 'book-1', animate: true };
    expect(nextEnterAnimation(current, 'book-2', false)).toEqual({
      key: 'book-2',
      animate: true,
    });
  });

  it('holds the decision across re-renders of the same session', () => {
    // Regression: the decision used to flip back to `true` on the next render,
    // replaying the enter animation mid-session.
    const stepped = nextEnterAnimation({ key: 'book-1', animate: true }, 'book-2', true);
    expect(stepped.animate).toBe(false);

    let state = stepped;
    for (let i = 0; i < 5; i++) {
      state = nextEnterAnimation(state, 'book-2', true);
      expect(state.animate).toBe(false);
    }
  });

  it('returns the same reference when the session is unchanged', () => {
    const current: EnterAnimationState = { key: 'book-1', animate: false };
    expect(nextEnterAnimation(current, 'book-1', true)).toBe(current);
  });

  it('animates again after the modal closes and reopens', () => {
    const open = nextEnterAnimation(INITIAL_ENTER_ANIMATION, 'book-1', true);
    const closed = nextEnterAnimation(open, null, true);
    expect(closed.key).toBeNull();

    const reopened = nextEnterAnimation(closed, 'book-1', true);
    expect(reopened.animate).toBe(true);
  });
});
