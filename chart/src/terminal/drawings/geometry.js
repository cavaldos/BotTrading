import { timeToLogical } from '../../plugins/PrimitiveLayer';

export { timeToLogical };

/** Inverse of timeToLogical: fractional bar index -> unix time (extrapolates past both ends). */
export function logicalToTime(times, logical) {
  const n = times.length;
  if (n === 0 || !Number.isFinite(logical)) return null;
  if (n === 1) return times[0];
  if (logical <= 0) return times[0] + logical * (times[1] - times[0]);
  if (logical >= n - 1) return times[n - 1] + (logical - (n - 1)) * (times[n - 1] - times[n - 2]);
  const i = Math.floor(logical);
  const frac = logical - i;
  return times[i] + frac * (times[i + 1] - times[i]);
}

/**
 * Pixel mapping for one frame. logicalToCoordinate only accepts integers,
 * so derive the linear mapping once (same trick as PrimitiveLayer).
 */
export function makeMapper(chart, series, times) {
  if (!chart || !series) return null;
  const ts = chart.timeScale();
  const x0 = ts.logicalToCoordinate(0);
  const x1 = ts.logicalToCoordinate(1);
  if (x0 === null || x1 === null) return null;
  const spacing = x1 - x0 || 1;
  return {
    spacing,
    toX: (t) => {
      const l = timeToLogical(times, t);
      return l === null ? null : x0 + l * spacing;
    },
    toY: (p) => series.priceToCoordinate(p),
    toLogical: (x) => (x - x0) / spacing,
    toPrice: (y) => series.coordinateToPrice(y),
    logicalOf: (t) => timeToLogical(times, t),
    timeOf: (l) => logicalToTime(times, l),
  };
}

export function distToSegment(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len2 = dx * dx + dy * dy;
  let t = len2 === 0 ? 0 : ((px - x1) * dx + (py - y1) * dy) / len2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

/** Extend the segment p1->p2 past p2 until it leaves the box [0,w]x[0,h]. */
export function extendRay(x1, y1, x2, y2, w, h) {
  const dx = x2 - x1;
  const dy = y2 - y1;
  if (dx === 0 && dy === 0) return { x: x2, y: y2 };
  const ts = [];
  if (dx > 0) ts.push((w - x1) / dx);
  if (dx < 0) ts.push(-x1 / dx);
  if (dy > 0) ts.push((h - y1) / dy);
  if (dy < 0) ts.push(-y1 / dy);
  const ahead = ts.filter(v => v > 0);
  if (ahead.length === 0) return { x: x2, y: y2 };
  const t = Math.max(1, Math.min(...ahead));
  return { x: x1 + dx * t, y: y1 + dy * t };
}
