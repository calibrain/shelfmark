import { useCallback, useState } from 'react';
import { UsersPanelRoute } from './types';

export const useUsersPanelState = () => {
  const [route, setRoute] = useState<UsersPanelRoute>({ kind: 'list' });

  const openCreate = useCallback(() => {
    setRoute({ kind: 'create' });
  }, []);

  const openEdit = useCallback((userId: number) => {
    setRoute({ kind: 'edit', userId });
  }, []);

  const openEditOverrides = useCallback((userId: number) => {
    setRoute({ kind: 'edit-overrides', userId });
  }, []);

  const backToList = useCallback(() => {
    setRoute({ kind: 'list' });
  }, []);

  return {
    route,
    openCreate,
    openEdit,
    openEditOverrides,
    backToList,
  };
};
