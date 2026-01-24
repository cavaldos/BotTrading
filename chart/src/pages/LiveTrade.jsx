import { useState, useMemo, useRef, useEffect } from 'react';
import TradingChart from '@/components/TradingChart';
import TradeFilter from '@/components/TradeFilter';
import TradeList from '@/components/TradeList';
import SymbolSelector from '@/components/SymbolSelector';
import { 
    useLiveChartData, 
    useLiveSymbols, 
    filterLiveTrades, 
    filterLiveMarkers,
    formatLastUpdate 
} from '@/hooks/useLiveChartData';
import './LiveTrade.css';


// Live trade filter component with "Open" option
function LiveTradeFilter({ filter, onFilterChange, summary }) {
    const filters = [
        { id: 'all', label: 'All Trades', count: summary?.total || 0 },
        { id: 'open', label: 'Open', count: summary?.open || 0, color: '#f59e0b' },
        { id: 'win', label: 'Wins', count: summary?.wins || 0, color: '#26a69a' },
        { id: 'loss', label: 'Losses', count: summary?.losses || 0, color: '#ef5350' },
    ];

    return (
        <div className="trade-filter">
            <div className="filter-buttons">
                {filters.map(f => (
                    <button
                        key={f.id}
                        className={`filter-btn ${filter === f.id ? 'active' : ''}`}
                        onClick={() => onFilterChange(f.id)}
                        style={filter === f.id && f.color ? { borderColor: f.color } : {}}
                    >
                        <span className="filter-label">{f.label}</span>
                        <span
                            className="filter-count"
                            style={f.color ? { color: f.color } : {}}
                        >
                            {f.count}
                        </span>
                    </button>
                ))}
            </div>

            {summary && (
                <div className="summary-stats">
                    <div className="stat">
                        <span className="stat-label">Win Rate</span>
                        <span className="stat-value" style={{ color: '#26a69a' }}>
                            {summary.winRate}%
                        </span>
                    </div>
                </div>
            )}
        </div>
    );
}


