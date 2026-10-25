import { useMemo, useRef, useState } from 'react';
import { Dialog } from './Dialog';
import { IconSearch } from './icons';

export default function SymbolSearch({ groups, sourceLabel, onSelect, onClose }) {
  const [query, setQuery] = useState('');
  const [focus, setFocus] = useState(0);
  const inputRef = useRef(null);
  const listRef = useRef(null);

  const list = useMemo(() => {
    const q = query.trim().toUpperCase();
    return groups.filter(g => !q || g.symbol.toUpperCase().includes(q));
  }, [groups, query]);

  const pick = (g, tf) => onSelect(g.symbol, tf);

  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const next = Math.max(0, Math.min(list.length - 1, focus + (e.key === 'ArrowDown' ? 1 : -1)));
      setFocus(next);
      listRef.current?.children[next]?.scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter' && list[focus]) {
      e.preventDefault();
      pick(list[focus]);
    }
  };

  return (
    <Dialog title="Symbol search" onClose={onClose} initialFocusRef={inputRef}>
      <label className="tv-search">
        <IconSearch size={18} />
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => { setQuery(e.target.value); setFocus(0); }}
          onKeyDown={onKeyDown}
          placeholder="Search symbol"
          aria-label="Search symbol"
          role="combobox"
          aria-expanded="true"
          aria-controls="tv-symbol-list"
          spellCheck={false}
          autoComplete="off"
        />
      </label>
      <div className="tv-dialog-list" id="tv-symbol-list" role="listbox" ref={listRef} aria-label="Symbols">
        {list.map((g, i) => (
          <div
            key={g.symbol}
            role="option"
            aria-selected={i === focus}
            className={`tv-sym-row ${i === focus ? 'is-focus' : ''}`}
            onPointerEnter={() => setFocus(i)}
          >
            <button type="button" onClick={() => pick(g)} style={{ textAlign: 'left', padding: '6px 0' }} tabIndex={-1}>
              <b>{g.symbol}</b>
              <br />
              <small>{sourceLabel}{g.meta ? ` · ${g.meta}` : ''}</small>
            </button>
            <span className="tv-sym-tfs">
              {g.tfs.map(t => (
                <button key={t.tf} type="button" onClick={() => pick(g, t.tf)} tabIndex={-1} aria-label={`${g.symbol} ${t.tf}`}>
                  <span>{t.tf}</span>
                </button>
              ))}
            </span>
          </div>
        ))}
        {list.length === 0 && <div className="tv-empty">No symbols match “{query}”.</div>}
      </div>
    </Dialog>
  );
}
