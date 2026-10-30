import { IconBrush, IconWatchlist, IconTrades, IconOrder, IconStrategy, IconExchange } from './icons';

/**
 * Phone bottom bar. Replaces the left drawing toolbar and the right panel strip,
 * which would take a fifth of a phone's width, so the chart gets the full width.
 */
export default function MobileNav({ toolsOpen, toolActive, onTools, panel, onPanel, testerOpen, onTester, pendingCount }) {
  const item = (key, label, Icon, pressed, onClick, extra = {}) => (
    <button
      key={key}
      type="button"
      className={`tv-mnav-btn ${extra.dot ? 'has-dot' : ''}`}
      aria-pressed={pressed}
      onClick={onClick}
      aria-label={extra.ariaLabel}
    >
      <span className="tv-mnav-icon">
        <Icon size={22} />
        {extra.badge > 0 && <span className="tv-strip-badge" aria-hidden="true">{extra.badge}</span>}
      </span>
      <span className="tv-mnav-label">{label}</span>
    </button>
  );

  return (
    <nav className="tv-panel tv-mnav" aria-label="Panels">
      {item('draw', 'Draw', IconBrush, toolsOpen, onTools, { dot: toolActive && !toolsOpen })}
      {item('watchlist', 'Watchlist', IconWatchlist, panel === 'watchlist', () => onPanel('watchlist'))}
      {item('trades', 'Trades', IconTrades, panel === 'trades', () => onPanel('trades'))}
      {item('trade', 'Trade', IconOrder, panel === 'trade', () => onPanel('trade'), {
        badge: pendingCount,
        ariaLabel: pendingCount ? `Trade, ${pendingCount} signal${pendingCount > 1 ? 's' : ''} waiting` : undefined,
      })}
      {item('orders', 'Orders', IconExchange, panel === 'orders', () => onPanel('orders'))}
      {item('tester', 'Tester', IconStrategy, testerOpen, onTester)}
    </nav>
  );
}
