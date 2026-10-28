import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError, clearToken, getToken, newClientId, readEvents, setToken } from './api';
import { ORDER_CHANGES, shortReason } from './orderText';

const STALE_MS = 45000; // the bot pings every 15 s; reconnect after 3 missed pings
const HISTORY_DELAY = 600; // reload the history shortly after a signal / order event

// Candle time "YYYY-MM-DD HH:MM:SS" (UTC) -> unix seconds
function utcSeconds(text) {
  const t = Date.parse(`${String(text || '').trim().replace(' ', 'T')}Z`);
  return Number.isFinite(t) ? t / 1000 : null;
}

/** History item (same shape as GET /api/history) for a bot signal of this run. */
function signalHistoryItem(s) {
  return {
    id: `sig:${s.key}`,
    source: 'signal',
    symbol: s.symbol,
    side: s.side,
    timeframe: s.signalTimeframe || null,
    orderType: s.orderType || 'market',
    time: s.decidedAt || s.createdAt,
    sentAt: s.createdAt,
    entryTime: utcSeconds(s.entryTime),
    entry: s.entry,
    stopLoss: s.stopLoss,
    takeProfit: s.takeProfit,
    risk: s.riskPerTrade,
    status: s.status,
    fillPrice: null,
    via: s.decidedVia || null,
    reason: s.status === 'FAILED' || s.status === 'EXPIRED' ? shortReason(s.resultText) : '',
  };
}

/**
 * The bot's history (files) plus this run's signals: a decision shows at once, also
 * when the bot was started before /api/history existed. File items win, but keep
 * the failure reason the bot only has in memory.
 */
function mergeHistory(fileItems, signals) {
  const byId = new Map(fileItems.map(i => [i.id, i]));
  const extra = [];
  signals.forEach(s => {
    if (!s?.key) return;
    const live = signalHistoryItem(s);
    const saved = byId.get(live.id);
    if (!saved) extra.push(live);
    else if (!saved.reason && live.reason) byId.set(live.id, { ...saved, reason: live.reason });
  });
  return [...byId.values(), ...extra]
    .map(i => (i.reason ? { ...i, reason: shortReason(i.reason) } : i))
    .sort((a, b) => (b.time || 0) - (a.time || 0));
}

function upsert(list, item) {
  const i = list.findIndex(x => x.id === item.id);
  if (i < 0) return [item, ...list];
  const next = list.slice();
  next[i] = item;
  return next;
}

/**
 * Live connection to the bot's trading API.
 *
 * conn: 'login' (no or bad token) | 'connecting' | 'online' | 'offline' (retrying)
 * signals: bot signals (pending and recently decided).
 * orders: orders followed on the exchange (approved signals and web orders) with their fill status.
 * history: bot signals and web orders from the bot's files (survives restarts), newest first.
 * approval: {enabled, timeoutMinutes, auto}; auto = the bot trades new signals without approval.
 * `onSignal(signal, {auto})` is called for each new bot signal, `onSignalFailed(signal)` when its order could not be placed,
 * `onOrderChange(order, change)` when an order fills, partly fills, does not fill or is cancelled.
 */
