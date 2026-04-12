import { useEffect } from 'react';

export const useAppStartupStatusFetch = (refreshStatus: () => Promise<void>): void => {
  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);
};
