import { AdminUser } from '../../../services/api';

export interface PerUserSettings {
  DESTINATION?: string;
  BOOKLORE_LIBRARY_ID?: string;
  BOOKLORE_PATH_ID?: string;
  EMAIL_RECIPIENTS?: Array<{ nickname: string; email: string }>;
}

export type OverrideKey =
  | 'destination'
  | 'booklore_library_id'
  | 'booklore_path_id'
  | 'email_recipients';

export const OVERRIDE_KEY_TO_SETTING_KEY: Record<OverrideKey, keyof PerUserSettings> = {
  destination: 'DESTINATION',
  booklore_library_id: 'BOOKLORE_LIBRARY_ID',
  booklore_path_id: 'BOOKLORE_PATH_ID',
  email_recipients: 'EMAIL_RECIPIENTS',
};

export const FALLBACK_OVERRIDABLE_SETTINGS = new Set<keyof PerUserSettings>([
  'DESTINATION',
  'BOOKLORE_LIBRARY_ID',
  'BOOKLORE_PATH_ID',
  'EMAIL_RECIPIENTS',
]);

export const EMPTY_OVERRIDES: Record<OverrideKey, boolean> = {
  destination: false,
  booklore_library_id: false,
  booklore_path_id: false,
  email_recipients: false,
};

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

export const normalizeAuthSource = (user: Pick<AdminUser, 'auth_source' | 'oidc_subject'>): AuthSource => {
  if (user.auth_source === 'builtin' || user.auth_source === 'oidc' || user.auth_source === 'proxy' || user.auth_source === 'cwa') {
    return user.auth_source;
  }
  return user.oidc_subject ? 'oidc' : 'builtin';
};

export const isUserActive = (user: Pick<AdminUser, 'is_active'>): boolean => user.is_active !== false;

export interface UserEditCapabilities {
  authSource: AuthSource;
  canSetPassword: boolean;
  canEditRole: boolean;
  canEditEmail: boolean;
  canEditDisplayName: boolean;
}

export const getUserEditCapabilities = (
  user: Pick<AdminUser, 'auth_source' | 'oidc_subject'>,
  oidcUseAdminGroup: boolean | undefined
): UserEditCapabilities => {
  const authSource = normalizeAuthSource(user);
  const roleManagedByOidcGroup = authSource === 'oidc' && oidcUseAdminGroup === true;

  return {
    authSource,
    canSetPassword: authSource === 'builtin',
    canEditRole: authSource === 'builtin' || (authSource === 'oidc' && !roleManagedByOidcGroup),
    canEditEmail: authSource === 'builtin' || authSource === 'proxy',
    canEditDisplayName: authSource !== 'oidc',
  };
};
