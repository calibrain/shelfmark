import { useEffect } from 'react';

import { policyTrace } from '../../utils/policyTrace';

interface UseAuthEstablishedStatusRefreshOptions {
  authChecked: boolean;
  isAuthenticated: boolean;
  isAdmin: boolean;
  username: string | null;
  refreshStatus: () => Promise<void>;
}

export const useAuthEstablishedStatusRefresh = ({
  authChecked,
  isAuthenticated,
  isAdmin,
  username,
  refreshStatus,
}: UseAuthEstablishedStatusRefreshOptions): void => {
  useEffect(() => {
    if (!authChecked || !isAuthenticated) {
      return;
    }
    policyTrace('auth.status', { authChecked, isAuthenticated, isAdmin, username });
    void refreshStatus();
  }, [authChecked, isAuthenticated, isAdmin, username, refreshStatus]);
};
