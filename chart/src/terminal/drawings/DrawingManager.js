/**
 * Drawing tools for lightweight-charts: one series primitive renders every
 * drawing (so a frame costs one pass), and pointer handlers on the chart
 * container create, select, drag and edit them.
 *
 * Mouse/touch events the chart would use for panning are stopped in the
 * capture phase only while a drawing gesture is active, so normal scrolling
 * and zooming keep working everywhere else.
 */
import { makeMapper } from './geometry';
import { TOOL_DEFS, renderDrawing, hitTestDrawing, moveHandle } from './tools';

const HISTORY_LIMIT = 100;
let idCounter = 0;
const newId = () => `d${Date.now().toString(36)}${(idCounter += 1)}`;

const clone = (v) => JSON.parse(JSON.stringify(v));

export function persistableDrawings(drawings) {
  return drawings
    .filter(d => !TOOL_DEFS[d.type]?.transient)
    .map(d => ({ id: d.id, type: d.type, points: d.points, style: d.style, locked: !!d.locked }));
}

export function sanitizeDrawings(list) {
  if (!Array.isArray(list)) return [];
  return list.filter(d => d && TOOL_DEFS[d.type] && Array.isArray(d.points)
    && d.points.every(p => Number.isFinite(p?.time) && Number.isFinite(p?.price)))
    .map(d => ({ ...d, id: d.id || newId(), style: { ...TOOL_DEFS[d.type].style, ...(d.style || {}) } }));
}

class DrawingPrimitive {
  constructor(manager) {
    this._manager = manager;
    const renderer = { draw: (target) => manager._draw(target) };
    this._paneViews = [{ renderer: () => renderer, zOrder: () => 'top' }];
  }

  attached(params) {
    this._manager._requestUpdate = params.requestUpdate;
  }

  detached() {
    this._manager._requestUpdate = null;
  }

  updateAllViews() {}

  paneViews() {
    return this._paneViews;
  }

  priceAxisViews() {
    return this._manager._priceAxisViews();
  }

  timeAxisViews() {
    return this._manager._timeAxisViews();
  }

  hitTest(x, y) {
    return this._manager._cursorHit(x, y);
  }
}

export class DrawingManager {
  constructor({ chart, container, onChange, onSelect, onToolChange, onTextEdit }) {
    this.chart = chart;
    this.container = container;
    this.series = null;
    this.times = [];
    this.bars = [];
    this.drawings = [];
    this.tool = null;
    this.magnet = false;
    this.hidden = false;
    this.locked = false;
    this.selectedId = null;
    this.hoverId = null;
    this.editingId = null;
    this.history = [];
    this.gesture = null;
    this.placing = null;
    this._blockChart = false;
    this._requestUpdate = null;
    this._cb = { onChange, onSelect, onToolChange, onTextEdit };
    this.primitive = new DrawingPrimitive(this);

    this._onPointerDown = this._onPointerDown.bind(this);
    this._onPointerMove = this._onPointerMove.bind(this);
    this._onPointerUp = this._onPointerUp.bind(this);
    this._onHoverMove = this._onHoverMove.bind(this);
    this._onDblClick = this._onDblClick.bind(this);
    this._blockIfActive = this._blockIfActive.bind(this);

    container.addEventListener('pointerdown', this._onPointerDown, true);
    container.addEventListener('pointermove', this._onHoverMove);
    container.addEventListener('dblclick', this._onDblClick, true);
    container.addEventListener('mousedown', this._blockIfActive, true);
    container.addEventListener('touchstart', this._blockIfActive, true);
  }

  destroy() {
    this.container.removeEventListener('pointerdown', this._onPointerDown, true);
    this.container.removeEventListener('pointermove', this._onHoverMove);
    this.container.removeEventListener('dblclick', this._onDblClick, true);
    this.container.removeEventListener('mousedown', this._blockIfActive, true);
    this.container.removeEventListener('touchstart', this._blockIfActive, true);
    window.removeEventListener('pointermove', this._onPointerMove);
    window.removeEventListener('pointerup', this._onPointerUp);
    if (this.series) this.series.detachPrimitive(this.primitive);
  }

