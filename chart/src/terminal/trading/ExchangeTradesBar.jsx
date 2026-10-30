import { formatDateTime, formatNumber } from '../lib/format';
import { IconRefresh } from '../ui/icons';

function clock(epochSeconds) {
  if (!Number.isFinite(epochSeconds)) return '';
  return new Date(epochSeconds * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
}

/**
 * Exchange source, bottom left of the chart: which account the trades come from,
 * how many are drawn, and why none are (not connected, no API key, error).
 */
export default function ExchangeTradesBar({ conn, view, symbol, trades, staleSince, onExchange, onConnect }) {
  const { accounts, accountsError, current, data, error, loading } = view;
  let body;
  if (conn === 'login') {
    body = (
      <>
        <span>Your exchange trades need the bot connection.</span>
        <button type="button" className="tv-btn tv-btn--primary tv-btn--xs" onClick={onConnect}>Connect</button>
      </>
    );
  } else if (conn !== 'online') {
    body = <span className="tv-muted">{conn === 'offline' ? 'Bot not reachable, retrying…' : 'Connecting to the bot…'}</span>;
  } else if (accountsError) {
    body = <span className="tv-down">{accountsError}</span>;
  } else if (!accounts) {
    body = <span className="tv-muted">Loading exchanges…</span>;
  } else if (!current) {
    body = <span className="tv-muted">No exchange API keys in the bot&apos;s .env</span>;
  } else {
    const open = trades.filter(t => !t.closed).length;
    const closed = trades.filter(t => t.closed);
    const pnl = closed.reduce((s, t) => s + (Number.isFinite(t.pnl) ? t.pnl : 0), 0);
    const wins = closed.filter(t => t.result === 'Win').length;
    body = (
      <>
        <label className="tv-visually-hidden" htmlFor="ex-bar-exchange">Exchange</label>
        <select id="ex-bar-exchange" className="tv-select tv-exbar-select" value={current.name} onChange={(e) => onExchange(e.target.value)}>
          {accounts.map(a => (
            <option key={a.name} value={a.name} disabled={!a.configured}>
              {a.label}{a.configured ? '' : ' (no API key)'}
            </option>
          ))}
        </select>
        {error ? (
          <span className="tv-down tv-exbar-text" title={error}>{error}</span>
        ) : data ? (
          <span className="tv-exbar-text">
            <b>{closed.length}</b> closed{open ? <> · <b>{open}</b> open</> : ''}
            {closed.length > 0 && (
              <>
                {' · '}{wins}W {closed.length - wins}L
                {' · '}<span className={pnl >= 0 ? 'tv-up' : 'tv-down'}>{pnl >= 0 ? '+' : '−'}{formatNumber(Math.abs(pnl))}</span>
              </>
            )}
            <span className="tv-muted tv-exbar-time"> · {clock(data.fetchedAt)}</span>
          </span>
        ) : (
          <span className="tv-muted tv-exbar-text">Reading {symbol} trades…</span>
        )}
        <button type="button" className="tv-btn tv-btn--icon tv-exbar-refresh" onClick={view.refresh} disabled={loading} aria-label="Refresh exchange trades">
          <IconRefresh size={14} className={loading ? 'tv-xo-spin' : undefined} />
        </button>
      </>
    );
  }
  return (
    <div className="tv-exbar-wrap">
      {staleSince && (
        <div className="tv-exbar tv-exbar--warn" role="alert">
          Chart candles stop at {formatDateTime(staleSince)} UTC: the live export is not running, newer trades cannot be shown. Start the bot with ./start.sh.
        </div>
      )}
      <div className="tv-exbar" aria-live="polite">{body}</div>
    </div>
  );
}
