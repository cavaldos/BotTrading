import { useEffect, useState } from 'react';
import { formatPrice } from '../lib/format';
import { Dialog } from '../ui/Dialog';
import { apiImageUrl } from './api';
import { notificationPermission, requestNotifications } from './notify';
import { approvedVia, countdown, isOpenOrder, orderDetail, orderTitle, shortReason, sideLabel, typeLabel } from './orderText';
import { useTicker } from './useTrading';
import { useSignalDecision } from './useSignalDecision';

const SIGNAL_STATUS = {
  PENDING: { label: 'Waiting', tone: 'accent' },
  PLACING: { label: 'Placing…', tone: 'warn' },
  PLACED: { label: 'Placed', tone: 'up' },
  FAILED: { label: 'Failed', tone: 'down' },
  REJECTED: { label: 'Rejected', tone: 'muted' },
  EXPIRED: { label: 'Expired', tone: 'muted' },
};

const ORDER_STATUS = {
  WAITING: { label: 'Waiting fill', tone: 'accent' },
  PARTIAL: { label: 'Partly filled', tone: 'warn' },
  FILLED: { label: 'Filled', tone: 'up' },
  NOT_FILLED: { label: 'Not filled', tone: 'down' },
  CANCELLED: { label: 'Cancelled', tone: 'muted' },
  UNKNOWN: { label: 'Unknown', tone: 'warn' },
};

