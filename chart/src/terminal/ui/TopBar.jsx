import { useEffect, useRef, useState } from 'react';
import { Popover } from './Popover';
import { menuKeyNav } from './menuKeyNav';
import { CHART_TYPES } from '../chart/ChartEngine';
import { SOURCES } from '../data/useTerminalData';
import { tfGroup } from '../lib/timeframes';
import { formatAgo } from '../lib/format';
import {
  IconSearch, IconChevronDown, IconIndicators, IconCamera, IconCandles, IconHollow, IconHeikin,
  IconBars, IconLine, IconArea, IconRefresh, IconUndo, IconMore, IconCalendar,
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

function TimeframeMenu({ options, value, onChange, compact }) {
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
        {!compact && quick.map(o => (
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
          aria-label={compact ? `Timeframe ${value || ''}` : 'All timeframes'}
          title="All timeframes"
        >
          {(compact || !quick.some(o => o.tf === value)) && <span className={`num ${compact ? 'tv-tf-current' : ''}`}>{value || '—'}</span>}
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

function LiveStatus({ lastUpdate, autoRefresh, onToggle, onRefresh, compact }) {
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
      {!compact && (
        <button type="button" className="tv-btn tv-btn--icon" data-tip="Refresh now" aria-label="Refresh now" onClick={onRefresh}>
          <IconRefresh size={17} />
        </button>
      )}
    </>
  );
}

const SOURCE_SHORT = { indicators: 'SMC' };

/** Phone: the data source as a dropdown instead of the segmented switch. */
function SourceMenu({ source, onSource }) {
  const ref = useRef(null);
  const [open, setOpen] = useState(false);
  const current = SOURCES[source];
  return (
    <>
      <button
        ref={ref}
        type="button"
        className="tv-btn tv-source-btn"
        onClick={() => setOpen(o => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Data source: ${current?.label}`}
      >
        {SOURCE_SHORT[source] || current?.label}
        <IconChevronDown size={14} />
      </button>
      <Popover anchorRef={ref} open={open} onClose={() => setOpen(false)} label="Data source">
        <div onKeyDown={menuKeyNav}>
          <div className="tv-menu-title">Data source</div>
          {Object.values(SOURCES).map(s => (
            <button
              key={s.id}
              type="button"
              role="menuitemradio"
              aria-checked={s.id === source}
              className={`tv-menu-item ${s.id === source ? 'is-active' : ''}`}
              onClick={() => { onSource(s.id); setOpen(false); }}
            >
              {s.label}
            </button>
          ))}
        </div>
      </Popover>
    </>
  );
}

/** Phone: refresh, undo and snapshot in one menu so the top bar fits. */
function MoreMenu({ onRefresh, onUndo, canUndo, onScreenshot, onGoTo }) {
  const ref = useRef(null);
  const [open, setOpen] = useState(false);
  const run = (fn) => () => {
    setOpen(false);
    fn?.();
  };
  return (
    <>
      <button
        ref={ref}
        type="button"
        className="tv-btn tv-btn--icon"
        onClick={() => setOpen(o => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="More actions"
      >
        <IconMore size={20} />
      </button>
      <Popover anchorRef={ref} open={open} onClose={() => setOpen(false)} align="right" label="More actions">
        <div onKeyDown={menuKeyNav}>
          {onRefresh && (
            <button type="button" role="menuitem" className="tv-menu-item" onClick={run(onRefresh)}>
              <IconRefresh size={18} />
              Refresh now
            </button>
          )}
          <button type="button" role="menuitem" className="tv-menu-item" onClick={run(onGoTo)} disabled={!onGoTo}>
            <IconCalendar size={18} />
            Go to date
          </button>
          <button type="button" role="menuitem" className="tv-menu-item" onClick={run(onUndo)} disabled={!canUndo}>
            <IconUndo size={18} />
            Undo drawing
          </button>
          <button type="button" role="menuitem" className="tv-menu-item" onClick={run(onScreenshot)}>
            <IconCamera size={18} />
            Download chart image
          </button>
        </div>
      </Popover>
    </>
  );
}

export default function TopBar({
  symbol, tf, tfOptions, onTf, chartType, onChartType, source, onSource,
  onOpenSymbol, onOpenIndicators, onGoTo, live, onScreenshot, onUndo, canUndo, compact,
}) {
  return (
    <header className="tv-topbar" aria-label="Chart toolbar">
      <button type="button" className="tv-btn tv-symbol-btn" onClick={onOpenSymbol} aria-label={`Symbol ${symbol || ''}. Search symbols`} title="Symbol search">
        <IconSearch size={16} />
        {symbol || 'Symbol'}
      </button>
      <span className="tv-sep" />
      <TimeframeMenu options={tfOptions} value={tf} onChange={onTf} compact={compact} />
      <span className="tv-sep" />
      <ChartTypeMenu value={chartType} onChange={onChartType} />
      <span className="tv-sep" />
      <button type="button" className="tv-btn" onClick={onOpenIndicators} title="Indicators (/)" aria-label="Indicators">
        <IconIndicators size={18} />
        <span className="tv-hide-phone">Indicators</span>
      </button>
      <span className="tv-sep" />
      {compact ? (
        <SourceMenu source={source} onSource={onSource} />
      ) : (
        <div className="tv-segment" role="group" aria-label="Data source">
          {Object.values(SOURCES).map(s => (
            <button key={s.id} type="button" aria-pressed={source === s.id} onClick={() => onSource(s.id)}>
              {s.label}
            </button>
          ))}
        </div>
      )}
      <span className="tv-spacer" />
      {live && <LiveStatus {...live} compact={compact} />}
      {compact ? (
        <MoreMenu onRefresh={live?.onRefresh} onUndo={onUndo} canUndo={canUndo} onScreenshot={onScreenshot} onGoTo={onGoTo} />
      ) : (
        <>
          <button type="button" className="tv-btn tv-btn--icon" data-tip="Go to date (Alt+G)" aria-label="Go to date" onClick={onGoTo} disabled={!onGoTo}>
            <IconCalendar size={18} />
          </button>
          <button type="button" className="tv-btn tv-btn--icon" data-tip="Undo drawing (Ctrl+Z)" aria-label="Undo drawing" onClick={onUndo} disabled={!canUndo}>
            <IconUndo size={18} />
          </button>
          <button type="button" className="tv-btn tv-btn--icon" data-tip="Take a snapshot" aria-label="Download chart image" onClick={onScreenshot}>
            <IconCamera size={19} />
          </button>
        </>
      )}
    </header>
  );
}
