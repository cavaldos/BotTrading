/**
 * Rectangle Primitive Plugin for Lightweight Charts
 * Draws filled rectangles on the chart (for Order Blocks and Trade Zones)
 */

/**
 * Create a rectangle primitive that can be attached to a series
 * @param {Object} options - Rectangle options
 * @param {number} options.startTime - Start time (unix timestamp)
 * @param {number} options.endTime - End time (unix timestamp)
 * @param {number} options.lowPrice - Low price
 * @param {number} options.highPrice - High price
 * @param {string} options.fillColor - Fill color (rgba)
 * @param {string} options.borderColor - Border color (optional)
 * @param {number} options.borderWidth - Border width (optional)
 * @param {string} options.label - Text label to display inside rectangle (optional)
 * @param {string} options.labelColor - Label text color (optional)
 */
export class RectanglePrimitive {
  constructor(options) {
    this._options = {
      startTime: options.startTime,
      endTime: options.endTime,
      lowPrice: options.lowPrice,
      highPrice: options.highPrice,
      fillColor: options.fillColor || 'rgba(100, 200, 50, 0.3)',
      borderColor: options.borderColor || null,
      borderWidth: options.borderWidth || 1,
      label: options.label || null,
      labelColor: options.labelColor || '#ffffff',
    };
    this._chart = null;
    this._series = null;
  }

  attached(params) {
    this._chart = params.chart;
    this._series = params.series;
  }

  detached() {
    this._chart = null;
    this._series = null;
  }

  updateAllViews() {
    // Called when the chart needs to update
  }

  paneViews() {
    return [new RectanglePaneView(this)];
  }

  getOptions() {
    return this._options;
  }

  getChart() {
    return this._chart;
  }

  getSeries() {
    return this._series;
  }
}

class RectanglePaneView {
  constructor(source) {
    this._source = source;
  }

  update() {}

  renderer() {
    return new RectangleRenderer(this._source);
  }

  zOrder() {
    return 'bottom'; // Draw below the candles
  }
}

class RectangleRenderer {
  constructor(source) {
    this._source = source;
  }

  draw(target) {
    const options = this._source.getOptions();
    const chart = this._source.getChart();
    const series = this._source.getSeries();

    if (!chart || !series) return;

    const timeScale = chart.timeScale();
    
    // Convert time to x coordinates
    const startX = timeScale.timeToCoordinate(options.startTime);
    const endX = timeScale.timeToCoordinate(options.endTime);
    
    if (startX === null || endX === null) return;

    // Convert price to y coordinates
    const highY = series.priceToCoordinate(options.highPrice);
    const lowY = series.priceToCoordinate(options.lowPrice);

    if (highY === null || lowY === null) return;

    target.useMediaCoordinateSpace(scope => {
      const ctx = scope.context;
      
      const x = Math.min(startX, endX);
      const y = Math.min(highY, lowY);
      const width = Math.abs(endX - startX);
      const height = Math.abs(lowY - highY);

      // Draw filled rectangle
      ctx.beginPath();
      ctx.rect(x, y, width, height);
      ctx.fillStyle = options.fillColor;
      ctx.fill();

      // Draw border if specified
      if (options.borderColor) {
        ctx.strokeStyle = options.borderColor;
        ctx.lineWidth = options.borderWidth;
        ctx.stroke();
      }

      // Draw label if specified
      if (options.label) {
        const fontSize = 11;
        ctx.font = `bold ${fontSize}px Arial`;
        ctx.fillStyle = options.labelColor;
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        
        // Position label at the left side, vertically centered
        const labelX = x + 5;
        const labelY = y + height / 2;
        
        // Draw text shadow for better visibility
        ctx.shadowColor = 'rgba(0, 0, 0, 0.8)';
        ctx.shadowBlur = 3;
        ctx.shadowOffsetX = 1;
        ctx.shadowOffsetY = 1;
        
        ctx.fillText(options.label, labelX, labelY);
        
        // Reset shadow
        ctx.shadowColor = 'transparent';
        ctx.shadowBlur = 0;
        ctx.shadowOffsetX = 0;
        ctx.shadowOffsetY = 0;
      }
    });
  }
}

/**
 * Create multiple rectangle primitives for trade zones
 * @param {Object} trade - Trade object
 * @returns {Array} Array of RectanglePrimitive instances
 */
export function createTradeZones(trade) {
  const isLong = trade.type === 'LONG';
  const rectangles = [];

  // Calculate TP% and SL%
  const tpPercent = Math.abs((trade.takeProfit - trade.entryPrice) / trade.entryPrice * 100);
  const slPercent = Math.abs((trade.stopLoss - trade.entryPrice) / trade.entryPrice * 100);

  // TP Zone (green)
  rectangles.push(new RectanglePrimitive({
    startTime: trade.entryTime,
    endTime: trade.exitTime,
    lowPrice: isLong ? trade.entryPrice : trade.takeProfit,
    highPrice: isLong ? trade.takeProfit : trade.entryPrice,
    fillColor: 'rgba(38, 166, 154, 0.25)',
    borderColor: 'rgba(38, 166, 154, 0.6)',
    borderWidth: 1,
    label: `TP: ${tpPercent.toFixed(2)}%`,
    labelColor: '#26a69a',
  }));

  // SL Zone (red)
  rectangles.push(new RectanglePrimitive({
    startTime: trade.entryTime,
    endTime: trade.exitTime,
    lowPrice: isLong ? trade.stopLoss : trade.entryPrice,
    highPrice: isLong ? trade.entryPrice : trade.stopLoss,
    fillColor: 'rgba(239, 83, 80, 0.25)',
    borderColor: 'rgba(239, 83, 80, 0.6)',
    borderWidth: 1,
    label: `SL: ${slPercent.toFixed(2)}%`,
    labelColor: '#ef5350',
  }));

  return rectangles;
}

/**
 * Create rectangle primitive for Order Block
 * @param {Object} ob - Order Block object
 * @param {number} numBars - Number of bars to extend (default: 20)
 * @returns {RectanglePrimitive}
 */
export function createOrderBlockRect(ob, numBars = 20) {
  const isBullish = ob.bias === 'BULLISH';
  
  // Calculate end time (start + numBars * timeframe)
  // For 15m = 900 seconds
  const barDuration = 900; // 15 minutes in seconds
  const endTime = ob.startTime + (numBars * barDuration);

  return new RectanglePrimitive({
    startTime: ob.startTime,
    endTime: endTime, // Extend full length for better visibility
    lowPrice: ob.low,
    highPrice: ob.high,
    fillColor: isBullish 
      ? 'rgba(38, 166, 154, 0.15)' 
      : 'rgba(239, 83, 80, 0.15)',
    borderColor: isBullish 
      ? 'rgba(38, 166, 154, 0.5)' 
      : 'rgba(239, 83, 80, 0.5)',
    borderWidth: 1,
  });
}

export default RectanglePrimitive;
