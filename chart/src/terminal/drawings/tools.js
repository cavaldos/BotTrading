// Drawing tool catalogue: defaults, handles, rendering and hit testing.
// A drawing is plain JSON so it can be saved:
//   { id, type, points: [{ time, price }], style: {...}, locked }
import { distToSegment, extendRay } from './geometry';

export const FONT = '-apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, Ubuntu, sans-serif';

export const FIB_LEVELS = [
  { level: 0, color: '#787b86' },
  { level: 0.236, color: '#f23645' },
  { level: 0.382, color: '#ff9800' },
  { level: 0.5, color: '#4caf50' },
  { level: 0.618, color: '#089981' },
  { level: 0.786, color: '#00bcd4' },
  { level: 1, color: '#787b86' },
];

// clicks: how many points the user places. 'drag' = freehand.
export const TOOL_DEFS = {
  trend: { label: 'Trend line', clicks: 2, shortcut: 'Alt+T', style: { color: '#2962ff', width: 2, lineStyle: 'solid' } },
  ray: { label: 'Ray', clicks: 2, style: { color: '#2962ff', width: 2, lineStyle: 'solid' } },
  hline: { label: 'Horizontal line', clicks: 1, shortcut: 'Alt+H', style: { color: '#2962ff', width: 1, lineStyle: 'solid' } },
  vline: { label: 'Vertical line', clicks: 1, shortcut: 'Alt+V', style: { color: '#2962ff', width: 1, lineStyle: 'solid' } },
  rect: { label: 'Rectangle', clicks: 2, shortcut: 'Alt+Shift+R', style: { color: '#9c27b0', width: 1, lineStyle: 'solid', fill: 'rgba(156, 39, 176, 0.2)' } },
  fib: { label: 'Fib retracement', clicks: 2, shortcut: 'Alt+F', style: { color: '#787b86', width: 1, lineStyle: 'solid' } },
  long: { label: 'Long position', clicks: 1, style: { color: '#089981', width: 1, lineStyle: 'solid' } },
  short: { label: 'Short position', clicks: 1, style: { color: '#f23645', width: 1, lineStyle: 'solid' } },
  measure: { label: 'Measure', clicks: 2, shortcut: 'Shift+click', transient: true, style: { color: '#2962ff', width: 1, lineStyle: 'solid' } },
  text: { label: 'Text', clicks: 1, style: { color: '#d1d4dc', width: 1, lineStyle: 'solid', text: '', fontSize: 14 } },
  brush: { label: 'Brush', clicks: 'drag', style: { color: '#ff9800', width: 2, lineStyle: 'solid' } },
};

export const COLOR_SWATCHES = [
  '#2962ff', '#089981', '#f23645', '#ff9800', '#ffeb3b', '#9c27b0', '#00bcd4', '#e91e63', '#d1d4dc', '#787b86',
];

function setDash(ctx, lineStyle) {
  if (lineStyle === 'dashed') ctx.setLineDash([6, 4]);
  else if (lineStyle === 'dotted') ctx.setLineDash([1.5, 3]);
  else ctx.setLineDash([]);
}

