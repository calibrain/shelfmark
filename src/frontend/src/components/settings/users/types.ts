import type { AdminUser } from '../../../services/api';
import type { LibraryCapability } from '../../../types';

export interface CreateUserFormState {
  username: string;
  email: string;
  password: string;
  password_confirm: string;
  display_name: string;
  role: string;
  library_capability: LibraryCapability;
}

export const INITIAL_CREATE_FORM: CreateUserFormState = {
  username: '',
  email: '',
  password: '',
  password_confirm: '',
  display_name: '',
  role: 'user',
  library_capability: 'download-capable',
};

export type UsersPanelRoute =
  | { kind: 'list' }
  | { kind: 'create' }
  | { kind: 'edit'; userId: number };

type AuthSource = AdminUser['auth_source'];

export const AUTH_SOURCE_LABEL: Record<AuthSource, string> = {
  builtin: 'Local',
  oidc: 'OIDC',
  proxy: 'Proxy',
  cwa: 'CWA',
};

export const AUTH_SOURCE_BADGE_CLASSES: Record<AuthSource, string> = {
  builtin: 'bg-zinc-500/15 opacity-70',
  oidc: 'bg-sky-500/15 text-sky-600 dark:text-sky-400',
  proxy: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400',
  cwa: 'bg-amber-500/15 text-amber-600 dark:text-amber-400',
};

export const canCreateLocalUsersForAuthMode = (authMode?: string): boolean => {
  const normalized = (authMode || 'none').toLowerCase();
  return normalized === 'none' || normalized === 'builtin' || normalized === 'oidc';
};
