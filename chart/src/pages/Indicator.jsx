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
  swing: true,
};

const FULL_SCREEN_APP_STYLE = {
  position: 'relative',
  width: '100%',
  height: 'calc(100vh - 40px)',
  minHeight: 'calc(100vh - 40px)',
  display: 'flex',
  flexDirection: 'column',
  backgroundColor: '#131722',
};

const INITIAL_BACKTEST_BARS = 300;
const PLAYBACK_SPEED_OPTIONS = [1, 2, 5, 10];

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

function IndicatorPage() {
  // Load available indicator symbols
  const { symbols, loading: symbolsLoading } = useIndicatorSymbols();

  // Selected symbol state
  const [selectedSymbol, setSelectedSymbol] = useState(null);

  // Indicator visibility toggles
  const [visibility, setVisibility] = useState(DEFAULT_VISIBILITY);
  const [backtestMode, setBacktestMode] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [playbackSpeed, setPlaybackSpeed] = useState(1);
  const [backtestIndex, setBacktestIndex] = useState(INITIAL_BACKTEST_BARS);

  const chartRef = useRef(null);

  // Auto-select first symbol when loaded
  useEffect(() => {
    if (symbols.length > 0 && !selectedSymbol) {
      setSelectedSymbol(symbols[0].id);
    }
  }, [symbols, selectedSymbol]);

  // Load indicator data for selected symbol
  const { data, loading: dataLoading, error } = useIndicatorData(selectedSymbol);
  const totalCandles = data?.candles?.length || 0;
  const maxBacktestIndex = Math.max(totalCandles - 1, 0);

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

  const handleFocusCurrentCandle = useCallback(() => {
    chartRef.current?.focusToLatestCandle?.();
  }, []);

  const handleToggleBacktest = useCallback(() => {
    setBacktestMode(prev => {
      if (prev) {
        setIsPlaying(false);
      } else if (totalCandles > 0 && backtestIndex >= maxBacktestIndex) {
        setBacktestIndex(Math.min(INITIAL_BACKTEST_BARS, maxBacktestIndex));
      }
      return !prev;
    });
  }, [totalCandles, backtestIndex, maxBacktestIndex]);

  const stepBacktest = useCallback((delta) => {
    setBacktestIndex(prev => clamp(prev + delta, 0, maxBacktestIndex));
  }, [maxBacktestIndex]);

  const handleResetBacktest = useCallback(() => {
    setIsPlaying(false);
    setBacktestIndex(Math.min(INITIAL_BACKTEST_BARS, maxBacktestIndex));
  }, [maxBacktestIndex]);

  const handleSliderChange = useCallback((e) => {
    const next = Number(e.target.value);
    setBacktestIndex(clamp(Number.isNaN(next) ? 0 : next, 0, maxBacktestIndex));
  }, [maxBacktestIndex]);

  useEffect(() => {
    setIsPlaying(false);
    if (totalCandles > 0) {
      setBacktestIndex(Math.min(INITIAL_BACKTEST_BARS, totalCandles - 1));
    } else {
      setBacktestIndex(0);
    }
  }, [selectedSymbol, totalCandles]);

  useEffect(() => {
    if (!backtestMode || !isPlaying || maxBacktestIndex <= 0) return undefined;

    const interval = window.setInterval(() => {
      setBacktestIndex(prev => {
        const next = prev + playbackSpeed;
        if (next >= maxBacktestIndex) {
          window.clearInterval(interval);
          setIsPlaying(false);
          return maxBacktestIndex;
        }
        return next;
      });
    }, 300);

    return () => window.clearInterval(interval);
  }, [backtestMode, isPlaying, playbackSpeed, maxBacktestIndex]);

  // Combined loading state
  const loading = symbolsLoading || dataLoading;

  const visibleData = useMemo(() => {
    if (!data) return null;
    if (!backtestMode || !data.candles?.length) return data;

    const boundedIndex = clamp(backtestIndex, 0, data.candles.length - 1);
    const cutoffTime = data.candles[boundedIndex]?.time;
    if (!cutoffTime) return data;

    return {
      ...data,
      candles: data.candles.slice(0, boundedIndex + 1),
      volume: data.volume?.slice(0, boundedIndex + 1) || [],
      bos: (data.bos || []).filter(item => item.breakTime <= cutoffTime && item.pivotTime <= cutoffTime),
      choch: (data.choch || []).filter(item => item.breakTime <= cutoffTime && item.pivotTime <= cutoffTime),
      orderBlocks: (data.orderBlocks || []).filter(item => item.startTime <= cutoffTime),
      fvg: (data.fvg || []).filter(item => item.time <= cutoffTime),
      eqhEql: (data.eqhEql || []).filter(item => item.endTime <= cutoffTime),
      imbalance: (data.imbalance || []).filter(item => item.time <= cutoffTime),
      breakerBlocks: (data.breakerBlocks || []).filter(item => item.startTime <= cutoffTime),
      mitigationBlocks: (data.mitigationBlocks || []).filter(item => (item.mitigationTime ?? item.startTime) <= cutoffTime),
      retracement: (data.retracement || []).filter(item => {
        const latestSwingTime = Math.max(item.swingHighTime || 0, item.swingLowTime || 0);
        return latestSwingTime <= cutoffTime;
      }),
      swing: (data.swing || []).filter(item => item.time <= cutoffTime),
    };
  }, [data, backtestMode, backtestIndex]);

  const displayedIndicatorCounts = useMemo(() => {
    if (!backtestMode || !visibleData) return indicatorCounts;
    return {
      bos: visibleData.bos?.length || 0,
      choch: visibleData.choch?.length || 0,
      orderBlocks: visibleData.orderBlocks?.length || 0,
      fvg: visibleData.fvg?.length || 0,
      eqhEql: visibleData.eqhEql?.length || 0,
      imbalance: visibleData.imbalance?.length || 0,
      breakerBlocks: visibleData.breakerBlocks?.length || 0,
      mitigationBlocks: visibleData.mitigationBlocks?.length || 0,
      retracement: visibleData.retracement?.length || 0,
      swing: visibleData.swing?.length || 0,
    };
  }, [backtestMode, visibleData, indicatorCounts]);

  const currentBacktestTime = useMemo(() => {
    if (!backtestMode || !visibleData?.candles?.length) return null;
    const lastTime = visibleData.candles[visibleData.candles.length - 1]?.time;
    return lastTime ? new Date(lastTime * 1000).toLocaleString() : null;
  }, [backtestMode, visibleData]);

  // Count total visible indicators
  const totalIndicators = useMemo(() => {
    if (!displayedIndicatorCounts) return 0;
    return Object.values(displayedIndicatorCounts).reduce((sum, n) => sum + n, 0);
  }, [displayedIndicatorCounts]);

  // Loading state
  if (loading && !data) {
    return (
      <div className="app" style={FULL_SCREEN_APP_STYLE}>
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
      <div className="app" style={FULL_SCREEN_APP_STYLE}>
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
    <div className="app" style={FULL_SCREEN_APP_STYLE}>
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
          <button
            onClick={handleFocusCurrentCandle}
            disabled={!visibleData?.candles?.length}
            style={{
              padding: '4px 10px',
              backgroundColor: 'rgba(41, 98, 255, 0.2)',
              color: '#2962ff',
              border: '1px solid rgba(41, 98, 255, 0.5)',
              borderRadius: '4px',
              cursor: visibleData?.candles?.length ? 'pointer' : 'not-allowed',
              fontSize: '11px',
              fontWeight: 700,
            }}
          >
            Current Candle
          </button>
          <button
            onClick={handleToggleBacktest}
            disabled={!data?.candles?.length}
            style={{
              padding: '4px 10px',
              backgroundColor: backtestMode ? 'rgba(38, 166, 154, 0.2)' : 'rgba(120, 123, 134, 0.15)',
              color: backtestMode ? '#26a69a' : '#a0a4b0',
              border: `1px solid ${backtestMode ? 'rgba(38, 166, 154, 0.5)' : 'rgba(120, 123, 134, 0.4)'}`,
              borderRadius: '4px',
              cursor: data?.candles?.length ? 'pointer' : 'not-allowed',
              fontSize: '11px',
              fontWeight: 700,
            }}
          >
            {backtestMode ? 'Backtest ON' : 'Backtest OFF'}
          </button>
        </div>
      </header>

      {backtestMode && (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '6px 12px',
          borderBottom: '1px solid rgba(54, 60, 78, 0.5)',
          backgroundColor: 'rgba(19, 23, 34, 0.95)',
          flexWrap: 'wrap',
          color: '#a0a4b0',
          fontSize: '12px',
        }}>
          <button
            onClick={() => stepBacktest(-1)}
            disabled={backtestIndex <= 0}
            style={{
              padding: '3px 8px',
              borderRadius: '3px',
              border: '1px solid rgba(120, 123, 134, 0.4)',
              backgroundColor: 'rgba(120, 123, 134, 0.15)',
              color: '#d1d4dc',
              cursor: backtestIndex <= 0 ? 'not-allowed' : 'pointer',
            }}
          >
            -1
          </button>

          <button
            onClick={() => setIsPlaying(prev => !prev)}
            disabled={backtestIndex >= maxBacktestIndex}
            style={{
              padding: '3px 10px',
              borderRadius: '3px',
              border: '1px solid rgba(41, 98, 255, 0.5)',
              backgroundColor: 'rgba(41, 98, 255, 0.2)',
              color: '#2962ff',
              cursor: backtestIndex >= maxBacktestIndex ? 'not-allowed' : 'pointer',
              fontWeight: 700,
            }}
          >
            {isPlaying ? 'Pause' : 'Play'}
          </button>

          <button
            onClick={() => stepBacktest(1)}
            disabled={backtestIndex >= maxBacktestIndex}
            style={{
              padding: '3px 8px',
              borderRadius: '3px',
              border: '1px solid rgba(120, 123, 134, 0.4)',
              backgroundColor: 'rgba(120, 123, 134, 0.15)',
              color: '#d1d4dc',
              cursor: backtestIndex >= maxBacktestIndex ? 'not-allowed' : 'pointer',
            }}
          >
            +1
          </button>

          <button
            onClick={handleResetBacktest}
            style={{
              padding: '3px 10px',
              borderRadius: '3px',
              border: '1px solid rgba(120, 123, 134, 0.4)',
              backgroundColor: 'transparent',
              color: '#a0a4b0',
              cursor: 'pointer',
            }}
          >
            Reset
          </button>

          <select
            value={playbackSpeed}
            onChange={(e) => setPlaybackSpeed(Number(e.target.value))}
            style={{
              padding: '3px 6px',
              backgroundColor: '#1e222d',
              color: '#d1d4dc',
              border: '1px solid rgba(54, 60, 78, 0.8)',
              borderRadius: '3px',
              fontSize: '11px',
              outline: 'none',
            }}
          >
            {PLAYBACK_SPEED_OPTIONS.map(speed => (
              <option key={speed} value={speed}>{speed}x</option>
            ))}
          </select>

          <input
            type="range"
            min={0}
            max={maxBacktestIndex}
            value={clamp(backtestIndex, 0, maxBacktestIndex)}
            onChange={handleSliderChange}
            style={{ width: '220px' }}
          />

          <span>
            Bar {clamp(backtestIndex + 1, 1, Math.max(totalCandles, 1)).toLocaleString()} / {Math.max(totalCandles, 0).toLocaleString()}
          </span>
          {currentBacktestTime && <span>{currentBacktestTime}</span>}
        </div>
      )}

      {/* Indicator Toggle Bar */}
      <IndicatorToggle
        visibility={visibility}
        onToggle={handleToggle}
        counts={displayedIndicatorCounts}
      />

      {/* Chart Container */}
      <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
        <IndicatorChart
          ref={chartRef}
          candles={visibleData?.candles}
          volume={visibleData?.volume}
          data={visibleData || {}}
          visibility={visibility}
          autoFollowLatest={!backtestMode}
        />
      </div>
    </div>
  );
}

export default IndicatorPage;
