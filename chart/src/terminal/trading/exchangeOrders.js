// Exchange account orders (GET /api/exchange/orders): labels, and what is drawn on the chart.
import { formatNumber, formatPrice } from '../lib/format';

// sessionStorage key: an order picked on the Orders page, shown by the chart once it opens
export const PENDING_FOCUS_KEY = 'tvc:pendingExchangeFocus';

export const KINDS = {
  entry: { label: 'Entry', short: 'Entry', color: '#5b8bff' },
  stop_loss: { label: 'Stop loss', short: 'SL', color: '#f23645' },
  take_profit: { label: 'Take profit', short: 'TP', color: '#089981' },
  close: { label: 'Close', short: 'Close', color: '#ff9800' },
};

export const STATUSES = {
  open: { label: 'Open', tone: 'accent' },
  partial: { label: 'Partly filled', tone: 'warn' },
  filled: { label: 'Filled', tone: 'up' },
  triggered: { label: 'Triggered', tone: 'warn' },
  canceled: { label: 'Cancelled', tone: 'muted' },
  rejected: { label: 'Rejected', tone: 'down' },
  expired: { label: 'Expired', tone: 'muted' },
};

/** Order size without trailing zeros: 0.021, 2.72, 21. */
export function qty(value) {
  return Number.isFinite(value) ? String(Number(value.toPrecision(8))) : '—';
}

const BUY = '#089981';
const SELL = '#f23645';

/** Status shown for an order: working orders with a fill are "partly filled". */
export function statusOf(o) {
  return o.status === 'open' && o.filled > 0 ? 'partial' : o.status;
}

/** The price the order works at: trigger for stop / target orders, else the limit price. */
export function levelOf(o) {
  return o.triggerPrice ?? o.price ?? null;
}

/** When the order filled (or was last changed). */
export function eventTime(o) {
  return o.filled > 0 ? (o.updated || o.time) : o.time;
}

export function hasFill(o) {
  return o.filled > 0 && Number.isFinite(o.average);
}

/** Entries say which position they open, exits what they close. */
export function kindText(o) {
  if (o.kind === 'entry') return o.side === 'buy' ? 'Open long' : 'Open short';
  const of = o.positionSide === 'SHORT' ? 'short' : 'long';
  return `${KINDS[o.kind]?.label || o.kind} ${of}`;
}

export function typeText(o) {
  const t = String(o.type || '').replace(/_/g, ' ').replace(/-/g, ' ').trim();
  if (o.kind === 'stop_loss' || o.kind === 'take_profit') return t.includes('limit') ? 'limit' : 'market';
  return t || 'order';
}

export function orderDetail(o) {
  const parts = [];
  const level = levelOf(o);
  if (hasFill(o)) parts.push(`filled ${formatPrice(o.average)}`);
  if (statusOf(o) !== 'filled' && Number.isFinite(level)) {
    parts.push(`${o.triggerPrice != null ? 'trigger' : 'limit'} ${formatPrice(level)}`);
  }
  const size = o.filled > 0 && o.filled < o.amount ? `${qty(o.filled)}/${qty(o.amount)}` : qty(o.amount);
  if (o.amount) parts.push(`qty ${size}`);
  if (o.kind === 'entry' && (o.stopLoss || o.takeProfit)) {
    parts.push([o.stopLoss && `SL ${formatPrice(o.stopLoss)}`, o.takeProfit && `TP ${formatPrice(o.takeProfit)}`].filter(Boolean).join(' '));
  }
  return parts.join(' · ');
}

/** Price lines on the chart: open positions and the orders still working. */
export function exchangeLines(data, symbol, focused) {
  if (!data || !symbol) return [];
  const lines = [];
  data.positions.filter(p => p.symbol === symbol).forEach(p => {
    const pnl = Number.isFinite(p.unrealizedPnl) ? ` ${p.unrealizedPnl >= 0 ? '+' : '−'}${formatNumber(Math.abs(p.unrealizedPnl))}` : '';
    lines.push({
      price: p.entryPrice,
      color: p.side === 'LONG' ? BUY : SELL,
      title: `${p.side === 'LONG' ? 'Long' : 'Short'} ${qty(p.size)}${pnl}`,
      dashed: false,
      width: 2,
    });
  });
  data.orders
    .filter(o => o.symbol === symbol && o.status === 'open')
    .forEach(o => {
      const k = KINDS[o.kind] || KINDS.entry;
      const label = o.kind === 'entry' ? `${o.side === 'buy' ? 'Buy' : 'Sell'} ${typeText(o)}` : k.short;
      lines.push({ price: levelOf(o), color: k.color, title: `${label} ${qty(o.amount)}`, style: 'dotted' });
    });
  // Order picked in the list that is no longer working: show where it was
  if (focused && focused.symbol === symbol && focused.status !== 'open') {
    const price = hasFill(focused) ? focused.average : levelOf(focused);
    lines.push({ price, color: '#ff9800', title: `${kindText(focused)} · ${STATUSES[statusOf(focused)]?.label || focused.status}`, dashed: false });
  }
  return lines.filter(l => Number.isFinite(l.price));
}

