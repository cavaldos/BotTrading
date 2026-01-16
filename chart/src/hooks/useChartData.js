import { useState, useEffect } from 'react';

/**
 * Hook to load list of available symbols from symbols.json
 * @returns {Object} { symbols, loading, error }
 */
export function useSymbols() {
  const [symbols, setSymbols] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const fetchSymbols = async () => {
      try {
        setLoading(true);
        setError(null);
        
        const response = await fetch('/data/symbols.json');
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
    };

    fetchSymbols();
  }, []);

  return { symbols, loading, error };
}

/**
 * Hook to load chart data for a specific symbol
 * @param {string} symbolId - Symbol ID (e.g., 'BTCUSDT_15m')
 * @returns {Object} { data, loading, error }
 */
export function useChartData(symbolId) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!symbolId) {
      setLoading(false);
      return;
    }

    const fetchData = async () => {
      try {
        setLoading(true);
        setError(null);
        
        const url = `/data/${symbolId}.json`;
        const response = await fetch(url);
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const jsonData = await response.json();
        setData(jsonData);
      } catch (err) {
        setError(err.message);
        console.error('Error loading chart data:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, [symbolId]);

  return { data, loading, error };
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

export default useChartData;
