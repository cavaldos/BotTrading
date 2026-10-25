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
import Tooltip from './ui/Tooltip';
import { DrawingBar, TextEditor } from './ui/DrawingBar';
import { IconToEnd, IconWatchlist, IconTrades, IconOrder } from './ui/icons';
import TradePanel from './trading/TradePanel';
import OrderDialog from './trading/OrderDialog';
import { useTrading } from './trading/useTrading';
import { announceSignal } from './trading/notify';
import './trading/trading.css';

const NARROW = '(max-width: 900px)';
const PHONE = '(max-width: 600px)';

function matches(query) {
  try {
    return window.matchMedia(query).matches;
  } catch {
    return false;
  }
}

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
      const trades = tfs.reduce((n, t) => n + (t.trades || 0), 0);
      return { ...g, tfs, meta: trades ? `${trades} trades` : `${tfs.length} timeframe${tfs.length > 1 ? 's' : ''}` };
    })
    .sort((a, b) => a.symbol.localeCompare(b.symbol));
}

function tradeMarkers(markers, rows) {
  const times = new Set();
  rows.forEach(r => {
    times.add(r.entryTime);
    if (Number.isFinite(r.exitTime)) times.add(r.exitTime);
  });
  return (markers || []).filter(m => times.has(m.time));
}

function filterRows(rows, filter) {
  switch (filter) {
    case 'win': return rows.filter(r => r.closed && r.r > 0);
    case 'loss': return rows.filter(r => r.closed && r.r <= 0);
    case 'open': return rows.filter(r => !r.closed);
    default: return rows;
  }
}

function isTyping(el) {
  if (!el) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
}

