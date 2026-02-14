const TRACE_STORAGE_KEY = 'SM_POLICY_TRACE';

declare global {
  interface Window {
    setPolicyTrace?: (enabled: boolean) => void;
  }
}

const readEnabledState = (): boolean => {
  if (typeof window === 'undefined') {
    return false;
  }
  try {
    return window.localStorage.getItem(TRACE_STORAGE_KEY) === '1';
  } catch {
    return false;
  }
};

export const isPolicyTraceEnabled = (): boolean => {
  return readEnabledState();
};

export const setPolicyTraceEnabled = (enabled: boolean): void => {
  if (typeof window === 'undefined') {
    return;
  }
  try {
    window.localStorage.setItem(TRACE_STORAGE_KEY, enabled ? '1' : '0');
  } catch {
    // Ignore localStorage access issues.
  }
};

export const policyTrace = (event: string, payload?: Record<string, unknown>): void => {
  if (!isPolicyTraceEnabled()) {
    return;
  }
  const timestamp = new Date().toISOString();
  if (payload) {
    console.debug(`[policy-trace ${timestamp}] ${event}`, payload);
    return;
  }
  console.debug(`[policy-trace ${timestamp}] ${event}`);
};

if (typeof window !== 'undefined') {
  window.setPolicyTrace = setPolicyTraceEnabled;
}
