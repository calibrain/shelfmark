export interface EnterAnimationState {
  key: string | null;
  animate: boolean;
}

export const INITIAL_ENTER_ANIMATION: EnterAnimationState = { key: null, animate: true };

/**
 * Decide whether a release modal session should play its enter animation.
 *
 * The decision is made once, when a session key first appears, and then held for
 * that session's lifetime — re-rendering mid-session must not restart the
 * animation. Swapping between sessions in combined mode is a step transition
 * rather than an entrance, so it does not animate.
 */
export const nextEnterAnimation = (
  current: EnterAnimationState,
  sessionKey: string | null,
  isCombinedMode: boolean,
): EnterAnimationState => {
  if (current.key === sessionKey) {
    return current;
  }

  return {
    key: sessionKey,
    animate: !isCombinedMode || current.key === null,
  };
};
