import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { menuKeyNav } from './menuKeyNav';

/**
 * Right-click menu on the chart at the pointer. items: [{ label, hint, onSelect, disabled, tone }]
 * ('sep' for a separator). Closes on outside press, Escape, scroll or resize.
 */
export default function ChartMenu({ x, y, title, items, onClose }) {
  const ref = useRef(null);
  const [pos, setPos] = useState({ left: x, top: y });

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    // Keep the menu on screen once its size is known
    setPos({
      left: Math.max(8, Math.min(x, window.innerWidth - r.width - 8)),
      top: Math.max(8, Math.min(y, window.innerHeight - r.height - 8)),
    });
  }, [x, y]);

  useEffect(() => {
    ref.current?.querySelector('button:not(:disabled)')?.focus({ preventScroll: true });
    const onDown = (e) => {
      if (!ref.current?.contains(e.target)) onClose();
    };
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener('pointerdown', onDown, true);
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('wheel', onClose, { passive: true });
    window.addEventListener('resize', onClose);
    return () => {
      document.removeEventListener('pointerdown', onDown, true);
      document.removeEventListener('keydown', onKey, true);
      window.removeEventListener('wheel', onClose);
      window.removeEventListener('resize', onClose);
    };
  }, [onClose]);

  return (
    <div ref={ref} className="tv-pop tv-chart-menu" style={pos} role="menu" aria-label={title} onKeyDown={menuKeyNav}>
      {title && <div className="tv-menu-title">{title}</div>}
      {items.map((item, i) => (item === 'sep' ? <div key={`sep${i}`} className="tv-menu-sep" /> : (
        <button
          key={item.label}
          type="button"
          role="menuitem"
          className={`tv-menu-item ${item.tone ? `tv-menu-item--${item.tone}` : ''}`}
          disabled={item.disabled}
          onClick={() => {
            onClose();
            item.onSelect?.();
          }}
        >
          {item.label}
          {item.hint && <small className="num">{item.hint}</small>}
        </button>
      )))}
    </div>
  );
}
