/**
 * IndicatorToggle - Panel with checkboxes to toggle each indicator on/off
 */

const INDICATOR_CONFIG = [
  {
    key: 'bos',
    label: 'BOS',
    description: 'Break of Structure',
    color: 'rgba(38, 166, 154, 0.8)',
    type: 'line',
  },
  {
    key: 'choch',
    label: 'CHoCH',
    description: 'Change of Character',
    color: 'rgba(156, 39, 176, 0.9)',
    type: 'line',
  },
  {
    key: 'orderBlocks',
    label: 'OB',
    description: 'Order Blocks',
    color: 'rgba(38, 166, 154, 0.5)',
    type: 'rect',
  },
  {
    key: 'fvg',
    label: 'FVG',
    description: 'Fair Value Gaps',
    color: 'rgba(100, 181, 246, 0.6)',
    type: 'rect',
  },
  {
    key: 'eqhEql',
    label: 'EQH/EQL',
    description: 'Equal Highs/Lows',
    color: 'rgba(33, 150, 243, 0.7)',
    type: 'line',
  },
  {
    key: 'imbalance',
    label: 'IMB',
    description: 'Imbalance',
    color: 'rgba(0, 188, 212, 0.7)',
    type: 'rect',
  },
  {
    key: 'breakerBlocks',
    label: 'BB',
    description: 'Breaker Blocks',
    color: 'rgba(244, 67, 54, 0.7)',
    type: 'rect',
  },
  {
    key: 'mitigationBlocks',
    label: 'MB',
    description: 'Mitigation Blocks',
    color: 'rgba(255, 152, 0, 0.7)',
    type: 'rect',
  },
  {
    key: 'retracement',
    label: 'Fib',
    description: 'Fibonacci Retracement',
    color: 'rgba(33, 150, 243, 0.7)',
    type: 'fib',
  },
];

function IndicatorToggle({ visibility, onToggle, counts = {} }) {
  const handleToggleAll = (show) => {
    INDICATOR_CONFIG.forEach(ind => {
      if (visibility[ind.key] !== show) {
        onToggle(ind.key);
      }
    });
  };

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: '6px',
      padding: '6px 12px',
      backgroundColor: 'rgba(19, 23, 34, 0.95)',
      borderBottom: '1px solid rgba(54, 60, 78, 0.5)',
      flexWrap: 'wrap',
      fontSize: '12px',
    }}>
      <span style={{ color: '#787b86', fontWeight: 'bold', marginRight: '4px' }}>
        Indicators:
      </span>

      {/* Show All / Hide All */}
      <button
        onClick={() => handleToggleAll(true)}
        style={{
          padding: '2px 8px',
          backgroundColor: 'rgba(41, 98, 255, 0.2)',
          color: '#2962ff',
          border: '1px solid rgba(41, 98, 255, 0.3)',
          borderRadius: '3px',
          cursor: 'pointer',
          fontSize: '11px',
        }}
      >
        All
      </button>
      <button
        onClick={() => handleToggleAll(false)}
        style={{
          padding: '2px 8px',
          backgroundColor: 'rgba(120, 123, 134, 0.15)',
          color: '#787b86',
          border: '1px solid rgba(120, 123, 134, 0.3)',
          borderRadius: '3px',
          cursor: 'pointer',
          fontSize: '11px',
          marginRight: '6px',
        }}
      >
        None
      </button>

      {/* Divider */}
      <div style={{
        width: '1px',
        height: '20px',
        backgroundColor: 'rgba(54, 60, 78, 0.5)',
        marginRight: '4px',
      }} />

      {INDICATOR_CONFIG.map(ind => {
        const isActive = visibility[ind.key];
        const count = counts[ind.key] || 0;

        return (
          <button
            key={ind.key}
            onClick={() => onToggle(ind.key)}
            title={`${ind.description} (${count})`}
            style={{
              padding: '3px 8px',
              backgroundColor: isActive ? 'rgba(255, 255, 255, 0.08)' : 'transparent',
              color: isActive ? ind.color : '#787b86',
              border: `1px solid ${isActive ? ind.color : 'rgba(120, 123, 134, 0.2)'}`,
              borderRadius: '3px',
              cursor: 'pointer',
              fontSize: '11px',
              fontWeight: isActive ? 'bold' : 'normal',
              transition: 'all 0.15s ease',
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              opacity: isActive ? 1 : 0.6,
            }}
          >
            {/* Color dot */}
            <span style={{
              display: 'inline-block',
              width: '6px',
              height: '6px',
              borderRadius: ind.type === 'line' ? '0' : '50%',
              backgroundColor: isActive ? ind.color : '#787b86',
            }} />
            {ind.label}
            {count > 0 && (
              <span style={{
                fontSize: '10px',
                color: isActive ? 'rgba(255,255,255,0.5)' : '#787b86',
              }}>
                {count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

export default IndicatorToggle;
