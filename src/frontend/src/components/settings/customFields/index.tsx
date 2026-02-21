import { ComponentType, ReactNode } from 'react';
import { OidcAdminHint } from './OidcAdminHint';
import { OidcEnvInfo } from './OidcEnvInfo';
import { RequestPolicyGridField } from './RequestPolicyGridField';
import { SettingsLabel } from './SettingsLabel';
import { UsersManagementField } from './UsersManagementField';
import {
  CustomSettingsFieldLayout,
  CustomSettingsFieldLayoutContext,
  CustomSettingsFieldRendererProps,
} from './types';

type CustomFieldRenderer = ComponentType<CustomSettingsFieldRendererProps>;
type CustomFieldLayoutResolver = (context: CustomSettingsFieldLayoutContext) => CustomSettingsFieldLayout;

interface CustomFieldDefinition {
  renderer: CustomFieldRenderer;
  getLayout?: CustomFieldLayoutResolver;
}

const CUSTOM_FIELD_DEFINITIONS: Record<string, CustomFieldDefinition> = {
  users_management: {
    renderer: UsersManagementField,
    getLayout: ({ uiState }) => {
      const routeKind = typeof uiState.routeKind === 'string' ? uiState.routeKind : 'list';
      const isSubpage = routeKind === 'edit-overrides';
      const onSave = typeof uiState.onSave === 'function'
        ? (uiState.onSave as () => void | Promise<void>)
        : undefined;
      return {
        takeOverTab: isSubpage,
        saveBar: isSubpage
          ? {
              hasChanges: Boolean(uiState.hasChanges),
              isSaving: Boolean(uiState.isSaving),
              onSave,
            }
          : undefined,
      };
    },
  },
  request_policy_grid: {
    renderer: RequestPolicyGridField,
  },
  settings_label: {
    renderer: SettingsLabel,
  },
  oidc_admin_hint: {
    renderer: OidcAdminHint,
  },
  oidc_env_info: {
    renderer: OidcEnvInfo,
  },
};

export const renderCustomSettingsField = (
  props: CustomSettingsFieldRendererProps
): ReactNode => {
  const definition = CUSTOM_FIELD_DEFINITIONS[props.field.component];
  const Renderer = definition?.renderer;
  if (!Renderer) {
    return (
      <p className="text-xs opacity-60">
        Unknown custom settings component: {props.field.component}
      </p>
    );
  }
  return <Renderer {...props} />;
};

export const getCustomSettingsFieldLayout = (
  context: CustomSettingsFieldLayoutContext
): CustomSettingsFieldLayout => {
  const definition = CUSTOM_FIELD_DEFINITIONS[context.field.component];
  if (!definition?.getLayout) {
    return {};
  }
  return definition.getLayout(context);
};

export type {
  CustomSettingsFieldLayout,
  CustomSettingsFieldLayoutContext,
  CustomSettingsFieldRendererProps,
} from './types';
