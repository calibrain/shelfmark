import { ReactNode } from 'react';

interface SettingsSubpageProps {
  children: ReactNode;
  hasBottomSaveBar?: boolean;
}

export const SettingsSubpage = ({
  children,
  hasBottomSaveBar = false,
}: SettingsSubpageProps) => {
  return (
    <div
      className="flex-1 overflow-y-auto p-6"
      style={{ paddingBottom: hasBottomSaveBar ? 'calc(5rem + env(safe-area-inset-bottom))' : '1.5rem' }}
    >
      {children}
    </div>
  );
};