function withAlpha(hex, alpha) {
  const m = /^#([0-9a-f]{6})$/i.exec(hex || '');
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

function labelBox(ctx, text, x, y, { bg, fg = '#ffffff', align = 'center', valign = 'middle', font = `12px ${FONT}` }) {
  ctx.font = font;
  const lines = String(text).split('\n');
  const lineH = 16;
  const padX = 8;
  const w = Math.max(...lines.map(l => ctx.measureText(l).width)) + padX * 2;
  const h = lines.length * lineH + 6;
  let bx = x;
  if (align === 'center') bx = x - w / 2;
  else if (align === 'right') bx = x - w;
  let by = y;
  if (valign === 'middle') by = y - h / 2;
  else if (valign === 'bottom') by = y - h;
  ctx.fillStyle = bg;
  ctx.beginPath();
  ctx.roundRect(bx, by, w, h, 4);
  ctx.fill();
  ctx.fillStyle = fg;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  lines.forEach((l, i) => ctx.fillText(l, bx + w / 2, by + 3 + lineH * i + lineH / 2));
  return { x: bx, y: by, w, h };
}

function fmtPrice(fmt, p) {
  return fmt ? fmt(p) : String(p);
}

/** Pixel positions of a drawing's points, or null when off the time scale. */
export function screenPoints(d, m) {
  const pts = d.points.map(p => ({ x: m.toX(p.time), y: m.toY(p.price) }));
  return pts.some(p => p.x === null || p.y === null) ? null : pts;
}

/** Handle positions in pixels (index matches moveHandle). */
export function handlePositions(d, pts, frame) {
  switch (d.type) {
    case 'rect':
      return [pts[0], pts[1], { x: pts[0].x, y: pts[1].y }, { x: pts[1].x, y: pts[0].y }];
    case 'vline':
      return [{ x: pts[0].x, y: frame.height / 2 }];
    case 'long':
    case 'short':
      return [pts[0], { x: pts[0].x, y: pts[1].y }, { x: pts[0].x, y: pts[2].y }, { x: pts[1].x, y: pts[0].y }];
    case 'brush':
      return [];
    default:
      return pts;
  }
}

/** Apply a handle drag. `pt` is the snapped {time, price} under the pointer. */
export function moveHandle(d, handle, pt) {
  const p = d.points;
  switch (d.type) {
    case 'rect':
      if (handle === 0) p[0] = { ...pt };
      else if (handle === 1) p[1] = { ...pt };
      else if (handle === 2) { p[0] = { ...p[0], time: pt.time }; p[1] = { ...p[1], price: pt.price }; }
      else if (handle === 3) { p[1] = { ...p[1], time: pt.time }; p[0] = { ...p[0], price: pt.price }; }
      break;
    case 'long':
    case 'short': {
      const isLong = d.type === 'long';
      const entry = p[0].price;
      if (handle === 0) {
        const dp = pt.price - entry;
        p[0] = { ...pt };
        p[1] = { ...p[1], price: p[1].price + dp };
        p[2] = { ...p[2], price: p[2].price + dp };
        if (p[1].time <= pt.time) { p[1].time = pt.time + 1; p[2].time = pt.time + 1; }
      } else if (handle === 1) {
        // target must stay on the profit side of entry
        p[1] = { ...p[1], price: isLong ? Math.max(pt.price, entry) : Math.min(pt.price, entry) };
      } else if (handle === 2) {
        p[2] = { ...p[2], price: isLong ? Math.min(pt.price, entry) : Math.max(pt.price, entry) };
      } else if (handle === 3) {
        const t = Math.max(pt.time, p[0].time + 1);
        p[1] = { ...p[1], time: t };
        p[2] = { ...p[2], time: t };
      }
      break;
    }
    default:
      if (p[handle]) p[handle] = { ...pt };
  }
}

/**
 * Hit test one drawing. Returns null, { part: 'body' } or { part: 'handle', handle }.
 */
export function hitTestDrawing(d, m, x, y, frame, { withHandles }) {
  const pts = screenPoints(d, m);
  if (!pts) return null;
  const tol = 6;

  if (withHandles) {
    const hs = handlePositions(d, pts, frame);
    for (let i = 0; i < hs.length; i += 1) {
      if (Math.hypot(hs[i].x - x, hs[i].y - y) <= 8) return { part: 'handle', handle: i };
    }
  }

  const inBox = (a, b, pad = 0) => (
    x >= Math.min(a.x, b.x) - pad && x <= Math.max(a.x, b.x) + pad
    && y >= Math.min(a.y, b.y) - pad && y <= Math.max(a.y, b.y) + pad
  );

  switch (d.type) {
    case 'trend':
    case 'measure':
      if (d.type === 'measure') return inBox(pts[0], pts[1]) ? { part: 'body' } : null;
      return distToSegment(x, y, pts[0].x, pts[0].y, pts[1].x, pts[1].y) <= tol ? { part: 'body' } : null;
    case 'ray': {
      const end = extendRay(pts[0].x, pts[0].y, pts[1].x, pts[1].y, frame.width, frame.height);
      return distToSegment(x, y, pts[0].x, pts[0].y, end.x, end.y) <= tol ? { part: 'body' } : null;
    }
    case 'hline':
      return Math.abs(y - pts[0].y) <= tol ? { part: 'body' } : null;
    case 'vline':
      return Math.abs(x - pts[0].x) <= tol ? { part: 'body' } : null;
    case 'rect':
      return inBox(pts[0], pts[1], tol) ? { part: 'body' } : null;
    case 'fib':
      return inBox(pts[0], pts[1], tol) ? { part: 'body' } : null;
    case 'long':
    case 'short': {
      const top = { x: pts[0].x, y: Math.min(pts[1].y, pts[2].y) };
      const bottom = { x: Math.max(pts[1].x, pts[0].x + 20), y: Math.max(pts[1].y, pts[2].y) };
      return inBox(top, bottom) ? { part: 'body' } : null;
    }
    case 'text': {
      const box = d._box;
      if (!box) return null;
      return x >= box.x - 2 && x <= box.x + box.w + 2 && y >= box.y - 2 && y <= box.y + box.h + 2 ? { part: 'body' } : null;
    }
    case 'brush':
      for (let i = 1; i < pts.length; i += 1) {
        if (distToSegment(x, y, pts[i - 1].x, pts[i - 1].y, pts[i].x, pts[i].y) <= tol) return { part: 'body' };
      }
      return null;
    default:
      return null;
  }
}

/** Render one drawing in media coordinates. */
export function renderDrawing(ctx, d, m, frame) {
  const pts = screenPoints(d, m);
  if (!pts) return;
  const s = d.style || {};
  ctx.save();
  ctx.lineWidth = s.width || 1;
  ctx.strokeStyle = s.color;
  setDash(ctx, s.lineStyle);

  switch (d.type) {
    case 'trend':
      ctx.beginPath();
      ctx.moveTo(pts[0].x, pts[0].y);
      ctx.lineTo(pts[1].x, pts[1].y);
      ctx.stroke();
      break;
    case 'ray': {
      const end = extendRay(pts[0].x, pts[0].y, pts[1].x, pts[1].y, frame.width, frame.height);
      ctx.beginPath();
      ctx.moveTo(pts[0].x, pts[0].y);
      ctx.lineTo(end.x, end.y);
      ctx.stroke();
      break;
    }
    case 'hline':
      ctx.beginPath();
      ctx.moveTo(0, pts[0].y);
      ctx.lineTo(frame.width, pts[0].y);
      ctx.stroke();
      break;
    case 'vline':
      ctx.beginPath();
      ctx.moveTo(pts[0].x, 0);
      ctx.lineTo(pts[0].x, frame.height);
      ctx.stroke();
      break;
    case 'rect': {
      const x = Math.min(pts[0].x, pts[1].x);
      const y = Math.min(pts[0].y, pts[1].y);
      const w = Math.abs(pts[1].x - pts[0].x);
      const h = Math.abs(pts[1].y - pts[0].y);
      ctx.fillStyle = s.fill || withAlpha(s.color, 0.2);
      ctx.fillRect(x, y, w, h);
      ctx.strokeRect(x, y, w, h);
      break;
    }
    case 'fib':
      renderFib(ctx, d, pts, m, frame);
      break;
    case 'long':
    case 'short':
      renderPosition(ctx, d, pts, frame);
      break;
    case 'measure':
      renderMeasure(ctx, d, pts, m, frame);
      break;
    case 'text': {
      const size = s.fontSize || 14;
      ctx.font = `${size}px ${FONT}`;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'top';
      const text = s.text || (frame.editingId === d.id ? '' : 'Text');
      const lines = text.split('\n');
      const lineH = size * 1.3;
      const w = Math.max(8, ...lines.map(l => ctx.measureText(l).width));
      const h = lines.length * lineH;
      const box = { x: pts[0].x, y: pts[0].y - h, w, h };
      // Cache for hit testing (not persisted: underscored keys are stripped on save)
      d._box = box;
      ctx.fillStyle = s.text ? s.color : withAlpha(s.color, 0.5);
      lines.forEach((l, i) => ctx.fillText(l, box.x, box.y + i * lineH));
      if (frame.selected) {
        ctx.setLineDash([3, 3]);
        ctx.strokeStyle = '#2962ff';
        ctx.lineWidth = 1;
        ctx.strokeRect(box.x - 3, box.y - 3, box.w + 6, box.h + 6);
      }
      break;
    }
    case 'brush':
      ctx.lineJoin = 'round';
      ctx.lineCap = 'round';
      ctx.beginPath();
      pts.forEach((p, i) => (i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)));
      ctx.stroke();
      break;
    default:
      break;
  }
  ctx.restore();

  if (frame.selected || frame.hovered) {
    const hs = handlePositions(d, pts, frame);
    ctx.save();
    ctx.setLineDash([]);
    hs.forEach(h => {
      ctx.beginPath();
      ctx.arc(h.x, h.y, frame.selected ? 5 : 4, 0, Math.PI * 2);
      ctx.fillStyle = '#131722';
      ctx.fill();
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = frame.selected ? '#2962ff' : 'rgba(41, 98, 255, 0.6)';
      ctx.stroke();
    });
    ctx.restore();
  }
}

