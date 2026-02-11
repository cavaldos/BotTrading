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
    this._toolType = 'ruler';
    this._position = null;
    this._activeHandle = null;
    this._isDraggingHandle = false;
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

  setToolType(toolType) {
    if (toolType === 'ruler' || toolType === 'long' || toolType === 'short') {
      if (this._toolType !== toolType) {
        this._toolType = toolType;
        this.clear();
      } else {
        this.updateAllViews();
      }
    }
  }

  getToolType() {
    return this._toolType;
  }

  startMeasurement(point) {
    if (!this._isEnabled) return;

    if (this._toolType === 'ruler') {
      this._startPoint = point;
      this._endPoint = point;
      this._isDrawing = true;
      return;
    }

    const hitHandle = this._hitTestPositionHandle(point);
    if (hitHandle) {
      this._activeHandle = hitHandle;
      this._isDraggingHandle = true;
      this.updateAllViews();
      return;
    }

    this._position = this._createPosition(point);
    this._startPoint = point;
    this._endPoint = point;
    this._isDrawing = true;
    this._activeHandle = null;
    this._isDraggingHandle = false;
    this.updateAllViews();
  }

  updateMeasurement(point) {
    if (!this._isEnabled) return;

    if (this._toolType === 'ruler') {
      if (!this._isDrawing) return;
      this._endPoint = point;
      this.updateAllViews();
      return;
    }

    if (this._isDraggingHandle && this._position) {
      this._updatePositionHandle(point);
      this.updateAllViews();
      return;
    }

    if (!this._isDrawing || !this._position) return;
    this._updatePositionFromCursor(point);
    this._endPoint = point;
    this.updateAllViews();
  }

  endMeasurement(point) {
    if (!this._isEnabled) return;

    if (this._toolType === 'ruler') {
      if (!this._isDrawing) return;
      this._endPoint = point;
      this._isDrawing = false;
      this.updateAllViews();
      return;
    }

    if (this._isDraggingHandle && this._position) {
      this._updatePositionHandle(point);
      this._isDraggingHandle = false;
      this._activeHandle = null;
      this.updateAllViews();
      return;
    }

    if (!this._isDrawing || !this._position) return;
    this._updatePositionFromCursor(point);
    this._isDrawing = false;
    this.updateAllViews();
  }

  clear() {
    this._startPoint = null;
    this._endPoint = null;
    this._position = null;
    this._activeHandle = null;
    this._isDraggingHandle = false;
    this._isDrawing = false;
    this.updateAllViews();
  }

  getState() {
    return {
      startPoint: this._startPoint,
      endPoint: this._endPoint,
      position: this._position,
      isDrawing: this._isDrawing,
      isEnabled: this._isEnabled,
      toolType: this._toolType,
      activeHandle: this._activeHandle,
      isDraggingHandle: this._isDraggingHandle,
    };
  }

  _toTimeNumber(value) {
    const time = Number(value);
    return Number.isFinite(time) ? time : 0;
  }

  _getPriceEpsilon(price) {
    return Math.max(Math.abs(Number(price) || 0) * 0.0004, 0.0001);
  }

  _createPosition(point) {
    const isLong = this._toolType === 'long';
    const entryPrice = Number(point.price);
    const entryTime = this._toTimeNumber(point.time);
    const baseRisk = Math.max(Math.abs(entryPrice) * 0.002, 0.0001);

    return {
      isLong,
      entryPrice,
      entryTime,
      endTime: entryTime + 900,
      stopPrice: isLong ? entryPrice - baseRisk : entryPrice + baseRisk,
      targetPrice: isLong ? entryPrice + baseRisk * 2 : entryPrice - baseRisk * 2,
    };
  }

  _updatePositionFromCursor(point) {
    if (!this._position) return;

    const entryPrice = this._position.entryPrice;
    const cursorPrice = Number(point.price);
    const rrRatio = 2;
    this._position.endTime = this._toTimeNumber(point.time);

    const favorableMove = this._position.isLong ? cursorPrice >= entryPrice : cursorPrice <= entryPrice;
    if (favorableMove) {
      this._position.targetPrice = cursorPrice;
      const rewardDistance = Math.abs(this._position.targetPrice - entryPrice);
      const riskDistance = rewardDistance > 0 ? rewardDistance / rrRatio : this._getPriceEpsilon(entryPrice) * 4;
      this._position.stopPrice = this._position.isLong ? entryPrice - riskDistance : entryPrice + riskDistance;
      return;
    }

    this._position.stopPrice = cursorPrice;
    const riskDistance = Math.abs(entryPrice - this._position.stopPrice);
    const rewardDistance = riskDistance > 0 ? riskDistance * rrRatio : this._getPriceEpsilon(entryPrice) * 8;
    this._position.targetPrice = this._position.isLong ? entryPrice + rewardDistance : entryPrice - rewardDistance;
  }

  _updatePositionHandle(point) {
    if (!this._position || !this._activeHandle) return;

    const entryPrice = this._position.entryPrice;
    const epsilon = this._getPriceEpsilon(entryPrice);
    const nextPrice = Number(point.price);
    this._position.endTime = this._toTimeNumber(point.time);

    if (this._activeHandle === 'target') {
      this._position.targetPrice = this._position.isLong
        ? Math.max(nextPrice, entryPrice + epsilon)
        : Math.min(nextPrice, entryPrice - epsilon);
      return;
    }

    if (this._activeHandle === 'stop') {
      this._position.stopPrice = this._position.isLong
        ? Math.min(nextPrice, entryPrice - epsilon)
        : Math.max(nextPrice, entryPrice + epsilon);
    }
  }

  _hitTestPositionHandle(point) {
    if (!this._position || !this._chart || !this._series) return null;
    if (point?.x == null || point?.y == null) return null;

    const timeScale = this._chart.timeScale();
    const anchorX = timeScale.timeToCoordinate(this._position.endTime);
    const tpY = this._series.priceToCoordinate(this._position.targetPrice);
    const slY = this._series.priceToCoordinate(this._position.stopPrice);
    if (anchorX == null || tpY == null || slY == null) return null;

    const radius = 10;
    const distance = (x, y) => Math.hypot(point.x - x, point.y - y);
    if (distance(anchorX, tpY) <= radius) return 'target';
    if (distance(anchorX, slY) <= radius) return 'stop';
    return null;
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
    const {
      startPoint,
      endPoint,
      toolType,
      position,
      activeHandle,
    } = state;

    const chart = this._source.getChart();
    const series = this._source.getSeries();

    if (!chart || !series) return;

    const timeScale = chart.timeScale();

    if (toolType === 'long' || toolType === 'short') {
      if (!position) return;
      const entryX = timeScale.timeToCoordinate(position.entryTime);
      const endX = timeScale.timeToCoordinate(position.endTime);
      const entryY = series.priceToCoordinate(position.entryPrice);
      const stopY = series.priceToCoordinate(position.stopPrice);
      const targetY = series.priceToCoordinate(position.targetPrice);
      if (entryX === null || endX === null || entryY === null || stopY === null || targetY === null) return;

      this._drawPositionTool(target, {
        position,
        entryX,
        endX,
        entryY,
        stopY,
        targetY,
        activeHandle,
      });
      return;
    }

    if (!startPoint || !endPoint) return;

    // Convert time/price to coordinates
    const startX = timeScale.timeToCoordinate(startPoint.time);
    const endX = timeScale.timeToCoordinate(endPoint.time);
    const startY = series.priceToCoordinate(startPoint.price);
    const endY = series.priceToCoordinate(endPoint.price);

    if (startX === null || endX === null || startY === null || endY === null) return;

    this._drawRuler(target, {
      startPoint,
      endPoint,
      startX,
      endX,
      startY,
      endY,
    });
  }

  _drawRuler(target, { startPoint, endPoint, startX, endX, startY, endY }) {
    const priceDiff = endPoint.price - startPoint.price;
    const percentChange = ((priceDiff / startPoint.price) * 100).toFixed(2);
    const timeDiff = Math.abs(endPoint.time - startPoint.time);
    const barsCount = Math.round(timeDiff / 900);

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
      ctx.fillText(`${barsCount} bars`, boxX + boxWidth / 2, boxY + 58);

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

  _drawPositionTool(target, {
    position,
    entryX,
    endX,
    entryY,
    stopY,
    targetY,
    activeHandle,
  }) {
    const { entryPrice, stopPrice, targetPrice, isLong } = position;
    const x = Math.min(entryX, endX);
    const xRight = Math.max(entryX, endX);
    const width = Math.max(Math.abs(endX - entryX), 26);

    const profitColor = 'rgba(38, 166, 154, 0.28)';
    const lossColor = 'rgba(239, 83, 80, 0.28)';
    const entryLineColor = '#d1d4dc';

    const rewardPct = Math.abs((targetPrice - entryPrice) / entryPrice) * 100;
    const riskPct = Math.abs((entryPrice - stopPrice) / entryPrice) * 100;

    target.useMediaCoordinateSpace(scope => {
      const ctx = scope.context;

      const rewardTop = Math.min(entryY, targetY);
      const rewardBottom = Math.max(entryY, targetY);
      const riskTop = Math.min(entryY, stopY);
      const riskBottom = Math.max(entryY, stopY);

      ctx.fillStyle = profitColor;
      ctx.fillRect(x, rewardTop, width, Math.max(rewardBottom - rewardTop, 1));

      ctx.fillStyle = lossColor;
      ctx.fillRect(x, riskTop, width, Math.max(riskBottom - riskTop, 1));

      ctx.strokeStyle = 'rgba(38, 166, 154, 0.85)';
      ctx.lineWidth = 1;
      ctx.strokeRect(x, rewardTop, width, Math.max(rewardBottom - rewardTop, 1));

      ctx.strokeStyle = 'rgba(239, 83, 80, 0.85)';
      ctx.strokeRect(x, riskTop, width, Math.max(riskBottom - riskTop, 1));

      ctx.strokeStyle = entryLineColor;
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(x, entryY);
      ctx.lineTo(x + width, entryY);
      ctx.stroke();
      ctx.setLineDash([]);

      const markerColor = isLong ? '#26a69a' : '#ef5350';
      ctx.beginPath();
      ctx.arc(entryX, entryY, 5, 0, 2 * Math.PI);
      ctx.fillStyle = markerColor;
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 2;
      ctx.stroke();

      const drawHandle = (y, label, key, color) => {
        const isActive = activeHandle === key;
        const radius = isActive ? 7 : 6;
        ctx.beginPath();
        ctx.arc(xRight, y, radius, 0, 2 * Math.PI);
        ctx.fillStyle = color;
        ctx.fill();
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = isActive ? 2.5 : 2;
        ctx.stroke();

        ctx.font = 'bold 10px Arial';
        ctx.fillStyle = color;
        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';
        ctx.fillText(label, xRight - 10, y);
      };

      drawHandle(targetY, 'TP', 'target', '#26a69a');
      drawHandle(stopY, 'SL', 'stop', '#ef5350');

      const statsX = x + width + 12;
      const statsY = Math.min(rewardTop, riskTop) - 8;
      const statsWidth = 156;
      const statsHeight = 68;

      ctx.beginPath();
      ctx.roundRect(statsX, statsY, statsWidth, statsHeight, 6);
      ctx.fillStyle = 'rgba(19, 23, 34, 0.95)';
      ctx.fill();
      ctx.strokeStyle = markerColor;
      ctx.lineWidth = 1.5;
      ctx.stroke();

      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.font = 'bold 12px Arial';
      ctx.fillStyle = markerColor;
      ctx.fillText(`${isLong ? 'LONG' : 'SHORT'} Position`, statsX + 8, statsY + 14);

      ctx.font = '11px Arial';
      ctx.fillStyle = '#d1d4dc';
      ctx.fillText(`Risk: ${riskPct.toFixed(2)}%`, statsX + 8, statsY + 32);
      ctx.fillText(`Reward: ${rewardPct.toFixed(2)}%`, statsX + 8, statsY + 46);
      ctx.fillText(`R:R 1:${(rewardPct / Math.max(riskPct, 0.0001)).toFixed(2)}`, statsX + 8, statsY + 60);
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

  const enable = (toolType = 'ruler') => {
    primitive.setToolType(toolType);

    if (primitive.isEnabled()) {
      primitive.clear();
      return;
    }

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
    
    container.addEventListener('mousedown', handleMouseDown, true);
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
    
    container.removeEventListener('mousedown', handleMouseDown, true);
    container.removeEventListener('mousemove', handleMouseMove);
    container.removeEventListener('mouseup', handleMouseUp);
    container.style.cursor = 'default';
    primitive.setEnabled(false);
  };

  const toggle = (toolType = 'ruler') => {
    if (primitive.isEnabled()) {
      if (primitive.getToolType() === toolType) {
        disable();
      } else {
        enable(toolType);
      }
    } else {
      enable(toolType);
    }
    return {
      enabled: primitive.isEnabled(),
      toolType: primitive.isEnabled() ? primitive.getToolType() : null,
    };
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
    getToolType: () => primitive.getToolType(),
  };
}

export default MeasurementPrimitive;
