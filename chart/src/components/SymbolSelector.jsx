import './SymbolSelector.css';

/**
 * SymbolSelector - Dropdown to select trading symbol
 * @param {Object} props
 * @param {Array} props.symbols - List of available symbols
 * @param {string} props.selectedSymbol - Currently selected symbol ID
 * @param {Function} props.onSymbolChange - Callback when symbol changes
 * @param {boolean} props.loading - Loading state
 */
function SymbolSelector({ symbols, selectedSymbol, onSymbolChange, loading }) {
  if (loading) {
    return (
      <div className="symbol-selector">
        <select disabled className="symbol-select loading">
          <option>Loading...</option>
        </select>
      </div>
    );
  }

  if (!symbols || symbols.length === 0) {
    return (
      <div className="symbol-selector">
        <select disabled className="symbol-select">
          <option>No symbols</option>
        </select>
      </div>
    );
  }

  const handleChange = (e) => {
    onSymbolChange(e.target.value);
  };

  // Find selected symbol info for display
  const selectedInfo = symbols.find(s => s.id === selectedSymbol);

  return (
    <div className="symbol-selector">
      <select 
        className="symbol-select"
        value={selectedSymbol}
        onChange={handleChange}
      >
        {symbols.map((sym) => (
          <option key={sym.id} value={sym.id}>
            {sym.symbol} {sym.timeframe} ({sym.trades} trades, {sym.winRate}%)
          </option>
        ))}
      </select>
      
      {selectedInfo && (
        <span className="symbol-info">
          <span className="timeframe-badge">{selectedInfo.timeframe}</span>
        </span>
      )}
    </div>
  );
}

export default SymbolSelector;
