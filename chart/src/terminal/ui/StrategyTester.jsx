import { useEffect, useMemo, useRef, useState } from 'react';
import { createChart, BaselineSeries, LineStyle } from 'lightweight-charts';
import { computeStats, equityCurve } from '../lib/tradeStats';
import { formatDateTime, formatDuration, formatPrice, formatSigned } from '../lib/format';
import { THEME } from '../chart/ChartEngine';
import { IconChevronDown, IconChevronUp, IconMaximize, IconMinimize, IconSort } from './icons';

const fmtR = (v) => formatSigned(v, 2, ' R');
const fmtPct = (v) => (Number.isFinite(v) ? `${v.toFixed(2)}%` : '—');
const fmtNum = (v, d = 2) => (Number.isFinite(v) ? v.toFixed(d) : v === Infinity ? '∞' : '—');
const tone = (v) => (!Number.isFinite(v) || v === 0 ? '' : v > 0 ? 'tv-up' : 'tv-down');

function EquityChart({ points }) {
  const hostRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);

  useEffect(() => {
    const chart = createChart(hostRef.current, {
      autoSize: true,
      layout: { background: { type: 'solid', color: THEME.bg }, textColor: THEME.text, fontFamily: THEME.font, fontSize: 11 },
      grid: { vertLines: { visible: false }, horzLines: { color: THEME.grid } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true },
      crosshair: { vertLine: { labelBackgroundColor: THEME.crosshairLabel }, horzLine: { labelBackgroundColor: THEME.crosshairLabel } },
      handleScroll: false,
      handleScale: false,
    });
    const series = chart.addSeries(BaselineSeries, {
      baseValue: { type: 'price', price: 0 },
      topLineColor: THEME.up,
      topFillColor1: 'rgba(8, 153, 129, 0.28)',
      topFillColor2: 'rgba(8, 153, 129, 0.02)',
      bottomLineColor: THEME.down,
      bottomFillColor1: 'rgba(242, 54, 69, 0.02)',
      bottomFillColor2: 'rgba(242, 54, 69, 0.28)',
      lineWidth: 2,
      priceFormat: { type: 'custom', formatter: (v) => `${v.toFixed(1)}R` },
    });
    series.createPriceLine({ price: 0, color: 'rgba(120, 123, 134, 0.5)', lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false });
    chartRef.current = chart;
    seriesRef.current = series;
    return () => chart.remove();
  }, []);

  useEffect(() => {
    seriesRef.current?.setData(points);
    chartRef.current?.timeScale().fitContent();
  }, [points]);

  return (
    <div className="tv-equity" aria-label="Equity curve in R">
      <div ref={hostRef} className="tv-equity-host" />
    </div>
  );
}

function Overview({ stats, points }) {
  const s = stats.all;
  return (
    <div className="tv-overview">
      <div className="tv-kpis">
        <div className="tv-kpi">
          <div className="tv-kpi-label">Net profit (after commission)</div>
          <div className={`tv-kpi-value num ${tone(s.netR)}`}>{fmtR(s.netR)}</div>
          <div className="tv-kpi-sub num">Gross {fmtR(s.grossNetR)} · fees {fmtNum(s.commissionR)} R</div>
        </div>
        <div className="tv-kpi">
          <div className="tv-kpi-label">Total closed trades</div>
          <div className="tv-kpi-value num">{s.closed}</div>
          <div className="tv-kpi-sub num">{s.open ? `${s.open} open` : `${stats.long.closed} long · ${stats.short.closed} short`}</div>
        </div>
        <div className="tv-kpi">
          <div className="tv-kpi-label">Percent profitable</div>
          <div className="tv-kpi-value num">{fmtPct(s.winRate)}</div>
          <div className="tv-kpi-sub num">{s.wins} / {s.closed}</div>
        </div>
        <div className="tv-kpi">
          <div className="tv-kpi-label">Profit factor</div>
          <div className={`tv-kpi-value num ${s.profitFactor >= 1 ? 'tv-up' : s.profitFactor != null ? 'tv-down' : ''}`}>{fmtNum(s.profitFactor, 3)}</div>
          <div className="tv-kpi-sub num">avg RR {fmtNum(s.avgRR)}</div>
        </div>
        <div className="tv-kpi">
          <div className="tv-kpi-label">Max drawdown</div>
          <div className="tv-kpi-value num tv-down">{fmtNum(s.maxDrawdown)} R</div>
          <div className="tv-kpi-sub num">max loss streak {s.maxConsecLosses}</div>
        </div>
        <div className="tv-kpi">
          <div className="tv-kpi-label">Avg trade</div>
          <div className={`tv-kpi-value num ${tone(s.avgR)}`}>{fmtR(s.avgR)}</div>
          <div className="tv-kpi-sub num">avg time {formatDuration(s.avgDuration)}</div>
        </div>
      </div>
      {points.length > 1 ? <EquityChart points={points} /> : <div className="tv-empty">Not enough closed trades for an equity curve.</div>}
    </div>
  );
}

