import { useState } from 'react';
import { formatNumber, formatPrice } from '../lib/format';
import { PHONE, matches, useMediaQuery } from '../lib/useMediaQuery';
import { IconChevronDown, IconChevronUp, IconClose } from '../ui/icons';
import { countdown } from './orderText';
import { useTicker } from './useTrading';
import { num } from './useOrderDraft';

// "Cancel if not filled in" choices for limit orders (minutes, 0 = never)
const FILL_TIMEOUTS = [5, 15, 30, 60, 240, 1440, 0];

function timeoutLabel(minutes) {
  if (!minutes) return 'Never (keep until filled)';
  if (minutes < 60) return `${minutes} min`;
  const h = minutes / 60;
  return h === 24 ? '1 day' : `${formatNumber(h, Number.isInteger(h) ? 0 : 1)} h`;
}

function pctFrom(entry, price) {
  if (!Number.isFinite(entry) || !Number.isFinite(price) || !entry) return null;
  return Math.abs((price - entry) / entry) * 100;
}

/**
 * Order ticket next to the chart (not modal: the order's lines stay draggable on the
 * chart while it is open). `order` is useOrderDraft(); every change is checked by the
 * bot and the order is only sent with the action button.
 */
export default function OrderTicket({ order, trading, signal, lastPrice, onClose, onPlaced }) {
  const { draft, update, preview, previewError, checking, placing, placeError, missing } = order;
  const isLong = draft.side === 'LONG';
  const isSignal = !!draft.signalId;
  const config = trading.config || {};
  // Phones: price fields folded so the order lines stay visible above the ticket (drag them instead)
  const phone = useMediaQuery(PHONE);
  const [folded, setFolded] = useState(() => matches(PHONE));
  const showFields = !phone || !folded;

  // Approving a signal: it must still be waiting (it can expire or be decided on Telegram meanwhile)
  const signalPending = signal?.status === 'PENDING';
  useTicker(isSignal && signalPending);
  const signalLeft = signalPending ? signal.expiresAt * 1000 - trading.serverNow() : 0;
  const signalGone = isSignal && !placing && !signalPending;
  let signalGoneText = 'This signal is no longer waiting for approval.';
  if (signal?.status === 'EXPIRED') signalGoneText = 'This signal expired: nothing was placed.';
  else if (signal && signal.status !== 'PENDING') signalGoneText = `This signal was already ${signal.status.toLowerCase()} (${signal.decidedVia || 'elsewhere'}).`;

  const ready = !!preview?.ok && !checking && !placing && !missing && trading.conn === 'online' && !signalGone;
  const place = async () => {
    if (!ready) return;
    const res = await order.place();
    if (res) onPlaced(res);
  };

  // Long <-> Short: mirror the stop and target around the entry
  const flip = () => {
    const entry = draft.orderType === 'limit' ? num(draft.entry) : (preview?.price ?? lastPrice);
    const sl = num(draft.stopLoss);
    const tp = num(draft.takeProfit);
    const mirror = (p) => (Number.isFinite(entry) && Number.isFinite(p) ? 2 * entry - p : '');
    update({ side: isLong ? 'SHORT' : 'LONG', stopLoss: mirror(sl), takeProfit: mirror(tp) });
  };

  const entryNow = draft.orderType === 'limit' ? num(draft.entry) : (preview?.price ?? lastPrice);
  const slPct = pctFrom(entryNow, num(draft.stopLoss));
  const tpPct = pctFrom(entryNow, num(draft.takeProfit));
  const base = draft.symbol.replace(/USDT$/, '');
  const defaultTimeout = Number.isFinite(config.limitTimeoutMinutes) ? config.limitTimeoutMinutes : 30;
  const timeouts = FILL_TIMEOUTS.includes(defaultTimeout) ? FILL_TIMEOUTS : [defaultTimeout, ...FILL_TIMEOUTS].sort((a, b) => (a || 1e9) - (b || 1e9));
  const action = `${isLong ? 'Buy' : 'Sell'} ${draft.orderType}`;
  const title = isSignal ? `Approve ${draft.symbol} ${draft.timeframe || ''}`.trim() : draft.symbol;

  return (
    <section
      className={`tv-ticket ${isLong ? 'is-long' : 'is-short'}`}
      aria-label={`${isLong ? 'Long' : 'Short'} order ${draft.symbol}`}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.stopPropagation();
          onClose();
        }
      }}
    >
      <header className="tv-ticket-head">
        <div className="tv-segment tv-ticket-side" role="group" aria-label="Side">
          <button type="button" aria-pressed={isLong} className="is-long" onClick={() => !isLong && flip()} disabled={isSignal && !isLong}>Long</button>
          <button type="button" aria-pressed={!isLong} className="is-short" onClick={() => isLong && flip()} disabled={isSignal && isLong}>Short</button>
        </div>
        <b className="tv-ticket-title">{title}</b>
        {phone && (
          <button
            type="button"
            className="tv-btn tv-btn--xs tv-ticket-fold"
            onClick={() => setFolded(v => !v)}
            aria-expanded={showFields}
            aria-controls="tk-fields"
          >
            Prices
            {showFields ? <IconChevronUp size={14} /> : <IconChevronDown size={14} />}
          </button>
        )}
        <button type="button" className="tv-btn tv-btn--icon tv-ticket-close" onClick={onClose} aria-label="Close order">
          <IconClose size={16} />
        </button>
      </header>

      {isSignal && draft.bot && (
        <p className="tv-ticket-note tv-muted num">
          Bot: entry {formatPrice(draft.bot.entry)} · stop {formatPrice(draft.bot.stopLoss)} · target {formatPrice(draft.bot.takeProfit)}
        </p>
      )}

      {showFields && (
        <div id="tk-fields" className="tv-ticket-fields">
          <div className="tv-segment tv-ticket-type" role="group" aria-label="Order type">
            <button type="button" aria-pressed={draft.orderType === 'market'} onClick={() => update({ orderType: 'market' })}>Market</button>
            <button
              type="button"
              aria-pressed={draft.orderType === 'limit'}
              onClick={() => update({ orderType: 'limit', entry: draft.entry || (Number.isFinite(entryNow) ? entryNow : '') })}
            >
              Limit
            </button>
          </div>

          <div className="tv-ticket-grid">
            <label htmlFor="tk-entry">Entry</label>
            {draft.orderType === 'limit' ? (
              <input id="tk-entry" className="tv-input num" inputMode="decimal" value={draft.entry} onChange={(e) => update({ entry: e.target.value })} />
            ) : (
              <output id="tk-entry" className="tv-order-market num">≈ {formatPrice(entryNow)}</output>
            )}
            <span />

            <label htmlFor="tk-sl">Stop</label>
            <input id="tk-sl" className="tv-input num" inputMode="decimal" value={draft.stopLoss} onChange={(e) => update({ stopLoss: e.target.value })} />
            <span className="num tv-down">{slPct != null ? `−${slPct.toFixed(2)}%` : ''}</span>

            <label htmlFor="tk-tp">Target</label>
            <input id="tk-tp" className="tv-input num" inputMode="decimal" value={draft.takeProfit} onChange={(e) => update({ takeProfit: e.target.value })} />
            <span className="num tv-up">{tpPct != null ? `+${tpPct.toFixed(2)}%` : ''}</span>

            <label htmlFor="tk-risk">Risk $</label>
            <input id="tk-risk" className="tv-input num" inputMode="decimal" value={draft.risk} onChange={(e) => update({ risk: e.target.value })} />
            <span className="tv-muted num">{config.maxRisk ? `max ${config.maxRisk}` : ''}</span>

            {draft.orderType === 'limit' && (
              <>
                <label htmlFor="tk-ttl">Cancel after</label>
                <select id="tk-ttl" className="tv-select" value={draft.fillTimeout} onChange={(e) => update({ fillTimeout: Number(e.target.value) })}>
                  {timeouts.map(m => <option key={m} value={m}>{timeoutLabel(m)}</option>)}
                </select>
                <span />
              </>
            )}
          </div>
        </div>
      )}

      <div className="tv-ticket-summary" aria-live="polite" aria-busy={checking}>
        {signalGone && <p className="tv-trade-error" role="alert">{signalGoneText}</p>}
        {missing && <p className="tv-muted">Set the stop, target and risk to check the order.</p>}
        {!missing && !preview && !previewError && <p className="tv-muted">Checking with the exchange…</p>}
        {previewError && <p className="tv-trade-error" role="alert">{previewError}</p>}
        {preview && (
          <>
            <dl className={`tv-ticket-stats num ${checking ? 'is-stale' : ''}`}>
              <div><dt>Quantity</dt><dd>{preview.quantity} {base}</dd></div>
              <div><dt>R:R</dt><dd>{preview.rr}</dd></div>
              <div><dt>Loss at stop</dt><dd className="tv-down">−${formatNumber(preview.riskUsd)}</dd></div>
              <div><dt>Profit at target</dt><dd className="tv-up">+${formatNumber(preview.rewardUsd)}</dd></div>
              <div className="tv-ticket-stats-wide">
                <dt>Margin ({preview.leverage}x)</dt>
                <dd>${formatNumber(preview.margin)}{preview.balance != null && <span className="tv-muted"> of ${formatNumber(preview.balance)} free</span>}</dd>
              </div>
            </dl>
            {preview.errors?.map(e => <p key={e} className="tv-trade-error" role="alert">{e}</p>)}
            {preview.warnings?.map(w => <p key={w} className="tv-trade-warn">{w}</p>)}
          </>
        )}
        {placeError && <p className="tv-trade-error" role="alert">{placeError}</p>}
      </div>

      <p className="tv-ticket-hint tv-muted">Drag the entry, stop and target lines on the chart. Shift+drag moves the entry alone.</p>

      <footer className="tv-ticket-foot">
        {isSignal && signalPending && (
          <span className={`tv-order-expiry num ${signalLeft < 60000 ? 'tv-down' : 'tv-muted'}`} role="timer" aria-label="Signal expires in">
            {countdown(signalLeft)}
          </span>
        )}
        <button type="button" className="tv-btn tv-btn--ghost" onClick={onClose}>Cancel</button>
        <button type="button" className={`tv-btn ${isLong ? 'tv-btn--buy' : 'tv-btn--sell'}`} onClick={place} disabled={!ready}>
          {placing ? 'Placing…' : isSignal ? `Approve · ${action}` : action}
        </button>
      </footer>
    </section>
  );
}
