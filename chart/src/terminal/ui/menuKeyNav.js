/** Arrow-key navigation between the buttons of a menu. */
export function menuKeyNav(e) {
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
  const items = [...e.currentTarget.querySelectorAll('button:not(:disabled)')];
  const idx = items.indexOf(document.activeElement);
  const next = e.key === 'ArrowDown' ? items[(idx + 1) % items.length] : items[(idx - 1 + items.length) % items.length];
  next?.focus();
  e.preventDefault();
}
