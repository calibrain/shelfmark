import { useEffect } from 'react';

interface UseShowOnboardingDebugOptions {
  setOnboardingOpen: (value: boolean) => void;
}

declare global {
  interface Window {
    showOnboarding?: () => void;
  }
}

export const useShowOnboardingDebug = ({
  setOnboardingOpen,
}: UseShowOnboardingDebugOptions): void => {
  useEffect(() => {
    window.showOnboarding = () => setOnboardingOpen(true);
    return () => {
      delete window.showOnboarding;
    };
  }, [setOnboardingOpen]);
};