  // ---- wiring ---------------------------------------------------------------

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

  setData(times, bars) {
    this.times = times || [];
    this.bars = bars || [];
    this.update();
  }

  update() {
    if (this._requestUpdate) this._requestUpdate();
  }

  // ---- public API -------------------------------------------------------------

  setDrawings(list) {
    this.drawings = sanitizeDrawings(clone(list || []));
    this.history = [];
    this.placing = null;
    this.gesture = null;
    this.editingId = null;
    this.selectedId = null;
    // Let the UI reset its drawing state (selection, count, undo)
    this._emitSelect();
    this._emitChange();
    this.update();
  }

  getDrawings() {
    return persistableDrawings(this.drawings);
  }

  setTool(tool) {
    this._cancelPlacing();
    this.tool = tool && TOOL_DEFS[tool] ? tool : null;
    if (this.tool) this._select(null);
    this.container.style.touchAction = this.tool ? 'none' : '';
    this.update();
  }

  setMagnet(on) {
    this.magnet = !!on;
  }

  setHidden(on) {
    this.hidden = !!on;
    if (this.hidden) this._select(null);
    this.update();
  }

  setLocked(on) {
    this.locked = !!on;
    if (this.locked) this._select(null);
    this.update();
  }

  /** Esc: drop the drawing being placed, otherwise the active tool, otherwise the selection. */
  cancel() {
    if (this.placing || this.gesture) {
      this._cancelPlacing();
      return true;
    }
    if (this.tool) {
      this.setTool(null);
      this._cb.onToolChange?.(null);
      return true;
    }
    if (this.drawings.some(d => TOOL_DEFS[d.type].transient)) {
      this.drawings = this.drawings.filter(d => !TOOL_DEFS[d.type].transient);
      this.update();
      return true;
    }
    if (this.selectedId) {
      this._select(null);
      return true;
    }
    return false;
  }

  deleteSelected() {
    if (!this.selectedId) return false;
    const d = this.drawings.find(x => x.id === this.selectedId);
    if (!d) return false;
    this._commit(() => {
      this.drawings = this.drawings.filter(x => x.id !== d.id);
    });
    this._select(null);
    return true;
  }

  removeAll() {
    if (this.drawings.length === 0) return;
    this._commit(() => {
      this.drawings = [];
    });
    this._select(null);
  }

  undo() {
    const prev = this.history.pop();
    if (!prev) return false;
    this._cancelPlacing();
    this.drawings = prev;
    if (!this.drawings.some(d => d.id === this.selectedId)) this._select(null);
    this._emitChange();
    this.update();
    return true;
  }

  canUndo() {
    return this.history.length > 0;
  }

  updateStyle(id, patch) {
    const idx = this.drawings.findIndex(x => x.id === id);
    if (idx < 0) return;
    this._commit(() => {
      const d = this.drawings[idx];
      this.drawings[idx] = { ...d, style: { ...d.style, ...patch } };
    });
    this._emitSelect();
  }

  setDrawingLocked(id, locked) {
    const idx = this.drawings.findIndex(x => x.id === id);
    if (idx < 0) return;
    this._commit(() => {
      this.drawings[idx] = { ...this.drawings[idx], locked };
    });
    this._emitSelect();
  }

  cloneSelected() {
    const d = this.drawings.find(x => x.id === this.selectedId);
    if (!d) return;
    const copy = { ...clone({ ...d, _box: undefined }), id: newId(), locked: false };
    // Offset the copy a few bars to the right so it is visible
    const m = this._mapper();
    if (m) {
      copy.points = copy.points.map(p => ({ ...p, time: m.timeOf((m.logicalOf(p.time) ?? 0) + 5) }));
    }
    this._commit(() => {
      this.drawings = [...this.drawings, copy];
    });
    this._select(copy.id);
  }

  setEditing(id) {
    this.editingId = id;
    this.update();
  }

