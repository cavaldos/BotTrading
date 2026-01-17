import { useEffect, useRef, useMemo, useImperativeHandle, forwardRef, useState, useCallback } from 'react';
import { createChart, CandlestickSeries, HistogramSeries } from 'lightweight-charts';
import { createSeriesMarkers } from 'lightweight-charts';
import { createTradeZones, createOrderBlockRect } from '../plugins/RectanglePrimitive';
import { MeasurementPrimitive, createMeasurementHandler } from '../plugins/MeasurementPlugin';

/**
 * TradingChart component using TradingView's lightweight-charts
 * Features:
 * - Candlestick chart with volume
 * - Trade markers (entry/exit)
 * - Trade zones (TP/SL rectangles)
 * - Order Block rectangles
 * - Crosshair and zoom/pan
 * - scrollToTime() method exposed via ref
 */
const TradingChart = forwardRef(function TradingChart({ 
  candles, 
  volume, 
  markers = [],
  trades = [],
  orderBlocks = [],
  showOrderBlocks = true,
}, ref) {
  const chartContainerRef = useRef(null);
  const chartRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const volumeSeriesRef = useRef(null);
  const markersRef = useRef(null);
  const primitivesRef = useRef([]);
  const measurementRef = useRef(null);
  const measurementHandlerRef = useRef(null);
  const [isMeasuring, setIsMeasuring] = useState(false);

  // Chart colors
  const colors = useMemo(() => ({
    background: '#131722',
    text: '#d1d4dc',
    grid: 'rgba(54, 60, 78, 0.5)',
    bullish: '#26a69a',
    bearish: '#ef5350',
    crosshair: '#9598a1',
  }), []);

  // Expose scrollToTrade method via ref
  useImperativeHandle(ref, () => ({
    scrollToTime: (time, barsAround = 30) => {
      if (!chartRef.current) return;
      
      const timeScale = chartRef.current.timeScale();
      const barDuration = 900; // 15 minutes in seconds
      
      // Set visible range around the target time
      const from = time - (barsAround * barDuration);
      const to = time + (barsAround * barDuration);
      
      timeScale.setVisibleRange({ from, to });
    },
    
    scrollToTrade: (time, priceRange, barsAround = 30) => {
      if (!chartRef.current || !candleSeriesRef.current) return;
      
      const timeScale = chartRef.current.timeScale();
      const barDuration = 900; // 15 minutes in seconds
      
      // Set visible range for time (horizontal)
      const from = time - (barsAround * barDuration);
      const to = time + (barsAround * barDuration);
      timeScale.setVisibleRange({ from, to });
      
      // Set visible range for price (vertical) with padding
      if (priceRange) {
        const { low, high } = priceRange;
        const padding = (high - low) * 0.5; // 50% padding for better visibility
        const minValue = low - padding;
        const maxValue = high + padding;
        
        // Get the series price scale and set visible range
        const seriesPriceScale = candleSeriesRef.current.priceScale();
        if (seriesPriceScale) {
          // Disable auto scale first
          seriesPriceScale.applyOptions({
            autoScale: false,
          });
          
          // Use setVisibleRange if available (lightweight-charts v5+)
          if (typeof seriesPriceScale.setVisibleRange === 'function') {
            seriesPriceScale.setVisibleRange({
              from: minValue,
              to: maxValue,
            });
          }
        }
      }
    },
    
    getChart: () => chartRef.current,
    
    // Measurement tool controls
    toggleMeasurement: () => {
      if (measurementHandlerRef.current) {
        const isEnabled = measurementHandlerRef.current.toggle();
        setIsMeasuring(isEnabled);
        return isEnabled;
      }
      return false;
    },
    clearMeasurement: () => {
      if (measurementHandlerRef.current) {
        measurementHandlerRef.current.clear();
      }
    },
    isMeasuring: () => isMeasuring,
  }), [isMeasuring]);

  // Initialize chart
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: 'solid', color: colors.background },
        textColor: colors.text,
      },
      grid: {
        vertLines: { color: colors.grid },
        horzLines: { color: colors.grid },
      },
      crosshair: {
        mode: 0, // Magnet mode - crosshair follows mouse cursor exactly
        vertLine: {
          color: colors.crosshair,
          width: 1,
          style: 0,
          labelBackgroundColor: '#2962ff',
        },
        horzLine: {
          color: colors.crosshair,
          width: 1,
          style: 0,
          labelBackgroundColor: '#2962ff',
        },
      },
      rightPriceScale: {
        borderColor: colors.grid,
        scaleMargins: {
          top: 0.1,
          bottom: 0.2,
        },
      },
      timeScale: {
        borderColor: colors.grid,
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: true,
      },
      handleScale: {
        axisPressedMouseMove: {
          time: true,
          price: true,
        },
        axisDoubleClickReset: {
          time: true,
          price: true,
        },
        mouseWheel: true,
        pinch: true,
      },
      rightPriceScale: {
        autoScale: false, // Disable auto-scale to allow manual vertical movement
      },
    });

    // Handle resize
    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({
          width: chartContainerRef.current.clientWidth,
          height: chartContainerRef.current.clientHeight,
        });
      }
    };

    // Initial size
    handleResize();
    window.addEventListener('resize', handleResize);

    // Create candlestick series
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: colors.bullish,
      downColor: colors.bearish,
      borderUpColor: colors.bullish,
      borderDownColor: colors.bearish,
      wickUpColor: colors.bullish,
      wickDownColor: colors.bearish,
    });

    // Create volume series (overlay at bottom)
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: {
        type: 'volume',
      },
      priceScaleId: '', // Overlay
    });

    volumeSeries.priceScale().applyOptions({
      scaleMargins: {
        top: 0.8,
        bottom: 0,
      },
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;

    // Initialize measurement tool
    const measurementPrimitive = new MeasurementPrimitive();
    candleSeries.attachPrimitive(measurementPrimitive);
    measurementRef.current = measurementPrimitive;
    measurementHandlerRef.current = createMeasurementHandler(chart, candleSeries, measurementPrimitive);

    return () => {
      window.removeEventListener('resize', handleResize);
      if (measurementHandlerRef.current) {
        measurementHandlerRef.current.disable();
      }
      chart.remove();
    };
  }, [colors]);

  // Update candle data
  useEffect(() => {
    if (!candleSeriesRef.current || !candles?.length) return;
    candleSeriesRef.current.setData(candles);
    chartRef.current?.timeScale().fitContent();
  }, [candles]);

  // Update volume data
  useEffect(() => {
    if (!volumeSeriesRef.current || !volume?.length) return;
    volumeSeriesRef.current.setData(volume);
  }, [volume]);

  // Update markers
  useEffect(() => {
    if (!candleSeriesRef.current) return;

    // Remove old markers
    if (markersRef.current) {
      markersRef.current.setMarkers([]);
    }

    // Add new markers
    if (markers?.length > 0) {
      // Sort markers by time (required by lightweight-charts)
      const sortedMarkers = [...markers].sort((a, b) => a.time - b.time);
      markersRef.current = createSeriesMarkers(candleSeriesRef.current, sortedMarkers);
    }
  }, [markers]);

  // Update trade zones (rectangles) and order blocks
  useEffect(() => {
    if (!candleSeriesRef.current) return;

    // Remove old primitives
    primitivesRef.current.forEach(primitive => {
      try {
        candleSeriesRef.current.detachPrimitive(primitive);
      } catch {
        // Ignore if already detached
      }
    });
    primitivesRef.current = [];

    // Add trade zone rectangles
    if (trades?.length > 0) {
      trades.forEach(trade => {
        const zones = createTradeZones(trade);
        zones.forEach(zone => {
          candleSeriesRef.current.attachPrimitive(zone);
          primitivesRef.current.push(zone);
        });
      });
    }

    // Add order block rectangles
    if (showOrderBlocks && orderBlocks?.length > 0) {
      // Only show recent order blocks to avoid clutter
      const recentOBs = orderBlocks.slice(-30);
      recentOBs.forEach(ob => {
        const obRect = createOrderBlockRect(ob, 150); // 100 bars length for better visibility
        candleSeriesRef.current.attachPrimitive(obRect);
        primitivesRef.current.push(obRect);
      });
    }
  }, [trades, orderBlocks, showOrderBlocks]);

  // Toggle measurement mode
  const handleToggleMeasurement = useCallback(() => {
    if (measurementHandlerRef.current) {
      const isEnabled = measurementHandlerRef.current.toggle();
      setIsMeasuring(isEnabled);
    }
  }, []);

  // Clear measurement
  const handleClearMeasurement = useCallback(() => {
    if (measurementHandlerRef.current) {
      measurementHandlerRef.current.clear();
    }
  }, []);

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      {/* Measurement toolbar */}
      <div style={{
        position: 'absolute',
        top: 10,
        right: 10,
        zIndex: 100,
        display: 'flex',
        gap: '8px',
      }}>
        <button
          onClick={handleToggleMeasurement}
          style={{
            padding: '8px 12px',
            backgroundColor: isMeasuring ? '#2962ff' : 'rgba(41, 98, 255, 0.3)',
            color: '#fff',
            border: isMeasuring ? '2px solid #2962ff' : '1px solid rgba(41, 98, 255, 0.5)',
            borderRadius: '4px',
            cursor: 'pointer',
            fontSize: '12px',
            fontWeight: 'bold',
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
          }}
          title="Toggle measurement tool (Ruler)"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21.3 8.7l-6-6-12 12 6 6 12-12z"/>
            <path d="M8 16l2-2"/>
            <path d="M6.5 13.5l2-2"/>
            <path d="M11 18.5l2-2"/>
            <path d="M13.5 16l2-2"/>
          </svg>
          {isMeasuring ? 'Measuring' : 'Ruler'}
        </button>
        {isMeasuring && (
          <button
            onClick={handleClearMeasurement}
            style={{
              padding: '8px 12px',
              backgroundColor: 'rgba(239, 83, 80, 0.3)',
              color: '#ef5350',
              border: '1px solid rgba(239, 83, 80, 0.5)',
              borderRadius: '4px',
              cursor: 'pointer',
              fontSize: '12px',
              fontWeight: 'bold',
            }}
            title="Clear measurement"
          >
            Clear
          </button>
        )}
      </div>
      
      <div 
        ref={chartContainerRef} 
        style={{ 
          width: '100%', 
          height: '100%',
          minHeight: '500px',
        }}
      />
    </div>
  );
});

export default TradingChart;
