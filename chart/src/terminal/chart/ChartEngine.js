/**
 * Imperative wrapper around one lightweight-charts instance.
 * React components call its methods; nothing here re-renders React, so
 * crosshair moves and data refreshes never rebuild the UI tree.
 */
import {
  createChart,
  CandlestickSeries,
  BarSeries,
  LineSeries,
  AreaSeries,
  HistogramSeries,
  CrosshairMode,
  LineStyle,
} from 'lightweight-charts';
import { PrimitiveLayer, timeToLogical } from '../../plugins/PrimitiveLayer';
import { createTradeZones } from '../../plugins/RectanglePrimitive';
import { applyPositionLabelsToMarkers } from '../../components/positionMarkers';
import { INDICATOR_BY_TYPE } from '../indicators/registry';
import { heikinAshi } from '../lib/timeframes';
import { priceDigits } from '../lib/format';
import { DrawingManager } from '../drawings/DrawingManager';
import { createMarkerPrimitives } from './markerPrimitives';

export const THEME = {
  bg: '#131722',
  text: '#b2b5be',
  grid: 'rgba(42, 46, 57, 0.6)',
  border: '#2a2e39',
  crosshair: '#758696',
  crosshairLabel: '#363a45',
  up: '#089981',
  down: '#f23645',
  font: '-apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, Ubuntu, sans-serif',
};

export const CHART_TYPES = [
  { id: 'candles', label: 'Candles' },
  { id: 'hollow', label: 'Hollow candles' },
  { id: 'heikin', label: 'Heikin Ashi' },
  { id: 'bars', label: 'Bars' },
  { id: 'line', label: 'Line' },
  { id: 'area', label: 'Area' },
];

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const pad = (n) => String(n).padStart(2, '0');

