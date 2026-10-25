import { useEffect, useId, useRef } from 'react';
import { IconClose } from './icons';

/** Modal dialog: Escape and backdrop click close it, focus returns to the opener. */
export function Dialog({ title, onClose, children, size, footer, initialFocusRef }) {
  const titleId = useId();
  const dialogRef = useRef(null);

  useEffect(() => {
    const opener = document.activeElement;
    const target = initialFocusRef?.current || dialogRef.current?.querySelector('input, select, button');
    target?.focus({ preventScroll: true });
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      } else if (e.key === 'Tab' && dialogRef.current) {
        // Keep focus inside the dialog
        const items = [...dialogRef.current.querySelectorAll('button, input, select, textarea, [tabindex="0"]')]
          .filter(el => !el.disabled);
        if (!items.length) return;
        const first = items[0];
        const last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          last.focus();
          e.preventDefault();
        } else if (!e.shiftKey && document.activeElement === last) {
          first.focus();
          e.preventDefault();
        }
      }
    };
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      if (opener && typeof opener.focus === 'function') opener.focus({ preventScroll: true });
    };
  }, [onClose, initialFocusRef]);

  return (
    <div className="tv-overlay" onPointerDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={dialogRef} className={`tv-dialog ${size ? `tv-dialog--${size}` : ''}`} role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <div className="tv-dialog-head">
          <h2 id={titleId}>{title}</h2>
          <button type="button" className="tv-btn tv-btn--icon" onClick={onClose} aria-label="Close">
            <IconClose size={20} />
          </button>
        </div>
        {children}
        {footer && <div className="tv-dialog-foot">{footer}</div>}
      </div>
    </div>
  );
}
