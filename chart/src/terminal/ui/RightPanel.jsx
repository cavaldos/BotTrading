import { useEffect, useRef } from 'react';
import { formatShortDateTime, formatDateTime, formatSigned, formatPrice } from '../lib/format';
import { IconChevronDown, IconChevronRight } from './icons';

// Live quotes come with the symbol list (symbols.json lastPrice / changePct): { [symbol]: { last, chg } }
function Watchlist({ groups, symbol, onSymbol, sourceId, currentQuote, quotes = {}, baseTf }) {
  const isBacktest = sourceId === 'backtest';
  // Backtest numbers are per file: show the timeframe being viewed (its source file when resampled)
  const cols = isBacktest ? [`Trades ${baseTf || ''}`.trim(), 'Win %'] : sourceId === 'live' ? ['Last', 'Chg %'] : ['Bars', 'TFs'];

  return (
    <div role="list" aria-label="Watchlist">
      <div className="tv-wl-head" aria-hidden="true">
        <span>Symbol</span>
        <span>{cols[0]}</span>
        <span>{cols[1]}</span>
      </div>
      {groups.map(g => {
        let a = '—';
        let b = '—';
        let bClass = '';
        if (isBacktest) {
          const entry = g.tfs.find(t => t.tf === baseTf);
          if (entry) {
            a = Number(entry.trades || 0).toLocaleString();
            b = entry.trades ? Number(entry.winRate || 0).toFixed(1) : '—';
          }
        } else if (sourceId === 'live') {
          const q = g.symbol === symbol && currentQuote ? currentQuote : quotes[g.symbol];
          if (q && Number.isFinite(q.last)) {
            a = formatPrice(q.last);
            b = formatSigned(q.chg, 2, '%');
            bClass = q.chg >= 0 ? 'tv-up' : 'tv-down';
          }
        } else {
          const bars = Math.max(...g.tfs.map(t => t.dataPoints || 0));
          a = bars >= 1000 ? `${Math.round(bars / 1000)}K` : String(bars);
          b = String(g.tfs.length);
        }
        return (
          <button
            key={g.symbol}
            type="button"
            role="listitem"
            className={`tv-wl-row ${g.symbol === symbol ? 'is-active' : ''}`}
            aria-current={g.symbol === symbol}
            onClick={() => onSymbol(g.symbol)}
          >
            <span className="tv-wl-sym">
              <b>{g.symbol}</b>
              <small>{g.tfs.map(t => t.tf).join(' · ')}</small>
            </span>
            <span className="num">{a}</span>
            <span className={`num ${bClass}`}>{b}</span>
          </button>
        );
      })}
      {groups.length === 0 && <div className="tv-empty">No symbols in this source.</div>}
    </div>
  );
}

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'win', label: 'Wins' },
  { id: 'loss', label: 'Losses' },
  { id: 'open', label: 'Open' },
];

function TradeListPanel({ rows, filter, onFilter, selectedId, onSelect, showOpen }) {
  const listRef = useRef(null);
  useEffect(() => {
    const el = listRef.current?.querySelector('.is-active');
    el?.scrollIntoView({ block: 'nearest' });
  }, [selectedId]);

  return (
    <>
      <div className="tv-filter" role="group" aria-label="Filter trades">
        {FILTERS.filter(f => showOpen || f.id !== 'open').map(f => (
          <button key={f.id} type="button" className="tv-chip" aria-pressed={filter === f.id} onClick={() => onFilter(f.id)}>
            {f.label}
          </button>
        ))}
      </div>
      <div ref={listRef} role="list" aria-label="Trades">
        {rows.map(r => {
          const isLong = r.type === 'LONG';
          // Live: what the bot did with this trade (sent for approval, expired, ...)
          const bot = r.bot && r.bot.status !== 'SKIPPED' ? r.bot : null;
          const botText = r.bot ? `Bot: ${r.bot.label}${r.bot.reason ? ` (${r.bot.reason})` : ''}` : undefined;
          return (
            <button
              key={r.key}
              type="button"
              role="listitem"
              className={`tv-tl-row ${r.key === selectedId ? 'is-active' : ''}`}
              onClick={() => onSelect(r)}
              aria-current={r.key === selectedId}
              title={botText}
            >
              <span className="tv-muted num">#{r.n}</span>
              <span className={`tv-tl-side ${isLong ? 'tv-up' : 'tv-down'}`}>
                {isLong ? 'Long' : 'Short'}
                {r.timeframe && <small className="tv-muted"> {r.timeframe}</small>}
                {bot && <i className="tv-tl-bot" style={{ background: bot.color }} aria-label={botText} />}
              </span>
              <span className="num tv-muted" style={{ overflow: 'hidden', textOverflow: 'ellipsis' }} title={formatDateTime(r.entryTimeRaw ?? r.entryTime)}>{formatShortDateTime(r.entryTimeRaw ?? r.entryTime)}</span>
              <span className={`num ${r.r == null ? 'tv-muted' : r.r > 0 ? 'tv-up' : 'tv-down'}`}>
                {r.r == null ? 'open' : formatSigned(r.r, 2, 'R')}
              </span>
            </button>
          );
        })}
        {rows.length === 0 && <div className="tv-empty">No trades for this filter.</div>}
      </div>
    </>
  );
}

const TITLES = { trades: 'Trades', watchlist: 'Watchlist', trade: 'Trade' };

/** Side panel; on phones (`compact`) it is a drawer under the chart. */
export default function RightPanel({ tab, onClose, watchlist, trades, trade, compact }) {
  const title = TITLES[tab] || 'Watchlist';
  return (
    <aside className="tv-panel tv-side" aria-label={title}>
      <div className="tv-side-head">
        <h2>{title}</h2>
        <button type="button" className="tv-btn tv-btn--icon" onClick={onClose} aria-label="Collapse panel" data-tip="Collapse" data-tip-side="left">
          {compact ? <IconChevronDown size={18} /> : <IconChevronRight size={18} />}
        </button>
      </div>
      <div className="tv-side-body">
        {tab === 'trade' && trade}
        {tab === 'trades' && <TradeListPanel {...trades} />}
        {tab !== 'trade' && tab !== 'trades' && <Watchlist {...watchlist} />}
      </div>
    </aside>
  );
}
