import { useMemo, useRef, useState } from 'react';
import { Dialog } from './Dialog';
import { IconSearch } from './icons';
import { INDICATORS } from '../indicators/registry';
import { SMC_OVERLAYS } from '../lib/smcOverlays';

export default function IndicatorsDialog({ active, onAdd, smcVisibility, onToggleSmc, smcCounts, smcNote, onClose }) {
  const [query, setQuery] = useState('');
  const [section, setSection] = useState('technicals');
  const inputRef = useRef(null);
  const q = query.trim().toLowerCase();

  const countByType = useMemo(() => {
    const m = {};
    active.forEach(i => { m[i.type] = (m[i.type] || 0) + 1; });
    return m;
  }, [active]);

  const technicals = INDICATORS.filter(d => !q || `${d.name} ${d.short} ${d.category}`.toLowerCase().includes(q));
  const smc = SMC_OVERLAYS.filter(o => !q || `${o.label} ${o.description}`.toLowerCase().includes(q));
  const showTech = q ? true : section === 'technicals';
  const showSmc = q ? true : section === 'smc';

  return (
    <Dialog title="Indicators" onClose={onClose} initialFocusRef={inputRef}>
      <label className="tv-search">
        <IconSearch size={18} />
        <input ref={inputRef} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search" aria-label="Search indicators" spellCheck={false} />
      </label>
      <div className="tv-dialog-cols">
        <div className="tv-dialog-nav" role="tablist" aria-orientation="vertical">
          <button type="button" role="tab" aria-selected={section === 'technicals'} aria-current={!q && section === 'technicals'} onClick={() => { setSection('technicals'); setQuery(''); }}>
            Technicals
          </button>
          <button type="button" role="tab" aria-selected={section === 'smc'} aria-current={!q && section === 'smc'} onClick={() => { setSection('smc'); setQuery(''); }}>
            SMC overlays
          </button>
        </div>
        <div className="tv-dialog-list">
          {showTech && (
            <>
              {q && <div className="tv-menu-title">Technicals</div>}
              {technicals.map(d => {
                const n = countByType[d.type] || 0;
                const single = d.kind === 'volume';
                return (
                  <button
                    key={d.type}
                    type="button"
                    className={`tv-ind-item ${n ? 'is-on' : ''}`}
                    onClick={() => onAdd(d.type)}
                    disabled={single && n > 0}
                    title={single && n > 0 ? 'Already on the chart' : `Add ${d.name}`}
                  >
                    <span className="tv-ind-name">{d.name}</span>
                    <span className="tv-ind-meta">
                      {n > 0 ? `${n} on chart · ` : ''}
                      {d.kind === 'pane' ? 'Own pane' : 'On price'}
                    </span>
                  </button>
                );
              })}
            </>
          )}
          {showSmc && (
            <>
              {q && <div className="tv-menu-title">SMC overlays</div>}
              {smcNote && <div className="tv-empty" style={{ textAlign: 'left', padding: '10px 20px' }}>{smcNote}</div>}
              {smc.map(o => {
                const on = !!smcVisibility[o.key];
                const count = smcCounts?.[o.key];
                return (
                  <button key={o.key} type="button" role="switch" aria-checked={on} className={`tv-ind-item ${on ? 'is-on' : ''}`} onClick={() => onToggleSmc(o.key)}>
                    <span className={`tv-check ${on ? 'is-on' : ''}`} aria-hidden="true">
                      {on && <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3"><path d="M5 12l5 5 9-10" /></svg>}
                    </span>
                    <span style={{ width: 8, height: 8, borderRadius: 2, background: o.color, flex: '0 0 8px' }} aria-hidden="true" />
                    <span>
                      <span className="tv-ind-name">{o.label}</span>
                      <br />
                      <span className="tv-ind-desc">{o.description}</span>
                    </span>
                    {Number.isFinite(count) && <span className="tv-ind-meta num">{count.toLocaleString()}</span>}
                  </button>
                );
              })}
            </>
          )}
          {showTech && showSmc && technicals.length === 0 && smc.length === 0 && (
            <div className="tv-empty">Nothing matches “{query}”.</div>
          )}
        </div>
      </div>
    </Dialog>
  );
}
