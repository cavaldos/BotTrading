// Technical indicator definitions. Each one declares its inputs, its plots and
// a compute() that returns Float64Arrays aligned to the candles.
import * as ta from './ta';

export const SOURCES = ['close', 'open', 'high', 'low', 'hl2', 'hlc3', 'ohlc4'];

const length = (def, label = 'Length') => ({ key: 'length', label, type: 'int', min: 1, max: 2000, default: def });
const source = { key: 'source', label: 'Source', type: 'select', options: SOURCES, default: 'close' };

function maDef(type, name, fn, color) {
  return {
    type,
    name,
    short: type.toUpperCase(),
    kind: 'overlay',
    category: 'Moving averages',
    inputs: [length(type === 'ema' ? 21 : 20), source],
    plots: [{ key: 'value', label: type.toUpperCase(), color, width: 2 }],
    compute: (candles, inp) => ({ value: fn(ta.sourceValues(candles, inp.source), inp.length) }),
    label: (inp) => `${type.toUpperCase()} ${inp.length} ${inp.source}`,
  };
}

export const INDICATORS = [
  {
    type: 'volume',
    name: 'Volume',
    short: 'Vol',
    kind: 'volume',
    category: 'Volume',
    description: 'Traded volume bars under the price',
    inputs: [],
    plots: [],
    label: () => 'Volume',
  },
  maDef('sma', 'Moving Average Simple', ta.sma, '#2962ff'),
  maDef('ema', 'Moving Average Exponential', ta.ema, '#ff9800'),
  maDef('wma', 'Moving Average Weighted', ta.wma, '#e040fb'),
  {
    type: 'bb',
    name: 'Bollinger Bands',
    short: 'BB',
    kind: 'overlay',
    category: 'Volatility',
    inputs: [
      length(20),
      source,
      { key: 'mult', label: 'StdDev', type: 'float', min: 0.1, max: 10, step: 0.1, default: 2 },
    ],
    plots: [
      { key: 'basis', label: 'Basis', color: '#ff6d00', width: 1 },
      { key: 'upper', label: 'Upper', color: '#2962ff', width: 1 },
      { key: 'lower', label: 'Lower', color: '#2962ff', width: 1 },
    ],
    compute: (candles, inp) => ta.bollinger(ta.sourceValues(candles, inp.source), inp.length, inp.mult),
    label: (inp) => `BB ${inp.length} ${inp.source} ${inp.mult}`,
  },
  {
    type: 'vwap',
    name: 'VWAP',
    short: 'VWAP',
    kind: 'overlay',
    category: 'Volume',
    description: 'Volume weighted average price, reset every UTC session',
    inputs: [{ key: 'anchor', label: 'Anchor', type: 'select', options: ['Session', 'Week'], default: 'Session' }],
    plots: [{ key: 'value', label: 'VWAP', color: '#2196f3', width: 2 }],
    needsVolume: true,
    compute: (candles, inp, volumes) => ({ value: ta.vwap(candles, volumes, inp.anchor) }),
    label: (inp) => `VWAP ${inp.anchor}`,
  },
  {
    type: 'rsi',
    name: 'Relative Strength Index',
    short: 'RSI',
    kind: 'pane',
    category: 'Oscillators',
    inputs: [length(14), source],
    plots: [{ key: 'value', label: 'RSI', color: '#7e57c2', width: 2 }],
    levels: [
      { key: 'upper', price: 70, label: 'Upper band' },
      { key: 'middle', price: 50, label: 'Middle band' },
      { key: 'lower', price: 30, label: 'Lower band' },
    ],
    range: { min: 0, max: 100 },
    compute: (candles, inp) => ({ value: ta.rsi(ta.sourceValues(candles, inp.source), inp.length) }),
    label: (inp) => `RSI ${inp.length} ${inp.source}`,
  },
  {
    type: 'macd',
    name: 'MACD',
    short: 'MACD',
    kind: 'pane',
    category: 'Oscillators',
    inputs: [
      { key: 'fast', label: 'Fast length', type: 'int', min: 1, max: 500, default: 12 },
      { key: 'slow', label: 'Slow length', type: 'int', min: 1, max: 500, default: 26 },
      { key: 'signal', label: 'Signal smoothing', type: 'int', min: 1, max: 100, default: 9 },
      source,
    ],
    plots: [
      { key: 'hist', label: 'Histogram', color: '#26a69a', colorDown: '#ef5350', width: 1, type: 'histogram' },
      { key: 'macd', label: 'MACD', color: '#2962ff', width: 2 },
      { key: 'signal', label: 'Signal', color: '#ff6d00', width: 2 },
    ],
    compute: (candles, inp) => ta.macd(ta.sourceValues(candles, inp.source), inp.fast, inp.slow, inp.signal),
    label: (inp) => `MACD ${inp.fast} ${inp.slow} ${inp.signal}`,
  },
  {
    type: 'stoch',
    name: 'Stochastic',
    short: 'Stoch',
    kind: 'pane',
    category: 'Oscillators',
    inputs: [
      { key: 'k', label: '%K length', type: 'int', min: 1, max: 500, default: 14 },
      { key: 'kSmooth', label: '%K smoothing', type: 'int', min: 1, max: 100, default: 1 },
      { key: 'd', label: '%D smoothing', type: 'int', min: 1, max: 100, default: 3 },
    ],
    plots: [
      { key: 'k', label: '%K', color: '#2962ff', width: 2 },
      { key: 'd', label: '%D', color: '#ff6d00', width: 2 },
    ],
    levels: [
      { key: 'upper', price: 80, label: 'Upper band' },
      { key: 'lower', price: 20, label: 'Lower band' },
    ],
    range: { min: 0, max: 100 },
    compute: (candles, inp) => ta.stochastic(candles, inp.k, inp.kSmooth, inp.d),
    label: (inp) => `Stoch ${inp.k} ${inp.kSmooth} ${inp.d}`,
  },
  {
    type: 'atr',
    name: 'Average True Range',
    short: 'ATR',
    kind: 'pane',
    category: 'Volatility',
    inputs: [length(14)],
    plots: [{ key: 'value', label: 'ATR', color: '#e91e63', width: 2 }],
    compute: (candles, inp) => ({ value: ta.atr(candles, inp.length) }),
    label: (inp) => `ATR ${inp.length}`,
  },
  {
    type: 'adx',
    name: 'Average Directional Index',
    short: 'ADX',
    kind: 'pane',
    category: 'Trend',
    inputs: [
      { key: 'diLength', label: 'DI length', type: 'int', min: 1, max: 500, default: 14 },
      { key: 'smoothing', label: 'ADX smoothing', type: 'int', min: 1, max: 500, default: 14 },
    ],
    plots: [
      { key: 'adx', label: 'ADX', color: '#ff5252', width: 2 },
      { key: 'plusDI', label: '+DI', color: '#26a69a', width: 1 },
      { key: 'minusDI', label: '−DI', color: '#ef5350', width: 1 },
    ],
    compute: (candles, inp) => ta.adx(candles, inp.diLength, inp.smoothing),
    label: (inp) => `ADX ${inp.diLength} ${inp.smoothing}`,
  },
];