const SUMMARY_ROWS = [
  ['Net profit (after commission)', 'netR', fmtR, true],
  ['Gross profit before commission', 'grossNetR', fmtR, true],
  ['Commission paid', 'commissionR', (v) => `${fmtNum(v)} R`],
  ['Net price move (before commission)', 'netPct', (v) => formatSigned(v, 2, '%'), true],
  ['Sum of winning trades', 'grossProfit', (v) => `${fmtNum(v)} R`],
  ['Sum of losing trades', 'grossLoss', (v) => `${fmtNum(v)} R`],
  ['Profit factor', 'profitFactor', (v) => fmtNum(v, 3)],
  ['Max drawdown', 'maxDrawdown', (v) => `${fmtNum(v)} R`],
  ['Max run-up', 'maxRunUp', (v) => `${fmtNum(v)} R`],
  ['Total closed trades', 'closed', (v) => String(v)],
  ['Open trades', 'open', (v) => String(v)],
  ['Winning trades', 'wins', (v) => String(v)],
  ['Losing trades', 'losses', (v) => String(v)],
  ['Percent profitable', 'winRate', fmtPct],
  ['Avg trade', 'avgR', fmtR, true],
  ['Avg winning trade', 'avgWin', fmtR, true],
  ['Avg losing trade', 'avgLoss', fmtR, true],
  ['Ratio avg win / avg loss', 'ratioAvgWinLoss', (v) => fmtNum(v, 3)],
  ['Largest winning trade', 'largestWin', fmtR, true],
  ['Largest losing trade', 'largestLoss', fmtR, true],
  ['Avg planned R:R', 'avgRR', (v) => fmtNum(v)],
  ['Max consecutive wins', 'maxConsecWins', (v) => String(v)],
  ['Max consecutive losses', 'maxConsecLosses', (v) => String(v)],
  ['Avg time in trade', 'avgDuration', formatDuration],
];

