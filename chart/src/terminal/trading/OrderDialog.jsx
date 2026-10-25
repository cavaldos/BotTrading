import { useEffect, useMemo, useRef, useState } from 'react';
import { formatNumber, formatPrice } from '../lib/format';
import { Dialog } from '../ui/Dialog';

const PREVIEW_DELAY = 350;
// Entry this close to the last price (in %) opens as a market order
const MARKET_NEAR_PCT = 0.1;

function toInput(value) {
  return Number.isFinite(value) && value > 0 ? formatPrice(value) : '';
}

function num(value) {
  const n = Number(String(value).replace(/,/g, '').trim());
  return Number.isFinite(n) && n > 0 ? n : null;
}

/**
 * Manual order ticket. Prefilled from a Long/Short position drawing (entry, stop,
 * target) or from the panel buttons. Every change is checked by the bot
 * (/api/orders/preview); the order is only sent with the confirm button.
 */
export default function OrderDialog({ trading, symbol, side, initial, lastPrice, onClose, onPlaced }) {
  const isLong = side === 'LONG';
  const config = trading.config || {};
  const nearLast = Number.isFinite(lastPrice) && Number.isFinite(initial?.entry)
    && Math.abs(initial.entry - lastPrice) / lastPrice * 100 <= MARKET_NEAR_PCT;
  const [orderType, setOrderType] = useState(initial?.entry && !nearLast ? 'limit' : 'market');
  const [entry, setEntry] = useState(toInput(initial?.entry ?? lastPrice));
  const [stopLoss, setStopLoss] = useState(toInput(initial?.stopLoss));
  const [takeProfit, setTakeProfit] = useState(toInput(initial?.takeProfit));
  const [risk, setRisk] = useState(String(config.defaultRisk ?? 5));
  const [preview, setPreview] = useState(null);
  const [previewError, setPreviewError] = useState(null);
  const [checking, setChecking] = useState(false);
  const [placing, setPlacing] = useState(false);
  const [placeError, setPlaceError] = useState(null);
  const firstFieldRef = useRef(null);

  const body = useMemo(() => ({
    symbol,
    side,
    orderType,
    entry: orderType === 'limit' ? num(entry) : undefined,
    stopLoss: num(stopLoss),
    takeProfit: num(takeProfit),
    risk: num(risk),
  }), [symbol, side, orderType, entry, stopLoss, takeProfit, risk]);

  const requestPreview = trading.preview;
  const missing = !body.stopLoss || !body.takeProfit || !body.risk || (orderType === 'limit' && !body.entry);

  useEffect(() => {
    setPlaceError(null);
    if (missing) {
      setPreview(null);
      setPreviewError(null);
      return undefined;
    }
    const ctrl = new AbortController();
    const timer = setTimeout(() => {
      setChecking(true);
      requestPreview(body, ctrl.signal)
        .then(res => {
          setPreview(res);
          setPreviewError(null);
        })
        .catch(err => {
          if (err.name === 'AbortError') return;
          setPreview(null);
          setPreviewError(err.message);
        })
        .finally(() => {
          if (!ctrl.signal.aborted) setChecking(false);
        });
    }, PREVIEW_DELAY);
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [body, missing, requestPreview]);

  const ready = !!preview?.ok && !checking && !placing && !missing && trading.conn === 'online';

  const place = async () => {
    if (!ready) return;
    setPlacing(true);
    setPlaceError(null);
    try {
      const res = await trading.placeOrder(body);
      if (res?.success) {
        onPlaced(res);
      } else {
        setPlaceError(res?.message || 'The exchange did not accept the order');
      }
    } catch (err) {
      setPlaceError(err.message);
    } finally {
      setPlacing(false);
    }
  };

  const sideLabel = isLong ? 'Long' : 'Short';
  const base = symbol.replace(/USDT$/, '');

  return (
    <Dialog
      title={`${sideLabel} ${symbol}`}
      onClose={onClose}
      size="sm"
      initialFocusRef={firstFieldRef}
      footer={(
        <>
          <button type="button" className="tv-btn tv-btn--ghost" onClick={onClose}>Cancel</button>
          <button type="button" className={`tv-btn ${isLong ? 'tv-btn--buy' : 'tv-btn--sell'}`} onClick={place} disabled={!ready}>
            {placing ? 'Placing…' : `${isLong ? 'Buy' : 'Sell'} ${orderType === 'market' ? 'market' : 'limit'}`}
          </button>
        </>
      )}
    >
      <form className="tv-dialog-body tv-order" onSubmit={(e) => { e.preventDefault(); place(); }}>
        <div className="tv-segment tv-order-type" role="group" aria-label="Order type">
          <button type="button" aria-pressed={orderType === 'market'} onClick={() => setOrderType('market')}>Market</button>
          <button type="button" aria-pressed={orderType === 'limit'} onClick={() => setOrderType('limit')}>Limit</button>
        </div>

        <div className="tv-field">
          <label htmlFor="ord-entry">Entry</label>
          {orderType === 'limit' ? (
            <input ref={firstFieldRef} id="ord-entry" className="tv-input num" inputMode="decimal" value={entry} onChange={(e) => setEntry(e.target.value)} />
          ) : (
            <output id="ord-entry" className="tv-order-market num">
              Market{preview?.price ? ` ≈ ${formatPrice(preview.price)}` : ''}
            </output>
          )}
        </div>
        <div className="tv-field">
          <label htmlFor="ord-sl">Stop loss</label>
          <input
            ref={orderType === 'market' ? firstFieldRef : undefined}
            id="ord-sl"
            className="tv-input num"
            inputMode="decimal"
            value={stopLoss}
            onChange={(e) => setStopLoss(e.target.value)}
          />
        </div>
        <div className="tv-field">
          <label htmlFor="ord-tp">Take profit</label>
          <input id="ord-tp" className="tv-input num" inputMode="decimal" value={takeProfit} onChange={(e) => setTakeProfit(e.target.value)} />
        </div>
        <div className="tv-field">
          <label htmlFor="ord-risk">
            Risk ($)
            {config.maxRisk ? <small className="tv-muted"> max {config.maxRisk}</small> : null}
          </label>
          <input id="ord-risk" className="tv-input num" inputMode="decimal" value={risk} onChange={(e) => setRisk(e.target.value)} />
        </div>

        <div className="tv-order-summary" aria-live="polite" aria-busy={checking}>
          {missing && <p className="tv-muted">Fill in the stop, target and risk to check the order.</p>}
          {!missing && !preview && !previewError && <p className="tv-muted">Checking with the exchange…</p>}
          {previewError && <p className="tv-trade-error" role="alert">{previewError}</p>}
          {preview && (
            <>
              <dl className={`tv-order-stats num ${checking ? 'is-stale' : ''}`}>
                <div><dt>Last price</dt><dd>{formatPrice(preview.price)}</dd></div>
                <div><dt>Quantity</dt><dd>{preview.quantity} {base}</dd></div>
                <div><dt>Notional</dt><dd>${formatNumber(preview.notional)}</dd></div>
                <div>
                  <dt>Margin ({preview.leverage}x)</dt>
                  <dd>${formatNumber(preview.margin)}{preview.balance != null ? <small className="tv-muted"> / ${formatNumber(preview.balance)} free</small> : null}</dd>
                </div>
                <div><dt>Loss at stop</dt><dd className="tv-down">−${formatNumber(preview.riskUsd)}</dd></div>
                <div><dt>Profit at target</dt><dd className="tv-up">+${formatNumber(preview.rewardUsd)}</dd></div>
                <div><dt>R:R</dt><dd>{preview.rr}</dd></div>
              </dl>
              {preview.errors?.map(e => <p key={e} className="tv-trade-error" role="alert">{e}</p>)}
              {preview.warnings?.map(w => <p key={w} className="tv-trade-warn">{w}</p>)}
            </>
          )}
          {placeError && <p className="tv-trade-error" role="alert">{placeError}</p>}
        </div>
      </form>
    </Dialog>
  );
}