export default function TerminalPage() {
  const [params, setParams] = useSearchParams();
  const rootRef = useRef(null);
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
  const tfOptions = useMemo(() => buildTimeframeOptions(group?.tfs || []), [group]);
  const tfOption = tfOptions.find(o => o.tf === normalizeTf(tf)) || tfOptions.find(o => o.native) || null;
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
  const [bottomOpen, setBottomOpen] = usePersisted('bottomOpen', !matches(PHONE));
  const [bottomHeight, setBottomHeight] = usePersisted('bottomHeight', 280);
  const [bottomTab, setBottomTab] = usePersisted('bottomTab', 'overview');
  const [magnet, setMagnet] = usePersisted('magnet', false);
  // Same default as fee_per_trade_r in the Python strategy config
  const [commissionR, setCommissionR] = usePersisted('commissionR', 0.5);
  const commission = Number.isFinite(Number(commissionR)) && Number(commissionR) >= 0 ? Number(commissionR) : 0.5;

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
  const [orderTicket, setOrderTicket] = useState(null);
  const [focusSignalId, setFocusSignalId] = useState(null);

  // ---- bot connection (signals to approve, manual orders) -------------------------
  const trading = useTrading({
    onSignal: (sig) => {
      setToast(`New signal: ${sig.symbol} ${sig.side === 'LONG' ? 'Long' : 'Short'} · waiting for approval`);
      announceSignal(sig);
      if (!matches(NARROW)) {
        setRightTab('trade');
        setRightOpen(true);
      }
    },
  });
  const pendingCount = trading.signals.filter(s => s.status === 'PENDING').length;

  useEffect(() => {
    const base = document.title.replace(/^\(\d+\) /, '');
    document.title = pendingCount ? `(${pendingCount}) ${base}` : base;
  }, [pendingCount]);

  // ---- data -------------------------------------------------------------------
  const wantSmc = Object.values(smcVisibility).some(Boolean);
  const file = useChartFile(source, tfOption, { wantSmc, autoRefresh });
  const data = file.data;
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

  useEffect(() => {
    if (!engine || !data) return;
    const barDuration = tfOption?.seconds || tfToSeconds(tfOption?.tf) || 60;
    engine.setSmcPrimitives(file.smc ? buildSmcPrimitives(file.smc, smcVisibility, { barDuration, candles: data.candles }) : []);
  }, [engine, data, file.smc, smcVisibility, tfOption]);

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

  // Entry / stop / target lines of the signals waiting on this symbol (and the one opened from the panel)
  useEffect(() => {
    if (!engine) return;
    const lines = [];
    trading.signals
      .filter(s => s.symbol === activeSymbol && (s.status === 'PENDING' || s.status === 'PLACING' || s.id === focusSignalId))
      .forEach(s => {
        const tag = `${s.side === 'LONG' ? 'Long' : 'Short'} ${s.signalTimeframe}`.trim();
        lines.push(
          { price: s.entry, color: '#5b8bff', title: `${tag} entry` },
          { price: s.stopLoss, color: '#f23645', title: 'Stop' },
          { price: s.takeProfit, color: '#089981', title: 'Target' },
        );
      });
    engine.setSignalLines(lines);
  }, [engine, trading.signals, activeSymbol, focusSignalId]);

  useEffect(() => { engine?.drawings.setTool(tool); }, [engine, tool]);
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
  };

  const selectTrade = useCallback((row) => {
    if (!row) return;
    setSelectedKey(row.key);
    engine?.focusTrade(row);
  }, [engine]);

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
    keyState.current = { engine, dialog: dialog || settingsUid || orderTicket, filteredRows, selectedKey, selectTrade, textEdit };
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
    const t = setTimeout(() => setToast(null), 2200);
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
  if (source === 'live' && !file.smc) smcNote = 'Live files do not include SMC data. Switch to Backtest or SMC data to see these overlays.';
  else if (file.smcState.loading) smcNote = 'Loading SMC data…';
  else if (file.smcState.error) {
    smcNote = `${file.smcState.error}. SMC overlays only show on timeframes with their own SMC file `
      + `(export: python -m src.indicators.main --tfs ${tfOption?.tf || '1h'}).`;
  }

  const lastClose = data?.candles?.[data.candles.length - 1];
  const firstOpen = data?.candles?.[0];
  const currentQuote = source === 'live' && lastClose && firstOpen
    ? { last: lastClose.close, chg: ((lastClose.close - firstOpen.open) / firstOpen.open) * 100 }
    : null;

  const settingsInst = settingsUid ? safeIndicators.find(i => i.uid === settingsUid) : null;
  const maxBottom = Math.max(200, (typeof window !== 'undefined' ? window.innerHeight : 800) - 200);
  const testerEmpty = !sourceDef.hasTrades
    ? 'SMC data files have no trades. Switch to Backtest to use the Strategy Tester.'
    : rows.length === 0 && data ? 'No trades in this file yet.' : null;

  let chartMessage = null;
  if (symbolList.error) {
    chartMessage = (<div><strong>Could not load the symbol list</strong>{symbolList.error}</div>);
  } else if (!symbolList.loading && groups.length === 0) {
    chartMessage = (<div><strong>No symbols</strong>{`${sourceDef.base}/symbols.json lists no data files.`}</div>);
  } else if (file.error && !data) {
    chartMessage = (<div><strong>Could not load {activeSymbol} {activeTf}</strong>{file.error}</div>);
  } else if ((file.loading || symbolList.loading) && !data) {
    chartMessage = (<div><div className="tv-spinner" aria-hidden="true" />Loading {activeSymbol || 'data'} {activeTf || ''}…</div>);
  }

  // Sources without trades (SMC data) show the watchlist instead of the trade list
  const shownRightTab = rightTab === 'trade' || sourceDef.hasTrades ? rightTab : 'watchlist';

  // ---- trading actions -------------------------------------------------------------------
  const livePrice = source === 'live' && lastClose ? lastClose.close : null;

  const openTradePanel = (message) => {
    setRightTab('trade');
    setRightOpen(true);
    if (message) setToast(message);
  };

  const canTicket = () => {
    if (trading.conn !== 'online') {
      openTradePanel('Connect to the bot in the Trade panel first');
      return false;
    }
    if (!activeSymbol || !trading.config?.symbols?.includes(activeSymbol)) {
      setToast(`${activeSymbol || 'This symbol'} is not one of the bot symbols`);
      return false;
    }
    return true;
  };

  const tradeFromDrawing = () => {
    const d = engine?.drawings.getSelected();
    if (!d || (d.type !== 'long' && d.type !== 'short') || !canTicket()) return;
    const [entry, target, stop] = d.points;
    setOrderTicket({
      symbol: activeSymbol,
      side: d.type === 'long' ? 'LONG' : 'SHORT',
      initial: { entry: entry.price, takeProfit: target.price, stopLoss: stop.price },
    });
  };

  const newOrder = (side) => {
    if (!canTicket()) return;
    setOrderTicket({ symbol: activeSymbol, side, initial: { entry: livePrice ?? undefined } });
  };

  const showSignal = (sig) => {
    setFocusSignalId(sig.id);
    if (source !== 'live') changeSource('live');
    setSymbol(sig.symbol);
    if (sig.signalTimeframe) setTf(sig.signalTimeframe);
    engine?.scrollToRealtime();
    if (matches(NARROW)) setRightOpen(false);
  };

  const toggleRight = (tab) => {
    if (rightOpen && shownRightTab === tab) {
      setRightOpen(false);
    } else {
      setRightTab(tab);
      setRightOpen(true);
    }
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
        live={sourceDef.live ? {
          lastUpdate: file.lastUpdate,
          autoRefresh,
          onToggle: () => setAutoRefresh(a => !a),
          onRefresh: file.refresh,
        } : null}
        onScreenshot={takeScreenshot}
        onUndo={() => {
          engine?.drawings.undo();
          setCanUndo(!!engine?.drawings.canUndo());
        }}
        canUndo={canUndo}
      />

      <div className="tv-body">
        <LeftToolbar
          tool={tool}
          onTool={setTool}
          magnet={magnet}
          onMagnet={setMagnet}
          locked={drawLocked}
          onLocked={setDrawLocked}
          hidden={drawHidden}
          onHidden={setDrawHidden}
          onRemoveAll={() => engine?.drawings.removeAll()}
          hasDrawings={drawingCount > 0}
        />

        <main className="tv-center">
          <ChartView onEngine={setEngine} handlers={handlers}>
            <Legend
              store={legendStore}
              engine={engine}
              title={activeSymbol ? `${activeSymbol} · ${activeTf} · ${sourceDef.label}` : ''}
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
            {chartMessage && <div className="tv-chart-msg" role="status">{chartMessage}</div>}
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
            onCommission={setCommissionR}
          />
        </main>

        {rightOpen && (
          <RightPanel
            tab={shownRightTab}
            onClose={() => setRightOpen(false)}
            watchlist={{
              groups,
              symbol: activeSymbol,
              onSymbol: (s) => {
                setSymbol(s);
                if (matches(NARROW)) setRightOpen(false);
              },
              sourceId: source,
              currentQuote,
              baseTf: tfOption?.baseTf || null,
            }}
            trades={{
              rows: filteredRows,
              filter: tradeFilter,
              onFilter: setTradeFilter,
              selectedId: selectedKey,
              onSelect: selectTrade,
              showOpen: source === 'live',
            }}
            trade={(
              <TradePanel
                trading={trading}
                activeSymbol={activeSymbol}
                onShowSignal={showSignal}
                onNewOrder={newOrder}
              />
            )}
          />
        )}

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
        </nav>
      </div>

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
      {settingsInst && (
        <IndicatorSettings inst={settingsInst} onApply={applyIndicator} onClose={() => setSettingsUid(null)} />
      )}
      {orderTicket && (
        <OrderDialog
          key={`${orderTicket.symbol}-${orderTicket.side}`}
          trading={trading}
          symbol={orderTicket.symbol}
          side={orderTicket.side}
          initial={orderTicket.initial}
          lastPrice={livePrice}
          onClose={() => setOrderTicket(null)}
          onPlaced={(res) => {
            setOrderTicket(null);
            const o = res.order || {};
            setToast(`${o.orderType === 'limit' ? 'Limit order placed' : 'Order filled'}: ${o.side === 'LONG' ? 'Long' : 'Short'} ${o.symbol}`);
          }}
        />
      )}
      <Tooltip rootRef={rootRef} />
      {toast && <div className="tv-toast" role="status">{toast}</div>}
    </div>
  );
}
