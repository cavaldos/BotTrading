import { Navigate } from 'react-router-dom';

// Old pages now open the unified chart with the matching data source.
export function BacktestRedirect() {
  return <Navigate to="/?source=backtest" replace />;
}

export function LiveRedirect() {
  return <Navigate to="/?source=live" replace />;
}

export function IndicatorsRedirect() {
  return <Navigate to="/?source=indicators" replace />;
}
