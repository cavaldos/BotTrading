/**
 * Trade markers drawn through PrimitiveLayer instead of createSeriesMarkers.
 * The built-in markers plugin calls series.data() (a full copy of every bar)
 * on each frame, which costs ~100 ms per frame with 500k candles. Here each
 * marker resolves its bar once and only visible ones are drawn.
 */
import { drawOutlinedText } from '../../plugins/canvasText';

const FONT = '-apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, Ubuntu, sans-serif';

class MarkerPrimitive {
  constructor(marker, bar, stackIndex) {
    this._m = marker;
    this._bar = bar;
    this._stack = stackIndex;
  }

  getTimeSpan() {
    return [this._m.time, this._m.time];
  }

  getZOrder() {
    return 'top';
  }

  drawShape(ctx, x, _x2, frame) {
    const m = this._m;
    const above = m.position === 'aboveBar';
    const anchorPrice = above ? this._bar.high : this._bar.low;
    const anchorY = frame.series.priceToCoordinate(anchorPrice);
    if (anchorY === null) return;
    const size = m.size === 1 ? 4 : 6;
    const dir = above ? -1 : 1;
    const gap = 6 + this._stack * (size * 2 + 16);
    const tipY = anchorY + dir * gap;

    ctx.fillStyle = m.color;
    ctx.beginPath();
    if (m.shape === 'arrowUp' || m.shape === 'arrowDown') {
      // Head at tipY pointing at the bar, shaft away from it
      const head = tipY;
      const s = m.shape === 'arrowUp' ? 1 : -1;
      ctx.moveTo(x, head);
      ctx.lineTo(x - size, head + s * size);
      ctx.lineTo(x - size / 2.4, head + s * size);
      ctx.lineTo(x - size / 2.4, head + s * size * 2);
      ctx.lineTo(x + size / 2.4, head + s * size * 2);
      ctx.lineTo(x + size / 2.4, head + s * size);
      ctx.lineTo(x + size, head + s * size);
      ctx.closePath();
      ctx.fill();
    } else if (m.shape === 'square') {
      ctx.fillRect(x - size / 2, tipY + (dir > 0 ? 0 : -size), size, size);
    } else {
      ctx.arc(x, tipY + dir * (size / 2), size / 2 + 0.5, 0, Math.PI * 2);
      ctx.fill();
    }

    if (m.text && frame.showLabels) {
      const extent = m.shape === 'arrowUp' || m.shape === 'arrowDown' ? size * 2 : size;
      ctx.font = `${m.size === 1 ? 10 : 12}px ${FONT}`;
      ctx.textAlign = 'center';
      ctx.textBaseline = dir > 0 ? 'top' : 'bottom';
      drawOutlinedText(ctx, m.text, x, tipY + dir * (extent + 3), m.color);
    }
  }
}

/** Build marker primitives; markers whose time has no bar are skipped. */
export function createMarkerPrimitives(markers, candles, times) {
  if (!markers?.length || !candles?.length) return [];
  const indexOf = (t) => {
    let lo = 0;
    let hi = times.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (times[mid] === t) return mid;
      if (times[mid] < t) lo = mid + 1;
      else hi = mid - 1;
    }
    return -1;
  };
  const stacks = new Map();
  const out = [];
  markers.forEach(m => {
    const i = indexOf(Number(m.time));
    if (i < 0) return;
    const key = `${m.time}|${m.position}`;
    const stack = stacks.get(key) || 0;
    stacks.set(key, stack + 1);
    out.push(new MarkerPrimitive(m, candles[i], stack));
  });
  return out;
}
