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
    // Single measurement path, so a resize that removes the active tab also
    // resets the indicator instead of leaving it stranded.
    const updateIndicator = () => {
      const activeButton = tabRefs.current[activeTab];
      if (!activeButton) {
        setTabIndicatorStyle({ left: 0, width: 0 });
        return;
      }

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
    window.addEventListener('resize', updateIndicator);

    return () => {
      window.removeEventListener('resize', updateIndicator);
    };
  }, [activeTab, tabRefs, tabsDependency]);

  return tabIndicatorStyle;
}
