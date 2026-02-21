import { ActionResult, CustomComponentFieldConfig, SettingsTab } from '../../../types/settings';

export interface CustomSettingsFieldRendererProps {
  field: CustomComponentFieldConfig;
  tab: SettingsTab;
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
  onAction: (key: string) => Promise<ActionResult>;
  uiState: Record<string, unknown>;
  onUiStateChange: (key: string, value: unknown) => void;
  isDisabled: boolean;
  disabledReason?: string;
  authMode?: string;
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
  onRefreshOverrideSummary?: () => void;
  onRefreshAuth?: () => Promise<void>;
}

export interface CustomSettingsFieldLayout {
  takeOverTab?: boolean;
  saveBar?: {
    hasChanges?: boolean;
    isSaving?: boolean;
    onSave?: () => void | Promise<void>;
  };
}

export interface CustomSettingsFieldLayoutContext {
  field: CustomComponentFieldConfig;
  tab: SettingsTab;
  values: Record<string, unknown>;
  uiState: Record<string, unknown>;
}
