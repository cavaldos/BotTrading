/**
 * Measurement Plugin for Lightweight Charts
 * Allows measuring price difference and percentage between two points
 */

export class MeasurementPrimitive {
  constructor() {
    this._chart = null;
    this._series = null;
    this._startPoint = null;
    this._endPoint = null;
    this._isDrawing = false;
    this._isEnabled = false;
    this._paneViews = [new MeasurementPaneView(this)];
  }

  attached(params) {
    this._chart = params.chart;
    this._series = params.series;
  }

  detached() {
    this._chart = null;
    this._series = null;
  }

  paneViews() {
    return this._paneViews;
  }

  updateAllViews() {
    this._paneViews.forEach(view => view.update());
  }

  setEnabled(enabled) {
    this._isEnabled = enabled;
    if (!enabled) {
      this.clear();
    }
  }

  isEnabled() {
    return this._isEnabled;
  }

  startMeasurement(point) {
    if (!this._isEnabled) return;
    this._startPoint = point;
    this._endPoint = point;
    this._isDrawing = true;
  }

  updateMeasurement(point) {
    if (!this._isDrawing || !this._isEnabled) return;
    this._endPoint = point;
    this.updateAllViews();
  }

  endMeasurement(point) {
    if (!this._isDrawing || !this._isEnabled) return;
    this._endPoint = point;
    this._isDrawing = false;
    this.updateAllViews();
  }

  clear() {
    this._startPoint = null;
    this._endPoint = null;
    this._isDrawing = false;
    this.updateAllViews();
  }

  getState() {
    return {
      startPoint: this._startPoint,
      endPoint: this._endPoint,
      isDrawing: this._isDrawing,
      isEnabled: this._isEnabled,
    };
  }

  getChart() {
    return this._chart;
  }

  getSeries() {
    return this._series;
  }
}

class MeasurementPaneView {
  constructor(source) {
    this._source = source;
  }

  update() {}

  renderer() {
    return new MeasurementRenderer(this._source);
  }

  zOrder() {
    return 'top';
  }
}

class MeasurementRenderer {
  constructor(source) {
    this._source = source;
  }

  draw(target) {
    const state = this._source.getState();
    const { startPoint, endPoint } = state;

    if (!startPoint || !endPoint) return;

    const chart = this._source.getChart();
    const series = this._source.getSeries();

    if (!chart || !series) return;

    const timeScale = chart.timeScale();

    // Convert time/price to coordinates
    const startX = timeScale.timeToCoordinate(startPoint.time);
    const endX = timeScale.timeToCoordinate(endPoint.time);
    const startY = series.priceToCoordinate(startPoint.price);
    const endY = series.priceToCoordinate(endPoint.price);

    if (startX === null || endX === null || startY === null || endY === null) return;

    // Calculate measurement values
    const priceDiff = endPoint.price - startPoint.price;
    const percentChange = ((priceDiff / startPoint.price) * 100).toFixed(2);
    const timeDiff = endPoint.time - startPoint.time;
    const barsCount = Math.round(timeDiff / 900); // 15min = 900 seconds

    target.useMediaCoordinateSpace(scope => {
      const ctx = scope.context;

      // Draw connecting line
      ctx.beginPath();
      ctx.moveTo(startX, startY);
      ctx.lineTo(endX, endY);
      ctx.strokeStyle = priceDiff >= 0 ? '#26a69a' : '#ef5350';
      ctx.lineWidth = 2;
      ctx.setLineDash([5, 5]);
      ctx.stroke();
      ctx.setLineDash([]);

      // Draw start point
      ctx.beginPath();
      ctx.arc(startX, startY, 6, 0, 2 * Math.PI);
      ctx.fillStyle = '#2962ff';
      ctx.fill();
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 2;
      ctx.stroke();

      // Draw end point
      ctx.beginPath();
      ctx.arc(endX, endY, 6, 0, 2 * Math.PI);
      ctx.fillStyle = priceDiff >= 0 ? '#26a69a' : '#ef5350';
      ctx.fill();
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 2;
      ctx.stroke();

      // Draw measurement box
      const boxWidth = 150;
      const boxHeight = 70;
      const boxX = (startX + endX) / 2 - boxWidth / 2;
      const boxY = Math.min(startY, endY) - boxHeight - 10;

      // Box background
      ctx.beginPath();
      ctx.roundRect(boxX, boxY, boxWidth, boxHeight, 6);
      ctx.fillStyle = 'rgba(19, 23, 34, 0.95)';
      ctx.fill();
      ctx.strokeStyle = priceDiff >= 0 ? '#26a69a' : '#ef5350';
      ctx.lineWidth = 2;
      ctx.stroke();

      // Text
      ctx.font = 'bold 14px Arial';
      ctx.textAlign = 'center';
      ctx.fillStyle = priceDiff >= 0 ? '#26a69a' : '#ef5350';
      
      // Percentage
      const sign = priceDiff >= 0 ? '+' : '';
      ctx.fillText(`${sign}${percentChange}%`, boxX + boxWidth / 2, boxY + 22);

      // Price difference
      ctx.font = '12px Arial';
      ctx.fillStyle = '#d1d4dc';
      ctx.fillText(`${sign}${priceDiff.toFixed(2)}`, boxX + boxWidth / 2, boxY + 42);

      // Bars count
      ctx.fillText(`${Math.abs(barsCount)} bars`, boxX + boxWidth / 2, boxY + 58);

      // Horizontal and vertical reference lines
      ctx.beginPath();
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = 'rgba(150, 150, 150, 0.5)';
      ctx.lineWidth = 1;

      // Horizontal line from start to end X
      ctx.moveTo(startX, startY);
      ctx.lineTo(endX, startY);
      
      // Vertical line from startY to endY at endX
      ctx.moveTo(endX, startY);
      ctx.lineTo(endX, endY);
      ctx.stroke();
      ctx.setLineDash([]);
    });
  }
}

