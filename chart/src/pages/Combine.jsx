import { useState, useMemo, useRef, useEffect, useCallback } from 'react';
import CombineChart from '@/components/CombineChart';
import TradeFilter from '@/components/TradeFilter';
import TradeList from '@/components/TradeList';
import IndicatorToggle from '@/components/IndicatorToggle';
import SymbolSelector from '@/components/SymbolSelector';
import { useSymbols, useChartData, filterTrades, filterMarkers } from '@/hooks/useChartData';
import { useIndicatorSymbols, useIndicatorData } from '@/hooks/useIndicatorData';

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
  gannSwing: false,
};

function CombinePage() {
  const { symbols: tradeSymbols, loading: tradeSymbolsLoading } = useSymbols();
  const { symbols: indicatorSymbols, loading: indicatorSymbolsLoading } = useIndicatorSymbols();

  const [selectedSymbol, setSelectedSymbol] = useState(null);
  const [filter, setFilter] = useState('all');
  const [selectedTradeId, setSelectedTradeId] = useState(null);
  const [visibility, setVisibility] = useState(DEFAULT_VISIBILITY);
  const chartRef = useRef(null);

  const commonSymbols = useMemo(() => {
    if (!tradeSymbols.length || !indicatorSymbols.length) return [];
    const indicatorIds = new Set(indicatorSymbols.map(s => s.id));
    return tradeSymbols.filter(s => indicatorIds.has(s.id));
  }, [tradeSymbols, indicatorSymbols]);

  const displaySymbols = commonSymbols.length > 0 ? commonSymbols : tradeSymbols;

  useEffect(() => {
    if (displaySymbols.length > 0 && !selectedSymbol) {
      setSelectedSymbol(displaySymbols[0].id);
    }
  }, [displaySymbols, selectedSymbol]);

  useEffect(() => {
    setFilter('all');
    setSelectedTradeId(null);
  }, [selectedSymbol]);

  const { data: tradeData, loading: tradeDataLoading, error: tradeError } = useChartData(selectedSymbol);
  const { data: indicatorData, loading: indicatorDataLoading, error: indicatorError } = useIndicatorData(selectedSymbol);

  const indicatorCounts = useMemo(() => {
    const sym = indicatorSymbols.find(s => s.id === selectedSymbol);
    return sym?.indicators || {};
  }, [indicatorSymbols, selectedSymbol]);

  const totalIndicators = useMemo(() => {
    if (!indicatorCounts) return 0;
    return Object.values(indicatorCounts).reduce((sum, n) => sum + n, 0);
  }, [indicatorCounts]);

  const filteredTrades = useMemo(() => {
    if (!tradeData?.trades) return [];
    return filterTrades(tradeData.trades, filter);
  }, [tradeData?.trades, filter]);

  const filteredMarkers = useMemo(() => {
    if (!tradeData?.markers || !tradeData?.trades) return [];
    const trades = filterTrades(tradeData.trades, filter);
    return filterMarkers(tradeData.markers, trades);
  }, [tradeData?.markers, tradeData?.trades, filter]);

  const combinedCandles = tradeData?.candles?.length ? tradeData.candles : (indicatorData?.candles || []);
  const combinedVolume = tradeData?.volume?.length ? tradeData.volume : (indicatorData?.volume || []);

  const handleTradeClick = useCallback((trade) => {
    setSelectedTradeId(trade.id);
    if (chartRef.current) {
      const priceRange = {
        low: Math.min(trade.entryPrice, trade.stopLoss, trade.takeProfit),
        high: Math.max(trade.entryPrice, trade.stopLoss, trade.takeProfit),
      };
      chartRef.current.scrollToTrade(trade.entryTime, priceRange, 40);
    }
  }, []);

  const handleTradeSelect = useCallback((trade) => {
    setSelectedTradeId(trade.id);
  }, []);

  const handleToggle = useCallback((key) => {
    setVisibility(prev => ({
      ...prev,
      [key]: !prev[key],
    }));
  }, []);

  const handleSymbolChange = (symbolId) => {
    setSelectedSymbol(symbolId);
  };

  const handleFocusCurrentCandle = () => {
    chartRef.current?.focusToLatestCandle?.();
  };

  const symbolsLoading = tradeSymbolsLoading || indicatorSymbolsLoading;
  const loading = symbolsLoading || tradeDataLoading || indicatorDataLoading;
  const error = tradeError || indicatorError;

  if (loading && !tradeData && !indicatorData) {
    return (
      <div className="app">
        <div className="loading-container">
          <div className="loading-spinner" />
          <p>Loading combined data...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="app">
        <div className="error-container">
          <h2>Error Loading Data</h2>
          <p>{error}</p>
          <button
            className="retry-btn"
            onClick={() => window.location.reload()}
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!symbolsLoading && displaySymbols.length === 0) {
    return (
      <div className="app">
        <div className="error-container">
          <h2>No Common Data Found</h2>
          <p>No symbols have both trade and indicator data available. Please check that both data sources exist.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="header">
        <div className="header-title">
          <h1>Combined View</h1>
          <SymbolSelector
            symbols={displaySymbols}
            selectedSymbol={selectedSymbol}
            onSymbolChange={handleSymbolChange}
            loading={symbolsLoading}
          />
        </div>
        <div className="header-info">
          <span>{tradeData?.candles?.length?.toLocaleString() || 0} candles</span>
          <span>{tradeData?.trades?.length || 0} trades</span>
          <span>{totalIndicators.toLocaleString()} indicators</span>
          <button
            className="chart-action-btn chart-action-btn-primary"
            onClick={handleFocusCurrentCandle}
            disabled={!tradeData?.candles?.length && !indicatorData?.candles?.length}
          >
            Current Candle
          </button>
        </div>
      </header>

      <IndicatorToggle
        visibility={visibility}
        onToggle={handleToggle}
        counts={indicatorCounts}
      />

      <TradeFilter
        filter={filter}
        onFilterChange={setFilter}
        summary={tradeData?.summary}
      />

      <div className="chart-container">
        <CombineChart
          ref={chartRef}
          candles={combinedCandles}
          volume={combinedVolume}
          markers={filteredMarkers}
          trades={filteredTrades}
          orderBlocks={tradeData?.orderBlocks}
          indicatorData={indicatorData || {}}
          indicatorVisibility={visibility}
          onTradeSelect={handleTradeSelect}
        />

        <TradeList
          trades={filteredTrades}
          onTradeClick={handleTradeClick}
          selectedTradeId={selectedTradeId}
        />
      </div>
    </div>
  );
}

export default CombinePage;