  /** Called by the text editor overlay. Empty text removes a text drawing. */
  commitText(id, text) {
    this.editingId = null;
    const d = this.drawings.find(x => x.id === id);
    if (!d) return;
    if (!text.trim()) {
      this._commit(() => {
        this.drawings = this.drawings.filter(x => x.id !== id);
      });
      this._select(null);
      return;
    }
    this.updateStyle(id, { text });
  }

  getSelected() {
    return this.drawings.find(x => x.id === this.selectedId) || null;
  }

  /** Pixel position of a drawing's first point, relative to the chart container. */
  anchorOf(id) {
    const d = this.drawings.find(x => x.id === id);
    const m = this._mapper();
    if (!d || !m) return null;
    const x = m.toX(d.points[0].time);
    const y = m.toY(d.points[0].price);
    if (x === null || y === null) return null;
    const off = this._paneOffset();
    return { x: x + off.x, y: y + off.y };
  }

  // ---- internals ------------------------------------------------------------

  _mapper() {
    return makeMapper(this.chart, this.series, this.times);
  }

  _paneElement() {
    return this.chart.panes()[0]?.getHTMLElement() || null;
  }

  _paneOffset() {
    const el = this._paneElement();
    if (!el) return { x: 0, y: 0 };
    const a = el.getBoundingClientRect();
    const b = this.container.getBoundingClientRect();
    return { x: a.left - b.left, y: a.top - b.top };
  }

  _paneSize() {
    const el = this._paneElement();
    return el ? { width: el.clientWidth, height: el.clientHeight } : { width: 0, height: 0 };
  }

  _localPoint(e) {
    const el = this._paneElement();
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;
    return { x, y, inside: x >= 0 && y >= 0 && x <= r.width && y <= r.height };
  }

  _valueAt(x, y, { snap = true } = {}) {
    const m = this._mapper();
    if (!m || this.times.length === 0) return null;
    const logical = m.toLogical(x);
    const idx = Math.round(logical);
    const time = m.timeOf(snap ? idx : logical);
    let price = m.toPrice(y);
    if (time === null || price === null) return null;
    const bar = this.bars[idx];
    if (snap && this.magnet && bar) {
      let best = price;
      let bestDist = Infinity;
      [bar.open, bar.high, bar.low, bar.close].forEach(p => {
        const py = m.toY(p);
        if (py === null) return;
        const dist = Math.abs(py - y);
        if (dist < bestDist) {
          bestDist = dist;
          best = p;
        }
      });
      price = best;
    }
    return { time, price };
  }

  _frame() {
    return { ...this._paneSize() };
  }

  _hit(x, y) {
    if (this.hidden) return null;
    const m = this._mapper();
    if (!m) return null;
    const frame = this._frame();
    // Top-most first; the selected drawing wins so its handles stay reachable
    const order = [...this.drawings].reverse();
    const selected = order.find(d => d.id === this.selectedId);
    if (selected) {
      const hit = hitTestDrawing(selected, m, x, y, frame, { withHandles: true });
      if (hit) return { drawing: selected, ...hit };
    }
    for (const d of order) {
      if (d.id === this.selectedId) continue;
      const hit = hitTestDrawing(d, m, x, y, frame, { withHandles: d.id === this.hoverId });
      if (hit) return { drawing: d, ...hit };
    }
    return null;
  }

  _cursorHit(x, y) {
    if (this.tool || this.placing) return { cursorStyle: 'crosshair', externalId: 'drawing-tool', zOrder: 'top' };
    if (this.gesture?.kind === 'move' || this.gesture?.kind === 'handle') {
      return { cursorStyle: 'grabbing', externalId: 'drawing', zOrder: 'top' };
    }
    if (this.locked) return null;
    const hit = this._hit(x, y);
    if (!hit) return null;
    const locked = hit.drawing.locked;
    return {
      cursorStyle: locked ? 'default' : (hit.part === 'handle' ? 'move' : 'pointer'),
      externalId: hit.drawing.id,
      zOrder: 'top',
    };
  }

  _select(id) {
    if (this.selectedId === id) return;
    this.selectedId = id;
    this._emitSelect();
    this.update();
  }

  _emitSelect() {
    this._cb.onSelect?.(this.getSelected());
  }

