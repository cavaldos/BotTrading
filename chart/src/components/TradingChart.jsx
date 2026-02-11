import { useEffect, useRef, useMemo, useImperativeHandle, forwardRef, useState, useCallback } from 'react';
import { createChart, CandlestickSeries, HistogramSeries } from 'lightweight-charts';
import { createSeriesMarkers } from 'lightweight-charts';
import { createTradeZones, createOrderBlockRect } from '../plugins/RectanglePrimitive';
import { MeasurementPrimitive, createMeasurementHandler } from '../plugins/MeasurementPlugin';
import { applyPositionLabelsToMarkers } from './positionMarkers';

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
  const hasInitialFitRef = useRef(false);
  const lastCandleTimeRef = useRef(null);
  const scrollAnimationRef = useRef(null);
  const [activeMeasurementTool, setActiveMeasurementTool] = useState(null);
  const preservePositionRef = useRef(false); // Flag to preserve scroll position on data update

  const animateVisibleRange = useCallback((timeScale, fromRange, toRange, duration = 260) => {
    if (!timeScale || !fromRange || !toRange) return;

    if (scrollAnimationRef.current) {
      cancelAnimationFrame(scrollAnimationRef.current);
      scrollAnimationRef.current = null;
    }

    const start = performance.now();
    const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3);

    const tick = (now) => {
      const progress = Math.min((now - start) / duration, 1);
      const eased = easeOutCubic(progress);

      timeScale.setVisibleRange({
        from: fromRange.from + (toRange.from - fromRange.from) * eased,
        to: fromRange.to + (toRange.to - fromRange.to) * eased,
      });

      if (progress < 1) {
        scrollAnimationRef.current = requestAnimationFrame(tick);
      } else {
        scrollAnimationRef.current = null;
      }
    };

    scrollAnimationRef.current = requestAnimationFrame(tick);
  }, []);

  // Chart colors
  const colors = useMemo(() => ({
    background: '#131722',
    text: '#d1d4dc',
    grid: 'rgba(54, 60, 78, 0.5)',
    bullish: '#26a69a',
    bearish: '#ef5350',
    crosshair: '#9598a1',
  }), []);

  const chartMarkers = useMemo(() => applyPositionLabelsToMarkers(markers, trades), [markers, trades]);

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

    focusToLatestCandle: () => {
      if (!chartRef.current || !candleSeriesRef.current || !candles?.length) return;

      const timeScale = chartRef.current.timeScale();
      const priceScale = candleSeriesRef.current.priceScale();
      const latestTime = candles[candles.length - 1]?.time;
      const latest = candles[candles.length - 1];
      const prevTime = candles[candles.length - 2]?.time;
      if (!latestTime || !latest) return;

      const barDuration = prevTime ? Math.max(1, latestTime - prevTime) : 60;
      const currentRange = timeScale.getVisibleRange();
      const currentSpan = currentRange
        ? Math.max(currentRange.to - currentRange.from, 30 * barDuration)
        : 160 * barDuration;
      const nextRange = {
        from: latestTime - currentSpan / 2,
        to: latestTime + currentSpan / 2,
      };

      if (currentRange) {
        animateVisibleRange(timeScale, currentRange, nextRange, 320);
      } else {
        timeScale.setVisibleRange(nextRange);
      }

      if (priceScale && typeof priceScale.setVisibleRange === 'function') {
        const latestHigh = Number(latest.high ?? latest.close ?? latest.open ?? 0);
        const latestLow = Number(latest.low ?? latest.close ?? latest.open ?? 0);
        const centerPrice = (latestHigh + latestLow) / 2;
        const candleHeight = Math.max(Math.abs(latestHigh - latestLow), Math.abs(centerPrice) * 0.0005);
        const halfRange = Math.max(candleHeight * 8, Math.abs(centerPrice) * 0.004, 0.0001);
        priceScale.applyOptions({ autoScale: false });
        priceScale.setVisibleRange({
          from: centerPrice - halfRange,
          to: centerPrice + halfRange,
        });
      }
    },
    
    getChart: () => chartRef.current,
    
    // Get current scroll/view position
    getScrollPosition: () => {
      if (!chartRef.current) return null;
      
      const timeScale = chartRef.current.timeScale();
      const visibleRange = timeScale.getVisibleRange();
      const logicalRange = timeScale.getVisibleLogicalRange();
      
      // Get price scale position
      let priceRange = null;
      if (candleSeriesRef.current) {
        const priceScale = candleSeriesRef.current.priceScale();
        // Try to get visible price range
        const barsInfo = candleSeriesRef.current.barsInLogicalRange(logicalRange);
        if (barsInfo) {
          priceRange = {
            from: barsInfo.barsBefore,
            to: barsInfo.barsAfter,
          };
        }
      }
      
      return {
        timeRange: visibleRange,
        logicalRange: logicalRange,
        priceRange: priceRange,
      };
    },
    
    // Restore scroll/view position
    restoreScrollPosition: (position) => {
      if (!chartRef.current || !position) return;
      
      preservePositionRef.current = true;
      
      const timeScale = chartRef.current.timeScale();
      
      // Restore time range
      if (position.timeRange) {
        timeScale.setVisibleRange(position.timeRange);
      } else if (position.logicalRange) {
        timeScale.setVisibleLogicalRange(position.logicalRange);
      }
    },
    
    // Set preserve position flag (prevents fitContent on next data update)
    setPreservePosition: (preserve) => {
      preservePositionRef.current = preserve;
    },
    
    // Measurement tool controls
    toggleMeasurement: (toolType = 'ruler') => {
      if (measurementHandlerRef.current) {
        const result = measurementHandlerRef.current.toggle(toolType);
        setActiveMeasurementTool(result.enabled ? result.toolType : null);
        return result.enabled;
      }
      return false;
    },
    clearMeasurement: () => {
      if (measurementHandlerRef.current) {
        measurementHandlerRef.current.clear();
      }
    },
    isMeasuring: () => activeMeasurementTool !== null,
  }), [activeMeasurementTool, candles, animateVisibleRange]);

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
      if (scrollAnimationRef.current) {
        cancelAnimationFrame(scrollAnimationRef.current);
        scrollAnimationRef.current = null;
      }
      chart.remove();
    };
  }, [colors]);

  // Update candle data
  useEffect(() => {
    if (!candleSeriesRef.current) return;
    if (!candles?.length) {
      hasInitialFitRef.current = false;
      lastCandleTimeRef.current = null;
      return;
    }

    const previousLastTime = lastCandleTimeRef.current;
    const latestCandle = candles[candles.length - 1];
    const latestTime = latestCandle?.time ?? null;
    const previousBarTime = candles[candles.length - 2]?.time ?? null;
    const barDuration = latestTime && previousBarTime ? Math.max(1, latestTime - previousBarTime) : 60;

    candleSeriesRef.current.setData(candles);

    if (!hasInitialFitRef.current) {
      chartRef.current?.timeScale().fitContent();
      hasInitialFitRef.current = true;
    } else if (previousLastTime && latestTime && latestTime > previousLastTime) {
      const timeScale = chartRef.current?.timeScale();
      const visibleRange = timeScale?.getVisibleRange();

      if (timeScale && visibleRange) {
        const nearRightEdge = visibleRange.to >= (previousLastTime - barDuration * 2);
        if (nearRightEdge) {
          const shift = latestTime - previousLastTime;
          animateVisibleRange(
            timeScale,
            visibleRange,
            { from: visibleRange.from + shift, to: visibleRange.to + shift },
            260,
          );
        }
      }
    }

    lastCandleTimeRef.current = latestTime;

    // Reset the preserve flag after update
    preservePositionRef.current = false;
  }, [candles, animateVisibleRange]);

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
    if (chartMarkers?.length > 0) {
      // Sort markers by time (required by lightweight-charts)
      const sortedMarkers = [...chartMarkers].sort((a, b) => a.time - b.time);
      markersRef.current = createSeriesMarkers(candleSeriesRef.current, sortedMarkers);
    }
  }, [chartMarkers]);

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
  const handleToggleMeasurement = useCallback((toolType) => {
    if (measurementHandlerRef.current) {
      const result = measurementHandlerRef.current.toggle(toolType);
      setActiveMeasurementTool(result.enabled ? result.toolType : null);
    }
  }, []);

  // Clear measurement
  const handleClearMeasurement = useCallback(() => {
    if (measurementHandlerRef.current) {
      measurementHandlerRef.current.clear();
    }
  }, []);

  const getToolButtonStyle = useCallback((toolType) => {
    const isActive = activeMeasurementTool === toolType;
    const activeColor = toolType === 'long' ? '#26a69a' : toolType === 'short' ? '#ef5350' : '#2962ff';
    return {
      padding: '8px 10px',
      backgroundColor: isActive ? `${activeColor}33` : 'rgba(41, 98, 255, 0.3)',
      color: isActive ? activeColor : '#fff',
      border: isActive ? `2px solid ${activeColor}` : '1px solid rgba(41, 98, 255, 0.5)',
      borderRadius: '4px',
      cursor: 'pointer',
      fontSize: '12px',
      fontWeight: 'bold',
      minWidth: '56px',
    };
  }, [activeMeasurementTool]);

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
          onClick={() => handleToggleMeasurement('ruler')}
          style={{
            ...getToolButtonStyle('ruler'),
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
          }}
          title="Draw ruler measurement"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21.3 8.7l-6-6-12 12 6 6 12-12z"/>
            <path d="M8 16l2-2"/>
            <path d="M6.5 13.5l2-2"/>
            <path d="M11 18.5l2-2"/>
            <path d="M13.5 16l2-2"/>
          </svg>
          Ruler
        </button>
        <button
          onClick={() => handleToggleMeasurement('long')}
          style={getToolButtonStyle('long')}
          title="Draw long position measurement"
        >
          Long
        </button>
        <button
          onClick={() => handleToggleMeasurement('short')}
          style={getToolButtonStyle('short')}
          title="Draw short position measurement"
        >
          Short
        </button>
        {activeMeasurementTool && (
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
