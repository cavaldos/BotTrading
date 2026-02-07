import { useState, useEffect, useCallback, useRef } from 'react';

/**
 * Hook to load list of available symbols from symbols.json
 * @param {number} refreshInterval - Interval in ms to refresh (0 = no auto-refresh)
 * @returns {Object} { symbols, loading, error, refresh }
 */
export function useSymbols(refreshInterval = 0) {
  const [symbols, setSymbols] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const intervalRef = useRef(null);

  const fetchSymbols = useCallback(async (showLoading = false) => {
    try {
      if (showLoading) setLoading(true);
      setError(null);
      
      // Add timestamp to prevent caching when refreshing
      const timestamp = refreshInterval > 0 ? `?t=${Date.now()}` : '';
      const response = await fetch(`/data/orders/symbols.json${timestamp}`);
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      
      const jsonData = await response.json();
      setSymbols(jsonData.symbols || []);
    } catch (err) {
      setError(err.message);
      console.error('Error loading symbols:', err);
    } finally {
      setLoading(false);
    }
  }, [refreshInterval]);

  // Initial fetch
  useEffect(() => {
    fetchSymbols(true);
  }, [fetchSymbols]);

  // Auto-refresh interval
  useEffect(() => {
    if (refreshInterval > 0) {
      intervalRef.current = setInterval(() => {
        fetchSymbols(false);
      }, refreshInterval);
    }

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
    };
  }, [fetchSymbols, refreshInterval]);

  return { 
    symbols, 
    loading, 
    error, 
    refresh: () => fetchSymbols(false) 
  };
}

/**
 * Hook to load chart data for a specific symbol with optional auto-refresh
 * @param {string} symbolId - Symbol ID (e.g., 'BTCUSDT_15m')
 * @param {number} refreshInterval - Interval in ms to refresh (0 = no auto-refresh)
 * @returns {Object} { data, loading, error, lastUpdate, refresh }
 */
export function useChartData(symbolId, refreshInterval = 0) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastUpdate, setLastUpdate] = useState(null);
  const intervalRef = useRef(null);

  const fetchData = useCallback(async (showLoading = false) => {
    if (!symbolId) {
      setLoading(false);
      return;
    }

    try {
      if (showLoading) setLoading(true);
      setError(null);
      
      // Add timestamp to prevent caching when refreshing
      const timestamp = refreshInterval > 0 ? `?t=${Date.now()}` : '';
      const url = `/data/${symbolId}.json${timestamp}`;
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      
      const jsonData = await response.json();
      setData(jsonData);
      setLastUpdate(new Date());
    } catch (err) {
      setError(err.message);
      console.error('Error loading chart data:', err);
    } finally {
      setLoading(false);
    }
  }, [symbolId, refreshInterval]);

  // Initial fetch when symbolId changes
  useEffect(() => {
    setData(null);
    setLastUpdate(null);
    fetchData(true);
  }, [fetchData]);

  // Auto-refresh interval
  useEffect(() => {
    if (symbolId && refreshInterval > 0) {
      intervalRef.current = setInterval(() => {
        fetchData(false);
      }, refreshInterval);
    }

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
    };
  }, [fetchData, symbolId, refreshInterval]);

  return { 
    data, 
    loading, 
    error, 
    lastUpdate,
    refresh: () => fetchData(false) 
  };
}

/**
 * Filter trades by result type
 * @param {Array} trades - Array of trade objects
 * @param {string} filter - 'all', 'win', or 'loss'
 * @returns {Array} Filtered trades
 */
export function filterTrades(trades, filter) {
  if (!trades) return [];
  
  switch (filter) {
    case 'win':
      return trades.filter(t => t.result === 'Win');
    case 'loss':
      return trades.filter(t => t.result === 'Loss');
    default:
      return trades;
  }
}

/**
 * Filter markers by trade IDs
 * @param {Array} markers - Array of marker objects
 * @param {Array} trades - Array of filtered trade objects  
 * @returns {Array} Filtered markers
 */
export function filterMarkers(markers, trades) {
  if (!markers || !trades) return [];
  
  const tradeIds = new Set(trades.map(t => t.id));
  
  // Each trade has 2 markers (entry + exit), so we need to match by time
  const tradeTimes = new Set();
  trades.forEach(t => {
    tradeTimes.add(t.entryTime);
    tradeTimes.add(t.exitTime);
  });
  
  return markers.filter(m => tradeTimes.has(m.time));
}

/**
 * Format time elapsed since last update
 * @param {Date} lastUpdate - Last update timestamp
 * @returns {string} Formatted string like "5s ago", "1m ago"
 */
export function formatLastUpdate(lastUpdate) {
  if (!lastUpdate) return 'Never';

  const seconds = Math.floor((Date.now() - lastUpdate.getTime()) / 1000);

  if (seconds < 60) {
    return `${seconds}s ago`;
  } else if (seconds < 3600) {
    return `${Math.floor(seconds / 60)}m ago`;
  } else {
    return `${Math.floor(seconds / 3600)}h ago`;
  }
}

export default useChartData;
