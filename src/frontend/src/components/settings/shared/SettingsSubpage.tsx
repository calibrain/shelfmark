import { ReactNode } from 'react';

interface SettingsSubpageProps {
  children: ReactNode;
}

export const SettingsSubpage = ({
  children,
}: SettingsSubpageProps) => {
  return (
    <div className="flex-1 overflow-y-auto p-6">
      {children}
    </div>
  );
};