function crosshairTime(time) {
  const d = new Date(Number(time) * 1000);
  return `${DAYS[d.getUTCDay()]} ${pad(d.getUTCDate())} ${MONTHS[d.getUTCMonth()]} '${String(d.getUTCFullYear()).slice(2)}  ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

function withAlpha(hex, alpha) {
  const m = /^#([0-9a-f]{6})$/i.exec(hex || '');
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

function lowerBound(arr, value) {
  let lo = 0;
  let hi = arr.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid] < value) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

export class ChartEngine {
  constructor(container, { onCrosshair, onTradeClick, onDrawingChange, onDrawingSelect, onToolChange, onTextEdit, onPanesChange } = {}) {
    this.container = container;
    this.cb = { onCrosshair, onTradeClick, onPanesChange };
    this.chartType = 'candles';
    this.candles = [];
    this.volume = [];
    this.display = [];
    this.times = [];
    this.trades = [];
    this.tradeByTime = new Map();
    this.markersData = [];
    this.indicators = new Map();
    this.volumeVisible = true;
    this.dataKey = null;

    this.chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { type: 'solid', color: THEME.bg },
        textColor: THEME.text,
        fontSize: 12,
        fontFamily: THEME.font,
        panes: { separatorColor: THEME.border, separatorHoverColor: 'rgba(41, 98, 255, 0.35)', enableResize: true },
      },
      grid: {
        vertLines: { color: THEME.grid },
        horzLines: { color: THEME.grid },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: THEME.crosshair, width: 1, style: LineStyle.Dashed, labelBackgroundColor: THEME.crosshairLabel },
        horzLine: { color: THEME.crosshair, width: 1, style: LineStyle.Dashed, labelBackgroundColor: THEME.crosshairLabel },
      },
      rightPriceScale: { borderColor: THEME.border, scaleMargins: { top: 0.1, bottom: 0.08 } },
      timeScale: {
        borderColor: THEME.border,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 12,
        barSpacing: 7,
        minBarSpacing: 0.3,
      },
      localization: { timeFormatter: crosshairTime },
      handleScale: { axisPressedMouseMove: { time: true, price: true }, axisDoubleClickReset: { time: true, price: true } },
    });

    // Volume lives in pane 0 on its own hidden scale. It is never removed, which
    // also keeps pane 0 alive while the main series is swapped for another type.
    this.volumeSeries = this.chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
      lastValueVisible: false,
      priceLineVisible: false,
    });
    this.volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });

    this.smcLayer = new PrimitiveLayer();
    this.tradeLayer = new PrimitiveLayer();
    this.drawings = new DrawingManager({
      chart: this.chart,
      container,
      onChange: onDrawingChange,
      onSelect: onDrawingSelect,
      onToolChange,
      onTextEdit,
    });

    this.mainSeries = null;
    this.signalLineSpecs = [];
    this.signalLines = [];
    this._createMainSeries();

    this._onCrosshairMove = this._onCrosshairMove.bind(this);
    this._onClick = this._onClick.bind(this);
    this.chart.subscribeCrosshairMove(this._onCrosshairMove);
    this.chart.subscribeClick(this._onClick);
  }

  destroy() {
    this.chart.unsubscribeCrosshairMove(this._onCrosshairMove);
    this.chart.unsubscribeClick(this._onClick);
    this.drawings.destroy();
    this.chart.remove();
  }

  // ---- main series ----------------------------------------------------------

  _seriesOptions(type) {
    const { up, down } = THEME;
    const digits = priceDigits(this.candles[this.candles.length - 1]?.close ?? 1);
    const priceFormat = { type: 'price', precision: digits, minMove: 1 / 10 ** digits };
    switch (type) {
      case 'hollow':
        return [CandlestickSeries, { upColor: 'rgba(0,0,0,0)', downColor: down, borderUpColor: up, borderDownColor: down, wickUpColor: up, wickDownColor: down, priceFormat }];
      case 'bars':
        return [BarSeries, { upColor: up, downColor: down, thinBars: false, priceFormat }];
      case 'line':
        return [LineSeries, { color: '#2962ff', lineWidth: 2, priceFormat }];
      case 'area':
        return [AreaSeries, { lineColor: '#2962ff', topColor: 'rgba(41, 98, 255, 0.28)', bottomColor: 'rgba(41, 98, 255, 0.02)', lineWidth: 2, priceFormat }];
      default:
        return [CandlestickSeries, { upColor: up, downColor: down, borderUpColor: up, borderDownColor: down, wickUpColor: up, wickDownColor: down, priceFormat }];
    }
  }

  _displayData() {
    const type = this.chartType;
    const bars = type === 'heikin' ? heikinAshi(this.candles) : this.candles;
    this.display = bars;
    if (type === 'line' || type === 'area') {
      return bars.map(c => ({ time: c.time, value: c.close }));
    }
    return bars;
  }

  _createMainSeries() {
    const old = this.mainSeries;
    const [def, options] = this._seriesOptions(this.chartType);
    const series = this.chart.addSeries(def, options, 0);
    series.setData(this._displayData());

    if (old) {
      [this.smcLayer, this.tradeLayer].forEach(layer => {
        try {
          old.detachPrimitive(layer);
        } catch {
          // already detached
        }
      });
      this.chart.removeSeries(old);
    }
    series.attachPrimitive(this.smcLayer);
    series.attachPrimitive(this.tradeLayer);
    this.drawings.attachTo(series);
    this.mainSeries = series;
    // Price lines lived on the old series and went away with it
    this.signalLines = [];
    this._applySignalLines();
    this.drawings.setData(this.times, this.display);
    // Markers sit on the displayed bars (Heikin Ashi changes highs/lows)
    if (old) this._rebuildTradeLayer();

    // Overlay indicators were drawn under the new series; re-add them on top
    if (old) {
      this.indicators.forEach((entry, uid) => {
        if (entry.def.kind === 'overlay') {
          this._removeIndicator(uid);
          this._addIndicator(entry.inst);
        }
      });
    }
  }

  setChartType(type) {
    if (type === this.chartType) return;
    this.chartType = type;
    if (this.candles.length) this._createMainSeries();
  }

  // ---- data -----------------------------------------------------------------

  /**
   * Replace candles/volume. `key` identifies the dataset; when it is unchanged
   * and the new data only extends the old (live refresh), bars are updated in
   * place so the user's view is kept.
   */
  setData(candles, volume, key) {
    const prev = this.candles;
    const sameSet = key === this.dataKey && prev.length > 0 && candles.length >= prev.length
      && candles[0]?.time === prev[0]?.time;
    this.candles = candles || [];
    this.volume = volume || [];
    this.times = this.candles.map(c => c.time);
    this.dataKey = key;

    if (sameSet && this.chartType !== 'heikin') {
      const from = Math.max(0, prev.length - 1);
      const data = this._displayData();
      for (let i = from; i < data.length; i += 1) this.mainSeries.update(data[i]);
      for (let i = from; i < this.volume.length; i += 1) this.volumeSeries.update(this.volume[i]);
    } else {
      const [, options] = this._seriesOptions(this.chartType);
      this.mainSeries.applyOptions({ priceFormat: options.priceFormat });
      this.mainSeries.setData(this._displayData());
      this.volumeSeries.setData(this.volume);
    }
    this.drawings.setData(this.times, this.display);
    this._recomputeIndicators();

    if (!sameSet) this.resetView();
  }

  resetView() {
    const n = this.candles.length;
    if (!n) return;
    this.mainSeries.priceScale().applyOptions({ autoScale: true });
    this.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 160), to: n + 12 });
  }

  scrollToRealtime() {
    this.mainSeries?.priceScale().applyOptions({ autoScale: true });
    this.chart.timeScale().scrollToRealTime();
  }

  getBarIndex(time) {
    const i = lowerBound(this.times, Number(time));
    return this.times[i] === Number(time) ? i : -1;
  }

  // ---- trades / overlays ------------------------------------------------------

  setTrades(trades, markers) {
    this.trades = trades || [];
    this.tradeByTime = new Map();
    this.trades.forEach(t => {
      [t.entryTime, t.exitTime].forEach((time, k) => {
        if (!Number.isFinite(time)) return;
        const list = this.tradeByTime.get(time) || [];
        list.push({ trade: t, entry: k === 0 });
        this.tradeByTime.set(time, list);
      });
    });
    this.markersData = applyPositionLabelsToMarkers(markers || [], this.trades)
      .slice()
      .sort((a, b) => a.time - b.time);
    this._rebuildTradeLayer();
  }

  _rebuildTradeLayer() {
    const shapes = [];
    this.trades.forEach((t, i) => {
      if ([t.entryPrice, t.stopLoss, t.takeProfit, t.entryTime].every(Number.isFinite)) {
        const exitTime = Number.isFinite(t.exitTime) ? t.exitTime : this.times[this.times.length - 1];
        shapes.push(...createTradeZones({ ...t, exitTime }, t.n ?? i + 1));
      }
    });
    shapes.push(...createMarkerPrimitives(this.markersData, this.display, this.times));
    this.tradeLayer.setPrimitives(shapes, this.times);
  }

  setSmcPrimitives(primitives) {
    this.smcLayer.setPrimitives(primitives, this.times);
  }

  /** Horizontal price lines for bot signals waiting for approval: [{ price, color, title }]. */
  setSignalLines(lines) {
    this.signalLineSpecs = (lines || []).filter(l => Number.isFinite(l?.price));
    this._applySignalLines();
  }

  _applySignalLines() {
    const series = this.mainSeries;
    if (!series) return;
    this.signalLines.forEach(line => {
      try {
        series.removePriceLine(line);
      } catch {
        // already gone
      }
    });
    this.signalLines = this.signalLineSpecs.map(l => series.createPriceLine({
      price: l.price,
      color: l.color,
      lineWidth: l.width || 1,
      lineStyle: l.dashed === false ? LineStyle.Solid : LineStyle.Dashed,
      axisLabelVisible: true,
      title: l.title || '',
    }));
  }

  focusTrade(trade, barsAround = 40) {
    const entry = Number(trade?.entryTime);
    if (!Number.isFinite(entry) || !this.times.length) return;
    const l1 = timeToLogical(this.times, entry) ?? 0;
    const l2 = Number.isFinite(trade.exitTime) ? (timeToLogical(this.times, trade.exitTime) ?? l1) : l1;
    this.chart.timeScale().setVisibleLogicalRange({ from: l1 - barsAround, to: Math.max(l2, l1) + barsAround });

    const prices = [trade.entryPrice, trade.stopLoss, trade.takeProfit, trade.exitPrice].map(Number).filter(Number.isFinite);
    if (prices.length) {
      const low = Math.min(...prices);
      const high = Math.max(...prices);
      const padding = Math.max(high - low, Math.abs(high) * 0.001) * 0.6;
      const scale = this.mainSeries.priceScale();
      scale.applyOptions({ autoScale: false });
      scale.setVisibleRange({ from: low - padding, to: high + padding });
    }
  }

  _onClick(param) {
    if (!this.cb.onTradeClick || param?.time == null) return;
    const list = this.tradeByTime.get(Number(param.time));
    if (!list?.length) return;
    const pick = list.find(x => x.entry) || list[list.length - 1];
    this.cb.onTradeClick(pick.trade);
  }

  // ---- indicators -------------------------------------------------------------

  /** Make the chart match the list of indicator instances (add / update / remove). */
  syncIndicators(list) {
    const wanted = new Map(list.map(inst => [inst.uid, inst]));
    let panesChanged = false;
    [...this.indicators.keys()].forEach(uid => {
      if (!wanted.has(uid)) {
        panesChanged = this._removeIndicator(uid) || panesChanged;
      }
    });
    list.forEach(inst => {
      const entry = this.indicators.get(inst.uid);
      if (!entry) {
        panesChanged = this._addIndicator(inst) || panesChanged;
        return;
      }
      const inputsKey = JSON.stringify(inst.inputs);
      const styleKey = JSON.stringify([inst.plots, inst.visible]);
      entry.inst = inst;
      if (inputsKey !== entry.inputsKey) {
        entry.inputsKey = inputsKey;
        this._computeIndicator(entry);
      }
      if (styleKey !== entry.styleKey) {
        entry.styleKey = styleKey;
        this._styleIndicator(entry);
      }
    });
    if (panesChanged) this.cb.onPanesChange?.();
  }

  _addIndicator(inst) {
    const def = INDICATOR_BY_TYPE[inst.type];
    if (!def) return false;
    const entry = {
      inst,
      def,
      series: {},
      priceLines: [],
      values: {},
      inputsKey: JSON.stringify(inst.inputs),
      styleKey: JSON.stringify([inst.plots, inst.visible]),
    };
    this.indicators.set(inst.uid, entry);

    if (def.kind === 'volume') {
      this.volumeSeries.applyOptions({ visible: inst.visible });
      return false;
    }

    const paneIndex = def.kind === 'pane' ? this.chart.panes().length : 0;
    def.plots.forEach(plot => {
      const style = inst.plots[plot.key] || plot;
      const common = {
        lastValueVisible: true,
        priceLineVisible: false,
        visible: inst.visible && style.visible !== false,
        crosshairMarkerVisible: plot.type !== 'histogram',
        title: '',
      };
      if (def.range) {
        common.autoscaleInfoProvider = () => ({ priceRange: { minValue: def.range.min, maxValue: def.range.max } });
      }
      const series = plot.type === 'histogram'
        ? this.chart.addSeries(HistogramSeries, { ...common, color: style.color }, paneIndex)
        : this.chart.addSeries(LineSeries, { ...common, color: style.color, lineWidth: style.width }, paneIndex);
      entry.series[plot.key] = series;
    });

    const first = entry.series[def.plots[0].key];
    (def.levels || []).forEach(level => {
      entry.priceLines.push(first.createPriceLine({
        price: level.price,
        color: 'rgba(120, 123, 134, 0.6)',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: false,
        title: '',
      }));
    });

    if (def.kind === 'pane') {
      const pane = first.getPane();
      pane.setStretchFactor(0.28);
      if (this.chart.panes()[0]) this.chart.panes()[0].setStretchFactor(1);
    }
    this._computeIndicator(entry);
    return def.kind === 'pane';
  }

  _removeIndicator(uid) {
    const entry = this.indicators.get(uid);
    if (!entry) return false;
    this.indicators.delete(uid);
    if (entry.def.kind === 'volume') {
      this.volumeSeries.applyOptions({ visible: false });
      return false;
    }
    let pane = null;
    Object.values(entry.series).forEach(s => {
      pane = s.getPane();
      this.chart.removeSeries(s);
    });
    if (pane && entry.def.kind === 'pane') {
      const idx = pane.paneIndex();
      if (idx > 0 && pane.getSeries().length === 0) this.chart.removePane(idx);
      return true;
    }
    return false;
  }

  _computeIndicator(entry) {
    const { def, inst } = entry;
    if (def.kind === 'volume' || !this.candles.length) {
      entry.values = {};
      Object.values(entry.series).forEach(s => s.setData([]));
      return;
    }
    const vols = def.needsVolume ? this._volumeArray() : null;
    const out = def.compute(this.candles, inst.inputs, vols);
    entry.values = out;
    def.plots.forEach(plot => {
      const arr = out[plot.key];
      const series = entry.series[plot.key];
      if (!arr || !series) return;
      const data = [];
      const style = inst.plots[plot.key] || plot;
      for (let i = 0; i < arr.length; i += 1) {
        const v = arr[i];
        if (Number.isNaN(v)) continue;
        if (plot.type === 'histogram') {
          const prev = i > 0 ? arr[i - 1] : v;
          const base = v >= 0 ? style.color : (style.colorDown || plot.colorDown || THEME.down);
          // Stronger colour while the histogram grows, faded while it shrinks
          const growing = v >= 0 ? v >= prev : v <= prev;
          data.push({ time: this.times[i], value: v, color: growing ? base : withAlpha(base, 0.45) });
        } else {
          data.push({ time: this.times[i], value: v });
        }
      }
      series.setData(data);
    });
  }

  _styleIndicator(entry) {
    const { def, inst } = entry;
    if (def.kind === 'volume') {
      this.volumeSeries.applyOptions({ visible: inst.visible });
      return;
    }
    let recolor = false;
    def.plots.forEach(plot => {
      const style = inst.plots[plot.key] || plot;
      const series = entry.series[plot.key];
      if (!series) return;
      if (plot.type === 'histogram') {
        series.applyOptions({ visible: inst.visible && style.visible !== false });
        recolor = true;
      } else {
        series.applyOptions({ color: style.color, lineWidth: style.width, visible: inst.visible && style.visible !== false });
      }
    });
    if (recolor) this._computeIndicator(entry);
  }

  _volumeArray() {
    const byTime = new Map(this.volume.map(v => [v.time, v.value]));
    return this.candles.map(c => byTime.get(c.time) || 0);
  }

  _recomputeIndicators() {
    this.indicators.forEach(entry => this._computeIndicator(entry));
  }

  /** Pane index for an indicator (0 = price pane), used to place legends. */
  paneOf(uid) {
    const entry = this.indicators.get(uid);
    if (!entry) return 0;
    const first = Object.values(entry.series)[0];
    return first ? first.getPane().paneIndex() : 0;
  }

  /** Top offset (px) of each pane relative to the chart container. */
  paneTops() {
    const root = this.container.getBoundingClientRect();
    return this.chart.panes().map(p => {
      const el = p.getHTMLElement();
      return el ? el.getBoundingClientRect().top - root.top : 0;
    });
  }

  // ---- legend -------------------------------------------------------------------

  /** Values for the legend at a bar index (defaults to the last bar). */
  legendAt(index) {
    const n = this.display.length;
    if (!n) return null;
    const i = index == null || index < 0 || index >= n ? n - 1 : index;
    const bar = this.display[i];
    const prev = this.display[i - 1];
    const vol = this.volume[i];
    const indicators = {};
    this.indicators.forEach((entry, uid) => {
      const vals = {};
      entry.def.plots.forEach(p => {
        const v = entry.values?.[p.key]?.[i];
        vals[p.key] = Number.isFinite(v) ? v : null;
      });
      indicators[uid] = vals;
    });
    return {
      index: i,
      bar,
      change: prev ? bar.close - prev.close : bar.close - bar.open,
      changePct: prev ? ((bar.close - prev.close) / prev.close) * 100 : ((bar.close - bar.open) / bar.open) * 100,
      volume: vol && vol.time === bar.time ? vol.value : null,
      indicators,
    };
  }

  _onCrosshairMove(param) {
    if (!this.cb.onCrosshair) return;
    const idx = param?.time != null ? this.getBarIndex(param.time) : -1;
    this.cb.onCrosshair(this.legendAt(idx >= 0 ? idx : null), idx >= 0);
  }

  // ---- drawings ---------------------------------------------------------------------

  formatPrice(price) {
    return this.mainSeries ? this.mainSeries.priceFormatter().format(price) : String(price);
  }

  takeScreenshot() {
    // Drawings and markers render on the top layer; include it, skip the crosshair
    return this.chart.takeScreenshot(true, false);
  }
}
