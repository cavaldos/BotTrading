import { useState, useEffect, useCallback } from 'react';

/**
 * Hook to load list of available indicator symbols from /data/indicators/symbols.json
 * @returns {Object} { symbols, loading, error, refresh }
 */
export function useIndicatorSymbols() {
  const [symbols, setSymbols] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchSymbols = useCallback(async (showLoading = false) => {
    try {
      if (showLoading) setLoading(true);
      setError(null);

      const response = await fetch('/data/indicators/symbols.json');
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const jsonData = await response.json();
      setSymbols(jsonData.symbols || []);
    } catch (err) {
      setError(err.message);
      console.error('Error loading indicator symbols:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSymbols(true);
  }, [fetchSymbols]);

  return {
    symbols,
    loading,
    error,
    refresh: () => fetchSymbols(false),
  };
}

/**
 * Hook to load indicator data for a specific symbol
 * @param {string} symbolId - Symbol ID (e.g., 'BTCUSDT_15m')
 * @returns {Object} { data, loading, error, refresh }
 */
export function useIndicatorData(symbolId) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchData = useCallback(async (showLoading = false) => {
    if (!symbolId) {
      setLoading(false);
      return;
    }

    try {
      if (showLoading) setLoading(true);
      setError(null);

      const url = `/data/indicators/${symbolId}.json`;
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const jsonData = await response.json();
      setData(jsonData);
    } catch (err) {
      setError(err.message);
      console.error('Error loading indicator data:', err);
    } finally {
      setLoading(false);
    }
  }, [symbolId]);

  useEffect(() => {
    setData(null);
    fetchData(true);
  }, [fetchData]);

  return {
    data,
    loading,
    error,
    refresh: () => fetchData(false),
  };
}

export default useIndicatorData;
