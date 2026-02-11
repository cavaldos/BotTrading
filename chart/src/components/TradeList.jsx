import { useState, useEffect, useRef, useCallback } from 'react';
import './TradeList.css';

/**
 * TradeList component - displays list of trades
 * Click on a trade to scroll chart to that position
 * Arrow Up/Down to navigate and focus trades
 */
export default function TradeList({ trades, onTradeClick, selectedTradeId }) {
  const [isCollapsed, setIsCollapsed] = useState(false);
  const listRef = useRef(null);
  const itemRefs = useRef([]);

  const selectedIndex = trades?.findIndex(t => t.id === selectedTradeId) ?? -1;

  const navigateTo = useCallback((index) => {
    if (!trades || trades.length === 0) return;
    const clamped = Math.max(0, Math.min(index, trades.length - 1));
    onTradeClick(trades[clamped]);
  }, [trades, onTradeClick]);

  useEffect(() => {
    const handleKeyDown = (e) => {
      if (!trades || trades.length === 0 || isCollapsed) return;

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        navigateTo(selectedIndex < 0 ? 0 : selectedIndex + 1);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        navigateTo(selectedIndex < 0 ? 0 : selectedIndex - 1);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [trades, selectedIndex, isCollapsed, navigateTo]);

  useEffect(() => {
    if (selectedIndex >= 0 && itemRefs.current[selectedIndex]) {
      itemRefs.current[selectedIndex].scrollIntoView({
        block: 'nearest',
        behavior: 'smooth',
      });
    }
  }, [selectedIndex]);

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
        <div className="trade-list-items" ref={listRef}>
          {trades.map((trade, index) => {
            const isLong = trade.type === 'LONG';
            const isWin = trade.result === 'Win';
            const isSelected = selectedTradeId === trade.id;
            
            return (
              <div
                key={trade.id}
                ref={el => itemRefs.current[index] = el}
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
