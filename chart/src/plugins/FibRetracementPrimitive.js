/**
 * Fibonacci Retracement Primitive Plugin for Lightweight Charts
 * Draws horizontal fib levels between swing high and swing low with labels
 *
 * Can be attached on its own, or drawn in bulk through PrimitiveLayer.
 */

import { drawOutlinedText } from './canvasText';

export class FibRetracementPrimitive {
  constructor(options) {
    this._options = {
      swingHighTime: options.swingHighTime,
      swingLowTime: options.swingLowTime,
      levels: options.levels || [], // [{ratio, price, label}]
      lineColor: options.lineColor || 'rgba(33, 150, 243, 0.6)',
      fillColor: options.fillColor || 'rgba(33, 150, 243, 0.05)',
      labelColor: options.labelColor || 'rgba(33, 150, 243, 0.9)',
      lineWidth: options.lineWidth || 1,
      direction: options.direction || 'DOWN', // 'UP' or 'DOWN'
      type: options.type || 'STANDARD',
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

  updateAllViews() {}

  paneViews() {
    return [new FibPaneView(this)];
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

  getTimeSpan() {
    return [this._options.swingHighTime, this._options.swingLowTime];
  }

  getZOrder() {
    return 'bottom';
  }

  /**
   * Draw in media coordinates. startX/endX are the x of swingHighTime/swingLowTime.
   * frame: { series, width, height, showLabels }
   */
  drawShape(ctx, startX, endX, frame) {
    const options = this._options;
    if (!options.levels || options.levels.length === 0) return;

    const leftX = Math.min(startX, endX);
    const rightX = Math.max(startX, endX);
    const width = rightX - leftX;

    if (width < 5) return; // Too small to render

    // Draw each level
    for (let i = 0; i < options.levels.length; i++) {
      const level = options.levels[i];
      const y = frame.series.priceToCoordinate(level.price);
      if (y === null) continue;

      // Draw level line
      ctx.strokeStyle = options.lineColor;
      ctx.lineWidth = options.lineWidth;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.moveTo(leftX, y);
      ctx.lineTo(rightX, y);
      ctx.stroke();
      ctx.setLineDash([]);

      // Fill between this level and next level
      if (i < options.levels.length - 1) {
        const nextLevel = options.levels[i + 1];
        const nextY = frame.series.priceToCoordinate(nextLevel.price);
        if (nextY !== null) {
          ctx.fillStyle = options.fillColor;
          ctx.fillRect(leftX, Math.min(y, nextY), width, Math.abs(nextY - y));
        }
      }

      if (!frame.showLabels) continue;

      // Draw label on the right side
      const fontSize = 10;
      ctx.font = `${fontSize}px Arial`;
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';

      const labelText = `${level.label} (${level.price.toFixed(1)})`;
      drawOutlinedText(ctx, labelText, rightX - 4, y - 2, options.labelColor);
    }
  }
}

class FibPaneView {
  constructor(source) {
    this._source = source;
  }

  update() {}

  renderer() {
    return new FibRenderer(this._source);
  }

  zOrder() {
    return 'bottom';
  }
}

class FibRenderer {
  constructor(source) {
    this._source = source;
  }

  draw(target) {
    const options = this._source.getOptions();
    const chart = this._source.getChart();
    const series = this._source.getSeries();

    if (!chart || !series) return;

    const timeScale = chart.timeScale();

    const startX = timeScale.timeToCoordinate(options.swingHighTime);
    const endX = timeScale.timeToCoordinate(options.swingLowTime);

    if (startX === null || endX === null) return;

    target.useMediaCoordinateSpace(scope => {
      this._source.drawShape(scope.context, startX, endX, {
        series,
        width: scope.mediaSize.width,
        height: scope.mediaSize.height,
        showLabels: true,
      });
    });
  }
}

/**
 * Create a Fibonacci retracement primitive from indicator data
 * @param {Object} item - Retracement data object
 * @returns {FibRetracementPrimitive}
 */
export function createFibRetracement(item) {
  return new FibRetracementPrimitive({
    swingHighTime: item.swingHighTime,
    swingLowTime: item.swingLowTime,
    levels: item.levels,
    lineColor: item.lineColor || 'rgba(33, 150, 243, 0.6)',
    fillColor: item.color || 'rgba(33, 150, 243, 0.05)',
    labelColor: item.lineColor || 'rgba(33, 150, 243, 0.9)',
    direction: item.direction,
    type: item.type,
  });
}

export default FibRetracementPrimitive;
