import { useEffect, useMemo, useRef, useState } from 'react';
import CombineChart from '@/components/CombineChart';

// Render-only page used by the live bot to screenshot a trade (src/live/core/snapshot.py).
// Data comes from window.__SNAPSHOT_DATA__ (injected by Playwright) or, for manual
// debugging, from /data/snapshots/<id>.json via #/snapshot?id=<id>.
// Sets window.__SNAPSHOT_READY__ = true once the chart is drawn.

const HEADER_HEIGHT = 48;

function priceDigits(value) {
  const abs = Math.abs(value);
  return abs >= 1000 ? 2 : abs >= 1 ? 4 : 6;
}

function formatPrice(value) {
  if (!Number.isFinite(value)) return '-';
  return value.toFixed(priceDigits(value));
}

function formatTimeGmt7(unixSeconds) {
  if (!Number.isFinite(unixSeconds)) return '-';
  const d = new Date((unixSeconds + 7 * 3600) * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} GMT+7`;
}

function snapshotIdFromHash() {
  const query = window.location.hash.split('?')[1] || '';
  return new URLSearchParams(query).get('id');
}

function useSnapshotData() {
  const [state, setState] = useState(() => {
    const injected = window.__SNAPSHOT_DATA__ || null;
    const missing = !injected && !snapshotIdFromHash();
    return {
      data: injected,
      error: missing ? 'No snapshot data (window.__SNAPSHOT_DATA__ or ?id=)' : null,
    };
  });

  useEffect(() => {
    const id = snapshotIdFromHash();
    if (state.data || !id) return;
    fetch(`/data/snapshots/${encodeURIComponent(id)}.json`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => setState({ data, error: null }))
      .catch((err) => setState({ data: null, error: err.message }));
  }, [state.data]);

  return state;
}

function HeaderItem({ label, value, color }) {
  return (
    <span style={{ display: 'inline-flex', gap: 6, alignItems: 'baseline' }}>
      <span style={{ color: '#787b86', fontSize: 13 }}>{label}</span>
      <span style={{ color: color || '#d1d4dc', fontSize: 16, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
        {value}
      </span>
    </span>
  );
}

function SnapshotPage() {
  const { data, error } = useSnapshotData();
  const chartRef = useRef(null);

  const trade = data?.trade;
  const isLong = trade?.type === 'LONG';
  const sideColor = isLong ? '#26a69a' : '#ef5350';

  // Chart trade sits on the signal-TF bar that contains the entry, so markers and boxes align.
  const chartTrades = useMemo(() => {
    if (!trade) return [];
    return [{ ...trade, entryTime: trade.entryBarTime ?? trade.entryTime }];
  }, [trade]);

  const markers = useMemo(() => {
    if (!trade) return [];
    return [{
      time: trade.entryBarTime ?? trade.entryTime,
      position: isLong ? 'belowBar' : 'aboveBar',
      color: sideColor,
      shape: isLong ? 'arrowUp' : 'arrowDown',
      text: isLong ? 'L1' : 'S1',
      size: 2,
    }];
  }, [trade, isLong, sideColor]);

  const indicatorData = useMemo(() => {
    if (!data) return {};
    const indicators = { ...(data.indicators || {}) };
    if (data.zone) {
      indicators.orderBlocks = [...(indicators.orderBlocks || []), data.zone];
    }
    return indicators;
  }, [data]);

  const visibility = useMemo(() => {
    const v = { ...(data?.visibility || {}) };
    if (data?.zone) v.orderBlocks = true;
    return v;
  }, [data]);

  useEffect(() => {
    if (!data || !chartRef.current) return undefined;
    window.__SNAPSHOT_READY__ = false;
    chartRef.current.setViewport(data.view || {});

    let cancelled = false;
    let timer = null;
    // Wait for the primitive layers to paint before signalling the screenshot.
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        timer = setTimeout(() => {
          if (!cancelled) window.__SNAPSHOT_READY__ = true;
        }, 150);
      });
    });
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [data]);

  if (error) {
    return <div style={{ color: '#ef5350', padding: 24, background: '#131722', minHeight: '100vh' }}>{error}</div>;
  }
  if (!data) {
    return <div style={{ color: '#d1d4dc', padding: 24, background: '#131722', minHeight: '100vh' }}>Loading…</div>;
  }

  const meta = data.metadata || {};

  return (
    <div
      id="snapshot-root"
      style={{
        position: 'fixed',
        inset: 0,
        background: '#131722',
        display: 'flex',
        flexDirection: 'column',
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Liberation Sans", Arial, sans-serif',
      }}
    >
      <div
        style={{
          height: HEADER_HEIGHT,
          flex: `0 0 ${HEADER_HEIGHT}px`,
          display: 'flex',
          alignItems: 'center',
          gap: 24,
          padding: '0 16px',
          borderBottom: '1px solid rgba(54, 60, 78, 0.8)',
        }}
      >
        <span style={{ color: '#d1d4dc', fontSize: 18, fontWeight: 700 }}>
          {meta.symbol} · {meta.signalTimeframe}
        </span>
        <span
          style={{
            color: '#131722',
            background: sideColor,
            fontSize: 14,
            fontWeight: 700,
            padding: '3px 10px',
            borderRadius: 4,
          }}
        >
          {trade?.type}
        </span>
        <HeaderItem label="Entry" value={formatPrice(trade?.entryPrice)} />
        <HeaderItem label="SL" value={formatPrice(trade?.stopLoss)} color="#ef5350" />
        <HeaderItem label="TP" value={formatPrice(trade?.takeProfit)} color="#26a69a" />
        <HeaderItem label="RR" value={Number.isFinite(trade?.rr) ? `1:${trade.rr.toFixed(2)}` : '-'} />
        {data.zone && <HeaderItem label="Zone" value={String(data.zone.type || 'OB').replace('_', ' ')} color="#ffc107" />}
        <span style={{ marginLeft: 'auto', color: '#787b86', fontSize: 13 }}>
          {formatTimeGmt7(trade?.entryTime)}
        </span>
      </div>
      <div style={{ flex: 1, minHeight: 0 }}>
        <CombineChart
          ref={chartRef}
          candles={data.candles}
          volume={data.volume}
          markers={markers}
          trades={chartTrades}
          orderBlocks={[]}
          showOrderBlocks={false}
          indicatorData={indicatorData}
          indicatorVisibility={visibility}
          snapshotMode
          pricePrecision={Number.isFinite(trade?.entryPrice) ? priceDigits(trade.entryPrice) : null}
        />
      </div>
    </div>
  );
}

export default SnapshotPage;
