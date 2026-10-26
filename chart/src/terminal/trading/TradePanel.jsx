import { useEffect, useRef, useState } from 'react';
import { formatPrice } from '../lib/format';
import { Dialog } from '../ui/Dialog';
import { apiImageUrl } from './api';
import { countdown, isOpenOrder, orderDetail, orderTitle, sideLabel, typeLabel } from './orderText';
import { useTicker } from './useTrading';

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

const CONFIRM_MS = 3000;

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
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const confirmTimer = useRef(null);
  useTicker(signal.status === 'PENDING');
  useEffect(() => () => clearTimeout(confirmTimer.current), []);

  const isLong = signal.side === 'LONG';
  const left = signal.expiresAt * 1000 - serverNow();
  const total = (timeoutMinutes || 5) * 60000;
  const pending = signal.status === 'PENDING';

  const decide = async (action) => {
    setBusy(action);
    setError(null);
    try {
      const res = await onDecide(signal.id, action);
      if (!res?.ok) setError(res?.message || 'Not applied');
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(null);
      setConfirming(false);
    }
  };

  const approve = () => {
    if (!confirming) {
      setConfirming(true);
      clearTimeout(confirmTimer.current);
      confirmTimer.current = setTimeout(() => setConfirming(false), CONFIRM_MS);
      return;
    }
    clearTimeout(confirmTimer.current);
    decide('approve');
  };

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
      {signal.status === 'PLACING' && (
        <p className="tv-muted tv-sig-note">Placing order (approved on {signal.decidedVia === 'web' ? 'the web' : `Telegram by ${signal.decidedBy}`})…</p>
      )}
      {error && <p className="tv-trade-error" role="alert">{error}</p>}
      <div className="tv-sig-actions">
        <button type="button" className="tv-btn tv-btn--ghost" onClick={() => onShow(signal)}>Show on chart</button>
        {pending && (
          <>
            <button type="button" className="tv-btn tv-btn--ghost" onClick={() => onEdit(signal)} disabled={!!busy}>
              Edit SL/TP
            </button>
            <button type="button" className="tv-btn tv-btn--ghost" onClick={() => decide('reject')} disabled={!!busy}>
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

function SignalHistory({ items }) {
  if (!items.length) return <div className="tv-empty">No decided signals yet.</div>;
  return (
    <ul className="tv-trade-list">
      {items.map(s => (
        <li key={s.id} className="tv-trade-row" title={s.resultText || undefined}>
          <span className="num tv-muted">{clock(s.decidedAt || s.createdAt)}</span>
          <b>{s.symbol}</b>
          <SideTag side={s.side} />
          <StatusTag map={SIGNAL_STATUS} status={s.status} />
        </li>
      ))}
    </ul>
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

// ---- panel ------------------------------------------------------------------------------

export default function TradePanel({ trading, activeSymbol, onShowSignal, onEditSignal, onShowOrder, onNewOrder }) {
  const { conn, error, signals, orders, config, approval } = trading;
  const [viewer, setViewer] = useState(null);

  if (conn === 'login') return <LoginForm error={error} onLogin={trading.login} />;

  const pending = signals
    .filter(s => s.status === 'PENDING' || s.status === 'PLACING')
    .sort((a, b) => a.createdAt - b.createdAt);
  const decided = signals
    .filter(s => s.status !== 'PENDING' && s.status !== 'PLACING')
    .sort((a, b) => (b.decidedAt || b.createdAt) - (a.decidedAt || a.createdAt))
    .slice(0, 15);
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

      <section aria-labelledby="trade-pending">
        <h3 id="trade-pending" className="tv-trade-h">
          Signals waiting
          {pending.length > 0 && <span className="tv-count">{pending.length}</span>}
        </h3>
        {!approval.enabled && conn === 'online' && (
          <div className="tv-empty">Approval is off on the bot (approval.enabled: false): signals are traded automatically.</div>
        )}
        {approval.enabled && pending.length === 0 && (
          <div className="tv-empty">No signals waiting. New bot signals show up here and on Telegram.</div>
        )}
        {pending.map(s => (
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
            : 'Or draw a Long/Short position on the chart, select it and press Trade.'}
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
        <h3 id="trade-history" className="tv-trade-h">Signal history</h3>
        <SignalHistory items={decided} />
      </section>

      {/* The image URL belongs to its card: close with it when the signal is decided */}
      {viewer && pending.some(s => s.id === viewer.sig.id) && (
        <Dialog title={`${viewer.sig.symbol} ${viewer.sig.signalTimeframe} · ${viewer.sig.side}`} onClose={() => setViewer(null)} size="lg">
          <div className="tv-sig-viewer">
            <img src={viewer.url} alt={`${viewer.sig.symbol} ${viewer.sig.side} setup`} />
          </div>
        </Dialog>
      )}
    </div>
  );
}
