// Strategy Tester metrics, computed only from the exported trades.
// R = signed move from entry to exit divided by the planned risk (|entry - stop|).
// Like the Python backtest (fee_per_trade_r), a fixed commission in R is taken
// from every closed trade; a trade whose R after commission is <= 0 is a loss.

export function tradeR(trade) {
  const entry = Number(trade.entryPrice);
  const exit = Number(trade.exitPrice);
  const stop = Number(trade.stopLoss);
  if (!Number.isFinite(entry) || !Number.isFinite(exit) || !Number.isFinite(stop)) return null;
  const risk = Math.abs(entry - stop);
  if (risk <= 0) return null;
  const dir = trade.type === 'SHORT' ? -1 : 1;
  return ((exit - entry) * dir) / risk;
}

export function tradePct(trade) {
  const entry = Number(trade.entryPrice);
  const exit = Number(trade.exitPrice);
  if (!Number.isFinite(entry) || !Number.isFinite(exit) || entry === 0) return null;
  const dir = trade.type === 'SHORT' ? -1 : 1;
  return ((exit - entry) * dir / entry) * 100;
}

export function plannedRR(trade) {
  const entry = Number(trade.entryPrice);
  const risk = Math.abs(entry - Number(trade.stopLoss));
  const reward = Math.abs(Number(trade.takeProfit) - entry);
  return risk > 0 && Number.isFinite(reward) ? reward / risk : null;
}

export function isClosed(trade) {
  if (trade.status === 'open' || trade.result === 'Pending') return false;
  return Number.isFinite(Number(trade.exitPrice)) && Number.isFinite(Number(trade.exitTime));
}

const exitTimeOf = (r) => Number(r.exitTimeRaw ?? r.exitTime);

/**
 * Rows used by the tester tables, in chronological order (index = order number).
 * `r` is after commission, `grossR` before it.
 */
export function enrichTrades(trades, commissionR = 0) {
  const fee = Number.isFinite(commissionR) && commissionR > 0 ? commissionR : 0;
  return (trades || []).map((t, i) => {
    const closed = isClosed(t);
    const grossR = closed ? tradeR(t) : null;
    return {
      ...t,
      n: i + 1,
      key: t.id != null ? `id${t.id}` : `n${i + 1}`,
      closed,
      grossR,
      r: Number.isFinite(grossR) ? grossR - fee : null,
      pct: closed ? tradePct(t) : null,
      rr: plannedRR(t),
      duration: closed ? Number(t.exitTimeRaw ?? t.exitTime) - Number(t.entryTimeRaw ?? t.entryTime) : null,
    };
  });
}

function summarize(rows) {
  // Exit order, so drawdown and streaks match the equity curve
  const closed = rows
    .filter(r => r.closed && Number.isFinite(r.r))
    .sort((a, b) => exitTimeOf(a) - exitTimeOf(b));
  const wins = closed.filter(r => r.r > 0);
  const losses = closed.filter(r => r.r <= 0);
  const grossProfit = wins.reduce((s, r) => s + r.r, 0);
  const grossLoss = -losses.reduce((s, r) => s + r.r, 0);
  const netR = grossProfit - grossLoss;
  const grossNetR = closed.reduce((s, r) => s + (r.grossR ?? r.r), 0);
  const netPct = closed.reduce((s, r) => s + (r.pct || 0), 0);

  let peak = 0;
  let equity = 0;
  let maxDD = 0;
  let maxRunUp = 0;
  let trough = 0;
  let streakW = 0;
  let streakL = 0;
  let maxStreakW = 0;
  let maxStreakL = 0;
  closed.forEach(r => {
    equity += r.r;
    if (equity > peak) peak = equity;
    if (equity < trough) trough = equity;
    maxDD = Math.max(maxDD, peak - equity);
    maxRunUp = Math.max(maxRunUp, equity - trough);
    if (r.r > 0) {
      streakW += 1;
      streakL = 0;
    } else {
      streakL += 1;
      streakW = 0;
    }
    maxStreakW = Math.max(maxStreakW, streakW);
    maxStreakL = Math.max(maxStreakL, streakL);
  });

  const avg = (list, key) => (list.length ? list.reduce((s, r) => s + r[key], 0) / list.length : null);
  const durations = closed.map(r => r.duration).filter(Number.isFinite);

  return {
    total: rows.length,
    closed: closed.length,
    open: rows.length - closed.length,
    wins: wins.length,
    losses: losses.length,
    winRate: closed.length ? (wins.length / closed.length) * 100 : null,
    netR,
    grossNetR,
    commissionR: Math.max(0, grossNetR - netR),
    netPct,
    grossProfit,
    grossLoss,
    profitFactor: grossLoss > 0 ? grossProfit / grossLoss : (grossProfit > 0 ? Infinity : null),
    maxDrawdown: maxDD,
    maxRunUp,
    avgR: avg(closed, 'r'),
    avgWin: avg(wins, 'r'),
    avgLoss: avg(losses, 'r'),
    ratioAvgWinLoss: wins.length && losses.length ? avg(wins, 'r') / Math.abs(avg(losses, 'r') || 1) : null,
    largestWin: wins.length ? Math.max(...wins.map(r => r.r)) : null,
    largestLoss: losses.length ? Math.min(...losses.map(r => r.r)) : null,
    maxConsecWins: maxStreakW,
    maxConsecLosses: maxStreakL,
    avgDuration: durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : null,
    avgRR: avg(rows.filter(r => Number.isFinite(r.rr)), 'rr'),
    expectancy: closed.length ? netR / closed.length : null,
  };
}

export function computeStats(rows) {
  return {
    all: summarize(rows),
    long: summarize(rows.filter(r => r.type === 'LONG')),
    short: summarize(rows.filter(r => r.type === 'SHORT')),
  };
}

/** Cumulative R after commission per closed trade, keyed by exit time (strictly increasing for the chart). */
export function equityCurve(rows) {
  const points = [];
  let equity = 0;
  let lastTime = -Infinity;
  rows
    .filter(r => r.closed && Number.isFinite(r.r))
    .slice()
    .sort((a, b) => exitTimeOf(a) - exitTimeOf(b))
    .forEach(r => {
      equity += r.r;
      let time = exitTimeOf(r);
      if (time <= lastTime) time = lastTime + 1;
      lastTime = time;
      points.push({ time, value: Number(equity.toFixed(4)) });
    });
  return points;
}
