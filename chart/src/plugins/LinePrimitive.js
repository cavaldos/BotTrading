/**
 * Line Primitive Plugin for Lightweight Charts
 * Draws horizontal lines between two time points (for BOS, CHoCH, EQH/EQL)
 */

/**
 * LinePrimitive - draws a horizontal line at a price level between two time points
 * with an optional label
 */
export class LinePrimitive {
  constructor(options) {
    this._options = {
      startTime: options.startTime,
      endTime: options.endTime,
      price: options.price,
      color: options.color || 'rgba(255, 255, 255, 0.5)',
      lineWidth: options.lineWidth || 1,
      lineStyle: options.lineStyle || 'solid', // 'solid', 'dashed', 'dotted'
      label: options.label || null,
      labelPosition: options.labelPosition || 'center', // 'left', 'center', 'right'
      labelColor: options.labelColor || null, // defaults to line color
      labelSize: options.labelSize || 10,
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
    return [new LinePaneView(this)];
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

class LinePaneView {
  constructor(source) {
    this._source = source;
  }

  update() {}

  renderer() {
    return new LineRenderer(this._source);
  }

  zOrder() {
    return 'bottom';
  }
}

class LineRenderer {
  constructor(source) {
    this._source = source;
  }

  draw(target) {
    const options = this._source.getOptions();
    const chart = this._source.getChart();
    const series = this._source.getSeries();

    if (!chart || !series) return;

    const timeScale = chart.timeScale();

    const startX = timeScale.timeToCoordinate(options.startTime);
    const endX = timeScale.timeToCoordinate(options.endTime);

    if (startX === null || endX === null) return;

    const y = series.priceToCoordinate(options.price);
    if (y === null) return;

    target.useMediaCoordinateSpace(scope => {
      const ctx = scope.context;

      // Set line style
      ctx.strokeStyle = options.color;
      ctx.lineWidth = options.lineWidth;

      if (options.lineStyle === 'dashed') {
        ctx.setLineDash([6, 4]);
      } else if (options.lineStyle === 'dotted') {
        ctx.setLineDash([2, 3]);
      } else {
        ctx.setLineDash([]);
      }

      // Draw line
      ctx.beginPath();
      ctx.moveTo(startX, y);
      ctx.lineTo(endX, y);
      ctx.stroke();
      ctx.setLineDash([]);

      // Draw label
      if (options.label) {
        const fontSize = options.labelSize;
        ctx.font = `bold ${fontSize}px Arial`;
        const labelColor = options.labelColor || options.color;
        ctx.fillStyle = labelColor;

        const textWidth = ctx.measureText(options.label).width;
        let labelX;

        switch (options.labelPosition) {
          case 'left':
            labelX = Math.min(startX, endX) + 4;
            break;
          case 'right':
            labelX = Math.max(startX, endX) - textWidth - 4;
            break;
          case 'center':
          default:
            labelX = (startX + endX) / 2 - textWidth / 2;
            break;
        }

        const labelY = y - 4;

        // Text shadow for readability
        ctx.shadowColor = 'rgba(0, 0, 0, 0.8)';
        ctx.shadowBlur = 3;
        ctx.shadowOffsetX = 1;
        ctx.shadowOffsetY = 1;

        ctx.fillText(options.label, labelX, labelY);

        ctx.shadowColor = 'transparent';
        ctx.shadowBlur = 0;
        ctx.shadowOffsetX = 0;
        ctx.shadowOffsetY = 0;
      }
    });
  }
}

/**
 * Create a BOS/CHoCH line primitive
 * @param {Object} item - BOS or CHoCH data object
 * @returns {LinePrimitive}
 */
export function createStructureLine(item) {
  return new LinePrimitive({
    startTime: item.pivotTime,
    endTime: item.breakTime,
    price: item.level,
    color: item.color,
    lineWidth: 1,
    lineStyle: item.lineStyle ?? (item.type === 'CHoCH' ? 'dashed' : 'solid'),
    label: item.type ?? 'BOS',
    labelPosition: 'center',
    labelSize: 10,
  });
}

/**
 * Create an EQH/EQL line primitive
 * @param {Object} item - EQH/EQL data object
 * @returns {LinePrimitive}
 */
export function createEqhEqlLine(item) {
  return new LinePrimitive({
    startTime: item.startTime,
    endTime: item.endTime,
    price: item.level,
    color: item.color,
    lineWidth: 1,
    lineStyle: item.lineStyle || 'dashed',
    label: item.type,
    labelPosition: 'center',
    labelSize: 9,
  });
}

export default LinePrimitive;
