import { useEffect, useEffectEvent, type DependencyList, type EffectCallback } from 'react';

export function useMountEffect(effect: EffectCallback): void {
  const runEffect = useEffectEvent(effect);

  useEffect(() => runEffect(), []);
}

export function useDependencyEffect(effect: EffectCallback, deps: DependencyList): void {
  const runEffect = useEffectEvent(effect);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => runEffect(), deps);
}
