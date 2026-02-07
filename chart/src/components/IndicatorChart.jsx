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
}, ref) {
  const chartContainerRef = useRef(null);
  const chartRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const volumeSeriesRef = useRef(null);
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
    getChart: () => chartRef.current,
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
  }), [isMeasuring, barDuration]);

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

    // Attach all primitives
    newPrimitives.forEach(p => {
      candleSeriesRef.current.attachPrimitive(p);
    });
    primitivesRef.current = newPrimitives;

  }, [data, visibility, barDuration]);

  // Toggle measurement mode
  const handleToggleMeasurement = useCallback(() => {
    if (measurementHandlerRef.current) {
      const isEnabled = measurementHandlerRef.current.toggle();
      setIsMeasuring(isEnabled);
    }
  }, []);

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

export default IndicatorChart;
