import { useLayoutEffect, useState, type MutableRefObject } from 'react';

interface TabIndicatorStyle {
  left: number;
  width: number;
}

export function useTabIndicator(
  tabRefs: MutableRefObject<Record<string, HTMLButtonElement | null>>,
  activeTab: string,
  tabsDependency: unknown,
): TabIndicatorStyle {
  const [tabIndicatorStyle, setTabIndicatorStyle] = useState({
    left: 0,
    width: 0,
  });

  useLayoutEffect(() => {
    const activeButton = tabRefs.current[activeTab];
    if (!activeButton) {
      return undefined;
    }

    const updateIndicator = () => {
      const containerRect = activeButton.parentElement?.getBoundingClientRect();
      const buttonRect = activeButton.getBoundingClientRect();
      if (!containerRect) {
        return;
      }

      setTabIndicatorStyle({
        left: buttonRect.left - containerRect.left,
        width: buttonRect.width,
      });
    };

    updateIndicator();

    const resizeHandler = () => {
      updateIndicator();
    };

    window.addEventListener('resize', resizeHandler);
    return () => {
      window.removeEventListener('resize', resizeHandler);
    };
  }, [activeTab, tabRefs, tabsDependency]);

  return tabIndicatorStyle;
}
