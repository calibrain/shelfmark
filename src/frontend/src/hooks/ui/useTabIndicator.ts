import { useLayoutEffect, useState, type MutableRefObject } from 'react';

interface TabIndicatorStyle {
  left: number;
  width: number;
}

// One shared instance, so the no-active-tab path below can set it repeatedly and React
// bails out on reference equality instead of re-rendering on every resize event.
const HIDDEN_INDICATOR: TabIndicatorStyle = { left: 0, width: 0 };

export function useTabIndicator(
  tabRefs: MutableRefObject<Record<string, HTMLButtonElement | null>>,
  activeTab: string,
  tabsDependency: unknown,
): TabIndicatorStyle {
  const [tabIndicatorStyle, setTabIndicatorStyle] = useState<TabIndicatorStyle>(HIDDEN_INDICATOR);

  useLayoutEffect(() => {
    // Single measurement path, so a resize that removes the active tab also
    // resets the indicator instead of leaving it stranded.
    const updateIndicator = () => {
      const activeButton = tabRefs.current[activeTab];
      if (!activeButton) {
        // The shared constant, not a fresh literal: this path now runs on every resize
        // event, and a new object would never be Object.is-equal to the current state,
        // so React would re-render on every frame of a window drag for an unchanged value.
        setTabIndicatorStyle(HIDDEN_INDICATOR);
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
    // `tabsDependency` is never read here - it exists only to re-run the measurement when
    // the tab set changes (callers pass `allTabs` / `showRequestsTab`). The buttons move
    // when tabs are added or removed, so without it the indicator sits under the old one.
    // oxlint-disable-next-line react/exhaustive-effect-dependencies
  }, [activeTab, tabRefs, tabsDependency]);

  return tabIndicatorStyle;
}
