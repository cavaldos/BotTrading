import { Link } from 'react-router-dom';
import { formatNumber, formatPrice } from '../lib/format';
import { IconRefresh } from '../ui/icons';
import { KINDS, STATUSES, eventTime, kindText, orderDetail, qty, statusOf, typeText } from './exchangeOrders';

const DAYS = [1, 3, 7, 14, 30];

const FILTERS = [
  { id: 'open', label: 'Open', test: o => o.status === 'open' },
  { id: 'filled', label: 'Filled', test: o => o.filled > 0 },
  { id: 'all', label: 'All', test: () => true },
];

function when(epochSeconds) {
  if (!Number.isFinite(epochSeconds) || epochSeconds <= 0) return '';
  const d = new Date(epochSeconds * 1000);
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  if (d.toDateString() === new Date().toDateString()) return time;
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${time}`;
}

function clock(epochSeconds) {
  if (!Number.isFinite(epochSeconds)) return '';
  return new Date(epochSeconds * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
}

function Positions({ items, focusedKey, onFocus }) {
  if (!items.length) return <div className="tv-empty tv-xo-empty">No open positions.</div>;
  return (
    <ul className="tv-hist" aria-label="Open positions">
      {items.map(p => {
        const key = `pos:${p.symbol}:${p.side}`;
        const long = p.side === 'LONG';
        return (
          <li key={key}>
            <button
              type="button"
              className={`tv-hist-row ${focusedKey === key ? 'is-active' : ''}`}
              aria-current={focusedKey === key}
              onClick={() => onFocus({ kind: 'position', key, item: p })}
              title={[
                Number.isFinite(p.markPrice) && `Mark ${formatPrice(p.markPrice)}`,
                p.liquidationPrice && `Liquidation ${formatPrice(p.liquidationPrice)}`,
              ].filter(Boolean).join(' · ') || undefined}
            >
              <span className="tv-hist-main">
                <b>{p.symbol}</b>
                <span className={`tv-tag ${long ? 'tv-tag--up' : 'tv-tag--down'}`}>{long ? 'Long' : 'Short'}</span>
                <span className={`num tv-xo-pnl ${p.unrealizedPnl >= 0 ? 'tv-up' : 'tv-down'}`}>
                  {Number.isFinite(p.unrealizedPnl) ? `${p.unrealizedPnl >= 0 ? '+' : '−'}${formatNumber(Math.abs(p.unrealizedPnl))}` : ''}
                </span>
              </span>
              <span className="tv-hist-sub num">
                {qty(p.size)} @ {formatPrice(p.entryPrice)}
                {p.leverage ? ` · ${Math.round(p.leverage)}x` : ''}
              </span>
              {(p.stopLoss || p.takeProfit) && (
                <span className="tv-hist-sub num">
                  {[p.stopLoss && `SL ${formatPrice(p.stopLoss)}`, p.takeProfit && `TP ${formatPrice(p.takeProfit)}`].filter(Boolean).join(' · ')}
                </span>
              )}
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function Orders({ items, focusedKey, onFocus }) {
  return (
    <ul className="tv-hist" aria-label="Exchange orders">
      {items.map(o => {
        const key = `ord:${o.symbol}:${o.id}`;
        const status = STATUSES[statusOf(o)] || { label: o.status, tone: 'muted' };
        const kind = KINDS[o.kind] || KINDS.entry;
        return (
          <li key={key}>
            <button
              type="button"
              className={`tv-hist-row ${focusedKey === key ? 'is-active' : ''}`}
              aria-current={focusedKey === key}
              onClick={() => onFocus({ kind: 'order', key, item: o })}
              title={`${o.type} · id ${o.id}`}
            >
              <span className="tv-hist-main">
                <span className="num tv-muted">{when(eventTime(o))}</span>
                <b>{o.symbol}</b>
                <span className={`tv-tag ${o.side === 'buy' ? 'tv-tag--up' : 'tv-tag--down'}`}>{o.side === 'buy' ? 'Buy' : 'Sell'}</span>
                <span className={`tv-tag tv-tag--${status.tone}`}>{status.label}</span>
              </span>
              <span className="tv-hist-sub">
                <i className="tv-xo-dot" style={{ background: kind.color }} aria-hidden="true" />
                {kindText(o)} · {typeText(o)}
              </span>
              <span className="tv-hist-sub num">{orderDetail(o)}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

/**
 * Orders tab: every order and open position on an exchange account (also ones placed
 * by hand on the exchange). A click shows it on the chart.
 */
export default function ExchangeOrdersPanel({ conn, view, settings, onSettings, activeSymbol, focusedKey, onFocus, onConnect }) {
  if (conn === 'login') {
    return (
      <div className="tv-trade">
        <div className="tv-empty">
          Connect to the bot to read your exchange orders.
          <div className="tv-xo-connect">
            <button type="button" className="tv-btn tv-btn--primary tv-btn--xs" onClick={onConnect}>Connect in the Trade panel</button>
          </div>
        </div>
      </div>
    );
  }
  if (conn !== 'online') return <div className="tv-empty">{conn === 'connecting' ? 'Connecting to the bot…' : 'The bot is not reachable. Retrying…'}</div>;

  const { accounts, accountsError, current, data, error, loading } = view;
  if (accountsError) return <div className="tv-empty tv-down" role="alert">{accountsError}</div>;
  if (!accounts) return <div className="tv-empty">Loading exchanges…</div>;
  if (!current) {
    return <div className="tv-empty">No exchange API keys in the bot&apos;s .env (BYBIT_DEMO_API_KEY, BINANCE_API_KEY or LIGHTER_API_PRIVATE_KEY).</div>;
  }

  const set = (patch) => onSettings({ ...settings, ...patch });
  const orders = data?.orders || [];
  const positions = data?.positions || [];
  const filter = FILTERS.find(f => f.id === settings.filter) || FILTERS[0];
  // Newest event first: a stop set yesterday that filled now goes on top
  const shown = orders.filter(filter.test).sort((a, b) => (eventTime(b) || 0) - (eventTime(a) || 0));
  const counts = Object.fromEntries(FILTERS.map(f => [f.id, orders.filter(f.test).length]));
  const symbolScope = settings.scope !== 'all';

  return (
    <div className="tv-trade tv-xo">
      <div className="tv-xo-bar">
        <label className="tv-visually-hidden" htmlFor="xo-exchange">Exchange</label>
        <select
          id="xo-exchange"
          className="tv-select tv-xo-select"
          value={current.name}
          onChange={(e) => set({ exchange: e.target.value })}
        >
          {accounts.map(a => (
            <option key={a.name} value={a.name} disabled={!a.configured}>
              {a.label}{a.active ? ' · bot' : ''}{a.configured ? '' : ' (no API key)'}
            </option>
          ))}
        </select>
        <label className="tv-visually-hidden" htmlFor="xo-days">History</label>
        <select id="xo-days" className="tv-select tv-xo-days" value={settings.days} onChange={(e) => set({ days: Number(e.target.value) })}>
          {DAYS.map(d => <option key={d} value={d}>{d}d</option>)}
        </select>
        <button
          type="button"
          className="tv-btn tv-btn--icon"
          onClick={view.refresh}
          disabled={loading}
          aria-label="Refresh"
          data-tip="Refresh"
          data-tip-side="left"
        >
          <IconRefresh size={16} className={loading ? 'tv-xo-spin' : undefined} />
        </button>
      </div>

      <div className="tv-xo-bar">
        <div className="tv-segment" role="group" aria-label="Symbols">
          <button type="button" aria-pressed={symbolScope} onClick={() => set({ scope: 'symbol' })} disabled={!activeSymbol}>
            {activeSymbol || 'Symbol'}
          </button>
          <button type="button" aria-pressed={!symbolScope} onClick={() => set({ scope: 'all' })}>All symbols</button>
        </div>
        <span className="tv-xo-show">
          <span id="xo-show-label">On chart</span>
          <button
            type="button"
            role="switch"
            aria-checked={!!settings.show}
            aria-labelledby="xo-show-label"
            className="tv-switch tv-xo-switch"
            onClick={() => set({ show: !settings.show })}
          >
            <span className="tv-switch-knob" aria-hidden="true" />
          </button>
        </span>
      </div>

      <p className="tv-muted tv-xo-status">
        <span role="status">{data ? `${data.label} · updated ${clock(data.fetchedAt)}` : loading ? `Reading ${current.label}…` : ''}</span>
        <Link to="/orders" className="tv-xo-page">Full page →</Link>
      </p>
      {error && <p className="tv-trade-error" role="alert">{error}</p>}

      {data && (
        <>
          <section aria-labelledby="xo-positions">
            <h3 id="xo-positions" className="tv-trade-h">
              Positions
              {positions.length > 0 && <span className="tv-count">{positions.length}</span>}
            </h3>
            <Positions items={positions} focusedKey={focusedKey} onFocus={onFocus} />
          </section>

          <section aria-labelledby="xo-orders">
            <h3 id="xo-orders" className="tv-trade-h">Orders</h3>
            <div className="tv-filter tv-hist-filter" role="group" aria-label="Filter orders">
              {FILTERS.map(f => (
                <button key={f.id} type="button" className="tv-chip" aria-pressed={filter.id === f.id} onClick={() => set({ filter: f.id })}>
                  {f.label} {counts[f.id] > 0 && <span className="num">{counts[f.id]}</span>}
                </button>
              ))}
            </div>
            {shown.length === 0
              ? (
                <div className="tv-empty tv-xo-empty">
                  {filter.id === 'open' ? 'No open orders' : 'No orders'}
                  {symbolScope && activeSymbol ? ` on ${activeSymbol}` : ''}
                  {filter.id === 'open' ? '.' : ` in the last ${settings.days} day${settings.days > 1 ? 's' : ''}.`}
                </div>
              )
              : <Orders items={shown} focusedKey={focusedKey} onFocus={onFocus} />}
          </section>
        </>
      )}
    </div>
  );
}
