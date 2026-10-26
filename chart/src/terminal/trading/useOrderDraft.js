import { useCallback, useEffect, useMemo, useState } from 'react';
import { formatPrice } from '../lib/format';

const PREVIEW_DELAY = 350;

export const toInput = (value) => (Number.isFinite(value) && value > 0 ? formatPrice(value) : '');

export function num(value) {
  const n = Number(String(value ?? '').replace(/,/g, '').trim());
  return Number.isFinite(n) && n > 0 ? n : null;
}

/**
 * Default stop / target around `entry` from the recent bars: stop 1.5x the average
 * bar range away (at least 0.2%), target at `rr` times the risk.
 */
export function defaultLevels(side, entry, candles, rr = 2) {
  if (!Number.isFinite(entry)) return {};
  const recent = (candles || []).slice(-14);
  const avgRange = recent.length ? recent.reduce((s, c) => s + (c.high - c.low), 0) / recent.length : 0;
  const risk = Math.max(avgRange * 1.5, entry * 0.002);
  const dir = side === 'LONG' ? 1 : -1;
  return { stopLoss: entry - dir * risk, takeProfit: entry + dir * risk * rr };
}

/**
 * The order being prepared. The ticket and the chart (draggable lines) both edit it;
 * prices are kept as typed text. The bot checks every change (/api/orders/preview)
 * and `place()` sends it, or approves the bot signal with these values.
 *
 * open({ symbol, side, orderType, entry, stopLoss, takeProfit, risk, signalId, timeframe, bot, autoLevels })
 *   numbers for prices; `autoLevels` asks the page to fill stop / target from the chart.
 */
export function useOrderDraft(trading) {
  const [draft, setDraft] = useState(null);
  const [preview, setPreview] = useState(null);
  // Body the preview was made for: dragging makes it stale until the next check
  const [previewKey, setPreviewKey] = useState(null);
  const [previewError, setPreviewError] = useState(null);
  const [checking, setChecking] = useState(false);
  const [placing, setPlacing] = useState(false);
  const [placeError, setPlaceError] = useState(null);

  const open = useCallback((d) => {
    setPreview(null);
    setPreviewError(null);
    setPlaceError(null);
    setDraft({
      id: Date.now(),
      symbol: d.symbol,
      side: d.side,
      orderType: d.orderType || 'market',
      entry: toInput(d.entry),
      stopLoss: toInput(d.stopLoss),
      takeProfit: toInput(d.takeProfit),
      risk: String(d.risk ?? trading.config?.defaultRisk ?? 5),
      fillTimeout: Number.isFinite(trading.config?.limitTimeoutMinutes) ? trading.config.limitTimeoutMinutes : 30,
      signalId: d.signalId || null,
      timeframe: d.timeframe || null,
      bot: d.bot || null,
      autoLevels: !!d.autoLevels,
    });
  }, [trading.config]);

  const close = useCallback(() => setDraft(null), []);

  // patch: fields as typed text, or numbers for prices (from the chart)
  const update = useCallback((patch) => {
    setDraft(d => {
      if (!d) return d;
      const next = { ...d, ...patch };
      ['entry', 'stopLoss', 'takeProfit'].forEach(k => {
        if (typeof patch[k] === 'number') next[k] = toInput(patch[k]);
      });
      return next;
    });
    setPlaceError(null);
  }, []);

  const symbol = draft?.symbol;
  const side = draft?.side;
  const orderType = draft?.orderType;
  const entry = orderType === 'limit' ? num(draft?.entry) : undefined;
  const stopLoss = num(draft?.stopLoss);
  const takeProfit = num(draft?.takeProfit);
  const risk = num(draft?.risk);
  const body = useMemo(
    () => (symbol ? { symbol, side, orderType, entry, stopLoss, takeProfit, risk } : null),
    [symbol, side, orderType, entry, stopLoss, takeProfit, risk],
  );
  const missing = !body || !body.stopLoss || !body.takeProfit || !body.risk || (body.orderType === 'limit' && !body.entry);
  const bodyKey = body ? JSON.stringify(body) : null;

  const requestPreview = trading.preview;
  useEffect(() => {
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
          setPreviewKey(JSON.stringify(body));
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

  /** Send the order (or approve the signal with it). Resolves to the bot's answer, or null when not sent. */
  const place = useCallback(async () => {
    if (!draft || missing) return null;
    setPlacing(true);
    setPlaceError(null);
    const order = { ...body, fillTimeoutMinutes: body.orderType === 'limit' ? draft.fillTimeout : undefined };
    try {
      if (draft.signalId) {
        const res = await trading.decide(draft.signalId, 'approve', order);
        if (res?.ok) return { signal: res.signal };
        setPlaceError(res?.message || 'The order was not placed');
      } else {
        const res = await trading.placeOrder(order);
        if (res?.success) return res;
        setPlaceError(res?.message || 'The exchange did not accept the order');
      }
    } catch (err) {
      setPlaceError(err.message);
    } finally {
      setPlacing(false);
    }
    return null;
  }, [draft, missing, body, trading]);

  const previewFresh = !!preview && previewKey === bodyKey;
  return { draft, open, close, update, body, missing, preview, previewFresh, previewError, checking, placing, placeError, place };
}
