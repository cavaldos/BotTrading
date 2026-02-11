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

const createPositionMarker = (entryTime, tradeType, orderIndex) => {
  const isLong = tradeType === 'LONG';
  const label = orderIndex != null ? `#${orderIndex} ${tradeType}` : tradeType;
  return {
    time: entryTime,
    position: isLong ? 'belowBar' : 'aboveBar',
    color: isLong ? LONG_COLOR : SHORT_COLOR,
    shape: isLong ? 'arrowUp' : 'arrowDown',
    text: label,
    size: 2,
  };
};

export function applyPositionLabelsToMarkers(markers = [], trades = []) {
  if (!Array.isArray(trades) || trades.length === 0) {
    return Array.isArray(markers) ? markers : [];
  }

  const entryTypeByTime = new Map();
  trades.forEach((trade, index) => {
    const entryTime = Number(trade?.entryTime);
    const tradeType = normalizeTradeType(trade?.type);
    if (!Number.isFinite(entryTime) || !tradeType) {
      return;
    }

    entryTypeByTime.set(entryTime, { tradeType, orderIndex: index + 1 });
  });

  const labeledMarkers = (Array.isArray(markers) ? markers : []).map((marker) => {
    if (!isEntryMarker(marker)) {
      return marker;
    }

    const entry = entryTypeByTime.get(Number(marker?.time));
    if (!entry) {
      return marker;
    }

    return {
      ...marker,
      ...createPositionMarker(Number(marker.time), entry.tradeType, entry.orderIndex),
      size: marker?.size ?? 2,
    };
  });

  const existingEntryTimes = new Set(
    labeledMarkers
      .filter((marker) => isEntryMarker(marker) && entryTypeByTime.has(Number(marker?.time)))
      .map((marker) => Number(marker.time)),
  );

  entryTypeByTime.forEach(({ tradeType, orderIndex }, entryTime) => {
    if (!existingEntryTimes.has(entryTime)) {
      labeledMarkers.push(createPositionMarker(entryTime, tradeType, orderIndex));
    }
  });

  return labeledMarkers;
}
