import './TradeFilter.css';

/**
 * TradeFilter component for filtering trades by result
 * @param {Object} props
 * @param {string} props.filter - Current filter: 'all', 'win', 'loss'
 * @param {function} props.onFilterChange - Callback when filter changes
 * @param {Object} props.summary - Trade summary stats
 */
export default function TradeFilter({ filter, onFilterChange, summary }) {
  const filters = [
    { id: 'all', label: 'All Trades', count: summary?.total || 0 },
    { id: 'win', label: 'Wins', count: summary?.wins || 0, color: '#26a69a' },
    { id: 'loss', label: 'Losses', count: summary?.losses || 0, color: '#ef5350' },
  ];

  return (
    <div className="trade-filter">
      <div className="filter-buttons">
        {filters.map(f => (
          <button
            key={f.id}
            className={`filter-btn ${filter === f.id ? 'active' : ''}`}
            onClick={() => onFilterChange(f.id)}
            style={filter === f.id && f.color ? { borderColor: f.color } : {}}
          >
            <span className="filter-label">{f.label}</span>
            <span 
              className="filter-count"
              style={f.color ? { color: f.color } : {}}
            >
              {f.count}
            </span>
          </button>
        ))}
      </div>
      
      {summary && (
        <div className="summary-stats">
          <div className="stat">
            <span className="stat-label">Win Rate</span>
            <span className="stat-value" style={{ color: '#26a69a' }}>
              {summary.winRate}%
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
