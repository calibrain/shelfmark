import { useEffect, useRef, type EffectCallback } from 'react';

export function useMountEffect(effect: EffectCallback): void {
  const effectRef = useRef(effect);
  effectRef.current = effect;

  useEffect(() => effectRef.current(), []);
}