function Summary({ stats }) {
  return (
    <table className="tv-table tv-summary">
      <thead>
        <tr><th scope="col" style={{ textAlign: 'left' }}>Metric</th><th scope="col">All</th><th scope="col">Long</th><th scope="col">Short</th></tr>
      </thead>
      <tbody>
        {SUMMARY_ROWS.map(([label, key, fmt, colored]) => (
          <tr key={key}>
            <td>{label}</td>
            {['all', 'long', 'short'].map(side => {
              const v = stats[side][key];
              return <td key={side} className={`num ${colored ? tone(v) : ''}`}>{v == null ? '—' : fmt(v)}</td>;
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const COLUMNS = [
  { key: 'n', label: '#', get: r => r.n },
  { key: 'type', label: 'Type', get: r => r.type },
  { key: 'entryTime', label: 'Entry time', get: r => r.entryTimeRaw ?? r.entryTime },
  { key: 'entryPrice', label: 'Entry', get: r => r.entryPrice },
  { key: 'exitTime', label: 'Exit time', get: r => r.exitTimeRaw ?? r.exitTime },
  { key: 'exitPrice', label: 'Exit', get: r => r.exitPrice },
  { key: 'stopLoss', label: 'Stop', get: r => r.stopLoss },
  { key: 'takeProfit', label: 'Target', get: r => r.takeProfit },
  { key: 'rr', label: 'Plan R:R', get: r => r.rr },
  { key: 'r', label: 'Net R', get: r => r.r },
  { key: 'grossR', label: 'Gross R', get: r => r.grossR },
  { key: 'pct', label: 'Change %', get: r => r.pct },
  { key: 'duration', label: 'Duration', get: r => r.duration },
];

function TradesTable({ rows, selectedId, onSelect }) {
  const [sort, setSort] = useState({ key: 'n', dir: 'desc' });
  const bodyRef = useRef(null);

  const sorted = useMemo(() => {
    const col = COLUMNS.find(c => c.key === sort.key) || COLUMNS[0];
    const dir = sort.dir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
      const va = col.get(a);
      const vb = col.get(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      return (va > vb ? 1 : va < vb ? -1 : 0) * dir;
    });
  }, [rows, sort]);

  useEffect(() => {
    bodyRef.current?.querySelector('tr.is-active')?.scrollIntoView({ block: 'nearest' });
  }, [selectedId]);

  const toggle = (key) => setSort(s => ({ key, dir: s.key === key && s.dir === 'desc' ? 'asc' : 'desc' }));

  if (rows.length === 0) return <div className="tv-empty">No trades for this filter.</div>;

  return (
    <table className="tv-table">
      <thead>
        <tr>
          {COLUMNS.map(c => (
            <th key={c.key} scope="col" aria-sort={sort.key === c.key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
              <button type="button" onClick={() => toggle(c.key)}>
                {c.label}
                <IconSort size={12} />
              </button>
            </th>
          ))}
        </tr>
      </thead>
      <tbody ref={bodyRef}>
        {sorted.map(r => (
          <tr
            key={r.key}
            className={r.key === selectedId ? 'is-active' : ''}
            onClick={() => onSelect(r)}
            tabIndex={0}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(r); } }}
            aria-selected={r.key === selectedId}
          >
            <td className="num">{r.n}</td>
            <td className={r.type === 'LONG' ? 'tv-up' : 'tv-down'}>{r.type === 'LONG' ? 'Long' : 'Short'}</td>
            <td className="num">{formatDateTime(r.entryTimeRaw ?? r.entryTime)}</td>
            <td className="num">{formatPrice(r.entryPrice)}</td>
            <td className="num">{r.closed ? formatDateTime(r.exitTimeRaw ?? r.exitTime) : 'Open'}</td>
            <td className="num">{r.closed ? formatPrice(r.exitPrice) : '—'}</td>
            <td className="num">{formatPrice(r.stopLoss)}</td>
            <td className="num">{formatPrice(r.takeProfit)}</td>
            <td className="num">{fmtNum(r.rr)}</td>
            <td className={`num ${tone(r.r)}`}>{r.r == null ? '—' : fmtR(r.r)}</td>
            <td className={`num ${tone(r.grossR)}`}>{r.grossR == null ? '—' : fmtR(r.grossR)}</td>
            <td className={`num ${tone(r.pct)}`}>{r.pct == null ? '—' : formatSigned(r.pct, 2, '%')}</td>
            <td className="num">{formatDuration(r.duration)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const TABS = [
  { id: 'overview', label: 'Overview', short: 'Overview' },
  { id: 'summary', label: 'Performance Summary', short: 'Summary' },
  { id: 'trades', label: 'List of Trades', short: 'Trades' },
];

export default function StrategyTester({
  rows, allRows, open, onToggle, height, onHeight, maxHeight, tab, onTab, selectedId, onSelect, title, emptyNote, onEmptyAction,
  commission, onCommission, compact,
}) {
  const [feeDraft, setFeeDraft] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [maximized, setMaximized] = useState(false);
  const stats = useMemo(() => computeStats(allRows), [allRows]);
  const points = useMemo(() => equityCurve(allRows), [allRows]);

  const startResize = (e) => {
    if (!open) return;
    e.preventDefault();
    const startY = e.clientY;
    const startH = height;
    setDragging(true);
    setMaximized(false);
    const move = (ev) => onHeight(Math.max(140, Math.min(maxHeight, startH + (startY - ev.clientY))));
    const up = () => {
      setDragging(false);
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  const onResizeKey = (e) => {
    if (e.key === 'ArrowUp') onHeight(Math.min(maxHeight, height + 20));
    if (e.key === 'ArrowDown') onHeight(Math.max(140, height - 20));
  };

  const effective = !open ? 38 : maximized ? maxHeight : height;

  // Phones open the tester from the bottom bar, so a closed tester takes no room
  if (compact && !open) return null;

  return (
    <section className="tv-panel tv-bottom" style={{ height: effective }} aria-label="Strategy Tester">
      {open && (
        <div
          className={`tv-resize ${dragging ? 'is-dragging' : ''}`}
          onPointerDown={startResize}
          onKeyDown={onResizeKey}
          role="separator"
          aria-orientation="horizontal"
          aria-label="Resize Strategy Tester"
          aria-valuenow={Math.round(height)}
          aria-valuemin={140}
          aria-valuemax={Math.round(maxHeight)}
          tabIndex={0}
        />
      )}
      <div className="tv-bottom-head">
        <h2 className="tv-hide-phone">Strategy Tester</h2>
        <div role="tablist" aria-label="Strategy Tester views" style={{ display: 'flex' }}>
          {TABS.map(t => (
            <button
              key={t.id}
              type="button"
              role="tab"
              className="tv-tab"
              aria-selected={open && tab === t.id}
              onClick={() => { onTab(t.id); if (!open) onToggle(true); }}
            >
              {compact ? t.short : t.label}
            </button>
          ))}
        </div>
        <span className="tv-spacer" />
        <label className="tv-fee" title="Fixed cost taken from every closed trade, like fee_per_trade_r in the Python backtest">
          <span>{compact ? 'Fee R' : 'Commission (R / trade)'}</span>
          <input
            className="tv-input num"
            type="number"
            inputMode="decimal"
            min="0"
            max="10"
            step="0.05"
            value={feeDraft ?? String(commission)}
            onChange={(e) => {
              setFeeDraft(e.target.value);
              const v = Number(e.target.value);
              if (e.target.value !== '' && Number.isFinite(v) && v >= 0 && v <= 10) onCommission(v);
            }}
            onBlur={() => setFeeDraft(null)}
            aria-label="Commission in R per trade"
          />
        </label>
        <span className="tv-status tv-hide-narrow">{title}</span>
        {open && (
          <button type="button" className="tv-btn tv-btn--icon" onClick={() => setMaximized(m => !m)} aria-label={maximized ? 'Restore panel size' : 'Maximize panel'} data-tip={maximized ? 'Restore' : 'Maximize'} data-tip-side="left">
            {maximized ? <IconMinimize size={16} /> : <IconMaximize size={16} />}
          </button>
        )}
        <button type="button" className="tv-btn tv-btn--icon" onClick={() => onToggle(!open)} aria-expanded={open} aria-label={open ? 'Collapse Strategy Tester' : 'Open Strategy Tester'} data-tip={open ? 'Collapse' : 'Open'} data-tip-side="left">
          {open ? <IconChevronDown size={18} /> : <IconChevronUp size={18} />}
        </button>
      </div>
      {open && (
        <div className="tv-bottom-body" role="tabpanel">
          {emptyNote ? (
            <div className="tv-empty">
              {emptyNote}
              {onEmptyAction && (
                <div style={{ marginTop: 10 }}>
                  <button type="button" className="tv-btn tv-btn--primary" onClick={onEmptyAction} style={{ display: 'inline-flex' }}>Open backtest data</button>
                </div>
              )}
            </div>
          ) : tab === 'overview' ? (
            <Overview stats={stats} points={points} />
          ) : tab === 'summary' ? (
            <Summary stats={stats} />
          ) : (
            <TradesTable rows={rows} selectedId={selectedId} onSelect={onSelect} />
          )}
        </div>
      )}
    </section>
  );
}
