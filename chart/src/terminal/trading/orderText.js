// Labels for orders followed on the exchange (src/live/core/order_tracker.py).
import { formatPrice } from '../lib/format';

export const OPEN_ORDER_STATUSES = ['WAITING', 'PARTIAL'];

export const isOpenOrder = (order) => OPEN_ORDER_STATUSES.includes(order?.status);

export const sideLabel = (side) => (String(side).toUpperCase() === 'LONG' ? 'Long' : 'Short');

export const typeLabel = (orderType) => (orderType === 'limit' ? 'Limit' : 'Market');

export const sourceLabel = (order) => (order.source === 'signal' ? `Bot ${order.timeframe || ''}`.trim() : 'Web');

/** Changes worth telling the user about (order SSE event `change`). */
export const ORDER_CHANGES = {
  // Also announced when the signal was approved on Telegram, or the bot trades without approval
  placed: { title: 'Order placed', tone: 'accent' },
  filled: { title: 'Order filled', tone: 'up' },
  partial: { title: 'Order partly filled', tone: 'warn' },
  not_filled: { title: 'Order not filled', tone: 'down' },
  cancelled: { title: 'Order cancelled', tone: 'muted' },
  unknown: { title: 'Order not confirmed', tone: 'warn' },
};

export function countdown(ms) {
  const s = Math.max(0, Math.ceil(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
}

/** One line on the state of an order. `now` (ms, server clock) adds the time left of a waiting limit order. */
export function orderDetail(order, now) {
  const base = order.symbol.replace(/USDT$/, '');
  if (order.filled > 0) {
    const at = formatPrice(order.avgPrice ?? order.price);
    if (order.status === 'PARTIAL') return `${order.filled} of ${order.quantity} ${base} filled at ${at}`;
    return `Filled at ${at} · ${order.filled} ${base}`;
  }
  if (isOpenOrder(order)) {
    if (order.orderType !== 'limit') return 'Waiting for the exchange…';
    const left = order.expiresAt > 0 && Number.isFinite(now) ? ` · cancels in ${countdown(order.expiresAt * 1000 - now)}` : '';
    return `Limit ${formatPrice(order.price)}${left}`;
  }
  return order.reason || '';
}

/** Tooltip with the levels and the full reason. */
export function orderTitle(order) {
  const parts = [
    `${sourceLabel(order)} · ${typeLabel(order.orderType)} ${formatPrice(order.price)}`,
    `Stop ${formatPrice(order.stopLoss)} · Target ${formatPrice(order.takeProfit)}`,
  ];
  if (order.reason) parts.push(order.reason);
  return parts.join('\n');
}

/**
 * One readable line from a bot / exchange result text, e.g. the multi-line
 * "BYBIT / ❌ THẤT BẠI / └─ Lỗi: bybit {"retCode":10005,"retMsg":"Permission denied…"}"
 * becomes "Permission denied, please check your API key permissions. (Bybit 10005)".
 */
export function shortReason(text) {
  const raw = String(text || '');
  const msg = /"retMsg"\s*:\s*"([^"]+)"/.exec(raw);
  if (msg) {
    const code = /"retCode"\s*:\s*(\d+)/.exec(raw);
    return `${msg[1]}${code ? ` (Bybit ${code[1]})` : ''}`;
  }
  return raw
    .split('\n')
    .map(l => l.replace(/^(?:\s|└|├|─|❌|⚠️|✅|🟡)+/u, '').trim())
    .filter(l => l && !/^(BYBIT|THẤT BẠI|KHÔNG ĐẶT LỆNH)$/i.test(l))
    .map(l => l.split('Lỗi:').pop().trim())
    .join(' · ')
    .slice(0, 300);
}
