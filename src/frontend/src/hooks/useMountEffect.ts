import { useEffect, useRef, type DependencyList, type EffectCallback } from 'react';

export function useMountEffect(effect: EffectCallback): void {
  const effectRef = useRef(effect);
  effectRef.current = effect;

  useEffect(() => effectRef.current(), []);
}

export function useDependencyEffect(effect: EffectCallback, deps: DependencyList): void {
  const effectRef = useRef(effect);
  effectRef.current = effect;

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => effectRef.current(), deps);
}
