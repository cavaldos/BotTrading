const LONG_COLOR = '#26a69a';
const SHORT_COLOR = '#ef5350';

const isEntryMarker = (marker) => marker?.shape === 'arrowUp' || marker?.shape === 'arrowDown';

const normalizeTradeType = (type) => {
  const normalized = String(type ?? '').toUpperCase();
  if (normalized === 'LONG' || normalized === 'SHORT') {
    return normalized;
  }
  return null;
};

const createPositionMarker = (entryTime, tradeType, orderIndex, tag) => {
  const isLong = tradeType === 'LONG';
  const base = orderIndex != null ? `#${orderIndex} ${tradeType}` : tradeType;
  return {
    time: entryTime,
    position: isLong ? 'belowBar' : 'aboveBar',
    color: isLong ? LONG_COLOR : SHORT_COLOR,
    shape: isLong ? 'arrowUp' : 'arrowDown',
    text: tag ? `${base} ${tag}` : base,
    size: 2,
  };
};

/**
 * Label each entry arrow with its trade ("#3 LONG", plus `trade.tag` such as the
 * signal timeframe). Several trades may enter on the same bar: arrows at one time
 * take the labels of the trades at that time in order; missing arrows are added.
 */
export function applyPositionLabelsToMarkers(markers = [], trades = []) {
  if (!Array.isArray(trades) || trades.length === 0) {
    return Array.isArray(markers) ? markers : [];
  }

  const entriesByTime = new Map();
  trades.forEach((trade, index) => {
    const entryTime = Number(trade?.entryTime);
    const tradeType = normalizeTradeType(trade?.type);
    if (!Number.isFinite(entryTime) || !tradeType) {
      return;
    }
    const list = entriesByTime.get(entryTime) || [];
    list.push({ tradeType, orderIndex: trade?.n ?? index + 1, tag: trade?.tag || '' });
    entriesByTime.set(entryTime, list);
  });

  const used = new Map();
  const labeledMarkers = (Array.isArray(markers) ? markers : []).map((marker) => {
    if (!isEntryMarker(marker)) {
      return marker;
    }
    const time = Number(marker?.time);
    const list = entriesByTime.get(time);
    if (!list) {
      return marker;
    }
    const k = used.get(time) || 0;
    used.set(time, k + 1);
    const entry = list[Math.min(k, list.length - 1)];
    return {
      ...marker,
      ...createPositionMarker(time, entry.tradeType, entry.orderIndex, entry.tag),
      size: marker?.size ?? 2,
    };
  });

  entriesByTime.forEach((list, entryTime) => {
    list.slice(used.get(entryTime) || 0).forEach(({ tradeType, orderIndex, tag }) => {
      labeledMarkers.push(createPositionMarker(entryTime, tradeType, orderIndex, tag));
    });
  });

  return labeledMarkers;
}
