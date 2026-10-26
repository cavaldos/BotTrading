/**
 * The order being prepared, drawn on the chart like TradingView's order lines:
 * entry, stop and target lines with the profit / loss zones. Each line can be dragged:
 *   target / stop  that level only (kept on its side of the entry)
 *   entry          the whole order moves, stop and target keep their distance
 *                  (Shift+drag moves the entry alone); a market order becomes a limit
 * New prices go out through `onChange(levels, { done })`. React owns the order and
 * passes it back with setOrder, so the ticket and the chart always agree.
 *
 * Pointer events are taken in the capture phase, before the drawing tools and the
 * chart, but only when they start on one of the lines.
 */
import { FONT } from '../drawings/tools';

const ZONE_MIN = 190; // px: shaded zone width when the last bar is near the right edge or off screen
const LABEL_H = 20;
const TOLERANCE = { mouse: 6, pen: 8, touch: 16 };

const ENTRY = '#2962ff';
const UP = '#089981';
const DOWN = '#f23645';

const KEYS = ['entry', 'stopLoss', 'takeProfit'];

class OrderPrimitive {
  constructor(overlay) {
    this._overlay = overlay;
    const renderer = { draw: (target) => overlay._draw(target) };
    this._paneViews = [{ renderer: () => renderer, zOrder: () => 'top' }];
  }

  attached(params) {
    this._overlay._requestUpdate = params.requestUpdate;
  }

  detached() {
    this._overlay._requestUpdate = null;
  }

  updateAllViews() {}

  paneViews() {
    return this._paneViews;
  }

  priceAxisViews() {
    return this._overlay._priceAxisViews();
  }

  hitTest(x, y) {
    const o = this._overlay;
    if (o.drag) return { cursorStyle: 'ns-resize', externalId: 'order-line', zOrder: 'top' };
    return o._hit(x, y, TOLERANCE.mouse) ? { cursorStyle: 'ns-resize', externalId: 'order-line', zOrder: 'top' } : null;
  }
}

const GRIP_W = 12;

function pillWidth(ctx, text, strong) {
  ctx.font = `${strong ? '600 ' : ''}12px ${FONT}`;
  return ctx.measureText(text).width + 16 + GRIP_W;
}

