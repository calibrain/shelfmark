type BookTargetChangeEvent = {
  provider: string;
  bookId: string;
  target: string;
  selected: boolean;
};

type Listener = (event: BookTargetChangeEvent) => void;

const listeners = new Set<Listener>();

export const onBookTargetChange = (listener: Listener): (() => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

export const emitBookTargetChange = (event: BookTargetChangeEvent): void => {
  listeners.forEach((listener) => listener(event));
};
