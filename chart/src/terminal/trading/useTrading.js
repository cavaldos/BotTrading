import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { api, ApiError, clearToken, getToken, newClientId, readEvents, setToken } from './api';
import { ORDER_CHANGES } from './orderText';

const STALE_MS = 45000; // the bot pings every 15 s; reconnect after 3 missed pings

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
 * `onSignal(signal)` is called for each new bot signal,
 * `onOrderChange(order, change)` when an order fills, partly fills, does not fill or is cancelled.
 */
export function useTrading({ onSignal, onOrderChange } = {}) {
  const [token, setTokenState] = useState(getToken);
  const [conn, setConn] = useState(() => (getToken() ? 'connecting' : 'login'));
  const [error, setError] = useState(null);
  const [signals, setSignals] = useState([]);
  const [orders, setOrders] = useState([]);
  const [config, setConfig] = useState(null);
  const [approval, setApproval] = useState({ enabled: false, timeoutMinutes: null });
  const clockOffset = useRef(0);
  const onSignalRef = useRef(onSignal);
  const onOrderChangeRef = useRef(onOrderChange);
  useLayoutEffect(() => {
    onSignalRef.current = onSignal;
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

    const handle = (name, data) => {
      lastSeen = Date.now();
      if (Number.isFinite(data?.serverTime)) clockOffset.current = data.serverTime * 1000 - Date.now();
      if (name === 'snapshot') {
        setSignals([...(data.signals?.pending || []), ...(data.signals?.recent || [])]);
        setOrders(data.orders || []);
        setConfig(data.config || null);
        setApproval({ enabled: !!data.approval, timeoutMinutes: data.timeoutMinutes });
        setConn('online');
        setError(null);
        retry = 0;
      } else if (name === 'signal' && data?.signal) {
        setSignals(list => upsert(list, data.signal));
        if (data.event === 'new') onSignalRef.current?.(data.signal);
      } else if (name === 'order' && data?.order) {
        setOrders(list => upsert(list, data.order));
        if (ORDER_CHANGES[data.change]) onOrderChangeRef.current?.(data.order, data.change);
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
    setError(null);
  }, []);

  // `changes` (approve only): {orderType, entry, stopLoss, takeProfit, risk, fillTimeoutMinutes} edited on the web
  const decide = useCallback(async (id, action, changes) => {
    const res = await api(`/signals/${id}/${action}`, { method: 'POST', body: changes });
    if (res?.signal) setSignals(list => upsert(list, res.signal));
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

  return { conn, error, signals, orders, config, approval, login, logout, decide, preview, placeOrder, cancelOrder, serverNow };
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
