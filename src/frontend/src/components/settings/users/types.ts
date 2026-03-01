import { AdminUser } from '../../../services/api';

export interface PerUserSettings {
  [key: string]: unknown;
  BOOKS_OUTPUT_MODE?: string;
  DESTINATION?: string;
  DESTINATION_AUDIOBOOK?: string;
  BOOKLORE_LIBRARY_ID?: string;
  BOOKLORE_PATH_ID?: string;
  EMAIL_RECIPIENT?: string;
  SEARCH_MODE?: string;
  METADATA_PROVIDER?: string;
  METADATA_PROVIDER_AUDIOBOOK?: string;
  DEFAULT_RELEASE_SOURCE?: string;
  USER_NOTIFICATION_ROUTES?: Array<Record<string, unknown>>;
  REQUESTS_ENABLED?: boolean;
  REQUEST_POLICY_DEFAULT_EBOOK?: string;
  REQUEST_POLICY_DEFAULT_AUDIOBOOK?: string;
  REQUEST_POLICY_RULES?: Array<Record<string, unknown>>;
  MAX_PENDING_REQUESTS_PER_USER?: number;
  REQUESTS_ALLOW_NOTES?: boolean;
}

export interface CreateUserFormState {
  username: string;
  email: string;
  password: string;
  password_confirm: string;
  display_name: string;
  role: string;
}

export const INITIAL_CREATE_FORM: CreateUserFormState = {
  username: '',
  email: '',
  password: '',
  password_confirm: '',
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
  oidc: 'bg-sky-500/15 text-sky-600 dark:text-sky-400',
  proxy: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400',
  cwa: 'bg-amber-500/15 text-amber-600 dark:text-amber-400',
};

export const canCreateLocalUsersForAuthMode = (authMode?: string): boolean => {
  const normalized = String(authMode || 'none').toLowerCase();
  return normalized === 'none' || normalized === 'builtin' || normalized === 'oidc';
};

export const getUsersHeadingDescriptionForAuthMode = (authMode?: string): string => {
  const normalized = String(authMode || 'none').toLowerCase();

  if (normalized === 'builtin') {
    return 'Create and manage user accounts directly. Passwords are stored locally and users sign in with their username and password.';
  }
  if (normalized === 'oidc') {
    return 'Users sign in through your identity provider. New accounts can be created automatically on first login when auto-provisioning is enabled, or you can pre-create users here and they\u2019ll be linked by email on first sign-in.';
  }
  if (normalized === 'proxy') {
    return 'Users are authenticated by your reverse proxy. Accounts are automatically created on first sign-in. If a local user with a matching username already exists, it will be linked instead.';
  }
  if (normalized === 'cwa') {
    return 'User accounts are synced from your Calibre-Web database. Users are matched by email, and new accounts are created here when new CWA users are found.';
  }
  return 'Authentication is disabled. Anyone can access Shelfmark without signing in.';
};