  _emitChange() {
    this._cb.onChange?.(this.getDrawings());
  }

  _commit(mutate, before = null) {
    const snapshot = before || clone(this.drawings.map(d => ({ ...d, _box: undefined })));
    mutate();
    this.history.push(snapshot);
    if (this.history.length > HISTORY_LIMIT) this.history.shift();
    this._emitChange();
    this.update();
  }

  _cancelPlacing() {
    if (this.placing) {
      this.drawings = this.drawings.filter(d => d !== this.placing);
      this.placing = null;
    }
    if (this.gesture) {
      if (this.gesture.kind === 'create' || this.gesture.kind === 'brush') {
        this.drawings = this.drawings.filter(d => d !== this.gesture.drawing);
      } else if (this.gesture.before) {
        this.drawings = this.gesture.before;
      }
      this._endGesture();
    }
    this.update();
  }

  _beginGesture(gesture) {
    this.gesture = gesture;
    this._blockChart = true;
    window.addEventListener('pointermove', this._onPointerMove);
    window.addEventListener('pointerup', this._onPointerUp);
  }

  _endGesture() {
    this.gesture = null;
    this._blockChart = false;
    window.removeEventListener('pointermove', this._onPointerMove);
    window.removeEventListener('pointerup', this._onPointerUp);
  }

  // The chart listens to mouse/touch events (not pointer events): swallow the
  // ones that belong to a pointerdown we handled, so it does not pan or click.
  _blockIfActive(e) {
    if (this._swallow || this._blockChart) {
      e.stopPropagation();
      // Also keep focus where it is (e.g. the text editor that just opened)
      if (e.type === 'mousedown') e.preventDefault();
      this._swallow = false;
    }
  }

  _finishCreate(d) {
    const before = this.gesture?.before || this._placingBefore || clone(this.drawings.filter(x => x !== d));
    this.placing = null;
    this._placingBefore = null;
    this._endGesture();
    const def = TOOL_DEFS[d.type];
    if (def.transient) {
      // Only one measurement at a time
      this.drawings = this.drawings.filter(x => x === d || !TOOL_DEFS[x.type].transient);
      this.update();
    } else {
      this.history.push(before);
      if (this.history.length > HISTORY_LIMIT) this.history.shift();
      this._emitChange();
      this._select(d.id);
    }
    if (this.tool) {
      this.tool = null;
      this.container.style.touchAction = '';
      this._cb.onToolChange?.(null);
    }
    if (d.type === 'text') {
      this.editingId = d.id;
      this._cb.onTextEdit?.(d);
    }
    this.update();
  }

  _createDrawing(type, pt) {
    const def = TOOL_DEFS[type];
    const d = { id: newId(), type, points: [{ ...pt }], style: { ...def.style }, locked: false };
    if (def.clicks === 2) d.points.push({ ...pt });
    if (type === 'long' || type === 'short') {
      const m = this._mapper();
      const size = this._paneSize();
      // Default risk: 6% of the visible price range, target 2R, 20 bars wide
      const top = m?.toPrice(0);
      const bottom = m?.toPrice(size.height);
      const span = Number.isFinite(top) && Number.isFinite(bottom) ? Math.abs(top - bottom) : Math.abs(pt.price) * 0.01;
      const risk = span * 0.06;
      const dir = type === 'long' ? 1 : -1;
      const endTime = m ? m.timeOf((m.logicalOf(pt.time) ?? 0) + 20) : pt.time;
      d.points = [
        { ...pt },
        { time: endTime, price: pt.price + dir * risk * 2 },
        { time: endTime, price: pt.price - dir * risk },
      ];
    }
    return d;
  }

  _claim(e) {
    this._swallow = true;
    e.stopPropagation();
  }

