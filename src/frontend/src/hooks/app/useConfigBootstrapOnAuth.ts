import { useEffect } from 'react';

interface UseConfigBootstrapOnAuthOptions {
  isAuthenticated: boolean;
  loadConfig: (mode?: 'initial' | 'settings-saved') => void | Promise<void>;
}

export const useConfigBootstrapOnAuth = ({
  isAuthenticated,
  loadConfig,
}: UseConfigBootstrapOnAuthOptions): void => {
  useEffect(() => {
    if (!isAuthenticated) {
      return;
    }
    void loadConfig('initial');
  }, [isAuthenticated, loadConfig]);
};
