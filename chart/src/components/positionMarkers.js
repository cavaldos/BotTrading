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

const createPositionMarker = (entryTime, tradeType) => {
  const isLong = tradeType === 'LONG';
  return {
    time: entryTime,
    position: isLong ? 'belowBar' : 'aboveBar',
    color: isLong ? LONG_COLOR : SHORT_COLOR,
    shape: isLong ? 'arrowUp' : 'arrowDown',
    text: tradeType,
    size: 2,
  };
};

export function applyPositionLabelsToMarkers(markers = [], trades = []) {
  if (!Array.isArray(trades) || trades.length === 0) {
    return Array.isArray(markers) ? markers : [];
  }

  const entryTypeByTime = new Map();
  trades.forEach((trade) => {
    const entryTime = Number(trade?.entryTime);
    const tradeType = normalizeTradeType(trade?.type);
    if (!Number.isFinite(entryTime) || !tradeType) {
      return;
    }

    entryTypeByTime.set(entryTime, tradeType);
  });

  const labeledMarkers = (Array.isArray(markers) ? markers : []).map((marker) => {
    if (!isEntryMarker(marker)) {
      return marker;
    }

    const tradeType = entryTypeByTime.get(Number(marker?.time));
    if (!tradeType) {
      return marker;
    }

    return {
      ...marker,
      ...createPositionMarker(Number(marker.time), tradeType),
      size: marker?.size ?? 2,
    };
  });

  const existingEntryTimes = new Set(
    labeledMarkers
      .filter((marker) => isEntryMarker(marker) && entryTypeByTime.has(Number(marker?.time)))
      .map((marker) => Number(marker.time)),
  );

  entryTypeByTime.forEach((tradeType, entryTime) => {
    if (!existingEntryTimes.has(entryTime)) {
      labeledMarkers.push(createPositionMarker(entryTime, tradeType));
    }
  });

  return labeledMarkers;
}