  _onPointerDown(e) {
    this._swallow = false;
    if (e.button !== 0) return;
    const p = this._localPoint(e);
    if (!p) return;

    // Second click of a two-click drawing
    if (this.placing) {
      this._claim(e);
      const pt = this._valueAt(p.x, p.y);
      if (pt) this.placing.points[1] = pt;
      this._finishCreate(this.placing);
      return;
    }

    if (!p.inside) return;

    const tool = this.tool || (e.shiftKey && !this.hidden ? 'measure' : null);
    if (tool) {
      const pt = this._valueAt(p.x, p.y, { snap: tool !== 'brush' });
      if (!pt) return;
      this._claim(e);
      const before = clone(this.drawings.map(d => ({ ...d, _box: undefined })));
      const d = this._createDrawing(tool, pt);
      this.drawings = [...this.drawings.filter(x => !TOOL_DEFS[x.type].transient), d];
      const def = TOOL_DEFS[tool];
      if (def.clicks === 1) {
        this.gesture = { before };
        this._finishCreate(d);
        return;
      }
      this._beginGesture({ kind: def.clicks === 'drag' ? 'brush' : 'create', drawing: d, startX: p.x, startY: p.y, before, lastX: p.x, lastY: p.y });
      this.update();
      return;
    }

    // Clicking anywhere removes a finished measurement (TradingView behaviour)
    const hadTransient = this.drawings.some(d => TOOL_DEFS[d.type].transient);

    if (this.locked || this.hidden) {
      if (hadTransient) this.cancel();
      return;
    }
    const hit = this._hit(p.x, p.y);
    if (!hit) {
      if (hadTransient) {
        this.drawings = this.drawings.filter(d => !TOOL_DEFS[d.type].transient);
        this.update();
      }
      this._select(null);
      return;
    }

    this._claim(e);
    const d = hit.drawing;
    if (!TOOL_DEFS[d.type].transient) this._select(d.id);
    if (d.locked) return;

    const before = clone(this.drawings.map(x => ({ ...x, _box: undefined })));
    const m = this._mapper();
    if (hit.part === 'handle') {
      this._beginGesture({ kind: 'handle', id: d.id, handle: hit.handle, before, moved: false });
    } else if (m) {
      this._beginGesture({
        kind: 'move',
        id: d.id,
        before,
        moved: false,
        startLogical: Math.round(m.toLogical(p.x)),
        startPrice: m.toPrice(p.y),
        origPoints: clone(d.points),
      });
    }
    this.update();
  }

  _onPointerMove(e) {
    const g = this.gesture;
    if (!g) return;
    const p = this._localPoint(e);
    if (!p) return;
    const m = this._mapper();
    if (!m) return;

    if (g.kind === 'create') {
      const pt = this._valueAt(p.x, p.y);
      if (pt) g.drawing.points[1] = pt;
      if (Math.hypot(p.x - g.startX, p.y - g.startY) > 4) g.moved = true;
    } else if (g.kind === 'brush') {
      if (Math.hypot(p.x - g.lastX, p.y - g.lastY) < 3) return;
      const pt = this._valueAt(p.x, p.y, { snap: false });
      if (pt) g.drawing.points.push(pt);
      g.lastX = p.x;
      g.lastY = p.y;
    } else if (g.kind === 'handle') {
      const d = this.drawings.find(x => x.id === g.id);
      const pt = this._valueAt(p.x, p.y);
      if (d && pt) {
        moveHandle(d, g.handle, pt);
        g.moved = true;
      }
    } else if (g.kind === 'move') {
      const d = this.drawings.find(x => x.id === g.id);
      const price = m.toPrice(p.y);
      if (!d || price === null) return;
      const dl = Math.round(m.toLogical(p.x)) - g.startLogical;
      const dp = price - g.startPrice;
      d.points = g.origPoints.map(pt => ({
        time: m.timeOf((m.logicalOf(pt.time) ?? 0) + dl),
        price: pt.price + dp,
      }));
      g.moved = true;
    }
    this.update();
  }