function clock(epochSeconds) {
  if (!Number.isFinite(epochSeconds) || epochSeconds <= 0) return '';
  return new Date(epochSeconds * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
}

function StatusTag({ map, status }) {
  const def = map[status] || { label: status, tone: 'muted' };
  return <span className={`tv-tag tv-tag--${def.tone}`}>{def.label}</span>;
}

function SideTag({ side }) {
  const isLong = String(side).toUpperCase() === 'LONG';
  return <span className={`tv-tag ${isLong ? 'tv-tag--up' : 'tv-tag--down'}`}>{sideLabel(side)}</span>;
}

// ---- login --------------------------------------------------------------------------

function LoginForm({ error, onLogin }) {
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
    <form className="tv-trade-login" onSubmit={submit}>
      <h3>Connect to the bot</h3>
      <p className="tv-muted">
        Enter <code>WEB_API_TOKEN</code> from the bot&apos;s <code>.env</code>. It is kept in this browser only.
      </p>
      <label className="tv-visually-hidden" htmlFor="trade-token">Bot token</label>
      <input
        id="trade-token"
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

// ---- signals ----------------------------------------------------------------------------

function useSignalImage(signal) {
  const [url, setUrl] = useState(null);
  const { id, hasImage } = signal;
  useEffect(() => {
    if (!hasImage) return undefined;
    const ctrl = new AbortController();
    let objectUrl = null;
    apiImageUrl(`/signals/${id}/image`, { signal: ctrl.signal })
      .then(u => {
        objectUrl = u;
        setUrl(u);
      })
      .catch(() => {});
    return () => {
      ctrl.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [id, hasImage]);
  return url;
}

function SignalCard({ signal, serverNow, timeoutMinutes, onDecide, onShow, onEdit, onImage }) {
  const image = useSignalImage(signal);
  const { confirming, busy, error, approve, reject } = useSignalDecision(signal.id, onDecide);
  useTicker(signal.status === 'PENDING');

  const isLong = signal.side === 'LONG';
  const left = signal.expiresAt * 1000 - serverNow();
  const total = (timeoutMinutes || 5) * 60000;
  const pending = signal.status === 'PENDING';

  return (
    <article className={`tv-sig ${isLong ? 'is-long' : 'is-short'}`} aria-label={`${signal.symbol} ${signal.side} signal`}>
      <header className="tv-sig-head">
        <b>{signal.symbol}</b>
        <span className="tv-muted">{signal.signalTimeframe}</span>
        <SideTag side={signal.side} />
        {pending ? (
          <span className={`tv-sig-timer num ${left < 60000 ? 'tv-down' : ''}`} aria-label="Time left">{countdown(left)}</span>
        ) : (
          <StatusTag map={SIGNAL_STATUS} status={signal.status} />
        )}
      </header>
      {pending && (
        <div className="tv-sig-progress" aria-hidden="true">
          <span style={{ transform: `scaleX(${Math.max(0, Math.min(1, left / total))})` }} />
        </div>
      )}
      {image && (
        <button type="button" className="tv-sig-image" onClick={() => onImage(image, signal)} aria-label="Enlarge chart image">
          <img src={image} alt={`${signal.symbol} ${signal.side} setup`} />
        </button>
      )}
      <dl className="tv-sig-levels num">
        <div><dt>Entry</dt><dd>{formatPrice(signal.entry)}</dd></div>
        <div><dt>Stop</dt><dd className="tv-down">{formatPrice(signal.stopLoss)}</dd></div>
        <div><dt>Target</dt><dd className="tv-up">{formatPrice(signal.takeProfit)}</dd></div>
        <div><dt>R:R</dt><dd>{signal.rr ?? '—'}</dd></div>
        <div><dt>Risk</dt><dd>${signal.riskPerTrade}</dd></div>
      </dl>
      {signal.status === 'FAILED' && (
        <p className="tv-trade-error tv-sig-note" role="alert">
          {shortReason(signal.resultText) || 'The exchange did not accept the order.'}
        </p>
      )}
      {signal.status === 'PLACING' && (
        <p className="tv-muted tv-sig-note">Placing order ({signal.decidedVia === 'auto' ? 'auto mode' : `approved on ${approvedVia(signal.decidedVia, signal.decidedBy)}`})…</p>
      )}
      {/* A failed order already shows its reason above */}
      {error && signal.status !== 'FAILED' && <p className="tv-trade-error" role="alert">{error}</p>}
      <div className="tv-sig-actions">
        <button type="button" className="tv-btn tv-btn--ghost" onClick={() => onShow(signal)}>Show on chart</button>
        {pending && (
          <>
            <button type="button" className="tv-btn tv-btn--ghost" onClick={() => onEdit(signal)} disabled={!!busy}>
              Edit SL/TP
            </button>
            <button type="button" className="tv-btn tv-btn--ghost" onClick={reject} disabled={!!busy}>
              {busy === 'reject' ? 'Rejecting…' : 'Reject'}
            </button>
            <button
              type="button"
              className={`tv-btn ${isLong ? 'tv-btn--buy' : 'tv-btn--sell'}`}
              onClick={approve}
              disabled={!!busy}
              aria-live="polite"
            >
              {busy === 'approve' ? 'Placing…' : confirming ? `Confirm ${isLong ? 'long' : 'short'}` : 'Approve'}
            </button>
          </>
        )}
      </div>
    </article>
  );
}

// ---- history ----------------------------------------------------------------------------

// What happened to each bot signal / web order (GET /api/history)
const HISTORY_STATUS = {
  PENDING: { label: 'Waiting', tone: 'accent' },
  PLACING: { label: 'Placing…', tone: 'warn' },
  WAITING: { label: 'Waiting fill', tone: 'accent' },
  PARTIAL: { label: 'Partly filled', tone: 'warn' },
  FILLED: { label: 'Filled', tone: 'up' },
  PLACED: { label: 'Placed', tone: 'up' },
  WIN: { label: 'Win', tone: 'up' },
  LOSS: { label: 'Loss', tone: 'down' },
  NOT_FILLED: { label: 'Not filled', tone: 'down' },
  CANCELLED: { label: 'Cancelled', tone: 'muted' },
  UNKNOWN: { label: 'Unknown', tone: 'warn' },
  FAILED: { label: 'Failed', tone: 'down' },
  EXPIRED: { label: 'Expired', tone: 'muted' },
  REJECTED: { label: 'Rejected', tone: 'muted' },
};
// Reached the exchange
const PLACED = ['WAITING', 'PARTIAL', 'FILLED', 'PLACED', 'WIN', 'LOSS', 'NOT_FILLED', 'CANCELLED', 'UNKNOWN'];

const HISTORY_FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'placed', label: 'Placed' },
  { id: 'missed', label: 'Not placed' },
];

function when(epochSeconds) {
  if (!Number.isFinite(epochSeconds) || epochSeconds <= 0) return '';
  const d = new Date(epochSeconds * 1000);
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  if (d.toDateString() === new Date().toDateString()) return time;
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${time}`;
}

function historyDetail(item) {
  const parts = [];
  if (item.fillPrice) parts.push(`filled ${formatPrice(item.fillPrice)}`);
  else if (item.entry) parts.push(`${item.orderType === 'limit' ? 'limit' : 'entry'} ${formatPrice(item.entry)}`);
  if (item.source === 'web') parts.push('web order');
  else if (item.via === 'auto') parts.push('auto mode');
  else if (item.via) parts.push(`${item.status === 'REJECTED' ? 'rejected' : 'approved'} on ${approvedVia(item.via)}`);
  return parts.join(' · ');
}

/** Bot signals and web orders; a click shows the order on the chart. */
function OrderHistory({ items, focusedId, onFocus }) {
  const [filter, setFilter] = useState('all');
  const shown = items.filter(i => (filter === 'all' ? true : filter === 'placed' ? PLACED.includes(i.status) : !PLACED.includes(i.status)));
  return (
    <>
      <div className="tv-filter tv-hist-filter" role="group" aria-label="Filter history">
        {HISTORY_FILTERS.map(f => (
          <button key={f.id} type="button" className="tv-chip" aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}>
            {f.label}
          </button>
        ))}
      </div>
      {shown.length === 0 && (
        <div className="tv-empty">{items.length ? 'Nothing for this filter.' : 'No orders yet. Signals you approve or reject and orders from the web show up here.'}</div>
      )}
      <ul className="tv-hist" aria-label="Order history">
        {shown.map(i => {
          const def = HISTORY_STATUS[i.status] || { label: i.status, tone: 'muted' };
          return (
            <li key={i.id}>
              <button
                type="button"
                className={`tv-hist-row ${focusedId === i.id ? 'is-active' : ''}`}
                aria-current={focusedId === i.id}
                onClick={() => onFocus(i)}
                title={i.reason || undefined}
              >
                <span className="tv-hist-main">
                  <span className="num tv-muted">{when(i.time)}</span>
                  <b>{i.symbol}</b>
                  {i.timeframe && <span className="tv-muted">{i.timeframe}</span>}
                  <SideTag side={i.side} />
                  <span className={`tv-tag tv-tag--${def.tone}`}>{def.label}</span>
                </span>
                <span className="tv-hist-sub num">{historyDetail(i)}</span>
                {i.reason && <span className={`tv-hist-sub ${i.status === 'FAILED' ? 'tv-down' : 'tv-muted'}`}>{i.reason}</span>}
              </button>
            </li>
          );
        })}
      </ul>
    </>
  );
}

function OrderList({ items, serverNow, onCancel, onShow }) {
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState(null);
  useTicker(items.some(o => isOpenOrder(o) && o.expiresAt > 0));
  if (!items.length) return <div className="tv-empty">No orders yet. Approved signals and web orders show up here until they fill.</div>;
  const cancel = async (order) => {
    setBusyId(order.id);
    setError(null);
    try {
      const res = await onCancel(order.id);
      if (!res?.success) setError(res?.message || 'Cancel failed');
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  };
  const now = serverNow();
  return (
    <>
      {error && <p className="tv-trade-error" role="alert">{error}</p>}
      <ul className="tv-trade-list">
        {items.map(o => {
          const waiting = o.status === 'WAITING' && o.orderType !== 'limit';
          const status = waiting ? { WAITING: { label: 'Filling…', tone: 'accent' } } : ORDER_STATUS;
          const canCancel = o.orderType === 'limit' && isOpenOrder(o);
          return (
            <li key={o.id} className={`tv-ord ${isOpenOrder(o) ? 'is-open' : ''}`} title={orderTitle(o)}>
              <div className="tv-ord-main">
                <span className="num tv-muted">{clock(o.placedAt)}</span>
                <button type="button" className="tv-ord-symbol" onClick={() => onShow(o)} aria-label={`Show ${o.symbol} on the chart`}>
                  {o.symbol}
                </button>
                <SideTag side={o.side} />
                <span className="tv-ord-type">{typeLabel(o.orderType)}</span>
                <StatusTag map={status} status={o.status} />
              </div>
              <div className="tv-ord-sub">
                <span className={`tv-ord-detail num ${o.status === 'NOT_FILLED' ? 'tv-down' : ''}`}>{orderDetail(o, now)}</span>
                {canCancel && (
                  <button type="button" className="tv-btn tv-btn--ghost tv-btn--xs" onClick={() => cancel(o)} disabled={busyId === o.id}>
                    {busyId === o.id ? 'Cancelling…' : 'Cancel'}
                  </button>
                )}
              </div>
              {o.slTpMissing && o.filled > 0 && (
                <p className="tv-trade-error">Stop loss / target not set on the exchange: {o.slTpMissing}</p>
              )}
            </li>
          );
        })}
      </ul>
    </>
  );
}

// ---- auto mode ----------------------------------------------------------------------------

/**
 * Auto mode switch: when on, the bot trades every new signal at once (no approval).
 * Turning it on asks once more, turning it off is immediate.
 */
function AutoTradeSwitch({ auto, onChange }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const apply = async (enabled) => {
    setBusy(true);
    setError(null);
    try {
      await onChange(enabled);
      setAsking(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className={`tv-auto ${auto ? 'is-on' : ''}`}>
      <div className="tv-auto-row">
        <span className="tv-auto-text">
          <b id="auto-trade-label">Auto trade</b>
          <span className="tv-muted">
            {auto ? 'On: new signals are traded at once, no approval.' : 'Off: each signal waits for your approval.'}
          </span>
        </span>
        <button
          type="button"
          role="switch"
          aria-checked={auto}
          aria-labelledby="auto-trade-label"
          className="tv-switch"
          disabled={busy || asking}
          onClick={() => (auto ? apply(false) : setAsking(true))}
        >
          <span className="tv-switch-knob" aria-hidden="true" />
        </button>
      </div>
      {asking && (
        <div className="tv-auto-confirm" role="alertdialog" aria-label="Turn on auto trade">
          <p>The bot will place an order for every new signal without asking. Signals already waiting still need you.</p>
          <div className="tv-auto-actions">
            <button type="button" className="tv-btn tv-btn--ghost tv-btn--xs" onClick={() => setAsking(false)} disabled={busy}>Cancel</button>
            <button type="button" className="tv-btn tv-btn--primary tv-btn--xs" onClick={() => apply(true)} disabled={busy}>
              {busy ? 'Turning on…' : 'Turn on auto trade'}
            </button>
          </div>
        </div>
      )}
      {error && <p className="tv-trade-error" role="alert">{error}</p>}
    </div>
  );
}

// ---- desktop alerts --------------------------------------------------------------------

/** Desktop notifications reach you while the bot terminal (or another app) is in front. */
function DesktopAlerts() {
  const [permission, setPermission] = useState(notificationPermission);
  if (permission === 'granted' || permission === 'unsupported') return null;
  if (permission === 'denied') {
    return (
      <p className="tv-trade-hint tv-trade-warn tv-trade-alerts">
        Desktop alerts are blocked for this site. Allow notifications in the browser&apos;s site settings to be told about new signals while another window is in front.
      </p>
    );
  }
  return (
    <div className="tv-trade-alerts">
      <span className="tv-muted">Get a desktop alert for new signals while another window is in front.</span>
      <button type="button" className="tv-btn tv-btn--xs tv-btn--primary" onClick={() => requestNotifications().then(setPermission)}>
        Turn on
      </button>
    </div>
  );
}

// ---- panel ------------------------------------------------------------------------------

export default function TradePanel({ trading, activeSymbol, onShowSignal, onEditSignal, onShowOrder, onNewOrder, onFocusHistory, focusedHistoryId }) {
  const { conn, error, signals, orders, config, approval } = trading;
  const [viewer, setViewer] = useState(null);

  if (conn === 'login') return <LoginForm error={error} onLogin={trading.login} />;

  // Waiting signals, plus the ones whose order failed in the last 5 minutes (tagged Failed, with the reason)
  const failedSince = trading.serverNow() - 5 * 60000;
  const cards = signals
    .filter(s => s.status === 'PENDING' || s.status === 'PLACING' || (s.status === 'FAILED' && s.decidedAt * 1000 > failedSince))
    .sort((a, b) => a.createdAt - b.createdAt);
  const pending = cards.filter(s => s.status !== 'FAILED');
  const canTrade = conn === 'online' && config?.symbols?.includes(activeSymbol);
  const openOrders = orders.filter(isOpenOrder);
  const shownOrders = [
    ...openOrders.sort((a, b) => b.placedAt - a.placedAt),
    ...orders.filter(o => !isOpenOrder(o)).sort((a, b) => (b.closedAt || b.updatedAt) - (a.closedAt || a.updatedAt)),
  ].slice(0, 15);

  return (
    <div className="tv-trade">
      <div className={`tv-trade-conn is-${conn}`} role="status">
        <span className="tv-trade-dot" aria-hidden="true" />
        <span>
          {conn === 'online' && 'Connected to the bot'}
          {conn === 'connecting' && 'Connecting…'}
          {conn === 'offline' && `Reconnecting… ${error ? `(${error})` : ''}`}
        </span>
        <button type="button" className="tv-btn tv-btn--xs" onClick={trading.logout}>Log out</button>
      </div>
      <DesktopAlerts />

      <section aria-labelledby="trade-pending">
        <h3 id="trade-pending" className="tv-trade-h">
          Signals waiting
          {pending.length > 0 && <span className="tv-count">{pending.length}</span>}
        </h3>
        {!approval.enabled && conn === 'online' && (
          <div className="tv-empty">Approval is off on the bot (approval.enabled: false): signals are traded automatically.</div>
        )}
        {approval.enabled && conn === 'online' && (
          <AutoTradeSwitch auto={!!approval.auto} onChange={trading.setAutoApprove} />
        )}
        {approval.enabled && cards.length === 0 && (
          <div className="tv-empty">
            {approval.auto
              ? 'No signals right now. In auto mode new signals are traded at once and show up under Orders and History.'
              : 'No signals waiting. New bot signals show up here and on Telegram.'}
          </div>
        )}
        {cards.map(s => (
          <SignalCard
            key={s.id}
            signal={s}
            serverNow={trading.serverNow}
            timeoutMinutes={approval.timeoutMinutes}
            onDecide={trading.decide}
            onShow={onShowSignal}
            onEdit={onEditSignal}
            onImage={(url, sig) => setViewer({ url, sig })}
          />
        ))}
      </section>

      <section aria-labelledby="trade-new">
        <h3 id="trade-new" className="tv-trade-h">New order{activeSymbol ? ` · ${activeSymbol}` : ''}</h3>
        <div className="tv-trade-new">
          <button type="button" className="tv-btn tv-btn--buy" disabled={!canTrade} onClick={() => onNewOrder('LONG')}>Long</button>
          <button type="button" className="tv-btn tv-btn--sell" disabled={!canTrade} onClick={() => onNewOrder('SHORT')}>Short</button>
        </div>
        <p className="tv-muted tv-trade-hint">
          {conn === 'online' && activeSymbol && !config?.symbols?.includes(activeSymbol)
            ? `${activeSymbol} is not one of the bot symbols.`
            : 'Or right-click the chart at a price. Then drag the entry, stop and target lines on the chart.'}
        </p>
      </section>

      <section aria-labelledby="trade-orders">
        <h3 id="trade-orders" className="tv-trade-h">
          Orders
          {openOrders.length > 0 && <span className="tv-count">{openOrders.length}</span>}
        </h3>
        <OrderList items={shownOrders} serverNow={trading.serverNow} onCancel={trading.cancelOrder} onShow={onShowOrder} />
      </section>

      <section aria-labelledby="trade-history">
        <h3 id="trade-history" className="tv-trade-h">History</h3>
        <OrderHistory items={trading.history || []} focusedId={focusedHistoryId} onFocus={onFocusHistory} />
      </section>

      {/* The image URL belongs to its card: close with it when the signal is decided */}
      {viewer && cards.some(s => s.id === viewer.sig.id) && (
        <Dialog title={`${viewer.sig.symbol} ${viewer.sig.signalTimeframe} · ${viewer.sig.side}`} onClose={() => setViewer(null)} size="lg">
          <div className="tv-sig-viewer">
            <img src={viewer.url} alt={`${viewer.sig.symbol} ${viewer.sig.side} setup`} />
          </div>
        </Dialog>
      )}
    </div>
  );
}
