import { useCallback, useSyncExternalStore } from 'react';

// Screen-size queries shared by the terminal (keep in sync with terminal.css)
export const NARROW = '(max-width: 900px)';
export const PHONE = '(max-width: 600px)';
// Phones held sideways: little height for the chart
export const SHORT = '(max-height: 500px)';

export function matches(query) {
  try {
    return window.matchMedia(query).matches;
  } catch {
    return false;
  }
}

/** True while the media query matches; re-renders when it flips (rotation, resize). */
export function useMediaQuery(query) {
  const subscribe = useCallback((onChange) => {
    let mq;
    try {
      mq = window.matchMedia(query);
    } catch {
      return () => {};
    }
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, [query]);
  return useSyncExternalStore(subscribe, () => matches(query), () => false);
}

function subscribeResize(onChange) {
  window.addEventListener('resize', onChange);
  return () => window.removeEventListener('resize', onChange);
}

/** Window inner height, kept current on resize and rotation. */
export function useWindowHeight() {
  return useSyncExternalStore(subscribeResize, () => window.innerHeight, () => 800);
}
