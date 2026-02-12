import { AdminUser } from '../../../services/api';

export interface PerUserSettings {
  [key: string]: unknown;
  BOOKS_OUTPUT_MODE?: string;
  DESTINATION?: string;
  BOOKLORE_LIBRARY_ID?: string;
  BOOKLORE_PATH_ID?: string;
  EMAIL_RECIPIENT?: string;
}

export interface CreateUserFormState {
  username: string;
  email: string;
  password: string;
  display_name: string;
  role: string;
}

export const INITIAL_CREATE_FORM: CreateUserFormState = {
  username: '',
  email: '',
  password: '',
  display_name: '',
  role: 'user',
};

export type UsersPanelRoute =
  | { kind: 'list' }
  | { kind: 'create' }
  | { kind: 'edit'; userId: number }
  | { kind: 'edit-overrides'; userId: number };

export type AuthSource = AdminUser['auth_source'];

export const AUTH_SOURCE_LABEL: Record<AuthSource, string> = {
  builtin: 'Local',
  oidc: 'OIDC',
  proxy: 'Proxy',
  cwa: 'CWA',
};

export const AUTH_SOURCE_BADGE_CLASSES: Record<AuthSource, string> = {
  builtin: 'bg-zinc-500/15 opacity-70',
  oidc: 'bg-sky-500/15 text-sky-400',
  proxy: 'bg-emerald-500/15 text-emerald-400',
  cwa: 'bg-amber-500/15 text-amber-400',
};

export const canCreateLocalUsersForAuthMode = (authMode?: string): boolean => {
  const normalized = String(authMode || 'none').toLowerCase();
  return normalized === 'none' || normalized === 'builtin' || normalized === 'oidc';
};

export const getUsersHeadingDescriptionForAuthMode = (authMode?: string): string => {
  const normalized = String(authMode || 'none').toLowerCase();

  if (normalized === 'builtin') {
    return 'Local users are managed here. Admins can create and manage local users.';
  }
  if (normalized === 'oidc') {
    return 'OIDC users can be auto-provisioned on login. Create local users for fallback admin access or when auto-provision is disabled.';
  }
  if (normalized === 'proxy') {
    return 'Proxy users are auto-created from proxy headers on first login. Local user creation is disabled in proxy mode.';
  }
  if (normalized === 'cwa') {
    return 'Users are managed in Calibre-Web and synced on login. Local user creation is disabled in CWA mode.';
  }
  return 'No authentication is enabled. You can create local users now to prepare for enabling authentication later.';
};
