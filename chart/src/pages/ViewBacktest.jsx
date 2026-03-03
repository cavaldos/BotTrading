import { useState, useMemo, useRef, useEffect } from 'react';
import TradingChart from '@/components/TradingChart';
import TradeFilter from '@/components/TradeFilter';
import TradeList from '@/components/TradeList';
import SymbolSelector from '@/components/SymbolSelector';
import { useChartData, useSymbols, filterTrades, filterMarkers } from '@/hooks/useChartData';


function ViewBacktest() {
    // Load available symbols
    const { symbols, loading: symbolsLoading } = useSymbols();

    // Selected symbol state (default to first symbol when loaded)
    const [selectedSymbol, setSelectedSymbol] = useState(null);

    // Auto-select first symbol when symbols are loaded
    useEffect(() => {
        if (symbols.length > 0 && !selectedSymbol) {
            setSelectedSymbol(symbols[0].id);
        }
    }, [symbols, selectedSymbol]);

    // Load chart data for selected symbol
    const { data, loading: dataLoading, error } = useChartData(selectedSymbol);

    const [filter, setFilter] = useState('all');
    const [selectedTradeId, setSelectedTradeId] = useState(null);
    const chartRef = useRef(null);

    // Reset filter and selected trade when symbol changes
    useEffect(() => {
        setFilter('all');
        setSelectedTradeId(null);
    }, [selectedSymbol]);

    // Filter trades and markers based on selected filter
    const filteredTrades = useMemo(() => {
        if (!data?.trades) return [];
        return filterTrades(data.trades, filter);
    }, [data?.trades, filter]);

    const filteredMarkers = useMemo(() => {
        if (!data?.markers || !data?.trades) return [];
        const trades = filterTrades(data.trades, filter);
        return filterMarkers(data.markers, trades);
    }, [data?.markers, data?.trades, filter]);

    // Handle trade click - scroll chart to trade position (both horizontal and vertical)
    const handleTradeClick = (trade) => {
        setSelectedTradeId(trade.id);

        // Scroll chart to trade entry time and price range
        if (chartRef.current) {
            // Calculate price range from entry, TP and SL
            const priceRange = {
                low: Math.min(trade.entryPrice, trade.stopLoss, trade.takeProfit),
                high: Math.max(trade.entryPrice, trade.stopLoss, trade.takeProfit),
            };
            chartRef.current.scrollToTrade(trade.entryTime, priceRange, 40);
        }
    };

    const handleTradeSelect = (trade) => {
        setSelectedTradeId(trade.id);
    };

    // Handle symbol change
    const handleSymbolChange = (symbolId) => {
        setSelectedSymbol(symbolId);
    };

    const handleFocusCurrentCandle = () => {
        chartRef.current?.focusToLatestCandle?.();
    };

    // Combined loading state
    const loading = symbolsLoading || dataLoading;

    // Loading state
    if (loading) {
        return (
            <div className="app">
                <div className="loading-container">
                    <div className="loading-spinner" />
                    <p>Loading chart data...</p>
                </div>
            </div>
        );
    }

    // Error state
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

    return (
        <div className="app">
            {/* Header */}
            <header className="header">
                <div className="header-title">
                    <h1>Trading Chart</h1>
                    <SymbolSelector
                        symbols={symbols}
                        selectedSymbol={selectedSymbol}
                        onSymbolChange={handleSymbolChange}
                        loading={symbolsLoading}
                    />
                </div>
                <div className="header-info">
                    <span>{data?.candles?.length?.toLocaleString() || 0} candles</span>
                    <span>{data?.trades?.length || 0} trades</span>
                    <button
                        className="chart-action-btn chart-action-btn-primary"
                        onClick={handleFocusCurrentCandle}
                        disabled={!data?.candles?.length}
                    >
                        Current Candle
                    </button>
                </div>
            </header>

            {/* Trade Filter */}
            <TradeFilter
                filter={filter}
                onFilterChange={setFilter}
                summary={data?.summary}
            />

            {/* Chart Container with Trade List overlay */}
            <div className="chart-container">
                <TradingChart
                    ref={chartRef}
                    candles={data?.candles}
                    volume={data?.volume}
                    markers={filteredMarkers}
                    trades={filteredTrades}
                    orderBlocks={data?.orderBlocks}
                    onTradeSelect={handleTradeSelect}
                />

                {/* Trade List - positioned top right of chart */}
                <TradeList
                    trades={filteredTrades}
                    onTradeClick={handleTradeClick}
                    selectedTradeId={selectedTradeId}
                />
            </div>
        </div>
    );
}

export default ViewBacktest;
