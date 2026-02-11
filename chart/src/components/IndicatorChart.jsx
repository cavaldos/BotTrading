import { useEffect, useRef, useMemo, useImperativeHandle, forwardRef, useState, useCallback } from 'react';
import { createChart, CandlestickSeries, HistogramSeries } from 'lightweight-charts';
import { RectanglePrimitive } from '../plugins/RectanglePrimitive';
import { createStructureLine, createEqhEqlLine } from '../plugins/LinePrimitive';
import { createFibRetracement } from '../plugins/FibRetracementPrimitive';
import { MeasurementPrimitive, createMeasurementHandler } from '../plugins/MeasurementPlugin';

/**
 * IndicatorChart component - TradingView chart with all SMC indicators
 * Renders: Candles, Volume, BOS, CHoCH, Order Blocks, FVG, EQH/EQL,
 *          Imbalance, Breaker Blocks, Mitigation Blocks, Fibonacci Retracement
 */
const IndicatorChart = forwardRef(function IndicatorChart({
  candles,
  volume,
  data = {},       // Full indicator data object
  visibility = {}, // Which indicators are visible
  autoFollowLatest = true,
}, ref) {
  const chartContainerRef = useRef(null);
  const chartRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const volumeSeriesRef = useRef(null);
  const primitivesRef = useRef([]);
  const measurementRef = useRef(null);
  const measurementHandlerRef = useRef(null);
  const hasInitialFitRef = useRef(false);
  const lastCandleTimeRef = useRef(null);
  const scrollAnimationRef = useRef(null);
  const [activeMeasurementTool, setActiveMeasurementTool] = useState(null);

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

  // Derive bar duration from candle data
  const barDuration = useMemo(() => {
    if (!candles || candles.length < 2) return 900;
    return candles[1].time - candles[0].time;
  }, [candles]);

  // Expose methods via ref
  useImperativeHandle(ref, () => ({
    scrollToTime: (time, barsAround = 50) => {
      if (!chartRef.current) return;
      const timeScale = chartRef.current.timeScale();
      const from = time - (barsAround * barDuration);
      const to = time + (barsAround * barDuration);
      timeScale.setVisibleRange({ from, to });
    },
    focusToLatestCandle: () => {
      if (!chartRef.current || !candleSeriesRef.current || !candles?.length) return;

      const timeScale = chartRef.current.timeScale();
      const priceScale = candleSeriesRef.current.priceScale();
      const latestTime = candles[candles.length - 1]?.time;
      const latest = candles[candles.length - 1];
      const prevTime = candles[candles.length - 2]?.time;
      if (!latestTime || !latest) return;

      const step = prevTime ? Math.max(1, latestTime - prevTime) : barDuration;
      const currentRange = timeScale.getVisibleRange();
      const currentSpan = currentRange
        ? Math.max(currentRange.to - currentRange.from, 30 * step)
        : 160 * step;
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
  }), [activeMeasurementTool, barDuration, candles, animateVisibleRange]);

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
        mode: 0,
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
        autoScale: false,
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
        axisPressedMouseMove: { time: true, price: true },
        axisDoubleClickReset: { time: true, price: true },
        mouseWheel: true,
        pinch: true,
      },
    });

    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({
          width: chartContainerRef.current.clientWidth,
          height: chartContainerRef.current.clientHeight,
        });
      }
    };

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

    // Create volume series
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: '',
    });

    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
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
    const barDurationStep = latestTime && previousBarTime ? Math.max(1, latestTime - previousBarTime) : 60;

    candleSeriesRef.current.setData(candles);

    if (!hasInitialFitRef.current) {
      chartRef.current?.timeScale().fitContent();
      hasInitialFitRef.current = true;
    } else if (autoFollowLatest && previousLastTime && latestTime && latestTime > previousLastTime) {
      const timeScale = chartRef.current?.timeScale();
      const visibleRange = timeScale?.getVisibleRange();

      if (timeScale && visibleRange) {
        const nearRightEdge = visibleRange.to >= (previousLastTime - barDurationStep * 2);
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
  }, [candles, autoFollowLatest, animateVisibleRange]);

  // Update volume data
  useEffect(() => {
    if (!volumeSeriesRef.current || !volume?.length) return;
    volumeSeriesRef.current.setData(volume);
  }, [volume]);

  // Update all indicator primitives
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

    const newPrimitives = [];

    // --- BOS lines ---
    if (visibility.bos && data.bos?.length > 0) {
      data.bos.forEach(item => {
        newPrimitives.push(createStructureLine(item));
      });
    }

    // --- CHoCH lines ---
    if (visibility.choch && data.choch?.length > 0) {
      data.choch.forEach(item => {
        newPrimitives.push(createStructureLine(item));
      });
    }

    // --- Order Blocks ---
    if (visibility.orderBlocks && data.orderBlocks?.length > 0) {
      data.orderBlocks.forEach(ob => {
        const endTime = ob.endTime || (ob.startTime + 150 * barDuration);
        newPrimitives.push(new RectanglePrimitive({
          startTime: ob.startTime,
          endTime: endTime,
          lowPrice: ob.low,
          highPrice: ob.high,
          fillColor: ob.color || (ob.bias === 'BULLISH'
            ? 'rgba(38, 166, 154, 0.15)'
            : 'rgba(239, 83, 80, 0.15)'),
          borderColor: null,
          borderWidth: 0,
          label: `OB ${ob.structureType || ''}`,
          labelColor: ob.bias === 'BULLISH' ? '#26a69a' : '#ef5350',
        }));
      });
    }

    // --- FVG zones ---
    if (visibility.fvg && data.fvg?.length > 0) {
      // Filter: only show unfilled FVGs to reduce clutter
      const fvgItems = data.fvg.filter(f => !f.isFilled);
      fvgItems.forEach(fvg => {
        const endTime = fvg.time + 80 * barDuration;
        newPrimitives.push(new RectanglePrimitive({
          startTime: fvg.time,
          endTime: endTime,
          lowPrice: fvg.bottom,
          highPrice: fvg.top,
          fillColor: fvg.color || (fvg.bias === 'BULLISH'
            ? 'rgba(38, 166, 154, 0.12)'
            : 'rgba(239, 83, 80, 0.12)'),
          borderColor: null,
          label: 'FVG',
          labelColor: fvg.bias === 'BULLISH'
            ? 'rgba(38, 166, 154, 0.6)'
            : 'rgba(239, 83, 80, 0.6)',
        }));
      });
    }

    // --- EQH/EQL lines ---
    if (visibility.eqhEql && data.eqhEql?.length > 0) {
      // Limit to prevent clutter - show most recent
      const eqItems = data.eqhEql.slice(-200);
      eqItems.forEach(item => {
        newPrimitives.push(createEqhEqlLine(item));
      });
    }

    // --- Imbalance zones ---
    if (visibility.imbalance && data.imbalance?.length > 0) {
      const imbItems = data.imbalance.filter(i => !i.isFilled);
      imbItems.forEach(imb => {
        const endTime = imb.time + 60 * barDuration;
        newPrimitives.push(new RectanglePrimitive({
          startTime: imb.time,
          endTime: endTime,
          lowPrice: imb.low,
          highPrice: imb.high,
          fillColor: imb.color || 'rgba(0, 188, 212, 0.15)',
          borderColor: 'rgba(0, 188, 212, 0.4)',
          borderWidth: 1,
          label: `IMB ${imb.strength?.toFixed(1) || ''}`,
          labelColor: 'rgba(0, 188, 212, 0.8)',
        }));
      });
    }

    // --- Breaker Blocks ---
    if (visibility.breakerBlocks && data.breakerBlocks?.length > 0) {
      data.breakerBlocks.forEach(bb => {
        const endTime = bb.endTime || (bb.startTime + 100 * barDuration);
        newPrimitives.push(new RectanglePrimitive({
          startTime: bb.startTime,
          endTime: endTime,
          lowPrice: bb.low,
          highPrice: bb.high,
          fillColor: bb.color || (bb.bias === 'BULLISH'
            ? 'rgba(38, 166, 154, 0.2)'
            : 'rgba(244, 67, 54, 0.2)'),
          borderColor: bb.borderColor || (bb.bias === 'BULLISH'
            ? 'rgba(38, 166, 154, 0.7)'
            : 'rgba(244, 67, 54, 0.7)'),
          borderWidth: 2,
          label: 'BB',
          labelColor: bb.bias === 'BULLISH' ? '#26a69a' : '#f44336',
        }));
      });
    }

    // --- Mitigation Blocks ---
    if (visibility.mitigationBlocks && data.mitigationBlocks?.length > 0) {
      data.mitigationBlocks.forEach(mb => {
        const endTime = mb.endTime || (mb.startTime + 80 * barDuration);
        newPrimitives.push(new RectanglePrimitive({
          startTime: mb.startTime,
          endTime: endTime,
          lowPrice: mb.low,
          highPrice: mb.high,
          fillColor: mb.color || (mb.bias === 'BULLISH'
            ? 'rgba(38, 166, 154, 0.1)'
            : 'rgba(239, 83, 80, 0.1)'),
          borderColor: mb.borderColor || (mb.bias === 'BULLISH'
            ? 'rgba(38, 166, 154, 0.4)'
            : 'rgba(239, 83, 80, 0.4)'),
          borderWidth: 1,
          label: `MB ${mb.mitigationPercent?.toFixed(0) || ''}%`,
          labelColor: mb.bias === 'BULLISH'
            ? 'rgba(38, 166, 154, 0.7)'
            : 'rgba(239, 83, 80, 0.7)',
        }));
      });
    }

    // --- Fibonacci Retracements ---
    if (visibility.retracement && data.retracement?.length > 0) {
      // Only show recent retracements to prevent clutter
      const fibItems = data.retracement.slice(-15);
      fibItems.forEach(item => {
        newPrimitives.push(createFibRetracement(item));
      });
    }

    // --- Swings (HH/HL/LH/LL) ---
    if (visibility.swing && data.swing?.length > 0) {
      // Show recent swings and avoid clutter; we'll draw a tiny labeled rect at the pivot price
      const items = data.swing.slice(-500);
      items.forEach(s => {
        // create a small box spanning one bar and small price range
        const start = s.time;
        const end = s.time + barDuration; // 1 bar width
        const pad = Math.abs((s.price || 0) * 0.002) || 0.0001; // small price pad

        newPrimitives.push(new RectanglePrimitive({
          startTime: start,
          endTime: end,
          lowPrice: s.price - pad,
          highPrice: s.price + pad,
          fillColor: 'rgba(0,0,0,0)',
          borderColor: null,
          borderWidth: 0,
          label: s.type,
          labelColor: s.isHigh ? '#ff5252' : '#00bfa5',
        }));
      });
    }

    // Attach all primitives
    newPrimitives.forEach(p => {
      candleSeriesRef.current.attachPrimitive(p);
    });
    primitivesRef.current = newPrimitives;

  }, [data, visibility, barDuration]);

  // Toggle measurement mode
  const handleToggleMeasurement = useCallback((toolType) => {
    if (measurementHandlerRef.current) {
      const result = measurementHandlerRef.current.toggle(toolType);
      setActiveMeasurementTool(result.enabled ? result.toolType : null);
    }
  }, []);

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
            <path d="M21.3 8.7l-6-6-12 12 6 6 12-12z" />
            <path d="M8 16l2-2" />
            <path d="M6.5 13.5l2-2" />
            <path d="M11 18.5l2-2" />
            <path d="M13.5 16l2-2" />
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

export default IndicatorChart;