export const INDICATOR_BY_TYPE = Object.fromEntries(INDICATORS.map(d => [d.type, d]));

let uidCounter = 0;
export function newIndicatorInstance(type) {
  const def = INDICATOR_BY_TYPE[type];
  if (!def) return null;
  uidCounter += 1;
  return {
    uid: `${type}-${Date.now().toString(36)}-${uidCounter}`,
    type,
    visible: true,
    inputs: Object.fromEntries(def.inputs.map(i => [i.key, i.default])),
    plots: Object.fromEntries(def.plots.map(p => [p.key, { color: p.color, width: p.width, visible: true }])),
  };
}

/** Repair instances loaded from storage (unknown types dropped, new inputs defaulted). */
export function sanitizeInstances(list) {
  if (!Array.isArray(list)) return null;
  return list
    .filter(inst => inst && INDICATOR_BY_TYPE[inst.type] && typeof inst.uid === 'string')
    .map(inst => {
      const def = INDICATOR_BY_TYPE[inst.type];
      const inputs = {};
      def.inputs.forEach(i => {
        const v = inst.inputs?.[i.key];
        inputs[i.key] = v === undefined || v === null ? i.default : v;
      });
      const plots = {};
      def.plots.forEach(p => {
        plots[p.key] = { color: p.color, width: p.width, visible: true, ...(inst.plots?.[p.key] || {}) };
      });
      return { uid: inst.uid, type: inst.type, visible: inst.visible !== false, inputs, plots };
    });
}

export const DEFAULT_INDICATORS = [
  { uid: 'volume-default', type: 'volume', visible: true, inputs: {}, plots: {} },
];
