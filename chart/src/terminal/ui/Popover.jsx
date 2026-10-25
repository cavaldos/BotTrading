import { useEffect, useLayoutEffect, useRef, useState } from 'react';

/**
 * Menu anchored to a button. Closes on outside pointerdown and on Escape
 * (focus goes back to the anchor). Opens instantly: menus are used constantly.
 */
export function Popover({ anchorRef, open, onClose, children, align = 'left', placement = 'bottom', className = '', label }) {
  const popRef = useRef(null);
  const [pos, setPos] = useState(null);

  useLayoutEffect(() => {
    if (!open || !anchorRef.current) return;
    const r = anchorRef.current.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const next = {};
    if (placement === 'right') {
      next.left = Math.min(r.right + 6, vw - 200);
      next.top = Math.min(r.top, vh - 200);
    } else {
      next.top = r.bottom + 4;
      if (align === 'right') next.right = Math.max(8, vw - r.right);
      else next.left = Math.max(8, Math.min(r.left, vw - 220));
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect -- position must be measured after layout
    setPos(next);
  }, [open, anchorRef, align, placement]);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (popRef.current?.contains(e.target) || anchorRef.current?.contains(e.target)) return;
      onClose();
    };
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
        anchorRef.current?.focus();
      }
    };
    document.addEventListener('pointerdown', onDown, true);
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('pointerdown', onDown, true);
      document.removeEventListener('keydown', onKey, true);
    };
  }, [open, onClose, anchorRef]);

  useEffect(() => {
    if (open && pos) {
      const first = popRef.current?.querySelector('[aria-checked="true"], .is-active, button');
      first?.focus({ preventScroll: true });
    }
  }, [open, pos]);

  if (!open || !pos) return null;
  return (
    <div ref={popRef} className={`tv-pop ${className}`} style={pos} role="menu" aria-label={label}>
      {children}
    </div>
  );
}
