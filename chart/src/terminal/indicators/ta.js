// Technical indicator math. Inputs/outputs are Float64Array aligned to the
// candle array; values that are not defined yet are NaN.

export function sourceValues(candles, source = 'close') {
  const n = candles.length;
  const out = new Float64Array(n);
  for (let i = 0; i < n; i += 1) {
    const c = candles[i];
    switch (source) {
      case 'open': out[i] = c.open; break;
      case 'high': out[i] = c.high; break;
      case 'low': out[i] = c.low; break;
      case 'hl2': out[i] = (c.high + c.low) / 2; break;
      case 'hlc3': out[i] = (c.high + c.low + c.close) / 3; break;
      case 'ohlc4': out[i] = (c.open + c.high + c.low + c.close) / 4; break;
      default: out[i] = c.close;
    }
  }
  return out;
}

function nanArray(n) {
  const out = new Float64Array(n);
  out.fill(NaN);
  return out;
}

// First index where the input is a number (inputs can start with NaN)
function firstValid(values) {
  for (let i = 0; i < values.length; i += 1) if (!Number.isNaN(values[i])) return i;
  return values.length;
}

export function sma(values, length) {
  const n = values.length;
  const out = nanArray(n);
  const start = firstValid(values);
  let sum = 0;
  for (let i = start; i < n; i += 1) {
    sum += values[i];
    if (i - start >= length) sum -= values[i - length];
    if (i - start >= length - 1) out[i] = sum / length;
  }
  return out;
}

export function ema(values, length) {
  const n = values.length;
  const out = nanArray(n);
  const start = firstValid(values);
  if (n - start < length) return out;
  const k = 2 / (length + 1);
  let seed = 0;
  for (let i = start; i < start + length; i += 1) seed += values[i];
  let prev = seed / length;
  out[start + length - 1] = prev;
  for (let i = start + length; i < n; i += 1) {
    prev = values[i] * k + prev * (1 - k);
    out[i] = prev;
  }
  return out;
}

// Wilder's smoothing (RMA), used by RSI / ATR / ADX
export function rma(values, length) {
  const n = values.length;
  const out = nanArray(n);
  const start = firstValid(values);
  if (n - start < length) return out;
  let seed = 0;
  for (let i = start; i < start + length; i += 1) seed += values[i];
  let prev = seed / length;
  out[start + length - 1] = prev;
  for (let i = start + length; i < n; i += 1) {
    prev = (prev * (length - 1) + values[i]) / length;
    out[i] = prev;
  }
  return out;
}

export function wma(values, length) {
  const n = values.length;
  const out = nanArray(n);
  const start = firstValid(values);
  const denom = (length * (length + 1)) / 2;
  let sum = 0;
  let weighted = 0;
  for (let i = start; i < n; i += 1) {
    const idx = i - start;
    if (idx < length) {
      sum += values[i];
      weighted += values[i] * (idx + 1);
      if (idx === length - 1) out[i] = weighted / denom;
    } else {
      // Slide: every weight drops by one, the new value enters with weight `length`
      weighted = weighted - sum + values[i] * length;
      sum = sum + values[i] - values[i - length];
      out[i] = weighted / denom;
    }
  }
  return out;
}

export function stdev(values, length) {
  const n = values.length;
  const out = nanArray(n);
  const start = firstValid(values);
  let sum = 0;
  let sumSq = 0;
  for (let i = start; i < n; i += 1) {
    sum += values[i];
    sumSq += values[i] * values[i];
    if (i - start >= length) {
      sum -= values[i - length];
      sumSq -= values[i - length] * values[i - length];
    }
    if (i - start >= length - 1) {
      const mean = sum / length;
      out[i] = Math.sqrt(Math.max(0, sumSq / length - mean * mean));
    }
  }
  return out;
}

export function movingAverage(type, values, length) {
  switch (type) {
    case 'EMA': return ema(values, length);
    case 'WMA': return wma(values, length);
    case 'RMA': return rma(values, length);
    default: return sma(values, length);
  }
}

export function bollinger(values, length, mult) {
  const basis = sma(values, length);
  const dev = stdev(values, length);
  const n = values.length;
  const upper = nanArray(n);
  const lower = nanArray(n);
  for (let i = 0; i < n; i += 1) {
    if (Number.isNaN(basis[i])) continue;
    upper[i] = basis[i] + mult * dev[i];
    lower[i] = basis[i] - mult * dev[i];
  }
  return { basis, upper, lower };
}

