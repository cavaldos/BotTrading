import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { resampleCandles, resampleTrades, volumeFromCandles } from '../lib/timeframes';

// Static JSON exported by the Python side; the format is unchanged.
export const SOURCES = {
  backtest: {
    id: 'backtest',
    label: 'Backtest',
    base: '/data/orders',
    smcBase: '/data/indicators',
    hasTrades: true,
  },
  indicators: {
    id: 'indicators',
    label: 'SMC data',
    base: '/data/indicators',
    smcBase: '/data/indicators',
    smcInline: true,
    hasTrades: false,
  },
  live: {
    id: 'live',
    label: 'Live',
    base: '/data/live',
    hasTrades: true,
    live: true,
    refreshMs: 5000,
    symbolsRefreshMs: 30000,
  },
};

const SMC_KEYS = ['bos', 'choch', 'orderBlocks', 'fvg', 'ifvg', 'eqhEql', 'imbalance', 'breakerBlocks', 'mitigationBlocks', 'retracement', 'swing', 'gannSwing'];

async function fetchJson(url, { bust = false, signal } = {}) {
  const res = await fetch(bust ? `${url}${url.includes('?') ? '&' : '?'}t=${Date.now()}` : url, { signal });
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  const type = res.headers.get('content-type') || '';
  if (!type.includes('json')) throw new Error(`${url}: not a JSON file (is the file exported?)`);
  return res.json();
}

/** symbols.json of a source, refreshed periodically for live data. */
export function useSymbolList(sourceId) {
  const source = SOURCES[sourceId] || SOURCES.backtest;
  const [state, setState] = useState({ symbols: [], loading: true, error: null, sourceId });

  useEffect(() => {
    let cancelled = false;
    const load = (initial) => {
      if (initial) setState({ symbols: [], loading: true, error: null, sourceId });
      fetchJson(`${source.base}/symbols.json`, { bust: !!source.live })
        .then(json => {
          if (!cancelled) setState({ symbols: Array.isArray(json.symbols) ? json.symbols : [], loading: false, error: null, sourceId });
        })
        .catch(err => {
          if (!cancelled) setState(s => ({ ...s, loading: false, error: err.message, sourceId }));
        });
    };
    load(true);
    const timer = source.symbolsRefreshMs ? setInterval(() => load(false), source.symbolsRefreshMs) : null;
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [sourceId, source]);

  return state.sourceId === sourceId ? state : { symbols: [], loading: true, error: null };
}

function hasSmc(json) {
  return json && SMC_KEYS.some(k => Array.isArray(json[k]) && json[k].length > 0);
}

/**
 * Loads one data file (and SMC overlays when wanted), refreshing live data.
 * tfOption: { tf, seconds, native, baseId } from buildTimeframeOptions.
 */
export function useChartFile(sourceId, tfOption, { wantSmc = false, autoRefresh = true } = {}) {
  const source = SOURCES[sourceId] || SOURCES.backtest;
  const baseId = tfOption?.baseId || null;
  // `raw` / `smc` hold the last finished request; a mismatching id means "loading"
  const [raw, setRaw] = useState({ id: null, json: null, error: null, lastUpdate: null });
  const [smc, setSmc] = useState({ id: null, json: null, error: null });
  const currentIdRef = useRef(baseId);

  const fetchRaw = useCallback((signal) => {
    if (!baseId) return;
    fetchJson(`${source.base}/${encodeURIComponent(baseId)}.json`, { bust: !!source.live, signal })
      .then(json => {
        if (currentIdRef.current === baseId) setRaw({ id: baseId, json, error: null, lastUpdate: new Date() });
      })
      .catch(err => {
        if (err.name === 'AbortError' || currentIdRef.current !== baseId) return;
        setRaw(r => (r.id === baseId && r.json ? { ...r, error: err.message } : { id: baseId, json: null, error: err.message, lastUpdate: null }));
      });
  }, [baseId, source]);

  useEffect(() => {
    currentIdRef.current = baseId;
    const ctrl = new AbortController();
    fetchRaw(ctrl.signal);
    return () => ctrl.abort();
  }, [baseId, fetchRaw]);

  useEffect(() => {
    if (!source.live || !autoRefresh || !baseId) return undefined;
    const timer = setInterval(() => fetchRaw(), source.refreshMs);
    return () => clearInterval(timer);
  }, [source, autoRefresh, baseId, fetchRaw]);

  // SMC overlays always come from the file of the timeframe on screen (SYMBOL_TF),
  // never from the smaller base timeframe a resampled view was built from.
  const smcId = baseId && tfOption?.tf ? `${baseId.replace(/_[^_]+$/, '')}_${tfOption.tf}` : null;
  const inlineSmc = !!tfOption?.native && smcId === baseId;
  const smcUrl = source.smcBase && smcId && !(inlineSmc && source.smcInline)
    ? `${source.smcBase}/${encodeURIComponent(smcId)}.json`
    : null;
  useEffect(() => {
    if (!wantSmc || !smcUrl || smc.id === smcUrl) return undefined;
    const ctrl = new AbortController();
    fetchJson(smcUrl, { signal: ctrl.signal })
      .then(json => setSmc({ id: smcUrl, json, error: null }))
      .catch(err => {
        if (err.name === 'AbortError') return;
        setSmc({ id: smcUrl, json: null, error: `No SMC data for ${smcId.replace('_', ' ')}` });
      });
    return () => ctrl.abort();
  }, [wantSmc, smcUrl, smcId, smc.id]);

  const current = raw.id === baseId
    ? { ...raw, loading: false }
    : { json: null, error: null, loading: !!baseId, lastUpdate: null };
  const json = current.json;

  const prepared = useMemo(() => {
    if (!json || !Array.isArray(json.candles)) return null;
    const candles = json.candles;
    const volume = Array.isArray(json.volume) ? json.volume : [];
    const trades = Array.isArray(json.trades) ? json.trades : [];
    const markers = Array.isArray(json.markers) ? json.markers : [];
    if (tfOption && !tfOption.native) {
      const r = resampleCandles(candles, volume, tfOption.seconds);
      const t = resampleTrades(trades, markers, tfOption.seconds);
      return { candles: r.candles, volume: r.volume, trades: t.trades, markers: t.markers, summary: json.summary, metadata: json.metadata };
    }
    return { candles, volume: volumeFromCandles(candles, volume), trades, markers, summary: json.summary, metadata: json.metadata };
  }, [json, tfOption]);

  let smcData = null;
  let smcState = { loading: false, error: null };
  if (inlineSmc && (source.smcInline || hasSmc(json))) {
    smcData = json;
  } else if (smcUrl && smc.id === smcUrl) {
    smcData = smc.json;
    smcState = { loading: false, error: smc.error };
  } else if (smcUrl && wantSmc) {
    smcState = { loading: true, error: null };
  }

  return {
    data: prepared,
    smc: smcData,
    smcState,
    loading: current.loading,
    error: current.error,
    lastUpdate: current.lastUpdate,
    refresh: () => fetchRaw(),
  };
}
