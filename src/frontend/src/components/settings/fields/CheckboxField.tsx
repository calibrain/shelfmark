import { CheckboxFieldConfig } from '../../../types/settings';
import { ToggleSwitch } from '../../shared';

interface CheckboxFieldProps {
  field: CheckboxFieldConfig;
  value: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean; // Override for dynamic disabled state
}

export const CheckboxField = ({ field: _field, value, onChange, disabled }: CheckboxFieldProps) => {
  return (
    <ToggleSwitch checked={value} onChange={onChange} disabled={disabled} />
  );
};
