export function priceDigits(value) {
  const abs = Math.abs(Number(value) || 0);
  if (abs >= 1000) return 2;
  if (abs >= 10) return 3;
  if (abs >= 1) return 4;
  if (abs >= 0.01) return 5;
  return 7;
}

export function formatPrice(value, digits) {
  if (!Number.isFinite(value)) return '—';
  return value.toFixed(digits ?? priceDigits(value));
}

export function formatNumber(value, digits = 2) {
  if (!Number.isFinite(value)) return '—';
  return value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function formatSigned(value, digits = 2, suffix = '') {
  if (!Number.isFinite(value)) return '—';
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${Math.abs(value).toFixed(digits)}${suffix}`;
}

export function formatVolume(value) {
  if (!Number.isFinite(value)) return '—';
  const abs = Math.abs(value);
  if (abs >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(value / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${(value / 1e3).toFixed(2)}K`;
  return value.toFixed(abs >= 100 ? 0 : 2);
}

const pad = (n) => String(n).padStart(2, '0');

// Chart times are unix seconds shown as UTC (lightweight-charts default)
export function formatDateTime(unixSeconds) {
  if (!Number.isFinite(unixSeconds)) return '—';
  const d = new Date(unixSeconds * 1000);
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

export function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return '—';
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ${m % 60 ? `${m % 60}m` : ''}`.trim();
  return `${Math.floor(h / 24)}d ${h % 24 ? `${h % 24}h` : ''}`.trim();
}

export function formatAgo(date) {
  if (!date) return 'never';
  const s = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

// Compact form for narrow lists: MM-DD HH:mm (UTC)
export function formatShortDateTime(unixSeconds) {
  if (!Number.isFinite(unixSeconds)) return '—';
  const d = new Date(unixSeconds * 1000);
  return `${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}