  _onPointerUp() {
    const g = this.gesture;
    if (!g) return;
    if (g.kind === 'create') {
      if (g.moved) {
        this._finishCreate(g.drawing);
      } else {
        // Click-click mode: wait for the second click
        this.placing = g.drawing;
        this._placingBefore = g.before;
        this._endGesture();
      }
      return;
    }
    if (g.kind === 'brush') {
      if (g.drawing.points.length < 2) {
        this.drawings = this.drawings.filter(d => d !== g.drawing);
        this._endGesture();
        this.update();
        return;
      }
      this._finishCreate(g.drawing);
      return;
    }
    this._endGesture();
    if (g.moved) {
      this.history.push(g.before);
      if (this.history.length > HISTORY_LIMIT) this.history.shift();
      this._emitChange();
      this._emitSelect();
    }
    this.update();
  }

  _onHoverMove(e) {
    if (this.gesture) return;
    const p = this._localPoint(e);
    if (!p) return;
    if (this.placing) {
      const pt = this._valueAt(p.x, p.y);
      if (pt) {
        this.placing.points[1] = pt;
        this.update();
      }
      return;
    }
    if (this.tool || this.locked || this.hidden || !p.inside) {
      if (this.hoverId) {
        this.hoverId = null;
        this.update();
      }
      return;
    }
    const hit = this._hit(p.x, p.y);
    const id = hit ? hit.drawing.id : null;
    if (id !== this.hoverId) {
      this.hoverId = id;
      this.update();
    }
  }

  _onDblClick(e) {
    const p = this._localPoint(e);
    if (!p || !p.inside || this.locked || this.hidden) return;
    const hit = this._hit(p.x, p.y);
    if (hit && hit.drawing.type === 'text' && !hit.drawing.locked) {
      e.stopPropagation();
      this._select(hit.drawing.id);
      this.editingId = hit.drawing.id;
      this._cb.onTextEdit?.(hit.drawing);
      this.update();
    }
  }

  // ---- rendering ------------------------------------------------------------

  _formatPrice() {
    const s = this.series;
    if (!s) return (p) => String(p);
    const f = s.priceFormatter();
    return (p) => f.format(p);
  }

  _draw(target) {
    if (this.hidden || this.drawings.length === 0) return;
    const m = this._mapper();
    if (!m) return;
    const formatPrice = this._formatPrice();
    target.useMediaCoordinateSpace(scope => {
      const ctx = scope.context;
      const base = { width: scope.mediaSize.width, height: scope.mediaSize.height, formatPrice, editingId: this.editingId };
      for (const d of this.drawings) {
        renderDrawing(ctx, d, m, {
          ...base,
          selected: d.id === this.selectedId,
          hovered: d.id === this.hoverId && d.id !== this.selectedId,
        });
      }
    });
  }

  _priceAxisViews() {
    if (this.hidden || !this.series) return [];
    const views = [];
    const fmt = this._formatPrice();
    const series = this.series;
    const add = (price, color) => {
      views.push({
        coordinate: () => series.priceToCoordinate(price) ?? -100,
        text: () => fmt(price),
        textColor: () => '#ffffff',
        backColor: () => color,
        visible: () => true,
        tickVisible: () => true,
      });
    };
    for (const d of this.drawings) {
      if (d.type === 'hline') add(d.points[0].price, d.style.color);
      else if (d.id === this.selectedId && d.type !== 'vline' && d.type !== 'brush' && d.type !== 'text') {
        d.points.forEach(p => add(p.price, '#2962ff'));
      }
    }
    return views;
  }

  _timeAxisViews() {
    if (this.hidden || !this.series) return [];
    const m = this._mapper();
    if (!m) return [];
    const views = [];
    const add = (time, color) => {
      const d = new Date(time * 1000);
      const pad = (n) => String(n).padStart(2, '0');
      const text = `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
      views.push({
        coordinate: () => m.toX(time) ?? -100,
        text: () => text,
        textColor: () => '#ffffff',
        backColor: () => color,
        visible: () => true,
        tickVisible: () => true,
      });
    };
    for (const d of this.drawings) {
      if (d.type === 'vline') add(d.points[0].time, d.style.color);
      else if (d.id === this.selectedId && d.type !== 'hline' && d.type !== 'brush') {
        const seen = new Set();
        d.points.forEach(p => {
          if (seen.has(p.time)) return;
          seen.add(p.time);
          add(p.time, '#2962ff');
        });
      }
    }
    return views;
  }
}
