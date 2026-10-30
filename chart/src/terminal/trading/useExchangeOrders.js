import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';

const POLL_MS = 20000; // the bot caches each answer ~10 s; Lighter allows 60 requests a minute

/**
 * Every order and open position on an exchange account (GET /api/exchange/orders),
 * also the ones placed by hand on the exchange. Polls while `enabled`.
 * With resource 'trades' (GET /api/exchange/trades): the same orders as trades
 * (entry -> exit with SL / TP), open ones + the last `limit` closed, for the chart.
 *
 * accounts: [{name, label, configured, active}] exchanges the bot can read.
 * data: {exchange, label, symbol, days, fetchedAt, orders, positions[, trades]} of the current query.
 */
export function useExchangeOrders({ online, enabled, exchange, symbol, days = 7, resource = 'orders', limit = 30, pollMs = POLL_MS }) {
  const [accounts, setAccounts] = useState(null);
  const [accountsError, setAccountsError] = useState(null);
  const [state, setState] = useState({ key: null, data: null, error: null, loading: false });
  const reqId = useRef(0);

  useEffect(() => {
    if (!online || !enabled || accounts) return undefined;
    const ctrl = new AbortController();
    api('/exchange/accounts', { signal: ctrl.signal })
      .then(list => {
        setAccounts(Array.isArray(list) ? list : []);
        setAccountsError(null);
      })
      .catch(err => {
        if (err.name === 'AbortError') return;
        // 404: the running bot was started before this tab existed
        setAccountsError(err.status === 404 ? 'Restart the bot to use this tab (it runs an older version).' : err.message);
      });
    return () => ctrl.abort();
  }, [online, enabled, accounts]);

  // The exchange shown: the one picked, else the one the bot trades on, else the first with keys
  const usable = (accounts || []).filter(a => a.configured);
  const current = usable.find(a => a.name === exchange) || usable.find(a => a.active) || usable[0] || null;
  const name = current?.name || null;
  const key = name ? `${resource}|${name}|${symbol || ''}|${resource === 'trades' ? limit : days}` : null;

  const load = useCallback(async (fresh) => {
    if (!name) return;
    const id = ++reqId.current;
    setState(s => ({ ...s, key, loading: true, ...(s.key === key ? {} : { data: null, error: null }) }));
    const q = new URLSearchParams(resource === 'trades' ? { exchange: name, limit: String(limit) } : { exchange: name, days: String(days) });
    if (symbol) q.set('symbol', symbol);
    if (fresh) q.set('fresh', '1');
    try {
      const data = await api(`/exchange/${resource}?${q}`);
      if (id === reqId.current) setState({ key, data, error: null, loading: false });
    } catch (err) {
      // Keep the last answer on screen: a failed refresh only shows the error
      const message = err.status === 404 ? 'Restart the bot: the running bot is an older version without this feature.' : err.message;
      if (id === reqId.current) setState(s => ({ ...s, key, error: message, loading: false }));
    }
  }, [name, symbol, days, key, resource, limit]);

  useEffect(() => {
    if (!online || !enabled || !name) return undefined;
    // First read at once (it marks the query as loading), then every pollMs
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load(false);
    const timer = setInterval(() => {
      if (!document.hidden) load(false);
    }, pollMs);
    return () => clearInterval(timer);
  }, [online, enabled, name, load, pollMs]);

  const shown = state.key === key ? state : { data: null, error: null, loading: !!key };
  return {
    accounts,
    accountsError,
    current,
    data: shown.data,
    error: shown.error,
    loading: shown.loading,
    refresh: () => load(true),
  };
}