function LiveTrade() {
    // Refresh intervals (ms)
    const SYMBOL_REFRESH = 30000;  // 30 seconds
    const DATA_REFRESH = 5000;     // 5 seconds

    // Load available symbols with auto-refresh
    const { symbols, loading: symbolsLoading } = useLiveSymbols(SYMBOL_REFRESH);

    // Selected symbol state
    const [selectedSymbol, setSelectedSymbol] = useState(null);

    // Auto-select first symbol when symbols are loaded
    useEffect(() => {
        if (symbols.length > 0 && !selectedSymbol) {
            setSelectedSymbol(symbols[0].id);
        }
    }, [symbols, selectedSymbol]);

    // Auto-refresh toggle state
    const [isAutoRefreshEnabled, setIsAutoRefreshEnabled] = useState(true);

    // Load chart data with auto-refresh
    const { 
        data, 
        loading: dataLoading, 
        error,
        lastUpdate,
        refresh 
    } = useLiveChartData(selectedSymbol, isAutoRefreshEnabled ? DATA_REFRESH : null);

    const [filter, setFilter] = useState('all');
    const [selectedTradeId, setSelectedTradeId] = useState(null);
    const chartRef = useRef(null);
    const scrollPositionRef = useRef(null);
    const isInitialLoadRef = useRef(true); // Track if this is the initial data load

    // Track last update display and countdown
    const [lastUpdateText, setLastUpdateText] = useState('Never');
    const [nextUpdateCountdown, setNextUpdateCountdown] = useState(DATA_REFRESH);
    
    useEffect(() => {
        const updateText = () => {
            setLastUpdateText(formatLastUpdate(lastUpdate));
        };
        updateText();
        const interval = setInterval(updateText, 1000);
        return () => clearInterval(interval);
    }, [lastUpdate]);

    // Countdown timer for next update
    useEffect(() => {
        if (!isAutoRefreshEnabled) {
            setNextUpdateCountdown(0);
            return;
        }

        const updateCountdown = () => {
            const now = Date.now();
            const timeSinceLastUpdate = now - (lastUpdate || now);
            const remaining = Math.max(0, DATA_REFRESH - timeSinceLastUpdate);
            setNextUpdateCountdown(remaining);
        };

        updateCountdown();
        const interval = setInterval(updateCountdown, 100);
        return () => clearInterval(interval);
    }, [lastUpdate, isAutoRefreshEnabled]);

    // Reset filter and selected trade when symbol changes
    useEffect(() => {
        setFilter('all');
        setSelectedTradeId(null);
        isInitialLoadRef.current = true; // Reset initial load flag when symbol changes
        scrollPositionRef.current = null; // Clear saved position
    }, [selectedSymbol]);

    // Save scroll position before data refresh and restore after
    useEffect(() => {
        if (!chartRef.current || !data) return;
        
        // On initial load, don't preserve position (let it fitContent)
        if (isInitialLoadRef.current) {
            isInitialLoadRef.current = false;
            return;
        }
        
        // For subsequent updates, save current position before data changes
        // and tell chart to preserve position on next update
        const currentPosition = chartRef.current.getScrollPosition?.();
        if (currentPosition) {
            scrollPositionRef.current = currentPosition;
            chartRef.current.setPreservePosition?.(true);
        }
    }, [data?.candles?.length]); // Only run when candles array length changes (new data loaded)

    // Restore scroll position after chart re-renders with new data
    useEffect(() => {
        if (!chartRef.current || !scrollPositionRef.current) return;
        
        // Use requestAnimationFrame to ensure chart has finished rendering
        const rafId = requestAnimationFrame(() => {
            chartRef.current.restoreScrollPosition?.(scrollPositionRef.current);
        });
        
        return () => cancelAnimationFrame(rafId);
    }, [data]);

    // Filter trades and markers
    const filteredTrades = useMemo(() => {
        if (!data?.trades) return [];
        return filterLiveTrades(data.trades, filter);
    }, [data?.trades, filter]);

    const filteredMarkers = useMemo(() => {
        if (!data?.markers || !data?.trades) return [];
        const trades = filterLiveTrades(data.trades, filter);
        return filterLiveMarkers(data.markers, trades);
    }, [data?.markers, data?.trades, filter]);

    // Handle trade click
    const handleTradeClick = (trade) => {
        setSelectedTradeId(trade.id);

        if (chartRef.current) {
            const priceRange = {
                low: Math.min(trade.entryPrice, trade.stopLoss, trade.takeProfit),
                high: Math.max(trade.entryPrice, trade.stopLoss, trade.takeProfit),
            };
            chartRef.current.scrollToTrade(trade.entryTime, priceRange, 40);
        }
    };

    // Handle symbol change
    const handleSymbolChange = (symbolId) => {
        setSelectedSymbol(symbolId);
    };

    // Combined loading state
    const loading = symbolsLoading || dataLoading;

    // Loading state (only for initial load)
    if (loading && !data) {
        return (
            <div className="app">
                <div className="loading-container">
                    <div className="loading-spinner" />
                    <p>Loading live trade data...</p>
                </div>
            </div>
        );
    }

    // Error state
    if (error && !data) {
        return (
            <div className="app">
                <div className="error-container">
                    <h2>Error Loading Data</h2>
                    <p>{error}</p>
                    <p className="error-hint">
                        Make sure the trading bot is running and exporting data.
                    </p>
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

    // No data state
    if (!data && symbols.length === 0) {
        return (
            <div className="app">
                <div className="error-container">
                    <h2>No Live Data Available</h2>
                    <p>Start the trading bot to see live trades.</p>
                    <button
                        className="retry-btn"
                        onClick={refresh}
                    >
                        Refresh
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
                    <h1>Live Trading</h1>
                    <span className="live-indicator">
                        <span className="live-dot"></span>
                        LIVE
                    </span>
                    <SymbolSelector
                        symbols={symbols}
                        selectedSymbol={selectedSymbol}
                        onSymbolChange={handleSymbolChange}
                        loading={symbolsLoading}
                    />
                </div>
                <div className="header-info">
                    <span className="update-info">
                        Updated: {lastUpdateText}
                    </span>
                    {isAutoRefreshEnabled && (
                        <span className="countdown-info">
                            Next update: {(nextUpdateCountdown / 1000).toFixed(1)}s
                        </span>
                    )}
                    <span>{data?.candles?.length?.toLocaleString() || 0} candles</span>
                    <span>{data?.trades?.length || 0} trades</span>
                    <button 
                        className={`toggle-refresh-btn ${isAutoRefreshEnabled ? 'active' : 'inactive'}`}
                        onClick={() => setIsAutoRefreshEnabled(!isAutoRefreshEnabled)}
                        title={isAutoRefreshEnabled ? 'Turn off auto-refresh' : 'Turn on auto-refresh'}
                    >
                        {isAutoRefreshEnabled ? 'Auto-Refresh ON' : 'Auto-Refresh OFF'}
                    </button>
                    <button 
                        className="refresh-btn" 
                        onClick={refresh}
                        title="Refresh now"
                    >
                        Refresh
                    </button>
                </div>
            </header>

            {/* Trade Filter with Open option */}
            <LiveTradeFilter
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
                    orderBlocks={[]}  // No order blocks for live trading
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

export default LiveTrade;
