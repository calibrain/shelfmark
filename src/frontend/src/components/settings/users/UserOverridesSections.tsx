import { Fragment, ReactElement } from 'react';
import { DeliveryPreferencesResponse } from '../../../services/api';
import { ActionResult } from '../../../types/settings';
import { SettingsTab } from '../../../types/settings';
import { UserNotificationOverridesSection } from './UserNotificationOverridesSection';
import { UserOverridesSection } from './UserOverridesSection';
import { UserRequestPolicyOverridesSection } from './UserRequestPolicyOverridesSection';
import { UserSearchPreferencesSection } from './UserSearchPreferencesSection';
import { PerUserSettings } from './types';

export type UserOverrideScope = 'admin' | 'self';
export type UserOverrideSectionId = 'delivery' | 'search' | 'notifications' | 'requestPolicy';

interface UserOverridesSectionsProps {
  scope: UserOverrideScope;
  sections?: UserOverrideSectionId[];
  deliveryPreferences: DeliveryPreferencesResponse | null;
  searchPreferences: DeliveryPreferencesResponse | null;
  notificationPreferences: DeliveryPreferencesResponse | null;
  isUserOverridable: (key: keyof PerUserSettings) => boolean;
  userSettings: PerUserSettings;
  setUserSettings: (updater: (prev: PerUserSettings) => PerUserSettings) => void;
  usersTab?: SettingsTab;
  globalUsersSettingsValues?: Record<string, unknown>;
  onTestNotificationRoutes?: (routes: Array<Record<string, unknown>>) => Promise<ActionResult>;
}

interface UserOverrideSectionDefinition {
  id: UserOverrideSectionId;
  adminOnly: boolean;
}

interface UserOverrideSectionNode {
  id: UserOverrideSectionId;
  node: ReactElement;
}

const USER_OVERRIDE_SECTION_DEFINITIONS: UserOverrideSectionDefinition[] = [
  { id: 'delivery', adminOnly: false },
  { id: 'search', adminOnly: false },
  { id: 'notifications', adminOnly: false },
  { id: 'requestPolicy', adminOnly: true },
];

const USER_OVERRIDE_SECTION_ORDER: UserOverrideSectionId[] =
  USER_OVERRIDE_SECTION_DEFINITIONS.map((section) => section.id);
const USER_OVERRIDE_SECTION_ID_SET = new Set<UserOverrideSectionId>(USER_OVERRIDE_SECTION_ORDER);

const USER_OVERRIDE_SECTION_META: Record<UserOverrideSectionId, UserOverrideSectionDefinition> = {
  delivery: { id: 'delivery', adminOnly: false },
  search: { id: 'search', adminOnly: false },
  notifications: { id: 'notifications', adminOnly: false },
  requestPolicy: { id: 'requestPolicy', adminOnly: true },
};

export const DEFAULT_SELF_USER_OVERRIDE_SECTIONS: UserOverrideSectionId[] =
  USER_OVERRIDE_SECTION_ORDER.filter((sectionId) => !USER_OVERRIDE_SECTION_META[sectionId].adminOnly);

const isUserOverrideSectionId = (value: string): value is UserOverrideSectionId => (
  USER_OVERRIDE_SECTION_ID_SET.has(value as UserOverrideSectionId)
);

export const normalizeUserOverrideSections = (
  sections: Iterable<unknown> | null | undefined,
  scope: UserOverrideScope,
): UserOverrideSectionId[] => {
  const fallbackSections = scope === 'self'
    ? DEFAULT_SELF_USER_OVERRIDE_SECTIONS
    : USER_OVERRIDE_SECTION_ORDER;

  if (!sections) {
    return fallbackSections;
  }

  const requestedValues = Array.from(sections);
  if (requestedValues.length === 0) {
    return [];
  }

  const requestedIds = new Set<UserOverrideSectionId>();
  requestedValues.forEach((value) => {
    const normalizedValue = String(value ?? '').trim();
    if (isUserOverrideSectionId(normalizedValue)) {
      requestedIds.add(normalizedValue);
    }
  });

  if (requestedIds.size === 0) {
    return fallbackSections;
  }

  return USER_OVERRIDE_SECTION_ORDER.filter((sectionId) => {
    if (!requestedIds.has(sectionId)) {
      return false;
    }
    if (scope === 'self' && USER_OVERRIDE_SECTION_META[sectionId].adminOnly) {
      return false;
    }
    return true;
  });
};

export const UserOverridesSections = ({
  scope,
  sections,
  deliveryPreferences,
  searchPreferences,
  notificationPreferences,
  isUserOverridable,
  userSettings,
  setUserSettings,
  usersTab,
  globalUsersSettingsValues,
  onTestNotificationRoutes,
}: UserOverridesSectionsProps) => {
  const activeSections = normalizeUserOverrideSections(sections, scope);

  const sectionNodes: UserOverrideSectionNode[] = [];

  activeSections.forEach((sectionId) => {
    if (sectionId === 'delivery') {
      if (!deliveryPreferences) {
        return;
      }
      sectionNodes.push({
        id: sectionId,
        node: (
          <UserOverridesSection
            deliveryPreferences={deliveryPreferences}
            isUserOverridable={isUserOverridable}
            userSettings={userSettings}
            setUserSettings={setUserSettings}
          />
        ),
      });
      return;
    }

    if (sectionId === 'notifications') {
      if (!notificationPreferences) {
        return;
      }
      sectionNodes.push({
        id: sectionId,
        node: (
          <UserNotificationOverridesSection
            notificationPreferences={notificationPreferences}
            isUserOverridable={isUserOverridable}
            userSettings={userSettings}
            setUserSettings={setUserSettings}
            onTestNotificationRoutes={onTestNotificationRoutes}
          />
        ),
      });
      return;
    }

    if (sectionId === 'search') {
      if (!searchPreferences) {
        return;
      }
      sectionNodes.push({
        id: sectionId,
        node: (
          <UserSearchPreferencesSection
            searchPreferences={searchPreferences}
            isUserOverridable={isUserOverridable}
            userSettings={userSettings}
            setUserSettings={setUserSettings}
          />
        ),
      });
      return;
    }

    if (!usersTab || !globalUsersSettingsValues) {
      return;
    }

    sectionNodes.push({
      id: sectionId,
      node: (
        <UserRequestPolicyOverridesSection
          usersTab={usersTab}
          globalUsersSettingsValues={globalUsersSettingsValues}
          isUserOverridable={isUserOverridable}
          userSettings={userSettings}
          setUserSettings={setUserSettings}
        />
      ),
    });
  });

  if (sectionNodes.length === 0) {
    return null;
  }

  return (
    <div className="space-y-5">
      {sectionNodes.map(({ id, node }, index) => (
        <Fragment key={id}>
          {index > 0 && <div className="border-t border-[var(--border-muted)]" />}
          {node}
        </Fragment>
      ))}
    </div>
  );
};