function renderFib(ctx, d, pts, m, frame) {
  const [a, b] = d.points;
  const left = Math.min(pts[0].x, pts[1].x);
  const right = Math.max(pts[0].x, pts[1].x);
  const levels = FIB_LEVELS.map(l => {
    // TradingView convention: level 0 sits on the second point, 1 on the first
    const price = b.price + (a.price - b.price) * l.level;
    return { ...l, price, y: m.toY(price) };
  }).filter(l => l.y !== null);

  for (let i = 0; i < levels.length - 1; i += 1) {
    ctx.fillStyle = withAlpha(levels[i + 1].color, 0.12);
    ctx.fillRect(left, Math.min(levels[i].y, levels[i + 1].y), right - left, Math.abs(levels[i + 1].y - levels[i].y));
  }
  ctx.setLineDash([]);
  ctx.font = `11px ${FONT}`;
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  levels.forEach(l => {
    ctx.strokeStyle = l.color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(left, l.y);
    ctx.lineTo(right, l.y);
    ctx.stroke();
    ctx.fillStyle = l.color;
    ctx.fillText(`${l.level} (${fmtPrice(frame.formatPrice, l.price)})`, left - 4, l.y);
  });
  // Trend line between the two anchors
  ctx.strokeStyle = 'rgba(120, 123, 134, 0.8)';
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(pts[0].x, pts[0].y);
  ctx.lineTo(pts[1].x, pts[1].y);
  ctx.stroke();
}

