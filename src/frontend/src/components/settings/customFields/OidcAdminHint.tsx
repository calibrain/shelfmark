import { useEffect, useState } from 'react';
import { getAdminUsers } from '../../../services/api';
import { CustomSettingsFieldRendererProps } from './types';

export const OidcAdminHint = ({ field }: CustomSettingsFieldRendererProps) => {
  const [needsAdmin, setNeedsAdmin] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    getAdminUsers()
      .then((users) => {
        if (!cancelled) {
          setNeedsAdmin(!users.some(u => u.role === 'admin' && u.auth_source === 'builtin'));
        }
      })
      .catch(() => {
        if (!cancelled) {
          setNeedsAdmin(true);
        }
      });
    return () => { cancelled = true; };
  }, []);

  if (!needsAdmin) return null;

  return (
    <div className="text-sm px-3 py-2 rounded-lg bg-amber-500/15 text-amber-700 dark:text-amber-300">
      {field.label}
    </div>
  );
};