/** Arrows at the bars where orders filled (times are snapped to bars by the chart engine). */
export function exchangeMarkers(data, symbol) {
  if (!data || !symbol) return [];
  return data.orders
    .filter(o => o.symbol === symbol && hasFill(o) && Number.isFinite(eventTime(o)))
    .map(o => {
      const buy = o.side === 'buy';
      const label = o.kind === 'entry' ? (buy ? 'Long' : 'Short') : KINDS[o.kind]?.short || 'Fill';
      return {
        time: eventTime(o),
        position: buy ? 'belowBar' : 'aboveBar',
        shape: buy ? 'arrowUp' : 'arrowDown',
        color: buy ? BUY : SELL,
        size: 1,
        text: `${label} ${formatPrice(o.average)}`,
      };
    });
}

// ---- Exchange source: account trades drawn like backtest trades ---------------------------

const UP = '#089981';
const DOWN = '#f23645';

/** Index of the bar that contains `t` (bars start at candles[i].time), or -1 outside the data. */
function barIndex(candles, t) {
  const n = candles.length;
  if (!n || !Number.isFinite(t) || t < candles[0].time) return -1;
  const bar = n > 1 ? candles[n - 1].time - candles[n - 2].time : 60;
  if (t >= candles[n - 1].time + bar) return -1;
  let lo = 0;
  let hi = n - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (candles[mid].time <= t) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

/**
 * GET /api/exchange/trades trades -> chart file trades + markers: times snapped to the
 * bars on screen (raw times kept as entryTimeRaw / exitTimeRaw), entry arrows and an
 * exit dot with how the trade ended ("TP 77606.9", "SL ...", "Close ...").
 */
export function exchangeChartTrades(trades, candles) {
  if (!Array.isArray(trades) || !candles?.length) return { trades: [], markers: [] };
  const last = candles[candles.length - 1].time;
  const snap = (t) => {
    const i = barIndex(candles, t);
    return i >= 0 ? candles[i].time : t;
  };
  const out = [];
  const markers = [];
  trades.forEach(t => {
    // Open position whose entry is older than the account history: start it at the last bar
    const entryRaw = Number.isFinite(t.entryTime) ? t.entryTime : last;
    const closed = t.status === 'closed' && Number.isFinite(t.exitTime);
    const trade = {
      ...t,
      entryTime: snap(entryRaw),
      entryTimeRaw: entryRaw,
      exitTime: closed ? snap(t.exitTime) : null,
      exitTimeRaw: closed ? t.exitTime : null,
      exitPrice: closed ? t.exitPrice : null,
    };
    out.push(trade);
    const long = t.type === 'LONG';
    markers.push({
      time: trade.entryTime,
      position: long ? 'belowBar' : 'aboveBar',
      color: long ? UP : DOWN,
      shape: long ? 'arrowUp' : 'arrowDown',
      text: long ? 'LONG' : 'SHORT', // relabelled "#n LONG" by the chart
      size: 2,
    });
    if (closed) {
      const win = t.result === 'Win';
      markers.push({
        time: trade.exitTime,
        position: win === long ? 'aboveBar' : 'belowBar',
        color: win ? UP : DOWN,
        shape: 'circle',
        text: `${win ? 'W' : 'X'} ${t.exitReason || 'Exit'} ${formatPrice(t.exitPrice)}`,
        size: 1,
      });
    }
  });
  return { trades: out, markers };
}

/** One line for a trade in lists and tooltips: "+6.23 USDT · +1.69R · TP". */
export function tradeResultText(t) {
  const parts = [];
  if (Number.isFinite(t.pnl)) parts.push(`${t.pnl >= 0 ? '+' : '−'}${formatNumber(Math.abs(t.pnl))}`);
  if (Number.isFinite(t.pnlR)) parts.push(`${t.pnlR >= 0 ? '+' : '−'}${Math.abs(t.pnlR).toFixed(2)}R`);
  if (t.exitReason) parts.push(t.exitReason);
  return parts.join(' · ');
}