function pill(ctx, text, x, y, { bg, fg = '#ffffff', grip = false, strong = false }) {
  ctx.font = `${strong ? '600 ' : ''}12px ${FONT}`;
  const gripW = grip ? GRIP_W : 0;
  const w = ctx.measureText(text).width + 16 + gripW;
  const top = y - LABEL_H / 2;
  ctx.fillStyle = bg;
  ctx.beginPath();
  ctx.roundRect(x, top, w, LABEL_H, 4);
  ctx.fill();
  if (grip) {
    // Two columns of dots: "drag me"
    ctx.fillStyle = 'rgba(255, 255, 255, 0.7)';
    for (let c = 0; c < 2; c += 1) {
      for (let r = -1; r <= 1; r += 1) {
        ctx.beginPath();
        ctx.arc(x + 7 + c * 4, y + r * 4, 1.1, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }
  ctx.fillStyle = fg;
  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, x + 8 + gripW, y + 0.5);
  return w;
}

export class OrderOverlay {
  constructor({ chart, container, isBusy, onChange }) {
    this.chart = chart;
    this.container = container;
    this.series = null;
    this.isBusy = isBusy || (() => false);
    this.onChange = onChange || (() => {});
    // { side, orderType, entry, lastPrice, stopLoss, takeProfit, extra: { entry, stopLoss, takeProfit } }
    this.order = null;
    this.lastTime = null;
    this.hover = null;
    this.drag = null;
    this.width = 0;
    this._swallow = false;
    this._requestUpdate = null;
    this.primitive = new OrderPrimitive(this);

    this._onPointerDown = this._onPointerDown.bind(this);
    this._onPointerMove = this._onPointerMove.bind(this);
    this._onPointerUp = this._onPointerUp.bind(this);
    this._onHover = this._onHover.bind(this);
    this._block = this._block.bind(this);
    container.addEventListener('pointerdown', this._onPointerDown, true);
    container.addEventListener('pointermove', this._onHover);
    container.addEventListener('mousedown', this._block, true);
    container.addEventListener('touchstart', this._block, true);
  }

  destroy() {
    this.container.removeEventListener('pointerdown', this._onPointerDown, true);
    this.container.removeEventListener('pointermove', this._onHover);
    this.container.removeEventListener('mousedown', this._block, true);
    this.container.removeEventListener('touchstart', this._block, true);
    this._stopListening();
    if (this.series) this.series.detachPrimitive(this.primitive);
  }

  attachTo(series) {
    if (this.series === series) return;
    if (this.series) {
      try {
        this.series.detachPrimitive(this.primitive);
      } catch {
        // series already removed
      }
    }
    this.series = series;
    if (series) series.attachPrimitive(this.primitive);
  }

  setLastTime(time) {
    this.lastTime = Number.isFinite(time) ? time : null;
  }

  /** The order to show, or null. While a line is dragged its prices win over the ones passed in. */
  setOrder(order) {
    if (order && this.drag && this.order) {
      const { entry, stopLoss, takeProfit, orderType } = this.order;
      this.order = { ...order, entry, stopLoss, takeProfit, orderType };
    } else {
      this.order = order ? { ...order } : null;
      if (!order) this._stopListening();
    }
    this._update();
  }

  // ---- geometry -----------------------------------------------------------------

  _update() {
    if (this._requestUpdate) this._requestUpdate();
  }

  _entryPrice(order = this.order) {
    if (!order) return null;
    return order.orderType === 'market' ? order.lastPrice ?? order.entry : order.entry;
  }

  _prices(order = this.order) {
    if (!order) return {};
    return { entry: this._entryPrice(order), stopLoss: order.stopLoss, takeProfit: order.takeProfit };
  }

  _y(price) {
    if (!this.series || !Number.isFinite(price)) return null;
    return this.series.priceToCoordinate(price);
  }

  _hit(x, y, tol) {
    if (!this.order || x < 0 || (this.width && x > this.width)) return null;
    const prices = this._prices();
    let best = null;
    let bestDist = Infinity;
    KEYS.forEach(key => {
      const py = this._y(prices[key]);
      if (py === null) return;
      const d = Math.abs(py - y);
      if (d <= tol && d < bestDist) {
        best = key;
        bestDist = d;
      }
    });
    return best;
  }

  _round(price) {
    const pf = this.series?.options().priceFormat || {};
    const step = pf.minMove || 0;
    if (!step || !Number.isFinite(price)) return price;
    const digits = Number.isFinite(pf.precision) ? pf.precision : 8;
    return Number((Math.round(price / step) * step).toFixed(digits));
  }

  _step() {
    return this.series?.options().priceFormat?.minMove || 0;
  }

  _localPoint(e) {
    const el = this.chart.panes()[0]?.getHTMLElement();
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;
    return { x, y, inside: x >= 0 && y >= 0 && x <= r.width && y <= r.height };
  }

  // ---- pointer --------------------------------------------------------------------

  _onPointerDown(e) {
    this._swallow = false;
    if (!this.order || e.button !== 0 || this.isBusy()) return;
    const p = this._localPoint(e);
    if (!p || !p.inside) return;
    const key = this._hit(p.x, p.y, TOLERANCE[e.pointerType] || TOLERANCE.mouse);
    if (!key) return;
    // Ours: the drawing tools and the chart (pan) do not see this press
    e.stopImmediatePropagation();
    e.preventDefault();
    this._swallow = true;
    this.drag = { key, start: { ...this._prices(), orderType: this.order.orderType }, shift: e.shiftKey, moved: false };
    this.hover = key;
    window.addEventListener('pointermove', this._onPointerMove);
    window.addEventListener('pointerup', this._onPointerUp);
    window.addEventListener('pointercancel', this._onPointerUp);
    this._update();
  }

  _onPointerMove(e) {
    const g = this.drag;
    if (!g || !this.order || !this.series) return;
    const p = this._localPoint(e);
    if (!p) return;
    const raw = this.series.coordinateToPrice(p.y);
    if (!Number.isFinite(raw)) return;
    const price = this._round(raw);
    const step = this._step() || Math.abs(price) * 1e-6;
    const isLong = this.order.side === 'LONG';
    const { start } = g;
    const next = { entry: start.entry, stopLoss: start.stopLoss, takeProfit: start.takeProfit, orderType: start.orderType };

    if (g.key === 'takeProfit') {
      next.takeProfit = isLong ? Math.max(price, start.entry + step) : Math.min(price, start.entry - step);
    } else if (g.key === 'stopLoss') {
      next.stopLoss = isLong ? Math.min(price, start.entry - step) : Math.max(price, start.entry + step);
    } else if (g.shift || e.shiftKey) {
      // Entry alone, between the stop and the target
      let entry = price;
      const lo = isLong ? start.stopLoss : start.takeProfit;
      const hi = isLong ? start.takeProfit : start.stopLoss;
      if (Number.isFinite(lo)) entry = Math.max(entry, lo + step);
      if (Number.isFinite(hi)) entry = Math.min(entry, hi - step);
      next.entry = entry;
      next.orderType = 'limit';
    } else {
      // Whole order: stop and target keep their distance to the entry
      const delta = price - start.entry;
      next.entry = price;
      if (Number.isFinite(start.stopLoss)) next.stopLoss = this._round(start.stopLoss + delta);
      if (Number.isFinite(start.takeProfit)) next.takeProfit = this._round(start.takeProfit + delta);
      next.orderType = 'limit';
    }
    if (g.key === 'entry' && !g.moved && Math.abs(price - start.entry) < step) return;
    g.moved = true;
    this.order = { ...this.order, ...next };
    this._update();
    this.onChange(next, { done: false, key: g.key });
  }

  _onPointerUp() {
    const g = this.drag;
    this._stopListening();
    if (!g) return;
    this.drag = null;
    if (g.moved && this.order) {
      const { entry, stopLoss, takeProfit, orderType } = this.order;
      this.onChange({ entry, stopLoss, takeProfit, orderType }, { done: true, key: g.key });
    }
    this._update();
  }

  _stopListening() {
    window.removeEventListener('pointermove', this._onPointerMove);
    window.removeEventListener('pointerup', this._onPointerUp);
    window.removeEventListener('pointercancel', this._onPointerUp);
  }

  // The chart pans on mouse/touch events: swallow the ones that belong to our press
  _block(e) {
    if (this._swallow || this.drag) {
      e.stopImmediatePropagation();
      if (e.type === 'mousedown') e.preventDefault();
      this._swallow = false;
    }
  }

  _onHover(e) {
    if (this.drag || !this.order) return;
    const p = this._localPoint(e);
    const key = p && p.inside && !this.isBusy() ? this._hit(p.x, p.y, TOLERANCE[e.pointerType] || TOLERANCE.mouse) : null;
    if (key !== this.hover) {
      this.hover = key;
      this._update();
    }
  }

  // ---- rendering ------------------------------------------------------------------

  _format(price) {
    if (!this.series || !Number.isFinite(price)) return '—';
    return this.series.priceFormatter().format(price);
  }

  _texts() {
    const o = this.order;
    const entry = this._entryPrice();
    const isLong = o.side === 'LONG';
    const extra = o.extra || {};
    const pct = (p) => (Number.isFinite(p) && entry ? Math.abs((p - entry) / entry) * 100 : null);
    const join = (...parts) => parts.filter(Boolean).join('  ');
    const risk = Number.isFinite(o.stopLoss) ? Math.abs(entry - o.stopLoss) : null;
    const reward = Number.isFinite(o.takeProfit) ? Math.abs(o.takeProfit - entry) : null;
    const rr = risk && reward ? (reward / risk).toFixed(2) : null;
    const kind = o.orderType === 'market' ? `market ≈ ${this._format(entry)}` : `limit ${this._format(entry)}`;
    return {
      entry: join(`${isLong ? 'Long' : 'Short'} ${kind}`, rr && `R:R ${rr}`, extra.entry),
      stopLoss: join(`Stop ${this._format(o.stopLoss)}`, pct(o.stopLoss) != null && `(−${pct(o.stopLoss).toFixed(2)}%)`, extra.stopLoss),
      takeProfit: join(`Target ${this._format(o.takeProfit)}`, pct(o.takeProfit) != null && `(+${pct(o.takeProfit).toFixed(2)}%)`, extra.takeProfit),
    };
  }

  _draw(target) {
    const o = this.order;
    if (!o || !this.series) return;
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      const W = mediaSize.width;
      this.width = W;
      const prices = this._prices();
      const yE = this._y(prices.entry);
      if (yE === null) return;
      const yS = this._y(prices.stopLoss);
      const yT = this._y(prices.takeProfit);

      // Zones start right after the last bar (or near the right edge when it is off screen),
      // and always leave room for the labels
      const texts = this._texts();
      const labelW = Math.max(...KEYS.map(k => pillWidth(ctx, texts[k], k === 'entry')));
      const lastX = this.lastTime != null ? this.chart.timeScale().timeToCoordinate(this.lastTime) : null;
      const room = Math.max(ZONE_MIN, labelW + 16);
      const x0 = Math.max(0, Math.min(lastX == null ? W - room : lastX + 6, W - room));
      const labelX = Math.max(4, Math.min(x0 + 8, W - labelW - 6));
      ctx.save();
      if (yT !== null) {
        ctx.fillStyle = 'rgba(8, 153, 129, 0.18)';
        ctx.fillRect(x0, Math.min(yE, yT), W - x0, Math.abs(yT - yE));
      }
      if (yS !== null) {
        ctx.fillStyle = 'rgba(242, 54, 69, 0.18)';
        ctx.fillRect(x0, Math.min(yE, yS), W - x0, Math.abs(yS - yE));
      }

      const active = this.drag?.key || this.hover;
      const line = (y, color, key, dashed) => {
        if (y === null) return;
        ctx.strokeStyle = color;
        ctx.lineWidth = active === key ? 2 : 1;
        ctx.setLineDash(dashed ? [6, 4] : []);
        ctx.beginPath();
        ctx.moveTo(0, Math.round(y) + 0.5);
        ctx.lineTo(W, Math.round(y) + 0.5);
        ctx.stroke();
      };
      line(yT, UP, 'takeProfit', true);
      line(yS, DOWN, 'stopLoss', true);
      line(yE, ENTRY, 'entry', o.orderType === 'market');

      // Labels at the start of the zones, pushed apart when the lines are close
      const labels = [
        { key: 'takeProfit', y: yT, bg: UP },
        { key: 'stopLoss', y: yS, bg: DOWN },
        { key: 'entry', y: yE, bg: ENTRY, strong: true },
      ].filter(l => l.y !== null).sort((a, b) => a.y - b.y);
      const gap = LABEL_H + 3;
      for (let i = 1; i < labels.length; i += 1) {
        labels[i].ly = Math.max(labels[i].y, (labels[i - 1].ly ?? labels[i - 1].y) + gap);
      }
      labels[0].ly = labels[0].ly ?? labels[0].y;
      ctx.setLineDash([]);
      labels.forEach(l => {
        const bg = active && active !== l.key ? `${l.bg}cc` : l.bg;
        pill(ctx, texts[l.key], labelX, l.ly ?? l.y, { bg, grip: true, strong: l.strong });
      });
      ctx.restore();
    });
  }

  _priceAxisViews() {
    const o = this.order;
    if (!o || !this.series) return [];
    const prices = this._prices();
    const series = this.series;
    const view = (price, color) => ({
      coordinate: () => series.priceToCoordinate(price) ?? -100,
      text: () => this._format(price),
      textColor: () => '#ffffff',
      backColor: () => color,
      visible: () => Number.isFinite(price),
      tickVisible: () => true,
    });
    return [view(prices.takeProfit, UP), view(prices.stopLoss, DOWN), view(prices.entry, ENTRY)];
  }
}
