import { useEffect, useRef, useState } from 'react';
import { Popover } from './Popover';
import { COLOR_SWATCHES, TOOL_DEFS } from '../drawings/tools';
import { IconLock, IconUnlock, IconTrash, IconCopy, IconText } from './icons';

const WIDTHS = [1, 2, 3, 4];
const LINE_STYLES = [
  { id: 'solid', label: 'Solid', dash: '' },
  { id: 'dashed', label: 'Dashed', dash: '6 4' },
  { id: 'dotted', label: 'Dotted', dash: '1.5 3' },
];

/** Floating toolbar for the selected drawing (color, width, style, lock, clone, delete). */
export function DrawingBar({ drawing, onStyle, onLock, onClone, onDelete, onEditText, onTrade }) {
  const colorRef = useRef(null);
  const widthRef = useRef(null);
  const [menu, setMenu] = useState(null);
  const s = drawing.style || {};
  const hasLine = !['text', 'long', 'short', 'measure'].includes(drawing.type);
  const isPosition = drawing.type === 'long' || drawing.type === 'short';

  return (
    <div className="tv-draw-bar" role="toolbar" aria-label={`${TOOL_DEFS[drawing.type]?.label} options`}>
      {isPosition && onTrade && (
        <button type="button" className="tv-btn tv-btn--trade" onClick={onTrade} data-tip="Open an order ticket from this position">
          Trade
        </button>
      )}
      <button ref={colorRef} type="button" className="tv-btn tv-btn--icon" onClick={() => setMenu(m => (m === 'color' ? null : 'color'))} aria-label="Color" data-tip="Color" aria-haspopup="menu" aria-expanded={menu === 'color'}>
        <span className="tv-swatch" style={{ background: s.color, width: 18, height: 18 }} />
      </button>
      {hasLine && (
        <button ref={widthRef} type="button" className="tv-btn" onClick={() => setMenu(m => (m === 'width' ? null : 'width'))} aria-label="Line width and style" data-tip="Line" aria-haspopup="menu" aria-expanded={menu === 'width'}>
          <svg width="22" height="12" aria-hidden="true">
            <line x1="1" y1="6" x2="21" y2="6" stroke="currentColor" strokeWidth={s.width || 1} strokeDasharray={LINE_STYLES.find(l => l.id === s.lineStyle)?.dash} />
          </svg>
          <span className="num">{s.width || 1}px</span>
        </button>
      )}
      {drawing.type === 'text' && (
        <button type="button" className="tv-btn tv-btn--icon" onClick={onEditText} aria-label="Edit text" data-tip="Edit text">
          <IconText size={17} />
        </button>
      )}
      <button type="button" className="tv-btn tv-btn--icon" onClick={onClone} aria-label="Clone" data-tip="Clone">
        <IconCopy size={17} />
      </button>
      <button type="button" className="tv-btn tv-btn--icon" aria-pressed={!!drawing.locked} onClick={onLock} aria-label={drawing.locked ? 'Unlock' : 'Lock'} data-tip={drawing.locked ? 'Unlock' : 'Lock'}>
        {drawing.locked ? <IconLock size={17} /> : <IconUnlock size={17} />}
      </button>
      <button type="button" className="tv-btn tv-btn--icon" onClick={onDelete} aria-label="Remove" data-tip="Remove" data-kbd="Del">
        <IconTrash size={17} />
      </button>

      <Popover anchorRef={colorRef} open={menu === 'color'} onClose={() => setMenu(null)} label="Colors">
        <div className="tv-swatch-grid">
          {COLOR_SWATCHES.map(c => (
            <button
              key={c}
              type="button"
              className="tv-swatch"
              style={{ background: c }}
              aria-label={c}
              aria-pressed={c === s.color}
              onClick={() => {
                const patch = { color: c };
                if (drawing.type === 'rect') patch.fill = `${c}33`;
                onStyle(patch);
                setMenu(null);
              }}
            />
          ))}
        </div>
      </Popover>
      <Popover anchorRef={widthRef} open={menu === 'width'} onClose={() => setMenu(null)} label="Line">
        <div className="tv-menu-title">Width</div>
        {WIDTHS.map(w => (
          <button key={w} type="button" role="menuitemradio" aria-checked={w === s.width} className={`tv-menu-item ${w === s.width ? 'is-active' : ''}`} onClick={() => { onStyle({ width: w }); setMenu(null); }}>
            <svg width="40" height="10" aria-hidden="true"><line x1="0" y1="5" x2="40" y2="5" stroke="currentColor" strokeWidth={w} /></svg>
            {w}px
          </button>
        ))}
        <div className="tv-menu-sep" />
        <div className="tv-menu-title">Style</div>
        {LINE_STYLES.map(l => (
          <button key={l.id} type="button" role="menuitemradio" aria-checked={l.id === (s.lineStyle || 'solid')} className={`tv-menu-item ${l.id === (s.lineStyle || 'solid') ? 'is-active' : ''}`} onClick={() => { onStyle({ lineStyle: l.id }); setMenu(null); }}>
            <svg width="40" height="10" aria-hidden="true"><line x1="0" y1="5" x2="40" y2="5" stroke="currentColor" strokeWidth="2" strokeDasharray={l.dash} /></svg>
            {l.label}
          </button>
        ))}
      </Popover>
    </div>
  );
}

/** Inline editor for text drawings, placed at the text anchor. */
export function TextEditor({ initial, x, y, onCommit, onCancel }) {
  const ref = useRef(null);
  const done = useRef(false);
  const [value, setValue] = useState(initial || '');
  const commit = () => {
    if (done.current) return;
    done.current = true;
    onCommit(value);
  };
  const cancel = () => {
    if (done.current) return;
    done.current = true;
    onCancel();
  };
  useEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);
  return (
    <textarea
      ref={ref}
      className="tv-text-editor"
      style={{ left: x, top: Math.max(4, y - 40) }}
      value={value}
      rows={2}
      aria-label="Drawing text"
      placeholder="Type text, Enter to save"
      onChange={(e) => setValue(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          commit();
        } else if (e.key === 'Escape') {
          e.preventDefault();
          cancel();
        }
      }}
    />
  );
}
