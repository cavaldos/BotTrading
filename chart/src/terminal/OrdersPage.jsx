import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import './terminal.css';
import './trading/trading.css';
import './trading/ordersPage.css';
import { api, ApiError, clearToken, getToken, setToken } from './trading/api';
import { useExchangeOrders } from './trading/useExchangeOrders';
import {
  KINDS, PENDING_FOCUS_KEY, STATUSES, eventTime, hasFill, kindText, levelOf, qty, statusOf, typeText,
} from './trading/exchangeOrders';
import { formatNumber, formatPrice } from './lib/format';
import { loadJSON, saveJSON } from './lib/storage';
import { IconRefresh } from './ui/icons';

const DAYS = [1, 3, 7, 14, 30];
const FILTERS = [
  { id: 'all', label: 'All', test: () => true },
  { id: 'open', label: 'Open', test: o => o.status === 'open' },
  { id: 'filled', label: 'Filled', test: o => o.filled > 0 },
  { id: 'canceled', label: 'Cancelled', test: o => ['canceled', 'expired', 'rejected'].includes(o.status) && !(o.filled > 0) },
];
const DEFAULTS = { exchange: null, symbol: '', days: 7, filter: 'all' };

function dateTime(epochSeconds) {
  if (!Number.isFinite(epochSeconds) || epochSeconds <= 0) return '—';
  const d = new Date(epochSeconds * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function money(value) {
  if (!Number.isFinite(value)) return '—';
  return `${value >= 0 ? '+' : '−'}${formatNumber(Math.abs(value))}`;
}

/** Token check against the bot: 'login' | 'checking' | 'online' | 'offline'. */
function useBotConnection() {
  const [token, setTokenState] = useState(getToken);
  const [conn, setConn] = useState(() => (getToken() ? 'checking' : 'login'));
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!token) return undefined;
    let stopped = false;
    let timer = null;
    const check = () => {
      api('/me', { token })
        .then(() => {
          if (stopped) return;
          setConn('online');
          setError(null);
        })
        .catch(err => {
          if (stopped) return;
          if (err.status === 401) {
            clearToken();
            setTokenState('');
            setConn('login');
            setError(err.message);
            return;
          }
          setConn('offline');
          setError(err.message);
          timer = setTimeout(check, 5000);
        });
    };
    check();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [token]);

  const login = useCallback(async (value) => {
    const t = String(value || '').trim();
    if (!t) throw new ApiError('Enter the token', 0);
    await api('/me', { token: t });
    setToken(t);
    setError(null);
    setConn('checking');
    setTokenState(t);
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setTokenState('');
    setConn('login');
  }, []);

  return { conn, error, login, logout };
}

function LoginCard({ error, onLogin }) {
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      await onLogin(value);
    } catch (err) {
      setMessage(err.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="tv-op-card tv-op-login" onSubmit={submit}>
      <h2>Connect to the bot</h2>
      <p className="tv-muted">
        Enter <code>WEB_API_TOKEN</code> from the bot&apos;s <code>.env</code>. It is kept in this browser only (same as the chart&apos;s Trade panel).
      </p>
      <label className="tv-visually-hidden" htmlFor="op-token">Bot token</label>
      <input
        id="op-token"
        className="tv-input"
        type="password"
        autoComplete="current-password"
        placeholder="Bot token"
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
      {(message || error) && <p className="tv-trade-error" role="alert">{message || error}</p>}
      <button type="submit" className="tv-btn tv-btn--primary tv-btn--block" disabled={busy || !value.trim()}>
        {busy ? 'Connecting…' : 'Connect'}
      </button>
    </form>
  );
}

function Stat({ label, value, tone }) {
  return (
    <div className="tv-op-stat">
      <span className="tv-muted">{label}</span>
      <b className={`num ${tone || ''}`}>{value}</b>
    </div>
  );
}