/** Session VWAP, reset at each UTC day (or week) boundary. */
export function vwap(candles, volumes, anchor = 'Session') {
  const n = candles.length;
  const out = nanArray(n);
  const period = anchor === 'Week' ? 604800 : 86400;
  const offset = anchor === 'Week' ? 4 * 86400 : 0;
  let session = null;
  let pv = 0;
  let vol = 0;
  for (let i = 0; i < n; i += 1) {
    const c = candles[i];
    const s = Math.floor((c.time - offset) / period);
    if (s !== session) {
      session = s;
      pv = 0;
      vol = 0;
    }
    const v = volumes[i] || 0;
    const tp = (c.high + c.low + c.close) / 3;
    pv += tp * v;
    vol += v;
    out[i] = vol > 0 ? pv / vol : tp;
  }
  return out;
}

export function rsi(values, length) {
  const n = values.length;
  const gains = new Float64Array(n);
  const losses = new Float64Array(n);
  gains[0] = NaN;
  losses[0] = NaN;
  for (let i = 1; i < n; i += 1) {
    const d = values[i] - values[i - 1];
    gains[i] = d > 0 ? d : 0;
    losses[i] = d < 0 ? -d : 0;
  }
  const avgGain = rma(gains, length);
  const avgLoss = rma(losses, length);
  const out = nanArray(n);
  for (let i = 0; i < n; i += 1) {
    if (Number.isNaN(avgGain[i])) continue;
    out[i] = avgLoss[i] === 0 ? 100 : 100 - 100 / (1 + avgGain[i] / avgLoss[i]);
  }
  return out;
}

export function macd(values, fast, slow, signalLength) {
  const fastMa = ema(values, fast);
  const slowMa = ema(values, slow);
  const n = values.length;
  const line = nanArray(n);
  for (let i = 0; i < n; i += 1) line[i] = fastMa[i] - slowMa[i];
  const signal = ema(line, signalLength);
  const hist = nanArray(n);
  for (let i = 0; i < n; i += 1) hist[i] = line[i] - signal[i];
  return { macd: line, signal, hist };
}

export function stochastic(candles, kLength, kSmooth, dSmooth) {
  const n = candles.length;
  const raw = nanArray(n);
  // Monotonic deques keep the rolling high/low O(n)
  const maxQ = [];
  const minQ = [];
  for (let i = 0; i < n; i += 1) {
    while (maxQ.length && candles[maxQ[maxQ.length - 1]].high <= candles[i].high) maxQ.pop();
    maxQ.push(i);
    while (minQ.length && candles[minQ[minQ.length - 1]].low >= candles[i].low) minQ.pop();
    minQ.push(i);
    if (maxQ[0] <= i - kLength) maxQ.shift();
    if (minQ[0] <= i - kLength) minQ.shift();
    if (i >= kLength - 1) {
      const hh = candles[maxQ[0]].high;
      const ll = candles[minQ[0]].low;
      raw[i] = hh === ll ? 50 : ((candles[i].close - ll) / (hh - ll)) * 100;
    }
  }
  const k = sma(raw, kSmooth);
  const d = sma(k, dSmooth);
  return { k, d };
}

export function trueRange(candles) {
  const n = candles.length;
  const out = new Float64Array(n);
  for (let i = 0; i < n; i += 1) {
    const c = candles[i];
    if (i === 0) {
      out[i] = c.high - c.low;
    } else {
      const pc = candles[i - 1].close;
      out[i] = Math.max(c.high - c.low, Math.abs(c.high - pc), Math.abs(c.low - pc));
    }
  }
  return out;
}

export function atr(candles, length) {
  return rma(trueRange(candles), length);
}

export function adx(candles, diLength, adxSmoothing) {
  const n = candles.length;
  const plusDM = new Float64Array(n);
  const minusDM = new Float64Array(n);
  for (let i = 1; i < n; i += 1) {
    const up = candles[i].high - candles[i - 1].high;
    const down = candles[i - 1].low - candles[i].low;
    plusDM[i] = up > down && up > 0 ? up : 0;
    minusDM[i] = down > up && down > 0 ? down : 0;
  }
  const tr = rma(trueRange(candles), diLength);
  const plus = rma(plusDM, diLength);
  const minus = rma(minusDM, diLength);
  const plusDI = nanArray(n);
  const minusDI = nanArray(n);
  const dx = nanArray(n);
  for (let i = 0; i < n; i += 1) {
    if (Number.isNaN(tr[i]) || tr[i] === 0) continue;
    plusDI[i] = (100 * plus[i]) / tr[i];
    minusDI[i] = (100 * minus[i]) / tr[i];
    const sum = plusDI[i] + minusDI[i];
    dx[i] = sum === 0 ? 0 : (100 * Math.abs(plusDI[i] - minusDI[i])) / sum;
  }
  return { adx: rma(dx, adxSmoothing), plusDI, minusDI };
}
