// Crosshair values for the legend. Kept outside React state so a mouse move
// re-renders only the legend (useSyncExternalStore), not the whole terminal.

export function createLegendStore() {
  let value = { data: null, hovering: false };
  const listeners = new Set();
  return {
    get: () => value,
    set: (data, hovering) => {
      value = { data, hovering };
      listeners.forEach(l => l());
    },
    subscribe: (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
  };
}
