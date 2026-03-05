import { useEffect, useState } from 'react';
import { getAdminUsers } from '../../../services/api';
import { CustomSettingsFieldRendererProps } from './types';

const ADMIN_CHECK_KEYS = new Set(['builtin_admin_requirement', 'oidc_admin_requirement']);

export const OidcAdminHint = ({ field }: CustomSettingsFieldRendererProps) => {
  const needsAdminCheck = ADMIN_CHECK_KEYS.has(field.key);
  const [visible, setVisible] = useState(!needsAdminCheck);

  useEffect(() => {
    if (!needsAdminCheck) return;
    let cancelled = false;
    getAdminUsers()
      .then((users) => {
        if (!cancelled) {
          setVisible(!users.some(u => u.role === 'admin' && u.auth_source === 'builtin'));
        }
      })
      .catch(() => {
        if (!cancelled) {
          setVisible(true);
        }
      });
    return () => { cancelled = true; };
  }, [needsAdminCheck]);

  if (!visible) return null;

  return (
    <div className="text-sm px-3 py-2 rounded-lg bg-amber-500/15 text-amber-700 dark:text-amber-300">
      {field.label}
    </div>
  );
};
