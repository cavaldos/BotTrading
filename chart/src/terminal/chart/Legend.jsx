import { useEffect, useState, useSyncExternalStore } from 'react';
import { INDICATOR_BY_TYPE } from '../indicators/registry';
import { formatVolume } from '../lib/format';
import { IconEye, IconEyeOff, IconSettings, IconClose, IconChevronUp, IconChevronDown } from '../ui/icons';

function StudyRow({ inst, values, format, onToggle, onSettings, onRemove }) {
  const def = INDICATOR_BY_TYPE[inst.type];
  const name = def.label(inst.inputs);
  return (
    <div className={`tv-study ${inst.visible ? '' : 'is-hidden'}`}>
      <span className="tv-study-name">{name}</span>
      <span className="tv-study-actions">
        <button type="button" onClick={() => onToggle(inst.uid)} aria-label={inst.visible ? `Hide ${def.name}` : `Show ${def.name}`} title={inst.visible ? 'Hide' : 'Show'}>
          {inst.visible ? <IconEye size={15} /> : <IconEyeOff size={15} />}
        </button>
        {def.inputs.length + def.plots.length > 0 && (
          <button type="button" onClick={() => onSettings(inst.uid)} aria-label={`${def.name} settings`} title="Settings">
            <IconSettings size={15} />
          </button>
        )}
        <button type="button" onClick={() => onRemove(inst.uid)} aria-label={`Remove ${def.name}`} title="Remove">
          <IconClose size={15} />
        </button>
      </span>
      {inst.visible && (
        <span className="tv-study-values num">
          {def.plots.map(p => {
            const style = inst.plots[p.key] || p;
            if (style.visible === false) return null;
            const v = values?.[p.key];
            const color = p.type === 'histogram' ? (v >= 0 ? style.color : (style.colorDown || p.colorDown)) : style.color;
            return <span key={p.key} style={{ color }}>{v == null ? '∅' : format(v)}</span>;
          })}
        </span>
      )}
    </div>
  );
}

export default function Legend({ store, engine, title, indicators, panesVersion, onToggle, onSettings, onRemove, volume }) {
  const { data } = useSyncExternalStore(store.subscribe, store.get);
  const [collapsed, setCollapsed] = useState(false);
  const [paneTops, setPaneTops] = useState([]);

  // Oscillator legends sit at the top of their pane
  useEffect(() => {
    if (!engine) return undefined;
    const measure = () => setPaneTops(engine.paneTops());
    const raf = requestAnimationFrame(measure);
    const ro = new ResizeObserver(measure);
    ro.observe(engine.container);
    // Pane separators can be dragged; re-measure after pointer interactions
    engine.container.addEventListener('pointerup', measure);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      engine.container.removeEventListener('pointerup', measure);
    };
  }, [engine, panesVersion, indicators.length]);

  const bar = data?.bar;
  const up = data ? data.change >= 0 : true;
  const fmt = (v) => (engine ? engine.formatPrice(v) : String(v));
  const fmtOsc = (v) => (Math.abs(v) >= 1000 ? formatVolume(v) : v.toFixed(2));

  const overlays = [];
  const panes = [];
  indicators.forEach(inst => {
    const def = INDICATOR_BY_TYPE[inst.type];
    if (!def) return;
    if (def.kind === 'pane') panes.push(inst);
    else overlays.push(inst);
  });

  return (
    <>
      <div className="tv-legend">
        <div className="tv-legend-row">
          <span className="tv-legend-title">{title}</span>
          {bar && (
            <span className={`tv-legend-ohlc num ${up ? 'tv-up' : 'tv-down'}`}>
              <span><b>O</b>{fmt(bar.open)}</span>
              <span><b>H</b>{fmt(bar.high)}</span>
              <span><b>L</b>{fmt(bar.low)}</span>
              <span><b>C</b>{fmt(bar.close)}</span>
              <span>{data.change >= 0 ? '+' : '−'}{fmt(Math.abs(data.change))} ({data.change >= 0 ? '+' : '−'}{Math.abs(data.changePct).toFixed(2)}%)</span>
            </span>
          )}
        </div>
        {!collapsed && overlays.map(inst => (
          <div className="tv-legend-row" key={inst.uid}>
            {inst.type === 'volume' ? (
              <div className={`tv-study ${inst.visible ? '' : 'is-hidden'}`}>
                <span className="tv-study-name">Vol</span>
                <span className="tv-study-actions">
                  <button type="button" onClick={() => onToggle(inst.uid)} aria-label={inst.visible ? 'Hide volume' : 'Show volume'} title={inst.visible ? 'Hide' : 'Show'}>
                    {inst.visible ? <IconEye size={15} /> : <IconEyeOff size={15} />}
                  </button>
                  <button type="button" onClick={() => onRemove(inst.uid)} aria-label="Remove volume" title="Remove">
                    <IconClose size={15} />
                  </button>
                </span>
                {inst.visible && data?.volume != null && (
                  <span className={`tv-study-values num ${up ? 'tv-up' : 'tv-down'}`}>{formatVolume(data.volume)}</span>
                )}
              </div>
            ) : (
              <StudyRow inst={inst} values={data?.indicators?.[inst.uid]} format={fmt} onToggle={onToggle} onSettings={onSettings} onRemove={onRemove} />
            )}
          </div>
        ))}
        {volume}
        {(overlays.length > 0) && (
          <div className="tv-legend-row">
            <button
              type="button"
              className="tv-legend-toggle"
              onClick={() => setCollapsed(c => !c)}
              aria-label={collapsed ? 'Show indicator legend' : 'Hide indicator legend'}
              aria-expanded={!collapsed}
              title={collapsed ? 'Show indicators legend' : 'Hide indicators legend'}
            >
              {collapsed ? <IconChevronDown size={14} /> : <IconChevronUp size={14} />}
            </button>
          </div>
        )}
      </div>
      {panes.map(inst => {
        const idx = engine ? engine.paneOf(inst.uid) : 0;
        const top = paneTops[idx];
        if (top == null || idx === 0) return null;
        return (
          <div key={inst.uid} className="tv-pane-legend" style={{ top: top + 4 }}>
            <StudyRow inst={inst} values={data?.indicators?.[inst.uid]} format={fmtOsc} onToggle={onToggle} onSettings={onSettings} onRemove={onRemove} />
          </div>
        );
      })}
    </>
  );
}