export function useTrading({ onSignal, onSignalFailed, onOrderChange } = {}) {
  const [token, setTokenState] = useState(getToken);
  const [conn, setConn] = useState(() => (getToken() ? 'connecting' : 'login'));
  const [error, setError] = useState(null);
  const [signals, setSignals] = useState([]);
  const [orders, setOrders] = useState([]);
  const [history, setHistory] = useState([]);
  const [config, setConfig] = useState(null);
  const [approval, setApproval] = useState({ enabled: false, timeoutMinutes: null, auto: false });
  const autoRef = useRef(false);
  const clockOffset = useRef(0);
  const onSignalRef = useRef(onSignal);
  const onSignalFailedRef = useRef(onSignalFailed);
  const onOrderChangeRef = useRef(onOrderChange);
  useLayoutEffect(() => {
    onSignalRef.current = onSignal;
    onSignalFailedRef.current = onSignalFailed;
    onOrderChangeRef.current = onOrderChange;
  });

  useEffect(() => {
    if (!token) {
      setConn('login');
      return undefined;
    }
    let stopped = false;
    let ctrl = null;
    let retry = 0;
    let retryTimer = null;
    let lastSeen = Date.now();
    let historyTimer = null;

    // Decisions and fills are written to the bot's files: reload the history after each event
    const reloadHistory = () => {
      clearTimeout(historyTimer);
      historyTimer = setTimeout(() => {
        api('/history', { token })
          .then(list => {
            if (!stopped && Array.isArray(list)) setHistory(list);
          })
          .catch(() => {});
      }, HISTORY_DELAY);
    };

    const handle = (name, data) => {
      lastSeen = Date.now();
      if (Number.isFinite(data?.serverTime)) clockOffset.current = data.serverTime * 1000 - Date.now();
      if (name === 'snapshot') {
        setSignals([...(data.signals?.pending || []), ...(data.signals?.recent || [])]);
        setOrders(data.orders || []);
        if (Array.isArray(data.history)) setHistory(data.history);
        setConfig(data.config || null);
        autoRef.current = !!data.autoApprove;
        setApproval({ enabled: !!data.approval, timeoutMinutes: data.timeoutMinutes, auto: autoRef.current });
        setConn('online');
        setError(null);
        retry = 0;
      } else if (name === 'signal' && data?.signal) {
        setSignals(list => upsert(list, data.signal));
        if (data.event === 'new') onSignalRef.current?.(data.signal, { auto: data.signal.decidedVia === 'auto' || autoRef.current });
        else if (data.signal.status === 'FAILED') onSignalFailedRef.current?.(data.signal);
        reloadHistory();
      } else if (name === 'mode') {
        autoRef.current = !!data?.autoApprove;
        setApproval(a => ({ ...a, auto: autoRef.current }));
      } else if (name === 'order' && data?.order) {
        setOrders(list => upsert(list, data.order));
        if (ORDER_CHANGES[data.change]) onOrderChangeRef.current?.(data.order, data.change);
        reloadHistory();
      }
    };

    const connect = async () => {
      ctrl = new AbortController();
      lastSeen = Date.now();
      try {
        await readEvents(handle, { signal: ctrl.signal, token });
        throw new ApiError('Connection to the bot closed', 0);
      } catch (err) {
        if (stopped) return;
        if (err.status === 401) {
          setConn('login');
          setError('Token rejected by the bot');
          return;
        }
        setConn('offline');
        setError(err.name === 'AbortError' ? 'Connection lost' : err.message);
        retry += 1;
        retryTimer = setTimeout(connect, Math.min(15000, 1000 * 2 ** Math.min(retry, 4)));
      }
    };

    setConn('connecting');
    connect();
    const watchdog = setInterval(() => {
      if (Date.now() - lastSeen > STALE_MS) ctrl?.abort();
    }, 5000);
    return () => {
      stopped = true;
      clearTimeout(retryTimer);
      clearTimeout(historyTimer);
      clearInterval(watchdog);
      ctrl?.abort();
    };
  }, [token]);

  const login = useCallback(async (value) => {
    const t = String(value || '').trim();
    if (!t) throw new ApiError('Enter the token', 0);
    await api('/me', { token: t });
    setToken(t);
    setError(null);
    setTokenState(t);
    // Asked on this click (a user gesture) so new signals can notify a hidden tab
    try {
      if (typeof Notification !== 'undefined' && Notification.permission === 'default') Notification.requestPermission();
    } catch {
      // not supported
    }
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setTokenState('');
    setSignals([]);
    setOrders([]);
    setHistory([]);
    setError(null);
  }, []);

  // `changes` (approve only): {orderType, entry, stopLoss, takeProfit, risk, fillTimeoutMinutes} edited on the web
  const decide = useCallback(async (id, action, changes) => {
    const res = await api(`/signals/${id}/${action}`, { method: 'POST', body: changes });
    if (res?.signal) setSignals(list => upsert(list, res.signal));
    return res;
  }, []);

  // Auto mode on the bot: new signals are traded without approval
  const setAutoApprove = useCallback(async (enabled) => {
    const res = await api('/approval/auto', { method: 'POST', body: { enabled } });
    autoRef.current = !!res?.autoApprove;
    setApproval(a => ({ ...a, auto: autoRef.current }));
    return res;
  }, []);

  const preview = useCallback((body, signal) => api('/orders/preview', { method: 'POST', body, signal }), []);

  const placeOrder = useCallback(async (body) => {
    const res = await api('/orders', { method: 'POST', body: { ...body, confirm: true, clientId: newClientId() } });
    if (res?.order) setOrders(list => upsert(list, res.order));
    return res;
  }, []);

  const cancelOrder = useCallback(async (id) => {
    const res = await api(`/orders/${id}/cancel`, { method: 'POST' });
    if (res?.order) setOrders(list => upsert(list, res.order));
    return res;
  }, []);

  const serverNow = useCallback(() => Date.now() + clockOffset.current, []);
  const mergedHistory = useMemo(() => mergeHistory(history, signals), [history, signals]);

  return { conn, error, signals, orders, history: mergedHistory, config, approval, login, logout, decide, setAutoApprove, preview, placeOrder, cancelOrder, serverNow };
}

/** Re-render every `ms` while `active`. */
export function useTicker(active, ms = 1000) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!active) return undefined;
    const t = setInterval(() => setTick(n => n + 1), ms);
    return () => clearInterval(t);
  }, [active, ms]);
}
