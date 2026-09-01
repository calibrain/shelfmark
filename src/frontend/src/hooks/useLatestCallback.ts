import { useCallback, useLayoutEffect, useRef } from 'react';

/**
 * A callback with a stable identity that always runs the latest render's implementation.
 *
 * `useEffectEvent` is React's answer to this shape, but its contract is narrower than it
 * looks: an Effect Event may only be called from inside an Effect, and must not be handed
 * to another component, stored in state, or registered with something that outlives the
 * Effect. Handlers that run from a DOM event, from an async continuation, or from a parent
 * holding the function in its own UI state are all outside that contract - React documents
 * the behaviour there as undefined, and the React Compiler advisories oxlint reports
 * ("existing memoization could not be preserved") are the same fact from the other side.
 *
 * So this is the supported shape for those callers. The ref is published in a layout
 * effect - after commit, before paint - rather than assigned during render, so a render
 * React later throws away cannot leak its closure into a handler, and no event can
 * observe the gap.
 *
 * Use `useEffectEvent` when the caller really is an Effect; use this everywhere else.
 */
export function useLatestCallback<Args extends unknown[], Result>(
  callback: (...args: Args) => Result,
): (...args: Args) => Result {
  const callbackRef = useRef(callback);

  useLayoutEffect(() => {
    callbackRef.current = callback;
  });

  return useCallback((...args: Args) => callbackRef.current(...args), []);
}
