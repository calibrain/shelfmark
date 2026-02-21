import { CustomSettingsFieldRendererProps } from './types';

const interpolate = (text: string): string =>
  text.replace(/\{origin\}/g, window.location.origin);

export const SettingsLabel = ({ field }: CustomSettingsFieldRendererProps) => {
  return (
    <div className="text-sm px-3 py-2 rounded-lg bg-sky-500/20">
      {field.label && <span className="opacity-60">{field.label} </span>}
      {field.description && (
        <code className="font-mono text-xs">{interpolate(field.description)}</code>
      )}
    </div>
  );
};
