import { useMountEffect } from '../useMountEffect';

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
  useMountEffect(() => {
    window.showOnboarding = () => setOnboardingOpen(true);
    return () => {
      delete window.showOnboarding;
    };
  });
};
