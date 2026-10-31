import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import './terminal.css';
import ChartView from './chart/ChartView';
import Legend from './chart/Legend';
import { createLegendStore } from './chart/legendStore';
import { SOURCES, useChartFile, useSymbolList } from './data/useTerminalData';
import { buildTimeframeOptions, normalizeTf, tfToSeconds } from './lib/timeframes';
import { buildSmcPrimitives, DEFAULT_SMC_VISIBILITY, SMC_OVERLAYS } from './lib/smcOverlays';
import { enrichTrades } from './lib/tradeStats';
import { loadJSON, saveJSON } from './lib/storage';
import { DEFAULT_INDICATORS, INDICATOR_BY_TYPE, newIndicatorInstance, sanitizeInstances } from './indicators/registry';
import TopBar from './ui/TopBar';
import LeftToolbar from './ui/LeftToolbar';
import RightPanel from './ui/RightPanel';
import StrategyTester from './ui/StrategyTester';
import SymbolSearch from './ui/SymbolSearch';
import IndicatorsDialog from './ui/IndicatorsDialog';
import IndicatorSettings from './ui/IndicatorSettings';
import GoToDate from './ui/GoToDate';
import Tooltip from './ui/Tooltip';
import { DrawingBar, TextEditor } from './ui/DrawingBar';
import MobileNav from './ui/MobileNav';
import { IconToEnd, IconWatchlist, IconTrades, IconOrder, IconExchange } from './ui/icons';
import { TOOL_DEFS } from './drawings/tools';
import { matches, NARROW, PHONE, SHORT, useMediaQuery, useWindowHeight } from './lib/useMediaQuery';
import TradePanel from './trading/TradePanel';
import OrderTicket from './trading/OrderTicket';
import { defaultLevels, num, useOrderDraft } from './trading/useOrderDraft';
import ChartMenu from './ui/ChartMenu';
import { formatDateTime, formatNumber, formatPrice } from './lib/format';
import OrderAlerts from './trading/OrderAlerts';
import SignalAlerts from './trading/SignalAlerts';
import { useTrading } from './trading/useTrading';
import { announceOrder, announceSignal, announceSignalFailed } from './trading/notify';
import { isOpenOrder } from './trading/orderText';
import ExchangeOrdersPanel from './trading/ExchangeOrdersPanel';
import { useExchangeOrders } from './trading/useExchangeOrders';
import { PENDING_FOCUS_KEY, eventTime, exchangeChartTrades, exchangeLines, exchangeMarkers, hasFill, levelOf } from './trading/exchangeOrders';
import ExchangeTradesBar from './trading/ExchangeTradesBar';
import './trading/trading.css';

/** useState that is mirrored to localStorage. */
function usePersisted(key, initial) {
  const [value, setValue] = useState(() => {
    const stored = loadJSON(key, undefined);
    return stored === undefined ? (typeof initial === 'function' ? initial() : initial) : stored;
  });
  useEffect(() => {
    saveJSON(key, value);
  }, [key, value]);
  return [value, setValue];
}

function groupSymbols(symbols) {
  const map = new Map();
  symbols.forEach(s => {
    if (!s?.id) return;
    const [symPart, tfPart] = String(s.id).split('_');
    const symbol = s.symbol || symPart;
    const tf = normalizeTf(s.timeframe || tfPart);
    if (!map.has(symbol)) map.set(symbol, { symbol, tfs: [] });
    map.get(symbol).tfs.push({ ...s, tf });
  });
  return [...map.values()]
    .map(g => {
      const tfs = g.tfs.sort((a, b) => (tfToSeconds(a.tf) || 0) - (tfToSeconds(b.tf) || 0));
      // Live entry-timeframe files repeat the trades of the signal timeframes (`tradesFrom`)
      const trades = tfs.reduce((n, t) => n + (t.tradesFrom ? 0 : t.trades || 0), 0);
      return { ...g, tfs, meta: trades ? `${trades} trades` : `${tfs.length} timeframe${tfs.length > 1 ? 's' : ''}` };
    })
    .sort((a, b) => a.symbol.localeCompare(b.symbol));
}

/** Live watchlist prices from symbols.json: the smallest timeframe of each symbol. */
function liveQuotes(groups) {
  const quotes = {};
  groups.forEach(g => {
    const t = g.tfs.find(x => Number.isFinite(x.lastPrice));
    if (t) quotes[g.symbol] = { last: t.lastPrice, chg: Number(t.changePct) || 0 };
  });
  return quotes;
}

function tradeMarkers(markers, rows) {
  const times = new Set();
  rows.forEach(r => {
    times.add(r.entryTime);
    if (Number.isFinite(r.exitTime)) times.add(r.exitTime);
  });
  return (markers || []).filter(m => times.has(m.time));
}

/** Low / high of the bars around `time`, so a focused exchange order shows whole candles. */
function barsRange(candles, time, around = 40) {
  const after = candles.findIndex(c => c.time > time);
  const i = after < 0 ? candles.length - 1 : Math.max(0, after - 1);
  const part = candles.slice(Math.max(0, i - around), i + around + 1);
  return [Math.min(...part.map(c => c.low)), Math.max(...part.map(c => c.high))];
}

// Orders tab (exchange account orders): exchange, symbol scope, history days, drawn on the chart, list filter
const DEFAULT_EXCHANGE_VIEW = { exchange: null, scope: 'symbol', days: 7, show: true, filter: 'open' };

function filterRows(rows, filter) {
  switch (filter) {
    case 'win': return rows.filter(r => r.closed && r.r > 0);
    case 'loss': return rows.filter(r => r.closed && r.r <= 0);
    case 'open': return rows.filter(r => !r.closed);
    default: return rows;
  }
}

// Shown on phones while a drawing tool waits for the chart to be touched
function toolHint(tool) {
  const def = TOOL_DEFS[tool];
  if (!def) return '';
  if (def.clicks === 'drag') return 'drag on the chart to draw';
  if (def.clicks === 1) return 'tap the chart to place it';
  return 'drag across the chart, or tap two points';
}

function isTyping(el) {
  if (!el) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
}