function PositionsTable({ items, onOpen }) {
  if (!items.length) return <div className="tv-empty">No open positions.</div>;
  return (
    <div className="tv-op-scroll">
      <table className="tv-op-table">
        <thead>
          <tr>
            <th scope="col">Symbol</th>
            <th scope="col">Side</th>
            <th scope="col" className="num">Size</th>
            <th scope="col" className="num">Entry</th>
            <th scope="col" className="num">Mark</th>
            <th scope="col" className="num">PnL</th>
            <th scope="col" className="num">Stop loss</th>
            <th scope="col" className="num">Target</th>
            <th scope="col" className="num">Liq.</th>
            <th scope="col" className="num">Lev.</th>
          </tr>
        </thead>
        <tbody>
          {items.map(p => (
            <tr key={`${p.symbol}:${p.side}`} onClick={() => onOpen({ kind: 'position', key: `pos:${p.symbol}:${p.side}`, item: p })}>
              <th scope="row">
                <button type="button" className="tv-op-link" onClick={(e) => { e.stopPropagation(); onOpen({ kind: 'position', key: `pos:${p.symbol}:${p.side}`, item: p }); }}>
                  {p.symbol}
                </button>
              </th>
              <td><span className={`tv-tag ${p.side === 'LONG' ? 'tv-tag--up' : 'tv-tag--down'}`}>{p.side === 'LONG' ? 'Long' : 'Short'}</span></td>
              <td className="num">{qty(p.size)}</td>
              <td className="num">{formatPrice(p.entryPrice)}</td>
              <td className="num">{formatPrice(p.markPrice)}</td>
              <td className={`num ${p.unrealizedPnl >= 0 ? 'tv-up' : 'tv-down'}`}>{money(p.unrealizedPnl)}</td>
              <td className="num">{formatPrice(p.stopLoss)}</td>
              <td className="num">{formatPrice(p.takeProfit)}</td>
              <td className="num tv-muted">{formatPrice(p.liquidationPrice)}</td>
              <td className="num tv-muted">{p.leverage ? `${Math.round(p.leverage)}x` : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OrdersTable({ items, onOpen }) {
  return (
    <div className="tv-op-scroll">
      <table className="tv-op-table">
        <thead>
          <tr>
            <th scope="col">Time</th>
            <th scope="col">Symbol</th>
            <th scope="col">Side</th>
            <th scope="col">Order</th>
            <th scope="col" className="num">Price</th>
            <th scope="col" className="num">Fill price</th>
            <th scope="col" className="num">Qty</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {items.map(o => {
            const key = `ord:${o.symbol}:${o.id}`;
            const status = STATUSES[statusOf(o)] || { label: o.status, tone: 'muted' };
            const level = levelOf(o);
            const open = () => onOpen({ kind: 'order', key, item: o });
            return (
              <tr key={key} onClick={open} title={`${o.type} · id ${o.id}`}>
                <td className="tv-op-time tv-muted">{dateTime(eventTime(o))}</td>
                <th scope="row">
                  <button type="button" className="tv-op-link" onClick={(e) => { e.stopPropagation(); open(); }}>{o.symbol}</button>
                </th>
                <td><span className={`tv-tag ${o.side === 'buy' ? 'tv-tag--up' : 'tv-tag--down'}`}>{o.side === 'buy' ? 'Buy' : 'Sell'}</span></td>
                <td>
                  <i className="tv-xo-dot" style={{ background: (KINDS[o.kind] || KINDS.entry).color }} aria-hidden="true" />
                  {kindText(o)} <span className="tv-muted tv-op-type">· {typeText(o)}</span>
                </td>
                <td className="num">
                  {Number.isFinite(level) ? formatPrice(level) : <span className="tv-muted">market</span>}
                  {o.triggerPrice != null && <small className="tv-muted"> trigger</small>}
                </td>
                <td className="num">{hasFill(o) ? formatPrice(o.average) : '—'}</td>
                <td className="num">{o.filled > 0 && o.filled < o.amount ? `${qty(o.filled)} / ${qty(o.amount)}` : qty(o.amount)}</td>
                <td><span className={`tv-tag tv-tag--${status.tone}`}>{status.label}</span></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Full page of the exchange account orders (#/orders): every order and open position
 * on Bybit, Binance or Lighter, also the ones placed by hand. A row opens the chart there.
 */
export default function OrdersPage() {
  const navigate = useNavigate();
  const bot = useBotConnection();
  const [stored, setStored] = useState(() => ({ ...DEFAULTS, ...(loadJSON('ordersPage', {}) || {}) }));
  const settings = { ...DEFAULTS, ...stored };
  const set = (patch) => setStored(s => {
    const next = { ...s, ...patch };
    saveJSON('ordersPage', next);
    return next;
  });

  useEffect(() => {
    document.title = 'Exchange orders';
  }, []);

  const view = useExchangeOrders({
    online: bot.conn === 'online',
    enabled: true,
    exchange: settings.exchange,
    symbol: settings.symbol || null,
    days: settings.days,
  });
  const data = view.data;

  // Symbols to pick from: every symbol seen on this exchange so far
  const [seen, setSeen] = useState({});
  const exchangeName = view.current?.name || '';
  // Updated while rendering (no effect needed): adds the symbols of each new answer
  const found = data && exchangeName ? [...data.orders.map(o => o.symbol), ...data.positions.map(p => p.symbol)] : [];
  const knownSymbols = seen[exchangeName] || [];
  const newSymbols = [...new Set(found)].filter(sym => !knownSymbols.includes(sym));
  if (newSymbols.length) setSeen({ ...seen, [exchangeName]: [...knownSymbols, ...newSymbols].sort() });
  const symbols = useMemo(() => {
    const list = seen[exchangeName] || [];
    return settings.symbol && !list.includes(settings.symbol) ? [...list, settings.symbol].sort() : list;
  }, [seen, exchangeName, settings.symbol]);

  const orders = useMemo(
    () => (data?.orders || []).slice().sort((a, b) => (eventTime(b) || 0) - (eventTime(a) || 0)),
    [data],
  );
  const positions = data?.positions || [];
  const filter = FILTERS.find(f => f.id === settings.filter) || FILTERS[0];
  const shown = orders.filter(filter.test);
  const counts = Object.fromEntries(FILTERS.map(f => [f.id, orders.filter(f.test).length]));
  const pnl = positions.reduce((sum, p) => sum + (Number.isFinite(p.unrealizedPnl) ? p.unrealizedPnl : 0), 0);

  // Open the Exchange chart on that symbol; TerminalPage picks up the order and selects its trade
  const openOnChart = (f) => {
    try {
      window.sessionStorage.setItem(PENDING_FOCUS_KEY, JSON.stringify({ ...f, exchange: exchangeName }));
    } catch {
      // the chart still opens on the symbol
    }
    navigate(`/?source=exchange&symbol=${encodeURIComponent(f.item.symbol)}`);
  };

  let body;
  if (bot.conn === 'login') {
    body = <LoginCard error={bot.error} onLogin={bot.login} />;
  } else if (bot.conn !== 'online') {
    body = <div className="tv-op-card tv-empty">{bot.conn === 'checking' ? 'Connecting to the bot…' : `The bot is not reachable. Retrying… ${bot.error ? `(${bot.error})` : ''}`}</div>;
  } else if (view.accountsError) {
    body = <div className="tv-op-card tv-empty tv-down" role="alert">{view.accountsError}</div>;
  } else if (!view.accounts) {
    body = <div className="tv-op-card tv-empty">Loading exchanges…</div>;
  } else if (!view.current) {
    body = <div className="tv-op-card tv-empty">No exchange API keys in the bot&apos;s .env (BYBIT_DEMO_API_KEY, BINANCE_API_KEY or LIGHTER_API_PRIVATE_KEY).</div>;
  } else {
    body = (
      <>
        <div className="tv-op-controls">
          <div className="tv-segment" role="group" aria-label="Exchange">
            {view.accounts.map(a => (
              <button
                key={a.name}
                type="button"
                aria-pressed={a.name === exchangeName}
                disabled={!a.configured}
                title={a.configured ? (a.active ? 'The exchange the bot trades on' : undefined) : 'No API key in the bot\'s .env'}
                onClick={() => set({ exchange: a.name, symbol: '' })}
              >
                {a.label}{a.active ? ' · bot' : ''}
              </button>
            ))}
          </div>
          <label className="tv-op-field">
            <span className="tv-muted">Symbol</span>
            <select className="tv-select" value={settings.symbol} onChange={(e) => set({ symbol: e.target.value })}>
              <option value="">All symbols</option>
              {symbols.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
          <label className="tv-op-field">
            <span className="tv-muted">History</span>
            <select className="tv-select" value={settings.days} onChange={(e) => set({ days: Number(e.target.value) })}>
              {DAYS.map(d => <option key={d} value={d}>{d} day{d > 1 ? 's' : ''}</option>)}
            </select>
          </label>
          <span className="tv-spacer" />
          <span className="tv-muted tv-op-updated" role="status">
            {view.loading && !data ? `Reading ${view.current.label}…` : data ? `Updated ${new Date(data.fetchedAt * 1000).toLocaleTimeString([], { hourCycle: 'h23' })}` : ''}
          </span>
          <button type="button" className="tv-btn" onClick={view.refresh} disabled={view.loading}>
            <IconRefresh size={16} className={view.loading ? 'tv-xo-spin' : undefined} />
            Refresh
          </button>
        </div>
        {view.error && <p className="tv-trade-error tv-op-error" role="alert">{view.error}</p>}
        {exchangeName === 'binance' && !settings.symbol && (
          <p className="tv-muted tv-op-note">
            Binance only gives order history per symbol: &quot;All symbols&quot; covers the last 7 days of the bot&apos;s symbols and the ones with an open order or position. Pick a symbol for its full history.
          </p>
        )}

        {data && (
          <>
            <div className="tv-op-stats">
              <Stat label="Open positions" value={positions.length} />
              <Stat label="Unrealized PnL" value={positions.length ? money(pnl) : '—'} tone={positions.length ? (pnl >= 0 ? 'tv-up' : 'tv-down') : ''} />
              <Stat label="Open orders" value={counts.open} />
              <Stat label={`Filled · ${settings.days}d`} value={counts.filled} />
            </div>

            <section className="tv-op-card" aria-labelledby="op-positions">
              <h2 id="op-positions" className="tv-trade-h">Positions</h2>
              <PositionsTable items={positions} onOpen={openOnChart} />
            </section>

            <section className="tv-op-card" aria-labelledby="op-orders">
              <div className="tv-op-card-head">
                <h2 id="op-orders" className="tv-trade-h">Orders</h2>
                <div className="tv-op-chips" role="group" aria-label="Filter orders">
                  {FILTERS.map(f => (
                    <button key={f.id} type="button" className="tv-chip" aria-pressed={filter.id === f.id} onClick={() => set({ filter: f.id })}>
                      {f.label} <span className="num">{counts[f.id]}</span>
                    </button>
                  ))}
                </div>
              </div>
              {shown.length
                ? <OrdersTable items={shown} onOpen={openOnChart} />
                : <div className="tv-empty">No orders for this filter in the last {settings.days} day{settings.days > 1 ? 's' : ''}.</div>}
            </section>
          </>
        )}
      </>
    );
  }

  return (
    <div className="tv tv-op">
      <header className="tv-topbar tv-op-top">
        <Link to="/?source=exchange" className="tv-btn" aria-label="Back to the chart">← Chart</Link>
        <span className="tv-sep" />
        <h1>Exchange orders</h1>
        <span className="tv-spacer" />
        {bot.conn === 'online' && (
          <button type="button" className="tv-btn" onClick={bot.logout}>Log out</button>
        )}
      </header>
      <main className="tv-op-main">{body}</main>
    </div>
  );
}
