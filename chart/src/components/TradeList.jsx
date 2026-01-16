import { useState } from 'react';
import './TradeList.css';

/**
 * TradeList component - displays list of trades
 * Click on a trade to scroll chart to that position
 */
export default function TradeList({ trades, onTradeClick, selectedTradeId }) {
  const [isCollapsed, setIsCollapsed] = useState(false);

  if (!trades || trades.length === 0) {
    return null;
  }

  return (
    <div className={`trade-list ${isCollapsed ? 'collapsed' : ''}`}>
      <div className="trade-list-header">
        <span className="trade-list-title">Trades</span>
        <button 
          className="toggle-btn"
          onClick={() => setIsCollapsed(!isCollapsed)}
          title={isCollapsed ? 'Expand' : 'Collapse'}
        >
          {isCollapsed ? '◀' : '▶'}
        </button>
      </div>
      
      {!isCollapsed && (
        <div className="trade-list-items">
          {trades.map((trade, index) => {
            const isLong = trade.type === 'LONG';
            const isWin = trade.result === 'Win';
            const isSelected = selectedTradeId === trade.id;
            
            return (
              <div
                key={trade.id}
                className={`trade-item ${isSelected ? 'selected' : ''} ${isWin ? 'win' : 'loss'}`}
                onClick={() => onTradeClick(trade)}
              >
                <span className="trade-number">#{index + 1}</span>
                <span className={`trade-type ${isLong ? 'long' : 'short'}`}>
                  {isLong ? '▲ LONG' : '▼ SHORT'}
                </span>
                <span className={`trade-result ${isWin ? 'win' : 'loss'}`}>
                  {isWin ? 'W' : 'L'}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
