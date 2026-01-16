import { useEffect, useRef, useMemo, useImperativeHandle, forwardRef } from 'react';
import { createChart, CandlestickSeries, HistogramSeries } from 'lightweight-charts';
import { createSeriesMarkers } from 'lightweight-charts';
import { createTradeZones, createOrderBlockRect } from '../plugins/RectanglePrimitive';

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

  // Chart colors
  const colors = useMemo(() => ({
    background: '#131722',
    text: '#d1d4dc',
    grid: 'rgba(54, 60, 78, 0.5)',
    bullish: '#26a69a',
    bearish: '#ef5350',
    crosshair: '#9598a1',
  }), []);

  // Expose scrollToTime method via ref
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
    
    getChart: () => chartRef.current,
  }), []);

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
        mode: 1, // Normal crosshair
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
        axisPressedMouseMove: true,
        mouseWheel: true,
        pinch: true,
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

    return () => {
      window.removeEventListener('resize', handleResize);
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
        const obRect = createOrderBlockRect(ob, 15); // 15 bars length
        candleSeriesRef.current.attachPrimitive(obRect);
        primitivesRef.current.push(obRect);
      });
    }
  }, [trades, orderBlocks, showOrderBlocks]);

  return (
    <div 
      ref={chartContainerRef} 
      style={{ 
        width: '100%', 
        height: '100%',
        minHeight: '500px',
      }}
    />
  );
});

export default TradingChart;