export default function TerminalPage() {
  const [params, setParams] = useSearchParams();
  const rootRef = useRef(null);
  const phone = useMediaQuery(PHONE);
  const short = useMediaQuery(SHORT);
  const windowHeight = useWindowHeight();
  const [legendStore] = useState(createLegendStore);
  const [engine, setEngineState] = useState(null);
  const setEngine = useCallback((e) => {
    // Handy for debugging in the browser console during development
    if (import.meta.env.DEV) window.__tvEngine = e;
    setEngineState(e);
  }, []);

  // ---- source / symbol / timeframe (URL first, then last used) -------------
  const [source, setSource] = useState(() => {
    const fromUrl = params.get('source');
    return SOURCES[fromUrl] ? fromUrl : (SOURCES[loadJSON('source', '')] ? loadJSON('source', '') : 'backtest');
  });
  const [symbol, setSymbol] = useState(() => params.get('symbol') || loadJSON(`last:${source}`, {})?.symbol || null);
  const [tf, setTf] = useState(() => params.get('tf') || loadJSON(`last:${source}`, {})?.tf || null);

  const symbolList = useSymbolList(source);
  const groups = useMemo(() => groupSymbols(symbolList.symbols), [symbolList.symbols]);
  const group = groups.find(g => g.symbol === symbol) || groups[0] || null;
  // Signal timeframes of the strategy config are the main ones; the rest stay in the menu
  const signalTfKey = (symbolList.signalTfs || []).map(normalizeTf).join(',');
  const tfOptions = useMemo(() => {
    const main = new Set(signalTfKey ? signalTfKey.split(',') : []);
    return buildTimeframeOptions(group?.tfs || []).map(o => ({ ...o, main: main.has(o.tf) }));
  }, [group, signalTfKey]);
  const tfOption = tfOptions.find(o => o.tf === normalizeTf(tf))
    || tfOptions.find(o => o.main)
    || tfOptions.find(o => o.native)
    || null;
  const activeSymbol = group?.symbol || null;
  const activeTf = tfOption?.tf || null;
  const sourceDef = SOURCES[source];

  useEffect(() => {
    saveJSON('source', source);
    if (!activeSymbol || !activeTf) return;
    saveJSON(`last:${source}`, { symbol: activeSymbol, tf: activeTf });
    const next = { source, symbol: activeSymbol, tf: activeTf };
    if (params.get('source') !== next.source || params.get('symbol') !== next.symbol || params.get('tf') !== next.tf) {
      setParams(next, { replace: true });
    }
  }, [source, activeSymbol, activeTf, params, setParams]);

  const changeSource = useCallback((id) => {
    if (id === source) return;
    const last = loadJSON(`last:${id}`, {}) || {};
    setSource(id);
    setSymbol(last.symbol || activeSymbol);
    setTf(last.tf || activeTf);
  }, [source, activeSymbol, activeTf]);

  // ---- persisted preferences -------------------------------------------------
  const [chartType, setChartType] = usePersisted('chartType', 'candles');
  const [indicators, setIndicators] = usePersisted('indicators', DEFAULT_INDICATORS);
  const safeIndicators = useMemo(() => sanitizeInstances(indicators) || DEFAULT_INDICATORS, [indicators]);
  const [smcVisibility, setSmcVisibility] = usePersisted('smcVisibility', DEFAULT_SMC_VISIBILITY);
  const [rightTab, setRightTab] = usePersisted('rightTabV2', 'trades');
  const [rightOpen, setRightOpen] = useState(() => !matches(NARROW) && loadJSON('rightOpen', true) !== false);
  // Phones (portrait or sideways) start with the tester closed: the chart comes first
  const [bottomOpen, setBottomOpen] = usePersisted('bottomOpen', () => !matches(PHONE) && !matches(SHORT));
  const [bottomHeight, setBottomHeight] = usePersisted('bottomHeight', () => (matches(PHONE) ? Math.round(window.innerHeight * 0.45) : 280));
  const [bottomTab, setBottomTab] = usePersisted('bottomTab', 'overview');
  const [magnet, setMagnet] = usePersisted('magnet', false);
  // Same default as fee_per_trade_r in the Python strategy config
  const [commissionR, setCommissionR] = usePersisted('commissionR', 0.5);
  const [exchangeView, setExchangeView] = usePersisted('exchangeOrders', DEFAULT_EXCHANGE_VIEW);
  const exSettings = { ...DEFAULT_EXCHANGE_VIEW, ...(exchangeView && typeof exchangeView === 'object' ? exchangeView : {}) };
  const commissionSetting = Number.isFinite(Number(commissionR)) && Number(commissionR) >= 0 ? Number(commissionR) : 0.5;
  // Exchange trades are real fills: no assumed backtest fee in their R
  const commission = sourceDef.exchangeTrades ? 0 : commissionSetting;

  useEffect(() => {
    saveJSON('rightOpen', rightOpen);
  }, [rightOpen]);

  // Collapse side panel when the window gets narrow
  useEffect(() => {
    let mq;
    try {
      mq = window.matchMedia(NARROW);
    } catch {
      return undefined;
    }
    const onChange = (e) => {
      if (e.matches) setRightOpen(false);
    };
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  // ---- transient UI ----------------------------------------------------------
  const [tool, setTool] = useState(null);
  // Phone: drawing palette shown over the chart
  const [toolsOpen, setToolsOpen] = useState(false);
  const [drawLocked, setDrawLocked] = useState(false);
  const [drawHidden, setDrawHidden] = useState(false);
  const [selectedDrawing, setSelectedDrawing] = useState(null);
  const [textEdit, setTextEdit] = useState(null);
  const [drawingCount, setDrawingCount] = useState(0);
  const [canUndo, setCanUndo] = useState(false);
  const [dialog, setDialog] = useState(null);
  const [settingsUid, setSettingsUid] = useState(null);
  const [tradeFilter, setTradeFilter] = useState('all');
  const [selectedKey, setSelectedKey] = useState(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [panesVersion, setPanesVersion] = useState(0);
  const [toast, setToast] = useState(null);
  // Right-click menu on the chart: { clientX, clientY, price }
  const [chartMenu, setChartMenu] = useState(null);
  const [orderAlerts, setOrderAlerts] = useState([]);
  const [focusSignalId, setFocusSignalId] = useState(null);
  // Order picked in the Trade panel history: { item, pending, found } (found = its trade is in the chart file)
  const [historyFocus, setHistoryFocus] = useState(null);
  // Exchange order / position picked in the Orders tab or on the Orders page (#/orders):
  // { kind: 'order' | 'position', key, item, pending }
  const [exFocus, setExFocus] = useState(() => {
    try {
      const f = JSON.parse(window.sessionStorage.getItem(PENDING_FOCUS_KEY) || 'null');
      return f?.item?.symbol ? { ...f, pending: true } : null;
    } catch {
      return null;
    }
  });
  // Taken once: a reload later opens the chart as usual
  useEffect(() => {
    try {
      window.sessionStorage.removeItem(PENDING_FOCUS_KEY);
    } catch {
      // storage blocked
    }
  }, []);
  // Signal cards hidden from the chart (they stay in the Trade panel)
  const [hiddenSignals, setHiddenSignals] = useState(() => new Set());
  // Latest show functions for desktop notification clicks (defined further down)
  const showRef = useRef({});
  // Stable so open dialogs keep focus while live data refreshes the page
  const closeDialog = useCallback(() => setDialog(null), []);
  const closeChartMenu = useCallback(() => setChartMenu(null), []);

  // ---- bot connection (signals to approve, manual orders) -------------------------
  const trading = useTrading({
    onSignal: (sig, { auto } = {}) => {
      setToast(`New signal: ${sig.symbol} ${sig.side === 'LONG' ? 'Long' : 'Short'} · ${auto ? 'auto trade: placing order' : 'waiting for approval'}`);
      announceSignal(sig, { auto, onClick: () => showRef.current.signal?.(sig) });
      if (!matches(NARROW)) {
        setRightTab('trade');
        setRightOpen(true);
      }
    },
    // Approved (here or on Telegram) but the exchange refused the order: its card shows why
    onSignalFailed: (sig) => {
      announceSignalFailed(sig, { onClick: () => showRef.current.signal?.(sig) });
      setToast(`Order failed: ${sig.side === 'LONG' ? 'Long' : 'Short'} ${sig.symbol}`);
      setHiddenSignals(set => {
        if (!set.has(sig.id)) return set;
        const next = new Set(set);
        next.delete(sig.id);
        return next;
      });
    },
    // Placed / filled / not filled: a card that stays until dismissed (one per order, newest last)
    onOrderChange: (order, change) => {
      announceOrder(order, change, { onClick: () => showRef.current.order?.(order) });
      setOrderAlerts(list => [
        ...list.filter(a => a.order.id !== order.id),
        { id: `${order.id}:${change}`, order, change },
      ].slice(-4));
    },
  });
  const pendingCount = trading.signals.filter(s => s.status === 'PENDING').length;
  // Order being prepared: ticket next to the chart + draggable lines on it
  const ticket = useOrderDraft(trading);
  const ticketDraft = ticket.draft;
  const ticketSignalId = ticketDraft?.signalId || null;

  // Exchange source: the trades on the exchange account (open + last 30) instead of the bot's trade log
  const exchangeSource = !!sourceDef.exchangeTrades;
  const exchangeTrades = useExchangeOrders({
    online: trading.conn === 'online',
    enabled: exchangeSource && !!activeSymbol,
    exchange: exSettings.exchange,
    symbol: activeSymbol,
    resource: 'trades',
    limit: 30,
    pollMs: 30000,
  });
  // Every order on the exchange account (Orders tab), read while the tab is open or drawn on the chart
  // (on the Exchange source the trades answer carries the working orders already)
  const exchangeOrders = useExchangeOrders({
    online: trading.conn === 'online',
    enabled: (rightOpen && rightTab === 'orders') || (exSettings.show && !exchangeSource),
    exchange: exSettings.exchange,
    symbol: exSettings.scope === 'all' ? null : activeSymbol,
    days: exSettings.days,
  });
  const exLineView = exchangeSource ? exchangeTrades : exchangeOrders;
  const exShow = exSettings.show && trading.conn === 'online';
  // Opened from the Orders page: the Orders tab shows the same exchange
  const focusExchangeName = exFocus?.exchange;
  useEffect(() => {
    if (focusExchangeName) setExchangeView(v => ({ ...DEFAULT_EXCHANGE_VIEW, ...v, exchange: focusExchangeName }));
  }, [focusExchangeName, setExchangeView]);
  // The bot's own exchange: its orders are already in the exchange list, do not draw them twice
  const exCoversBot = exShow && !!exLineView.current?.active && !!exLineView.data;
  const exFocusOrder = exFocus?.kind === 'order' ? exFocus.item : null;
  const exLines = useMemo(
    () => exchangeLines(exShow ? exLineView.data : { orders: [], positions: [] }, activeSymbol, exchangeSource ? null : exFocusOrder),
    [exShow, exLineView.data, activeSymbol, exFocusOrder, exchangeSource],
  );
  // Fill arrows of the Orders tab; the Exchange source draws whole trades instead
  const exMarkers = useMemo(
    () => (exShow && !exchangeSource ? exchangeMarkers(exchangeOrders.data, activeSymbol) : []),
    [exShow, exchangeSource, exchangeOrders.data, activeSymbol],
  );

  useEffect(() => {
    const base = document.title.replace(/^\(\d+\) /, '');
    document.title = pendingCount ? `(${pendingCount}) ${base}` : base;
  }, [pendingCount]);

  // ---- data -------------------------------------------------------------------
  const wantSmc = Object.values(smcVisibility).some(Boolean);
  // Live: symbols.json tells when the file on screen was re-exported
  const fileVersion = group?.tfs.find(t => t.id === tfOption?.baseId)?.updatedAt ?? null;
  const file = useChartFile(source, tfOption, { wantSmc, autoRefresh, version: fileVersion });
  const exTradesData = exchangeTrades.data;
  const data = useMemo(() => {
    const d = file.data;
    if (!d || !exchangeSource) return d;
    // Live candles with the exchange account's trades, snapped to the bars on screen
    const list = exTradesData && exTradesData.symbol === activeSymbol ? exTradesData.trades : [];
    return { ...d, ...exchangeChartTrades(list, d.candles) };
  }, [file.data, exchangeSource, exTradesData, activeSymbol]);
  const lastBar = data?.candles?.[data.candles.length - 1];
  const livePrice = sourceDef.live && lastBar ? lastBar.close : null;
  const rows = useMemo(() => enrichTrades(data?.trades, commission), [data, commission]);
  const filteredRows = useMemo(() => filterRows(rows, tradeFilter), [rows, tradeFilter]);
  const markers = useMemo(() => tradeMarkers(data?.markers, filteredRows), [data, filteredRows]);
  const dataKey = tfOption ? `${source}|${tfOption.baseId}|${tfOption.tf}` : null;

  // ---- push state into the chart engine -------------------------------------------
  useEffect(() => {
    if (!engine || !data) return;
    engine.setData(data.candles, data.volume, dataKey);
    legendStore.set(engine.legendAt(null), false);
  }, [engine, data, dataKey, legendStore]);

  useEffect(() => {
    if (!engine || !data) return;
    engine.setTrades(filteredRows, markers);
  }, [engine, data, filteredRows, markers]);

  // Fills on the exchange account (snapped to the bars on screen, so redone with new data)
  useEffect(() => {
    if (!engine || !data) return;
    engine.setExchangeMarkers(exMarkers);
  }, [engine, data, exMarkers]);

  const barDuration = tfOption?.seconds || tfToSeconds(tfOption?.tf) || 60;
  useEffect(() => {
    if (!engine || !data) return;
    engine.setSmcPrimitives(file.smc ? buildSmcPrimitives(file.smc, smcVisibility, { barDuration, candles: data.candles }) : []);
  }, [engine, data, file.smc, smcVisibility, barDuration]);

  useEffect(() => {
    if (!engine) return;
    engine.syncIndicators(safeIndicators);
    legendStore.set(engine.legendAt(null), false);
  }, [engine, safeIndicators, legendStore]);

  useEffect(() => {
    engine?.setChartType(chartType);
    if (engine) legendStore.set(engine.legendAt(null), false);
  }, [engine, chartType, legendStore]);

  const symbolRef = useRef(activeSymbol);
  useEffect(() => {
    symbolRef.current = activeSymbol;
    if (!engine || !activeSymbol) return;
    const saved = loadJSON(`drawings:${activeSymbol}`, []);
    // Emits onDrawingSelect(null) and onDrawingChange, which reset the UI state
    engine.drawings.setDrawings(saved);
  }, [engine, activeSymbol]);

  const historyLines = historyFocus && !historyFocus.pending && !historyFocus.found && historyFocus.item.symbol === activeSymbol
    ? historyFocus.item
    : null;

  // Entry / stop / target lines of the signals waiting on this symbol (and the one opened from the panel)
  useEffect(() => {
    if (!engine) return;
    const lines = [];
    trading.signals
      .filter(s => s.symbol === activeSymbol && s.id !== ticketSignalId
        && (s.status === 'PENDING' || s.status === 'PLACING' || s.id === focusSignalId))
      .forEach(s => {
        const tag = `${s.side === 'LONG' ? 'Long' : 'Short'} ${s.signalTimeframe}`.trim();
        lines.push(
          { price: s.entry, color: '#5b8bff', title: `${tag} entry` },
          { price: s.stopLoss, color: '#f23645', title: 'Stop' },
          { price: s.takeProfit, color: '#089981', title: 'Target' },
        );
      });
    // Limit orders on the exchange still waiting to fill
    trading.orders
      .filter(o => !exCoversBot && o.symbol === activeSymbol && o.orderType === 'limit' && isOpenOrder(o))
      .forEach(o => {
        lines.push(
          { price: o.price, color: '#5b8bff', title: `${o.side === 'LONG' ? 'Long' : 'Short'} limit`, dashed: false },
          { price: o.stopLoss, color: '#f23645', title: 'Order stop', dashed: false },
          { price: o.takeProfit, color: '#089981', title: 'Order target', dashed: false },
        );
      });
    // History order without a trade box in the chart file (web order, or older than the data)
    if (historyLines) {
      const tag = `${historyLines.side === 'LONG' ? 'Long' : 'Short'} ${String(historyLines.status || '').toLowerCase().replace('_', ' ')}`;
      lines.push(
        { price: historyLines.fillPrice || historyLines.entry, color: '#ff9800', title: tag, dashed: false },
        { price: historyLines.stopLoss, color: '#f23645', title: 'Stop', dashed: false },
        { price: historyLines.takeProfit, color: '#089981', title: 'Target', dashed: false },
      );
    }
    // Orders tab: positions and working orders on the exchange account
    lines.push(...exLines);
    engine.setSignalLines(lines);
  }, [engine, trading.signals, trading.orders, activeSymbol, focusSignalId, ticketSignalId, historyLines, exLines, exCoversBot]);

  // The order being prepared, with draggable entry / stop / target lines
  const draftOnChart = ticketDraft && ticketDraft.symbol === activeSymbol ? ticketDraft : null;
  const marketPrice = ticket.preview?.price ?? livePrice;
  useEffect(() => {
    if (!engine) return;
    if (!draftOnChart) {
      engine.setOrderDraft(null);
      return;
    }
    const pv = ticket.previewFresh ? ticket.preview : null;
    const base = draftOnChart.symbol.replace(/USDT$/, '');
    engine.setOrderDraft({
      side: draftOnChart.side,
      orderType: draftOnChart.orderType,
      entry: num(draftOnChart.entry),
      lastPrice: marketPrice,
      stopLoss: num(draftOnChart.stopLoss),
      takeProfit: num(draftOnChart.takeProfit),
      extra: pv ? {
        entry: `${pv.quantity} ${base}`,
        stopLoss: `−$${formatNumber(pv.riskUsd)}`,
        takeProfit: `+$${formatNumber(pv.rewardUsd)}`,
      } : {},
    });
  }, [engine, draftOnChart, marketPrice, ticket.preview, ticket.previewFresh]);

  // New orders without levels get a stop / target from the recent bars once the live data is on screen
  useEffect(() => {
    const d = ticketDraft;
    const candles = data?.candles;
    if (!d?.autoLevels || !sourceDef.live || d.symbol !== activeSymbol || !candles?.length || !engine) return;
    const entry = d.orderType === 'limit' && num(d.entry) ? num(d.entry) : candles[candles.length - 1].close;
    const lv = defaultLevels(d.side, entry, candles);
    ticket.update({
      autoLevels: false,
      stopLoss: num(d.stopLoss) ?? engine.roundPrice(lv.stopLoss),
      takeProfit: num(d.takeProfit) ?? engine.roundPrice(lv.takeProfit),
      ...(d.orderType === 'limit' && !num(d.entry) ? { entry } : {}),
    });
  }, [ticketDraft, sourceDef.live, activeSymbol, data, engine, ticket]);

  useEffect(() => { engine?.drawings.setTool(tool); }, [engine, tool]);
  useEffect(() => { engine?.setCompact(phone); }, [engine, phone]);
  useEffect(() => { engine?.drawings.setMagnet(magnet); }, [engine, magnet]);
  useEffect(() => { engine?.drawings.setLocked(drawLocked); }, [engine, drawLocked]);
  useEffect(() => { engine?.drawings.setHidden(drawHidden); }, [engine, drawHidden]);

  // ---- engine callbacks ----------------------------------------------------------------
  const handlers = {
    onCrosshair: (d, hovering) => legendStore.set(d, hovering),
    onTradeClick: (trade) => {
      setSelectedKey(trade.key);
      if (!rightOpen && !bottomOpen) return;
      if (rightOpen) setRightTab('trades');
    },
    onDrawingChange: (list) => {
      if (symbolRef.current) saveJSON(`drawings:${symbolRef.current}`, list);
      setDrawingCount(list.length);
      setCanUndo(!!engine?.drawings.canUndo());
    },
    onDrawingSelect: (d) => {
      setSelectedDrawing(d ? { ...d } : null);
      if (!d) setTextEdit(null);
    },
    onToolChange: (t) => setTool(t),
    onTextEdit: (d) => {
      const pos = engine?.drawings.anchorOf(d.id);
      if (pos) setTextEdit({ id: d.id, initial: d.style?.text || '', ...pos });
    },
    onPanesChange: () => setPanesVersion(v => v + 1),
    // Lines of the order ticket dragged on the chart
    onOrderDrag: (lv) => {
      ticket.update({
        orderType: lv.orderType,
        ...(lv.orderType === 'limit' ? { entry: lv.entry } : {}),
        ...(Number.isFinite(lv.stopLoss) ? { stopLoss: lv.stopLoss } : {}),
        ...(Number.isFinite(lv.takeProfit) ? { takeProfit: lv.takeProfit } : {}),
      });
    },
    onChartMenu: (m) => setChartMenu(m),
  };

  const selectTrade = useCallback((row) => {
    if (!row) return;
    setSelectedKey(row.key);
    // A trade outside the candles on screen: say why instead of scrolling to an empty chart
    const candles = data?.candles;
    if (candles?.length) {
      const first = candles[0].time;
      const last = candles[candles.length - 1].time;
      const entry = Number(row.entryTimeRaw ?? row.entryTime);
      const exit = Number(row.exitTimeRaw ?? row.exitTime);
      if (entry > last + Math.max(3 * barDuration, 300)) {
        setToast(`#${row.n} opened ${formatDateTime(entry)} UTC, after the chart data (ends ${formatDateTime(last)} UTC). Is the live export running? Start the bot with ./start.sh`);
        return;
      }
      if ((Number.isFinite(exit) ? exit : entry) < first) {
        setToast(`#${row.n} (${formatDateTime(entry)} UTC) is older than the chart data, which starts ${formatDateTime(first)} UTC`);
        return;
      }
    }
    engine?.focusTrade(row);
  }, [engine, data, barDuration]);

  // History click: once the order's symbol / timeframe is on screen, focus its trade (or its prices)
  useEffect(() => {
    const f = historyFocus;
    if (!f?.pending || !engine || source !== 'live' || !data?.candles?.length) return;
    const it = f.item;
    if (activeSymbol !== it.symbol || data.metadata?.symbol !== it.symbol) return;
    const wantTf = it.timeframe ? normalizeTf(it.timeframe) : null;
    if (wantTf && tfOptions.some(o => o.tf === wantTf) && activeTf !== wantTf) return;
    const row = it.source === 'signal' && Number.isFinite(it.entryTime)
      ? rows.find(r => r.type === it.side && r.entryTimeRaw === it.entryTime && (!r.timeframe || !it.timeframe || r.timeframe === it.timeframe))
      : null;
    const at = it.entryTime || it.time;
    // Runs once per click, after the symbol's data has loaded (it cannot be done in the click handler)
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (row) selectTrade(row);
    else engine.focusTrade({ entryTime: at, entryPrice: it.fillPrice || it.entry, stopLoss: it.stopLoss, takeProfit: it.takeProfit });
    setHistoryFocus({ item: it, pending: false, found: !!row });
    if (!row && at < data.candles[0].time) setToast('This order is older than the chart data: only its prices are shown');
  }, [historyFocus, engine, source, data, activeSymbol, activeTf, tfOptions, rows, selectTrade]);

  // Orders tab click: once the symbol's data is on screen, show the fill / order level / position
  useEffect(() => {
    const f = exFocus;
    if (!f?.pending || !engine || !sourceDef.live || !data?.candles?.length || activeSymbol !== f.item.symbol) return;
    const it = f.item;
    const candles = data.candles;
    const last = candles[candles.length - 1];
    let older = false;
    const recent = barsRange(candles, last.time);
    // Exchange source: wait for the account's trades, then select the trade the order belongs to
    if (exchangeSource && (!exTradesData || exTradesData.symbol !== it.symbol || (f.exchange && exTradesData.exchange !== f.exchange))) return;
    const tradeRow = exchangeSource
      ? rows.find(r => (f.kind === 'position' ? !r.closed && r.type === it.side : (r.orders || []).includes(it.id)))
      : null;
    // Runs once per click, after the symbol's data (and its exchange trades) have loaded:
    // it cannot be done in the click handler
    if (tradeRow) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      selectTrade(tradeRow);
    } else if (f.kind === 'position') {
      engine.focusTrade({ entryTime: last.time, entryPrice: it.entryPrice, stopLoss: it.stopLoss, takeProfit: it.takeProfit, priceRange: recent });
    } else if (it.status === 'open' && !hasFill(it)) {
      // Still working: recent bars, with the order's price in view
      engine.focusTrade({ entryTime: last.time, entryPrice: levelOf(it), priceRange: recent });
    } else {
      const at = eventTime(it);
      older = at < candles[0].time;
      engine.focusTrade({ entryTime: at, entryPrice: hasFill(it) ? it.average : levelOf(it), priceRange: older ? [] : barsRange(candles, at) });
    }
    setExFocus({ ...f, pending: false });
    if (older) setToast('This order is older than the chart data: only its price is shown');
  }, [exFocus, engine, sourceDef.live, data, activeSymbol, exchangeSource, exTradesData, rows, selectTrade]);

  // ---- indicator actions ------------------------------------------------------------
  const addIndicator = (type) => {
    const def = INDICATOR_BY_TYPE[type];
    if (def?.kind === 'volume' && safeIndicators.some(i => i.type === 'volume')) return;
    const inst = newIndicatorInstance(type);
    if (inst) setIndicators([...safeIndicators, inst]);
  };
  const toggleIndicator = (uid) => setIndicators(safeIndicators.map(i => (i.uid === uid ? { ...i, visible: !i.visible } : i)));
  const removeIndicator = (uid) => setIndicators(safeIndicators.filter(i => i.uid !== uid));
  const applyIndicator = (inst) => setIndicators(safeIndicators.map(i => (i.uid === inst.uid ? inst : i)));
  const toggleSmc = (key) => setSmcVisibility(v => ({ ...v, [key]: !v[key] }));

  // ---- keyboard shortcuts --------------------------------------------------------------
  const keyState = useRef({});
  useLayoutEffect(() => {
    keyState.current = { engine, dialog: dialog || settingsUid, filteredRows, selectedKey, selectTrade, textEdit, closeTicket: ticketDraft ? ticket.close : null };
  });
  useEffect(() => {
    const onKey = (e) => {
      const s = keyState.current;
      if (s.dialog || s.textEdit || isTyping(e.target)) return;
      const mod = e.ctrlKey || e.metaKey;
      if (e.altKey && !mod) {
        const map = { KeyT: 'trend', KeyH: 'hline', KeyV: 'vline', KeyF: 'fib' };
        if (e.code === 'KeyR' && e.shiftKey) {
          setTool('rect');
        } else if (e.code === 'KeyR') {
          s.engine?.resetView();
        } else if (e.code === 'KeyG') {
          if (s.engine?.times.length) setDialog('goto');
        } else if (map[e.code]) {
          setTool(map[e.code]);
        } else {
          return;
        }
        e.preventDefault();
        return;
      }
      if (mod && !e.shiftKey && e.code === 'KeyZ') {
        if (s.engine?.drawings.undo()) {
          setCanUndo(s.engine.drawings.canUndo());
          e.preventDefault();
        }
        return;
      }
      if (e.key === 'Escape') {
        if (s.engine?.drawings.cancel()) {
          setTool(null);
          return;
        }
        if (s.closeTicket) {
          s.closeTicket();
          return;
        }
        setHistoryFocus(null);
        setExFocus(null);
        if (s.selectedKey) setSelectedKey(null);
        return;
      }
      if ((e.key === 'Delete' || e.key === 'Backspace') && !mod) {
        if (s.engine?.drawings.deleteSelected()) e.preventDefault();
        return;
      }
      if (e.key === '/' && !mod) {
        e.preventDefault();
        setDialog('indicators');
        return;
      }
      if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && s.filteredRows.length && !mod) {
        // Step through trades like the old trade list
        if (e.target.closest?.('.tv-dialog-list, .tv-pop, [role="menu"]')) return;
        const rows = s.filteredRows;
        const idx = rows.findIndex(r => r.key === s.selectedKey);
        const next = idx < 0 ? 0 : Math.max(0, Math.min(rows.length - 1, idx + (e.key === 'ArrowDown' ? 1 : -1)));
        e.preventDefault();
        s.selectTrade(rows[next]);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // ---- screenshot -------------------------------------------------------------------------
  const takeScreenshot = () => {
    if (!engine) return;
    const shot = engine.takeScreenshot();
    const header = 36;
    const ratio = shot.width / (engine.container.clientWidth || shot.width);
    const canvas = document.createElement('canvas');
    canvas.width = shot.width;
    canvas.height = shot.height + header * ratio;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#131722';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.scale(ratio, ratio);
    const fontFamily = getComputedStyle(rootRef.current).fontFamily;
    const title = `${activeSymbol} · ${activeTf} · ${sourceDef.label}`;
    ctx.font = `600 15px ${fontFamily}`;
    ctx.fillStyle = '#f0f3fa';
    ctx.textBaseline = 'middle';
    ctx.fillText(title, 12, header / 2);
    const titleWidth = ctx.measureText(title).width;
    const legend = engine.legendAt(null);
    if (legend) {
      const b = legend.bar;
      ctx.font = `13px ${fontFamily}`;
      ctx.fillStyle = legend.change >= 0 ? '#089981' : '#f23645';
      ctx.fillText(`O ${engine.formatPrice(b.open)}  H ${engine.formatPrice(b.high)}  L ${engine.formatPrice(b.low)}  C ${engine.formatPrice(b.close)}`, 12 + titleWidth + 16, header / 2);
    }
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.drawImage(shot, 0, header * ratio);
    const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '');
    canvas.toBlob((blob) => {
      if (!blob) return;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${activeSymbol}_${activeTf}_${stamp}.png`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setToast('Chart image downloaded');
    }, 'image/png');
  };

  useEffect(() => {
    if (!toast) return undefined;
    // Long messages (why a trade cannot be shown) stay long enough to be read
    const t = setTimeout(() => setToast(null), Math.min(9000, Math.max(2200, toast.length * 55)));
    return () => clearTimeout(t);
  }, [toast]);

  // ---- derived view state -------------------------------------------------------------------
  const smcCounts = useMemo(() => {
    if (file.smc) return Object.fromEntries(SMC_OVERLAYS.map(o => [o.key, Array.isArray(file.smc[o.key]) ? file.smc[o.key].length : 0]));
    if (!tfOption?.native) return null;
    const entry = group?.tfs.find(t => t.id === tfOption?.baseId);
    return entry?.indicators || null;
  }, [file.smc, group, tfOption]);

  let smcNote = null;
  if (sourceDef.live && !file.smc) {
    const natives = (group?.tfs || []).map(t => t.tf).join(', ');
    smcNote = tfOption && !tfOption.native
      ? `On Live, SMC overlays come with the bot's timeframes (${natives}). Pick one of them to see the overlays.`
      : 'This live file has no SMC data yet. The live export (started by start.sh) adds it on its next update.';
  } else if (file.smcState.loading) smcNote = 'Loading SMC data…';
  else if (file.smcState.error) {
    smcNote = `${file.smcState.error}. SMC overlays only show on timeframes with their own SMC file `
      + `(export: python -m src.indicators.main --tfs ${tfOption?.tf || '1h'}).`;
  }

  const lastClose = data?.candles?.[data.candles.length - 1];
  const firstOpen = data?.candles?.[0];
  const currentQuote = sourceDef.live && lastClose && firstOpen
    ? { last: lastClose.close, chg: ((lastClose.close - firstOpen.open) / firstOpen.open) * 100 }
    : null;

  const settingsInst = settingsUid ? safeIndicators.find(i => i.uid === settingsUid) : null;
  // Leave the chart at least ~200px; on short (sideways phone) screens the tester gets at most ~half
  const maxBottom = short ? Math.max(140, Math.round(windowHeight * 0.5)) : Math.max(200, windowHeight - 200);
  let testerEmpty = !sourceDef.hasTrades
    ? 'SMC data files have no trades. Switch to Backtest to use the Strategy Tester.'
    : rows.length === 0 && data ? 'No trades in this file yet.' : null;
  if (exchangeSource && rows.length === 0) {
    if (trading.conn !== 'online') testerEmpty = 'Connect to the bot (Trade panel) to load your exchange trades.';
    else if (exchangeTrades.error || exchangeTrades.accountsError) testerEmpty = exchangeTrades.error || exchangeTrades.accountsError;
    else if (!exTradesData) testerEmpty = 'Loading exchange trades…';
    else testerEmpty = `No ${activeSymbol} trades on ${exTradesData.label} in the last ${exTradesData.days} days.`;
  }

  // With data on screen a failed refresh only shows in the LIVE badge (OFFLINE)
  const liveOffline = sourceDef.live && data ? (symbolList.error || file.error || null) : null;
  let chartMessage = null;
  if (symbolList.error && groups.length === 0) {
    chartMessage = (<div><strong>Could not load the symbol list</strong>{symbolList.error}</div>);
  } else if (!symbolList.loading && !symbolList.error && groups.length === 0) {
    chartMessage = (<div><strong>No symbols</strong>{`${sourceDef.base}/symbols.json lists no data files.`}</div>);
  } else if (file.error && !data) {
    chartMessage = (<div><strong>Could not load {activeSymbol} {activeTf}</strong>{file.error}</div>);
  } else if ((file.loading || symbolList.loading) && !data) {
    chartMessage = (<div><div className="tv-spinner" aria-hidden="true" />Loading {activeSymbol || 'data'} {activeTf || ''}…</div>);
  }

  // Sources without trades (SMC data) show the watchlist instead of the trade list
  const shownRightTab = rightTab === 'trade' || rightTab === 'orders' || sourceDef.hasTrades ? rightTab : 'watchlist';
  const quotes = useMemo(() => (sourceDef.live ? liveQuotes(groups) : {}), [sourceDef.live, groups]);

  // Signals waiting for a decision, shown over the chart unless the Trade panel (which lists them) is open
  const tradePanelOpen = rightOpen && shownRightTab === 'trade';
  const recentMs = trading.serverNow() - 5 * 60000;
  const alertSignals = trading.conn === 'online' && !tradePanelOpen
    ? trading.signals
      .filter(s => !hiddenSignals.has(s.id)
        && (s.status === 'PENDING' || s.status === 'PLACING' || (s.status === 'FAILED' && s.decidedAt * 1000 > recentMs)))
      .sort((a, b) => a.createdAt - b.createdAt)
    : [];

  // ---- trading actions -------------------------------------------------------------------

  // Phones have room for one drawer under the chart: a side panel or the tester
  const openRight = (tab) => {
    setRightTab(tab);
    setRightOpen(true);
    if (phone) setBottomOpen(false);
  };

  const toggleTester = () => {
    if (!bottomOpen && phone) setRightOpen(false);
    setBottomOpen(!bottomOpen);
  };

  const openTradePanel = (message) => {
    openRight('trade');
    if (message) setToast(message);
  };

  const tradable = trading.conn === 'online' && !!activeSymbol && !!trading.config?.symbols?.includes(activeSymbol);

  const canTicket = () => {
    if (trading.conn !== 'online') {
      openTradePanel('Connect to the bot in the Trade panel first');
      return false;
    }
    if (!tradable) {
      setToast(`${activeSymbol || 'This symbol'} is not one of the bot symbols`);
      return false;
    }
    return true;
  };

  /**
   * Open the order on the chart: draggable lines + ticket. Without stop / target they are
   * set from the recent bars once live data is on screen (orders are always made on Live).
   */
  const startOrder = ({ side, orderType = 'market', entry, stopLoss, takeProfit }) => {
    if (!canTicket()) return;
    if (!sourceDef.live) {
      changeSource('live');
      setSymbol(activeSymbol);
      setToast('Switched to Live data to place the order');
    }
    ticket.open({
      symbol: activeSymbol,
      side,
      orderType,
      entry,
      stopLoss,
      takeProfit,
      autoLevels: !Number.isFinite(stopLoss) || !Number.isFinite(takeProfit),
    });
    // The side panel would hide the order zone on narrow screens
    if (matches(NARROW)) setRightOpen(false);
  };

  const tradeFromDrawing = () => {
    const d = engine?.drawings.getSelected();
    if (!d || (d.type !== 'long' && d.type !== 'short')) return;
    const [entry, target, stop] = d.points;
    const near = Number.isFinite(livePrice) && Math.abs(entry.price - livePrice) / livePrice <= 0.001;
    startOrder({
      side: d.type === 'long' ? 'LONG' : 'SHORT',
      orderType: near ? 'market' : 'limit',
      entry: entry.price,
      takeProfit: target.price,
      stopLoss: stop.price,
    });
  };

  const newOrder = (side) => {
    startOrder({ side, orderType: 'market' });
    engine?.scrollToRealtime();
  };

  const showSignal = (sig) => {
    setFocusSignalId(sig.id);
    if (!sourceDef.live) changeSource('live');
    setSymbol(sig.symbol);
    if (sig.signalTimeframe) setTf(sig.signalTimeframe);
    engine?.scrollToRealtime();
    if (matches(NARROW)) setRightOpen(false);
  };

  // Approve a signal with edited levels: show it on the chart and open the ticket prefilled
  const editSignal = (sig) => {
    if (trading.conn !== 'online') {
      setToast('The bot is not connected');
      return;
    }
    showSignal(sig);
    ticket.open({
      symbol: sig.symbol,
      side: sig.side,
      orderType: 'market',
      entry: sig.entry,
      stopLoss: sig.stopLoss,
      takeProfit: sig.takeProfit,
      risk: sig.riskPerTrade,
      signalId: sig.id,
      timeframe: sig.signalTimeframe,
      bot: { entry: sig.entry, stopLoss: sig.stopLoss, takeProfit: sig.takeProfit },
    });
  };

  // Right-click menu: start an order at that price, or move the open order's levels there
  const chartMenuItems = () => {
    if (!chartMenu) return [];
    const price = chartMenu.price;
    const at = formatPrice(price);
    if (trading.conn !== 'online') {
      return [{ label: 'Connect to the bot to trade', onSelect: () => openTradePanel() }];
    }
    if (!tradable) return [{ label: `${activeSymbol || 'This symbol'} is not one of the bot symbols`, disabled: true }];
    const above = Number.isFinite(livePrice) && price > livePrice;
    const items = [];
    if (draftOnChart) {
      items.push(
        { label: `Entry here (limit) ${at}`, onSelect: () => ticket.update({ orderType: 'limit', entry: price }) },
        { label: `Stop here ${at}`, tone: 'down', onSelect: () => ticket.update({ stopLoss: price }) },
        { label: `Target here ${at}`, tone: 'up', onSelect: () => ticket.update({ takeProfit: price }) },
        'sep',
      );
    }
    items.push(
      { label: `Long limit ${at}`, hint: above ? 'above price: fills now' : '', tone: 'up', onSelect: () => startOrder({ side: 'LONG', orderType: 'limit', entry: price }) },
      { label: `Short limit ${at}`, hint: !above && Number.isFinite(livePrice) ? 'below price: fills now' : '', tone: 'down', onSelect: () => startOrder({ side: 'SHORT', orderType: 'limit', entry: price }) },
      'sep',
      { label: 'Long at market', tone: 'up', onSelect: () => startOrder({ side: 'LONG', orderType: 'market' }) },
      { label: 'Short at market', tone: 'down', onSelect: () => startOrder({ side: 'SHORT', orderType: 'market' }) },
    );
    return items;
  };

  // Order history (Trade panel): show that order on the chart
  const focusHistory = (item) => {
    if (source !== 'live') changeSource('live');
    setSymbol(item.symbol);
    if (item.timeframe) setTf(item.timeframe);
    setTradeFilter('all');
    setHistoryFocus({ item, pending: true });
    if (matches(NARROW)) setRightOpen(false);
  };

  // Orders tab: show an exchange order / position on the Exchange chart (its trade box)
  const focusExchange = (f) => {
    const sym = f.item.symbol;
    if (sourceDef.live && groups.length && !groups.some(g => g.symbol === sym)) {
      setExFocus({ ...f, pending: false });
      setToast(`${sym} has no live chart data (the live export only covers the bot symbols)`);
      return;
    }
    if (!exchangeSource) changeSource('exchange');
    setSymbol(sym);
    setExFocus({ ...f, pending: true });
    if (matches(NARROW)) setRightOpen(false);
  };

  const showOrder = (order) => {
    if (!sourceDef.live) changeSource('live');
    setSymbol(order.symbol);
    if (order.timeframe) setTf(order.timeframe);
    engine?.scrollToRealtime();
    if (matches(NARROW)) setRightOpen(false);
  };

  useLayoutEffect(() => {
    showRef.current = { signal: showSignal, order: showOrder };
  });

  // Decision from a signal card over the chart: the card closes, so confirm it with a toast
  const decideFromAlert = async (id, action) => {
    const sig = trading.signals.find(s => s.id === id);
    const res = await trading.decide(id, action);
    if (res?.ok && sig) {
      const what = `${sig.side === 'LONG' ? 'Long' : 'Short'} ${sig.symbol}`;
      setToast(action === 'approve' ? `Approved: ${what} · order sent, waiting for the exchange to fill` : `Rejected: ${what}`);
    }
    return res;
  };

  const toggleRight = (tab) => {
    if (rightOpen && shownRightTab === tab) {
      setRightOpen(false);
    } else {
      openRight(tab);
    }
  };

  const toolbarProps = {
    tool,
    // Phone: close the palette once a tool is picked so the whole chart can be drawn on
    onTool: (t) => {
      setTool(t);
      if (phone && t) setToolsOpen(false);
    },
    magnet,
    onMagnet: setMagnet,
    locked: drawLocked,
    onLocked: setDrawLocked,
    hidden: drawHidden,
    onHidden: setDrawHidden,
    onRemoveAll: () => engine?.drawings.removeAll(),
    hasDrawings: drawingCount > 0,
  };

  return (
    <div className="tv" ref={rootRef}>
      <TopBar
        symbol={activeSymbol}
        tf={activeTf}
        tfOptions={tfOptions}
        onTf={setTf}
        chartType={chartType}
        onChartType={setChartType}
        source={source}
        onSource={changeSource}
        onOpenSymbol={() => setDialog('symbol')}
        onOpenIndicators={() => setDialog('indicators')}
        onGoTo={data?.candles?.length ? () => setDialog('goto') : null}
        live={sourceDef.live ? {
          lastUpdate: file.lastUpdate,
          autoRefresh,
          offline: liveOffline,
          onToggle: () => setAutoRefresh(a => !a),
          onRefresh: file.refresh,
        } : null}
        bot={{ conn: trading.conn, pending: pendingCount, auto: trading.approval.enabled && trading.approval.auto, onClick: () => toggleRight('trade') }}
        onScreenshot={takeScreenshot}
        onUndo={() => {
          engine?.drawings.undo();
          setCanUndo(!!engine?.drawings.canUndo());
        }}
        canUndo={canUndo}
        compact={phone}
      />

      <div className="tv-body">
        {!phone && <LeftToolbar {...toolbarProps} />}

        <main className="tv-center">
          <ChartView onEngine={setEngine} handlers={handlers}>
            <Legend
              store={legendStore}
              engine={engine}
              title={activeSymbol ? `${activeSymbol} · ${activeTf} · ${sourceDef.label}${exchangeSource && exchangeTrades.current ? ` ${exchangeTrades.current.label}` : ''}` : ''}
              indicators={safeIndicators}
              panesVersion={panesVersion}
              onToggle={toggleIndicator}
              onSettings={setSettingsUid}
              onRemove={removeIndicator}
            />
            {selectedDrawing && !textEdit && (
              <DrawingBar
                drawing={selectedDrawing}
                onStyle={(patch) => engine?.drawings.updateStyle(selectedDrawing.id, patch)}
                onLock={() => engine?.drawings.setDrawingLocked(selectedDrawing.id, !selectedDrawing.locked)}
                onClone={() => engine?.drawings.cloneSelected()}
                onDelete={() => engine?.drawings.deleteSelected()}
                onEditText={() => handlers.onTextEdit(selectedDrawing)}
                onTrade={tradeFromDrawing}
              />
            )}
            {textEdit && (
              <TextEditor
                key={textEdit.id}
                initial={textEdit.initial}
                x={textEdit.x}
                y={textEdit.y}
                onCommit={(text) => {
                  engine?.drawings.commitText(textEdit.id, text);
                  setTextEdit(null);
                }}
                onCancel={() => {
                  engine?.drawings.commitText(textEdit.id, textEdit.initial);
                  setTextEdit(null);
                }}
              />
            )}
            <button
              type="button"
              className="tv-chart-fab"
              style={{ right: 72, bottom: 36 }}
              onClick={() => engine?.scrollToRealtime()}
              aria-label="Scroll to the most recent bar"
              data-tip="Go to realtime"
              data-tip-side="left"
            >
              <IconToEnd size={16} />
            </button>
            {phone && toolsOpen && <LeftToolbar {...toolbarProps} floating />}
            {phone && tool && !toolsOpen && (
              <div className="tv-tool-hint" role="status">
                <span><b>{TOOL_DEFS[tool]?.label}</b>: {toolHint(tool)}</span>
                <button type="button" className="tv-btn tv-btn--ghost tv-btn--xs" onClick={() => setTool(null)}>Cancel</button>
              </div>
            )}
            {chartMessage && <div className="tv-chart-msg" role="status">{chartMessage}</div>}
            {exchangeSource && (
              <ExchangeTradesBar
                conn={trading.conn}
                view={exchangeTrades}
                symbol={activeSymbol}
                trades={rows}
                // Candles stop well before the time they were read: the live export is not running
                staleSince={lastBar && file.lastUpdate && file.lastUpdate.getTime() / 1000 - lastBar.time > Math.max(900, 5 * barDuration) ? lastBar.time : null}
                onExchange={(name) => {
                  setExFocus(null);
                  setExchangeView(v => ({ ...DEFAULT_EXCHANGE_VIEW, ...v, exchange: name }));
                }}
                onConnect={() => openRight('trade')}
              />
            )}
            {ticketDraft && (
              <OrderTicket
                key={ticketDraft.id}
                order={ticket}
                trading={trading}
                lastPrice={ticketDraft.symbol === activeSymbol ? livePrice : null}
                signal={ticketDraft.signalId ? trading.signals.find(s => s.id === ticketDraft.signalId) : undefined}
                onClose={ticket.close}
                onPlaced={(res) => {
                  const d = ticketDraft;
                  ticket.close();
                  const what = `${d.side === 'LONG' ? 'Long' : 'Short'} ${d.symbol}`;
                  const limit = (res.order?.orderType || res.signal?.orderType) === 'limit';
                  setToast(`${d.signalId ? 'Approved' : 'Order sent'}: ${what}${limit ? ' limit' : ''} · waiting for the exchange to fill`);
                }}
              />
            )}
          </ChartView>

          <StrategyTester
            rows={filteredRows}
            allRows={rows}
            open={bottomOpen}
            onToggle={setBottomOpen}
            height={Math.min(bottomHeight, maxBottom)}
            onHeight={setBottomHeight}
            maxHeight={maxBottom}
            tab={bottomTab}
            onTab={setBottomTab}
            selectedId={selectedKey}
            onSelect={selectTrade}
            title={activeSymbol ? `${activeSymbol} ${activeTf} · ${rows.length} trades` : ''}
            emptyNote={testerEmpty}
            onEmptyAction={!sourceDef.hasTrades ? () => changeSource('backtest') : null}
            commission={commission}
            onCommission={sourceDef.exchangeTrades ? null : setCommissionR}
            compact={phone}
          />
        </main>

        {rightOpen && (
          <RightPanel
            tab={shownRightTab}
            compact={phone}
            onClose={() => setRightOpen(false)}
            watchlist={{
              groups,
              symbol: activeSymbol,
              onSymbol: (s) => {
                setSymbol(s);
                if (matches(NARROW)) setRightOpen(false);
              },
              sourceId: sourceDef.live ? 'live' : source,
              currentQuote,
              quotes,
              baseTf: tfOption?.baseTf || null,
            }}
            trades={{
              rows: filteredRows,
              filter: tradeFilter,
              onFilter: setTradeFilter,
              selectedId: selectedKey,
              onSelect: selectTrade,
              showOpen: !!sourceDef.live,
            }}
            trade={(
              <TradePanel
                trading={trading}
                activeSymbol={activeSymbol}
                onShowSignal={showSignal}
                onEditSignal={editSignal}
                onShowOrder={showOrder}
                onNewOrder={newOrder}
                onFocusHistory={focusHistory}
                focusedHistoryId={historyFocus?.item.id || null}
              />
            )}
            orders={(
              <ExchangeOrdersPanel
                conn={trading.conn}
                view={exchangeOrders}
                settings={exSettings}
                onSettings={(next) => {
                  // The picked order belongs to the exchange shown
                  if (next.exchange !== exSettings.exchange) setExFocus(null);
                  setExchangeView(next);
                }}
                activeSymbol={activeSymbol}
                focusedKey={exFocus?.key || null}
                onFocus={focusExchange}
                onConnect={() => openRight('trade')}
              />
            )}
          />
        )}

        {!phone && (
          <nav className="tv-panel tv-side-strip" aria-label="Panels">
            <button type="button" className="tv-tool" aria-pressed={rightOpen && shownRightTab === 'watchlist'} onClick={() => toggleRight('watchlist')} aria-label="Watchlist" data-tip="Watchlist" data-tip-side="left">
              <IconWatchlist size={20} />
            </button>
            <button type="button" className="tv-tool" aria-pressed={rightOpen && shownRightTab === 'trades'} onClick={() => toggleRight('trades')} aria-label="Trades" data-tip="Trades" data-tip-side="left">
              <IconTrades size={20} />
            </button>
            <button
              type="button"
              className="tv-tool"
              aria-pressed={rightOpen && shownRightTab === 'trade'}
              onClick={() => toggleRight('trade')}
              aria-label={pendingCount ? `Trade, ${pendingCount} signal${pendingCount > 1 ? 's' : ''} waiting` : 'Trade'}
              data-tip="Trade"
              data-tip-side="left"
            >
              <IconOrder size={20} />
              {pendingCount > 0 && <span className="tv-strip-badge" aria-hidden="true">{pendingCount}</span>}
            </button>
            <button type="button" className="tv-tool" aria-pressed={rightOpen && shownRightTab === 'orders'} onClick={() => toggleRight('orders')} aria-label="Exchange orders" data-tip="Exchange orders" data-tip-side="left">
              <IconExchange size={20} />
            </button>
          </nav>
        )}
      </div>

      {phone && (
        <MobileNav
          toolsOpen={toolsOpen}
          toolActive={!!tool}
          onTools={() => setToolsOpen(o => !o)}
          panel={rightOpen ? shownRightTab : null}
          onPanel={toggleRight}
          testerOpen={bottomOpen}
          onTester={toggleTester}
          pendingCount={pendingCount}
        />
      )}

      {dialog === 'symbol' && (
        <SymbolSearch
          groups={groups}
          sourceLabel={sourceDef.label}
          onClose={() => setDialog(null)}
          onSelect={(s, t) => {
            setSymbol(s);
            if (t) setTf(t);
            setDialog(null);
          }}
        />
      )}
      {dialog === 'indicators' && (
        <IndicatorsDialog
          active={safeIndicators}
          onAdd={addIndicator}
          smcVisibility={smcVisibility}
          onToggleSmc={toggleSmc}
          smcCounts={smcCounts}
          smcNote={smcNote}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === 'goto' && engine && firstOpen && lastClose && (
        <GoToDate
          firstTime={firstOpen.time}
          lastTime={lastClose.time}
          barSeconds={tfOption?.seconds || tfToSeconds(tfOption?.tf) || 60}
          view={engine.visibleTimes()}
          onDate={(t) => engine.goToTime(t)}
          onRange={(a, b) => engine.goToRange(a, b)}
          onClose={closeDialog}
        />
      )}
      {settingsInst && (
        <IndicatorSettings inst={settingsInst} onApply={applyIndicator} onClose={() => setSettingsUid(null)} />
      )}
      {chartMenu && (
        <ChartMenu
          x={chartMenu.clientX}
          y={chartMenu.clientY}
          title={activeSymbol ? `${activeSymbol} · ${formatPrice(chartMenu.price)}` : ''}
          items={chartMenuItems()}
          onClose={closeChartMenu}
        />
      )}
      <Tooltip rootRef={rootRef} />
      <SignalAlerts
        signals={alertSignals}
        serverNow={trading.serverNow}
        timeoutMinutes={trading.approval.timeoutMinutes}
        onDecide={decideFromAlert}
        onShow={showSignal}
        onDismiss={(id) => setHiddenSignals(set => new Set(set).add(id))}
        onOpenPanel={() => openRight('trade')}
      />
      <OrderAlerts
        alerts={orderAlerts}
        onDismiss={(id) => setOrderAlerts(list => list.filter(a => a.id !== id))}
        onShow={showOrder}
      />
      {toast && <div className="tv-toast" role="status">{toast}</div>}
    </div>
  );
}
