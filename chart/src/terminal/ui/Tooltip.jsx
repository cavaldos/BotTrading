import { useEffect, useRef, useState } from 'react';

/**
 * One shared tooltip for every element with data-tip (and optional
 * data-tip-side="right|left|bottom", data-kbd). Rendered position: fixed so
 * scrolling toolbars do not clip it. Shows after a short hover delay or on
 * keyboard focus.
 */
export default function Tooltip({ rootRef }) {
  const [tip, setTip] = useState(null);
  const timer = useRef(null);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return undefined;

    const place = (el) => {
      const r = el.getBoundingClientRect();
      const side = el.dataset.tipSide || 'bottom';
      const style = {};
      if (side === 'right') {
        style.left = r.right + 8;
        style.top = r.top + r.height / 2;
        style.translate = '0 -50%';
      } else if (side === 'left') {
        style.right = window.innerWidth - r.left + 8;
        style.top = r.top + r.height / 2;
        style.translate = '0 -50%';
      } else {
        style.top = r.bottom + 6;
        style.left = Math.min(Math.max(8, r.left + r.width / 2), window.innerWidth - 8);
        style.translate = r.left + r.width / 2 > window.innerWidth - 120 ? '-100% 0' : '-50% 0';
      }
      return { text: el.dataset.tip, kbd: el.dataset.kbd, style };
    };

    const show = (el, delay) => {
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setTip(place(el)), delay);
    };
    const hide = () => {
      clearTimeout(timer.current);
      setTip(null);
    };
    const onOver = (e) => {
      if (e.pointerType === 'touch') return;
      const el = e.target.closest?.('[data-tip]');
      if (el && root.contains(el)) show(el, 450);
    };
    const onOut = (e) => {
      const el = e.target.closest?.('[data-tip]');
      if (el && !el.contains(e.relatedTarget)) hide();
    };
    const onFocus = (e) => {
      const el = e.target.closest?.('[data-tip]');
      if (el && el.matches(':focus-visible')) show(el, 0);
    };

    root.addEventListener('pointerover', onOver);
    root.addEventListener('pointerout', onOut);
    root.addEventListener('focusin', onFocus);
    root.addEventListener('focusout', hide);
    root.addEventListener('pointerdown', hide, true);
    window.addEventListener('scroll', hide, true);
    return () => {
      clearTimeout(timer.current);
      root.removeEventListener('pointerover', onOver);
      root.removeEventListener('pointerout', onOut);
      root.removeEventListener('focusin', onFocus);
      root.removeEventListener('focusout', hide);
      root.removeEventListener('pointerdown', hide, true);
      window.removeEventListener('scroll', hide, true);
    };
  }, [rootRef]);

  if (!tip) return null;
  return (
    <div className="tv-tooltip" style={tip.style} role="tooltip">
      {tip.text}
      {tip.kbd && <kbd>{tip.kbd}</kbd>}
    </div>
  );
}
