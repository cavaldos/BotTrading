// Timeframe parsing, client-side resampling and candle transforms.

const UNIT_SECONDS = { s: 1, m: 60, h: 3600, d: 86400, D: 86400, w: 604800, W: 604800 };

// Bars are aligned to UTC; weeks start on Monday (unix epoch was a Thursday).
const WEEK_OFFSET = 4 * 86400;

export const STANDARD_TFS = ['1m', '2m', '3m', '5m', '15m', '30m', '45m', '1h', '2h', '4h', '12h', '1D', '1W'];

export function tfToSeconds(tf) {
  const m = /^(\d+)\s*([smhdDwW])$/.exec(String(tf || '').trim());
  if (!m) return null;
  return Number(m[1]) * UNIT_SECONDS[m[2]];
}

export function normalizeTf(tf) {
  const secs = tfToSeconds(tf);
  if (!secs) return tf;
  if (secs % 604800 === 0) return `${secs / 604800}W`;
  if (secs % 86400 === 0) return `${secs / 86400}D`;
  if (secs % 3600 === 0 && secs >= 3600) return `${secs / 3600}h`;
  if (secs % 60 === 0) return `${secs / 60}m`;
  return `${secs}s`;
}

export function tfGroup(tf) {
  const secs = tfToSeconds(tf) || 0;
  if (secs >= 86400) return 'Days';
  if (secs >= 3600) return 'Hours';
  return 'Minutes';
}

/**
 * Timeframes the user can pick for one symbol.
 * natives: [{ tf, id }] files that exist. Larger standard TFs are offered when a
 * native file divides them evenly, and are built by resampling that file.
 */
export function buildTimeframeOptions(natives) {
  const nativeList = (natives || [])
    .map(n => ({ ...n, tf: normalizeTf(n.tf), seconds: tfToSeconds(n.tf) }))
    .filter(n => n.seconds);
  if (nativeList.length === 0) return [];

  const minNative = Math.min(...nativeList.map(n => n.seconds));
  const options = new Map();

  nativeList.forEach(n => {
    options.set(n.tf, { tf: n.tf, seconds: n.seconds, native: true, baseId: n.id, baseTf: n.tf });
  });

  STANDARD_TFS.forEach(tf => {
    const seconds = tfToSeconds(tf);
    if (options.has(tf) || seconds <= minNative) return;
    const bases = nativeList
      .filter(n => seconds % n.seconds === 0)
      .sort((a, b) => b.seconds - a.seconds);
    if (bases.length === 0) return;
    options.set(tf, { tf, seconds, native: false, baseId: bases[0].id, baseTf: bases[0].tf });
  });

  return [...options.values()].sort((a, b) => a.seconds - b.seconds);
}

export function bucketStart(time, seconds) {
  if (seconds % 604800 === 0) {
    return Math.floor((time - WEEK_OFFSET) / seconds) * seconds + WEEK_OFFSET;
  }
  return Math.floor(time / seconds) * seconds;
}

const UP_VOLUME = 'rgba(38, 166, 154, 0.5)';
const DOWN_VOLUME = 'rgba(239, 83, 80, 0.5)';

export function volumeFromCandles(candles, volume) {
  // Recolour the volume bars to the candle direction with TradingView-like opacity
  const out = new Array(volume.length);
  for (let i = 0; i < volume.length; i += 1) {
    const c = candles[i];
    const up = c ? c.close >= c.open : true;
    out[i] = { time: volume[i].time, value: volume[i].value, color: up ? UP_VOLUME : DOWN_VOLUME };
  }
  return out;
}

/** Aggregate candles (and matching volume) into larger bars. */
export function resampleCandles(candles, volume, seconds) {
  const outCandles = [];
  const outVolume = [];
  const volByTime = new Map();
  if (volume?.length) {
    for (let i = 0; i < volume.length; i += 1) volByTime.set(volume[i].time, volume[i].value);
  }

  let cur = null;
  let curVol = 0;
  for (let i = 0; i < candles.length; i += 1) {
    const c = candles[i];
    const t = bucketStart(c.time, seconds);
    if (!cur || cur.time !== t) {
      if (cur) {
        outCandles.push(cur);
        outVolume.push({ time: cur.time, value: curVol, color: cur.close >= cur.open ? UP_VOLUME : DOWN_VOLUME });
      }
      cur = { time: t, open: c.open, high: c.high, low: c.low, close: c.close };
      curVol = 0;
    } else {
      if (c.high > cur.high) cur.high = c.high;
      if (c.low < cur.low) cur.low = c.low;
      cur.close = c.close;
    }
    curVol += volByTime.get(c.time) || 0;
  }
  if (cur) {
    outCandles.push(cur);
    outVolume.push({ time: cur.time, value: curVol, color: cur.close >= cur.open ? UP_VOLUME : DOWN_VOLUME });
  }
  return { candles: outCandles, volume: outVolume };
}

/** Move trade and marker times onto the resampled bars so markers line up. */
export function resampleTrades(trades, markers, seconds) {
  const snap = (t) => (Number.isFinite(t) ? bucketStart(t, seconds) : t);
  return {
    trades: (trades || []).map(t => ({
      ...t,
      entryTime: snap(t.entryTime),
      exitTime: snap(t.exitTime),
    })),
    markers: (markers || []).map(m => ({ ...m, time: snap(m.time) })),
  };
}

export function heikinAshi(candles) {
  const out = new Array(candles.length);
  let prevOpen = 0;
  let prevClose = 0;
  for (let i = 0; i < candles.length; i += 1) {
    const c = candles[i];
    const close = (c.open + c.high + c.low + c.close) / 4;
    const open = i === 0 ? (c.open + c.close) / 2 : (prevOpen + prevClose) / 2;
    out[i] = {
      time: c.time,
      open,
      high: Math.max(c.high, open, close),
      low: Math.min(c.low, open, close),
      close,
    };
    prevOpen = open;
    prevClose = close;
  }
  return out;
}

export function barSeconds(candles) {
  if (!candles || candles.length < 2) return 60;
  // Median of the first gaps avoids being fooled by one missing bar
  const gaps = [];
  for (let i = 1; i < Math.min(candles.length, 50); i += 1) gaps.push(candles[i].time - candles[i - 1].time);
  gaps.sort((a, b) => a - b);
  return Math.max(1, gaps[Math.floor(gaps.length / 2)]);
}