/**
 * Create measurement tool handler
 * @param {Object} chart - Lightweight chart instance
 * @param {Object} series - Candle series
 * @param {MeasurementPrimitive} primitive - Measurement primitive instance
 * @returns {Object} Handler methods
 */
export function createMeasurementHandler(chart, series, primitive) {
  const container = chart.chartElement();
  
  // Store original scroll/scale options to restore later
  let originalOptions = null;
  
  const getPointFromEvent = (e) => {
    const rect = container.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    const timeScale = chart.timeScale();
    const time = timeScale.coordinateToTime(x);
    const price = series.coordinateToPrice(y);

    if (time === null || price === null) return null;

    return { time, price, x, y };
  };

  const handleMouseDown = (e) => {
    if (!primitive.isEnabled()) return;
    if (e.button !== 0) return; // Left click only
    
    e.preventDefault();
    e.stopPropagation();
    
    const point = getPointFromEvent(e);
    if (point) {
      primitive.startMeasurement(point);
    }
  };

  const handleMouseMove = (e) => {
    if (!primitive.isEnabled()) return;
    
    const point = getPointFromEvent(e);
    if (point) {
      primitive.updateMeasurement(point);
    }
  };

  const handleMouseUp = (e) => {
    if (!primitive.isEnabled()) return;
    
    const point = getPointFromEvent(e);
    if (point) {
      primitive.endMeasurement(point);
    }
  };

  const enable = () => {
    // Save current options and disable chart scrolling/panning
    originalOptions = {
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: true,
      },
      handleScale: {
        axisPressedMouseMove: { time: true, price: true },
        mouseWheel: true,
        pinch: true,
      },
    };
    
    // Disable all scroll and scale interactions
    chart.applyOptions({
      handleScroll: {
        mouseWheel: false,
        pressedMouseMove: false,
        horzTouchDrag: false,
        vertTouchDrag: false,
      },
      handleScale: {
        axisPressedMouseMove: { time: false, price: false },
        mouseWheel: false,
        pinch: false,
      },
    });
    
    container.addEventListener('mousedown', handleMouseDown, { capture: true });
    container.addEventListener('mousemove', handleMouseMove);
    container.addEventListener('mouseup', handleMouseUp);
    container.style.cursor = 'crosshair';
    primitive.setEnabled(true);
  };

  const disable = () => {
    // Restore original scroll/scale options
    if (originalOptions) {
      chart.applyOptions(originalOptions);
    }
    
    container.removeEventListener('mousedown', handleMouseDown, { capture: true });
    container.removeEventListener('mousemove', handleMouseMove);
    container.removeEventListener('mouseup', handleMouseUp);
    container.style.cursor = 'default';
    primitive.setEnabled(false);
  };

  const toggle = () => {
    if (primitive.isEnabled()) {
      disable();
    } else {
      enable();
    }
    return primitive.isEnabled();
  };

  const clear = () => {
    primitive.clear();
  };

  return {
    enable,
    disable,
    toggle,
    clear,
    isEnabled: () => primitive.isEnabled(),
  };
}

export default MeasurementPrimitive;
