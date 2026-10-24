/**
 * Primitive Layer Plugin for Lightweight Charts
 * Draws many Rectangle / Line / Fib primitives through a single series primitive.
 *
 * Attaching thousands of primitives one by one makes the chart walk all of them
 * on every frame (new pane views, time lookups, canvas state) even when they are
 * far off-screen, and detaching them is O(n^2). The layer maps every primitive to
 * bar indexes once, then each frame only draws the ones overlapping the visible range.
 *
 * Primitives must implement getTimeSpan(), getZOrder() and drawShape(ctx, x1, x2, frame).
 */

const Z_ORDERS = ['bottom', 'normal', 'top'];

// Hide labels when more shapes than this are on screen (unreadable and slow anyway)
const LABEL_LIMIT = 600;

// Rectangles are drawn at least this wide, so they can poke into view from the left
const MIN_SHAPE_WIDTH_PX = 40;

function lowerBound(length, isBefore) {
  let lo = 0;
  let hi = length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (isBefore(mid)) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }
  return lo;
}

/**
 * Convert a unix time to a (fractional) logical bar index.
 * Times between bars are interpolated, times outside the data are extrapolated
 * with the first/last bar step, so shapes extending past the last candle still draw.
 */
export function timeToLogical(times, time) {
  const n = times.length;
  if (n === 0 || !Number.isFinite(time)) return null;
  if (n === 1) return 0;

  const first = times[0];
  const last = times[n - 1];
  if (time <= first) return (time - first) / ((times[1] - first) || 1);
  if (time >= last) return (n - 1) + (time - last) / ((last - times[n - 2]) || 1);

  const idx = lowerBound(n, i => times[i] < time);
  if (times[idx] === time) return idx;
  const prev = times[idx - 1];
  return idx - 1 + (time - prev) / ((times[idx] - prev) || 1);
}

export class PrimitiveLayer {
  constructor() {
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
    this._primitives = [];
    this._times = [];
    this._groups = {};
    this._paneViews = Z_ORDERS.map(zOrder => new LayerPaneView(this, zOrder));
  }

  attached(params) {
    this._chart = params.chart;
    this._series = params.series;
    this._requestUpdate = params.requestUpdate;
  }

  detached() {
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
  }

  /**
   * Replace all primitives drawn by this layer.
   * @param {Array} primitives - Rectangle / Line / Fib primitive instances
   * @param {number[]} times - Sorted candle times (unix seconds) of the series
   */
  setPrimitives(primitives, times) {
    this._primitives = Array.isArray(primitives) ? primitives : [];
    this._times = Array.isArray(times) ? times : [];
    this._rebuild();
    if (this._requestUpdate) {
      this._requestUpdate();
    }
  }

  _rebuild() {
    const groups = {};

    this._primitives.forEach(primitive => {
      const [startTime, endTime] = primitive.getTimeSpan();
      const l1 = timeToLogical(this._times, startTime);
      const l2 = timeToLogical(this._times, endTime);
      if (l1 === null || l2 === null) return;

      const zOrder = Z_ORDERS.includes(primitive.getZOrder()) ? primitive.getZOrder() : 'bottom';
      if (!groups[zOrder]) {
        groups[zOrder] = { entries: [], maxSpan: 0 };
      }
      groups[zOrder].entries.push({
        primitive,
        l1,
        l2,
        lo: Math.min(l1, l2),
        hi: Math.max(l1, l2),
      });
    });

    // Sort by start so a frame only scans the window [from - maxSpan, to]
    Object.values(groups).forEach(group => {
      group.entries.sort((a, b) => a.lo - b.lo);
      group.maxSpan = group.entries.reduce((max, e) => Math.max(max, e.hi - e.lo), 0);
    });

    this._groups = groups;
  }

  updateAllViews() {}

  paneViews() {
    return this._paneViews;
  }

  getGroup(zOrder) {
    return this._groups[zOrder];
  }

  getChart() {
    return this._chart;
  }

  getSeries() {
    return this._series;
  }
}

class LayerPaneView {
  constructor(layer, zOrder) {
    this._zOrder = zOrder;
    this._renderer = new LayerRenderer(layer, zOrder);
  }

  update() {}

  renderer() {
    return this._renderer;
  }

  zOrder() {
    return this._zOrder;
  }
}

class LayerRenderer {
  constructor(layer, zOrder) {
    this._layer = layer;
    this._zOrder = zOrder;
  }

  draw(target) {
    const group = this._layer.getGroup(this._zOrder);
    const chart = this._layer.getChart();
    const series = this._layer.getSeries();
    if (!group || group.entries.length === 0 || !chart || !series) return;

    const timeScale = chart.timeScale();
    const range = timeScale.getVisibleLogicalRange();
    // logicalToCoordinate only accepts integer indexes, so derive the linear mapping once
    const x0 = timeScale.logicalToCoordinate(0);
    const x1 = timeScale.logicalToCoordinate(1);
    if (!range || x0 === null || x1 === null) return;

    const barSpacing = x1 - x0;
    const margin = 1 + MIN_SHAPE_WIDTH_PX / Math.max(barSpacing, 0.01);
    const from = range.from - margin;
    const to = range.to + 1;

    const { entries, maxSpan } = group;
    const visible = [];
    const startIdx = lowerBound(entries.length, i => entries[i].lo < from - maxSpan);
    for (let i = startIdx; i < entries.length; i += 1) {
      const entry = entries[i];
      if (entry.lo > to) break;
      if (entry.hi >= from) visible.push(entry);
    }
    if (visible.length === 0) return;

    target.useMediaCoordinateSpace(scope => {
      const ctx = scope.context;
      const frame = {
        series,
        width: scope.mediaSize.width,
        height: scope.mediaSize.height,
        showLabels: visible.length <= LABEL_LIMIT,
      };

      for (let i = 0; i < visible.length; i += 1) {
        const entry = visible[i];
        entry.primitive.drawShape(ctx, x0 + entry.l1 * barSpacing, x0 + entry.l2 * barSpacing, frame);
      }
    });
  }
}

export default PrimitiveLayer;