function renderPosition(ctx, d, pts, frame) {
  const isLong = d.type === 'long';
  const [entry, target, stop] = d.points;
  const x = pts[0].x;
  const w = Math.max(pts[1].x - pts[0].x, 20);
  const yE = pts[0].y;
  const yT = pts[1].y;
  const yS = pts[2].y;

  ctx.setLineDash([]);
  ctx.fillStyle = 'rgba(8, 153, 129, 0.22)';
  ctx.fillRect(x, Math.min(yE, yT), w, Math.abs(yT - yE));
  ctx.fillStyle = 'rgba(242, 54, 69, 0.22)';
  ctx.fillRect(x, Math.min(yE, yS), w, Math.abs(yS - yE));

  ctx.strokeStyle = '#787b86';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x, yE);
  ctx.lineTo(x + w, yE);
  ctx.stroke();

  const reward = Math.abs(target.price - entry.price);
  const risk = Math.abs(entry.price - stop.price);
  const pct = (v) => ((v / entry.price) * 100).toFixed(2);
  const rr = risk > 0 ? (reward / risk).toFixed(2) : '—';
  const cx = x + w / 2;
  const fp = (p) => fmtPrice(frame.formatPrice, p);

  if (frame.selected || frame.hovered || w > 120) {
    labelBox(ctx, `Target: ${fp(target.price)} (${pct(reward)}%)`, cx, isLong ? yT - 4 : yT + 4, {
      bg: '#089981', valign: isLong ? 'bottom' : 'top',
    });
    labelBox(ctx, `Stop: ${fp(stop.price)} (${pct(risk)}%)`, cx, isLong ? yS + 4 : yS - 4, {
      bg: '#f23645', valign: isLong ? 'top' : 'bottom',
    });
  }
  labelBox(ctx, `${isLong ? 'Long' : 'Short'}  R:R ${rr}`, cx, yE, { bg: 'rgba(42, 46, 57, 0.92)', fg: '#d1d4dc' });
}

function renderMeasure(ctx, d, pts, m, frame) {
  const [a, b] = d.points;
  const up = b.price >= a.price;
  const color = up ? '#2962ff' : '#f23645';
  const x1 = Math.min(pts[0].x, pts[1].x);
  const x2 = Math.max(pts[0].x, pts[1].x);
  const y1 = Math.min(pts[0].y, pts[1].y);
  const y2 = Math.max(pts[0].y, pts[1].y);
  ctx.setLineDash([]);
  ctx.fillStyle = withAlpha(color, 0.2);
  ctx.fillRect(x1, y1, x2 - x1, y2 - y1);

  // Arrows across the middle of the box
  const cx = (x1 + x2) / 2;
  const cy = (y1 + y2) / 2;
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(cx, pts[0].y);
  ctx.lineTo(cx, pts[1].y);
  ctx.moveTo(pts[0].x, cy);
  ctx.lineTo(pts[1].x, cy);
  ctx.stroke();
  const arrow = (x, y, dx, dy) => {
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x - dx * 6 - dy * 4, y - dy * 6 - dx * 4);
    ctx.moveTo(x, y);
    ctx.lineTo(x - dx * 6 + dy * 4, y - dy * 6 + dx * 4);
    ctx.stroke();
  };
  arrow(cx, pts[1].y, 0, Math.sign(pts[1].y - pts[0].y) || 1);
  arrow(pts[1].x, cy, Math.sign(pts[1].x - pts[0].x) || 1, 0);

  const diff = b.price - a.price;
  const pct = a.price ? (diff / a.price) * 100 : 0;
  const bars = Math.round((m.logicalOf(b.time) ?? 0) - (m.logicalOf(a.time) ?? 0));
  const secs = Math.abs(b.time - a.time);
  const dur = secs >= 86400 ? `${Math.floor(secs / 86400)}d ${Math.floor((secs % 86400) / 3600)}h`
    : secs >= 3600 ? `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m` : `${Math.round(secs / 60)}m`;
  const sign = diff >= 0 ? '+' : '−';
  const text = `${sign}${fmtPrice(frame.formatPrice, Math.abs(diff))} (${sign}${Math.abs(pct).toFixed(2)}%)\n${bars} bars, ${dur}`;
  labelBox(ctx, text, cx, up ? y1 - 6 : y2 + 6, { bg: color, valign: up ? 'bottom' : 'top' });
}
