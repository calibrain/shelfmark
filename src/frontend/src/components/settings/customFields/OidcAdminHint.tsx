import { useState } from 'react';

import { useMountEffect } from '../../../hooks/useMountEffect';
import { getAdminUsers } from '../../../services/api';
import type { CustomSettingsFieldRendererProps } from './types';

const ADMIN_CHECK_KEYS = new Set(['builtin_admin_requirement', 'oidc_admin_requirement']);

export const OidcAdminHint = ({ field }: CustomSettingsFieldRendererProps) => {
  const needsAdminCheck = ADMIN_CHECK_KEYS.has(field.key);
  const [visible, setVisible] = useState(!needsAdminCheck);

  useMountEffect(() => {
    if (!needsAdminCheck) {
      setVisible(true);
      return;
    }

    void getAdminUsers()
      .then((users) => {
        setVisible(!users.some((u) => u.role === 'admin' && u.auth_source === 'builtin'));
      })
      .catch(() => {
        setVisible(true);
      });
  });

  if (!visible) return null;

  return (
    <div className="rounded-lg bg-amber-500/15 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
      {field.label}
    </div>
  );
};
