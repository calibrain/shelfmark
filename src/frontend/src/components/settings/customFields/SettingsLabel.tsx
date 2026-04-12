import { CustomSettingsFieldRendererProps } from './types';

const interpolate = (text: string): string => text.replace(/\{origin\}/g, window.location.origin);

export const SettingsLabel = ({ field }: CustomSettingsFieldRendererProps) => {
  return (
    <div className="rounded-lg bg-sky-500/20 px-3 py-2 text-sm">
      {field.label && <span className="opacity-60">{field.label} </span>}
      {field.description && (
        <code className="font-mono text-xs">{interpolate(field.description)}</code>
      )}
    </div>
  );
};
