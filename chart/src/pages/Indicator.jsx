import { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import IndicatorChart from '@/components/IndicatorChart';
import IndicatorToggle from '@/components/IndicatorToggle';
import { useIndicatorSymbols, useIndicatorData } from '@/hooks/useIndicatorData';

// Default visibility: show structure indicators, hide noisy ones
const DEFAULT_VISIBILITY = {
  bos: true,
  choch: true,
  orderBlocks: true,
  fvg: false,
  eqhEql: false,
  imbalance: false,
  breakerBlocks: true,
  mitigationBlocks: false,
  retracement: false,
};

function IndicatorPage() {
  // Load available indicator symbols
  const { symbols, loading: symbolsLoading } = useIndicatorSymbols();

  // Selected symbol state
  const [selectedSymbol, setSelectedSymbol] = useState(null);

  // Indicator visibility toggles
  const [visibility, setVisibility] = useState(DEFAULT_VISIBILITY);

  const chartRef = useRef(null);

  // Auto-select first symbol when loaded
  useEffect(() => {
    if (symbols.length > 0 && !selectedSymbol) {
      setSelectedSymbol(symbols[0].id);
    }
  }, [symbols, selectedSymbol]);

  // Load indicator data for selected symbol
  const { data, loading: dataLoading, error } = useIndicatorData(selectedSymbol);

  // Get indicator counts from the selected symbol metadata
  const indicatorCounts = useMemo(() => {
    const sym = symbols.find(s => s.id === selectedSymbol);
    return sym?.indicators || {};
  }, [symbols, selectedSymbol]);

  // Toggle an indicator's visibility
  const handleToggle = useCallback((key) => {
    setVisibility(prev => ({
      ...prev,
      [key]: !prev[key],
    }));
  }, []);

  // Handle symbol change
  const handleSymbolChange = (e) => {
    setSelectedSymbol(e.target.value);
  };

  // Combined loading state
  const loading = symbolsLoading || dataLoading;

  // Count total visible indicators
  const totalIndicators = useMemo(() => {
    if (!indicatorCounts) return 0;
    return Object.values(indicatorCounts).reduce((sum, n) => sum + n, 0);
  }, [indicatorCounts]);

  // Loading state
  if (loading && !data) {
    return (
      <div className="app">
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100%',
          color: '#d1d4dc',
          flexDirection: 'column',
          gap: '16px',
        }}>
          <div style={{
            width: '40px',
            height: '40px',
            border: '3px solid rgba(41, 98, 255, 0.3)',
            borderTop: '3px solid #2962ff',
            borderRadius: '50%',
            animation: 'spin 1s linear infinite',
          }} />
          <p>Loading indicator data...</p>
          <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
        </div>
      </div>
    );
  }

  // Error state
  if (error) {
    return (
      <div className="app">
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100%',
          color: '#ef5350',
          flexDirection: 'column',
          gap: '12px',
        }}>
          <h2 style={{ margin: 0 }}>Error Loading Indicator Data</h2>
          <p style={{ color: '#d1d4dc' }}>{error}</p>
          <button
            onClick={() => window.location.reload()}
            style={{
              padding: '8px 16px',
              backgroundColor: 'rgba(41, 98, 255, 0.3)',
              color: '#2962ff',
              border: '1px solid rgba(41, 98, 255, 0.5)',
              borderRadius: '4px',
              cursor: 'pointer',
            }}
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  // Find selected symbol info
  const selectedInfo = symbols.find(s => s.id === selectedSymbol);

  return (
    <div className="app" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Header */}
      <header style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '8px 16px',
        borderBottom: '1px solid rgba(54, 60, 78, 0.5)',
        backgroundColor: '#131722',
        flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <h1 style={{ margin: 0, fontSize: '16px', color: '#d1d4dc', fontWeight: 600 }}>
            SMC Indicators
          </h1>

          {/* Symbol Selector */}
          <select
            value={selectedSymbol || ''}
            onChange={handleSymbolChange}
            disabled={symbolsLoading}
            style={{
              padding: '6px 10px',
              backgroundColor: '#1e222d',
              color: '#d1d4dc',
              border: '1px solid rgba(54, 60, 78, 0.8)',
              borderRadius: '4px',
              fontSize: '13px',
              cursor: 'pointer',
              outline: 'none',
            }}
          >
            {symbols.map(sym => (
              <option key={sym.id} value={sym.id}>
                {sym.symbol} {sym.timeframe} ({sym.dataPoints.toLocaleString()} candles)
              </option>
            ))}
          </select>

          {selectedInfo && (
            <span style={{
              padding: '3px 8px',
              backgroundColor: 'rgba(41, 98, 255, 0.2)',
              color: '#2962ff',
              borderRadius: '3px',
              fontSize: '11px',
              fontWeight: 'bold',
            }}>
              {selectedInfo.timeframe}
            </span>
          )}
        </div>

        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '16px',
          color: '#787b86',
          fontSize: '12px',
        }}>
          <span>{data?.candles?.length?.toLocaleString() || 0} candles</span>
          <span>{totalIndicators.toLocaleString()} indicators</span>
        </div>
      </header>

      {/* Indicator Toggle Bar */}
      <IndicatorToggle
        visibility={visibility}
        onToggle={handleToggle}
        counts={indicatorCounts}
      />

      {/* Chart Container */}
      <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
        <IndicatorChart
          ref={chartRef}
          candles={data?.candles}
          volume={data?.volume}
          data={data || {}}
          visibility={visibility}
        />
      </div>
    </div>
  );
}

export default IndicatorPage;
