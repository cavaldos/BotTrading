import { useEffect, useRef } from 'react';
import { ChartEngine } from './ChartEngine';

/**
 * Hosts one ChartEngine. The engine is created once; callbacks are read
 * through a ref so they can change without recreating the chart.
 */
export default function ChartView({ onEngine, handlers, children }) {
  const hostRef = useRef(null);
  const handlersRef = useRef(handlers);

  useEffect(() => {
    handlersRef.current = handlers;
  });

  useEffect(() => {
    const call = (name) => (...args) => handlersRef.current?.[name]?.(...args);
    const engine = new ChartEngine(hostRef.current, {
      onCrosshair: call('onCrosshair'),
      onTradeClick: call('onTradeClick'),
      onDrawingChange: call('onDrawingChange'),
      onDrawingSelect: call('onDrawingSelect'),
      onToolChange: call('onToolChange'),
      onTextEdit: call('onTextEdit'),
      onPanesChange: call('onPanesChange'),
      onOrderDrag: call('onOrderDrag'),
      onChartMenu: call('onChartMenu'),
    });
    onEngine(engine);
    return () => {
      onEngine(null);
      engine.destroy();
    };
  }, [onEngine]);

  return (
    <div className="tv-panel tv-chart-panel">
      <div ref={hostRef} className="tv-chart-host" />
      {children}
    </div>
  );
}
