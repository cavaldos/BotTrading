import { useEffect, useRef, useState } from 'react';
import { Popover } from './Popover';
import { menuKeyNav } from './menuKeyNav';
import { CHART_TYPES } from '../chart/ChartEngine';
import { SOURCES } from '../data/useTerminalData';
import { tfGroup } from '../lib/timeframes';
import { formatAgo } from '../lib/format';
import {
  IconSearch, IconChevronDown, IconIndicators, IconCamera, IconCandles, IconHollow, IconHeikin,
  IconBars, IconLine, IconArea, IconRefresh, IconUndo,
} from './icons';

const CHART_TYPE_ICONS = {
  candles: IconCandles,
  hollow: IconHollow,
  heikin: IconHeikin,
  bars: IconBars,
  line: IconLine,
  area: IconArea,
};

const QUICK = ['1m', '5m', '15m', '1h', '4h', '1D'];

function TimeframeMenu({ options, value, onChange }) {
  const ref = useRef(null);
  const [open, setOpen] = useState(false);
  const groups = {};
  options.forEach(o => {
    const g = tfGroup(o.tf);
    (groups[g] = groups[g] || []).push(o);
  });

  // Quick buttons: files that exist plus common TFs, always including the current one
  const quick = options.filter(o => o.native || QUICK.includes(o.tf)).slice(0, 6);
  if (value && !quick.some(o => o.tf === value)) {
    const cur = options.find(o => o.tf === value);
    if (cur) quick.push(cur);
  }

  return (
    <>
      <div className="tv-tf-quick" role="group" aria-label="Timeframe">
        {quick.map(o => (
          <button
            key={o.tf}
            type="button"
            className={`tv-btn ${o.tf === value ? '' : 'tv-hide-phone'}`}
            aria-pressed={o.tf === value}
            onClick={() => onChange(o.tf)}
            title={o.native ? `${o.tf} (data file)` : `${o.tf} (resampled from ${o.baseTf})`}
          >
            {o.tf}
          </button>
        ))}
        <button
          ref={ref}
          type="button"
          className="tv-btn"
          onClick={() => setOpen(o => !o)}
          aria-haspopup="menu"
          aria-expanded={open}
          aria-label="All timeframes"
          title="All timeframes"
        >
          {!quick.some(o => o.tf === value) && <span className="num">{value || '—'}</span>}
          <IconChevronDown size={14} />
        </button>
      </div>
      <Popover anchorRef={ref} open={open} onClose={() => setOpen(false)} label="Timeframes">
        <div onKeyDown={menuKeyNav}>
          {Object.entries(groups).map(([g, list]) => (
            <div key={g}>
              <div className="tv-menu-title">{g}</div>
              {list.map(o => (
                <button
                  key={o.tf}
                  type="button"
                  role="menuitemradio"
                  aria-checked={o.tf === value}
                  className={`tv-menu-item ${o.tf === value ? 'is-active' : ''}`}
                  onClick={() => { onChange(o.tf); setOpen(false); }}
                >
                  {o.tf}
                  <small>{o.native ? 'data file' : `from ${o.baseTf}`}</small>
                </button>
              ))}
            </div>
          ))}
          {options.length === 0 && <div className="tv-empty">No timeframes</div>}
        </div>
      </Popover>
    </>
  );
}

function ChartTypeMenu({ value, onChange }) {
  const ref = useRef(null);
  const [open, setOpen] = useState(false);
  const Icon = CHART_TYPE_ICONS[value] || IconCandles;
  const label = CHART_TYPES.find(t => t.id === value)?.label;
  return (
    <>
      <button
        ref={ref}
        type="button"
        className="tv-btn tv-btn--icon"
        data-tip={label}
        onClick={() => setOpen(o => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Chart type: ${label}`}
      >
        <Icon size={20} />
      </button>
      <Popover anchorRef={ref} open={open} onClose={() => setOpen(false)} label="Chart type">
        <div onKeyDown={menuKeyNav}>
          {CHART_TYPES.map(t => {
            const TIcon = CHART_TYPE_ICONS[t.id];
            return (
              <button
                key={t.id}
                type="button"
                role="menuitemradio"
                aria-checked={t.id === value}
                className={`tv-menu-item ${t.id === value ? 'is-active' : ''}`}
                onClick={() => { onChange(t.id); setOpen(false); }}
              >
                <TIcon size={20} />
                {t.label}
              </button>
            );
          })}
        </div>
      </Popover>
    </>
  );
}

function LiveStatus({ lastUpdate, autoRefresh, onToggle, onRefresh }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => tick(n => n + 1), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <>
      <button
        type="button"
        className={`tv-live-badge ${autoRefresh ? '' : 'is-paused'}`}
        onClick={onToggle}
        aria-pressed={autoRefresh}
        title={autoRefresh ? 'Auto refresh every 5s (click to pause)' : 'Auto refresh paused (click to resume)'}
      >
        <span className="tv-live-dot" />
        {autoRefresh ? 'LIVE' : 'PAUSED'}
      </button>
      <span className="tv-status tv-hide-narrow" aria-live="off">Updated {formatAgo(lastUpdate)}</span>
      <button type="button" className="tv-btn tv-btn--icon" data-tip="Refresh now" aria-label="Refresh now" onClick={onRefresh}>
        <IconRefresh size={17} />
      </button>
    </>
  );
}

export default function TopBar({
  symbol, tf, tfOptions, onTf, chartType, onChartType, source, onSource,
  onOpenSymbol, onOpenIndicators, live, onScreenshot, onUndo, canUndo,
}) {
  return (
    <header className="tv-topbar" aria-label="Chart toolbar">
      <button type="button" className="tv-btn tv-symbol-btn" onClick={onOpenSymbol} aria-label={`Symbol ${symbol || ''}. Search symbols`} title="Symbol search">
        <IconSearch size={16} />
        {symbol || 'Symbol'}
      </button>
      <span className="tv-sep" />
      <TimeframeMenu options={tfOptions} value={tf} onChange={onTf} />
      <span className="tv-sep" />
      <ChartTypeMenu value={chartType} onChange={onChartType} />
      <span className="tv-sep" />
      <button type="button" className="tv-btn" onClick={onOpenIndicators} title="Indicators (/)">
        <IconIndicators size={18} />
        <span className="tv-hide-phone">Indicators</span>
      </button>
      <span className="tv-sep" />
      <div className="tv-segment" role="group" aria-label="Data source">
        {Object.values(SOURCES).map(s => (
          <button key={s.id} type="button" aria-pressed={source === s.id} onClick={() => onSource(s.id)}>
            {s.label}
          </button>
        ))}
      </div>
      <span className="tv-spacer" />
      {live && <LiveStatus {...live} />}
      <button type="button" className="tv-btn tv-btn--icon" data-tip="Undo drawing (Ctrl+Z)" aria-label="Undo drawing" onClick={onUndo} disabled={!canUndo}>
        <IconUndo size={18} />
      </button>
      <button type="button" className="tv-btn tv-btn--icon" data-tip="Take a snapshot" aria-label="Download chart image" onClick={onScreenshot}>
        <IconCamera size={19} />
      </button>
    </header>
  );
}
