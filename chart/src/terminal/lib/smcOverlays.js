/**
 * Builds the SMC overlay primitives (BOS, CHoCH, OB, FVG, ...) from the
 * /data/indicators/<id>.json payload. Shared by CombineChart (snapshot page)
 * and the terminal chart; all shapes are drawn through one PrimitiveLayer.
 */
import { RectanglePrimitive } from '../../plugins/RectanglePrimitive';
import { createStructureLine, createEqhEqlLine, createSwingSegmentLine } from '../../plugins/LinePrimitive';
import { createFibRetracement } from '../../plugins/FibRetracementPrimitive';

export const SMC_OVERLAYS = [
  { key: 'bos', label: 'BOS', description: 'Break of Structure', color: '#2962ff' },
  { key: 'choch', label: 'CHoCH', description: 'Change of Character', color: '#ff9800' },
  { key: 'orderBlocks', label: 'Order Blocks', description: 'Bullish / bearish order blocks', color: '#26a69a' },
  { key: 'fvg', label: 'FVG', description: 'Fair Value Gaps (unfilled)', color: '#64b5f6' },
  { key: 'ifvg', label: 'IFVG', description: 'Inverse Fair Value Gaps', color: '#bdbdbd' },
  { key: 'eqhEql', label: 'EQH / EQL', description: 'Equal highs and lows', color: '#f23645' },
  { key: 'imbalance', label: 'Imbalance', description: 'Volume imbalance zones', color: '#00bcd4' },
  { key: 'breakerBlocks', label: 'Breaker Blocks', description: 'Failed order blocks', color: '#f44336' },
  { key: 'mitigationBlocks', label: 'Mitigation Blocks', description: 'Mitigated order blocks', color: '#ff9800' },
  { key: 'retracement', label: 'Fib Retracement', description: 'Auto Fibonacci on swings', color: '#2196f3' },
  { key: 'swing', label: 'Swings', description: 'Swing labels HH / HL / LH / LL', color: '#ff5252' },
  { key: 'gannSwing', label: 'Gann Swing', description: 'Gann swing zigzag', color: '#ffb300' },
];

export const DEFAULT_SMC_VISIBILITY = {
  bos: true,
  choch: true,
  orderBlocks: true,
  fvg: false,
  ifvg: false,
  eqhEql: false,
  imbalance: false,
  breakerBlocks: true,
  mitigationBlocks: false,
  retracement: false,
  swing: true,
  gannSwing: false,
};

export function buildSmcPrimitives(indicatorData = {}, indicatorVisibility = {}, { barDuration = 900, candles = [] } = {}) {
  const newPrimitives = [];

  // BOS lines
  if (indicatorVisibility.bos && indicatorData.bos?.length > 0) {
    indicatorData.bos.forEach(item => {
      newPrimitives.push(createStructureLine(item));
    });
  }

  // CHoCH lines
  if (indicatorVisibility.choch && indicatorData.choch?.length > 0) {
    indicatorData.choch.forEach(item => {
      newPrimitives.push(createStructureLine(item));
    });
  }

  // Order Blocks (from indicator data)
  if (indicatorVisibility.orderBlocks && indicatorData.orderBlocks?.length > 0) {
    indicatorData.orderBlocks.forEach(ob => {
      const endTime = ob.endTime || (ob.startTime + 150 * barDuration);
      newPrimitives.push(new RectanglePrimitive({
        startTime: ob.startTime,
        endTime: endTime,
        lowPrice: ob.low,
        highPrice: ob.high,
        fillColor: ob.color || (ob.bias === 'BULLISH'
          ? 'rgba(38, 166, 154, 0.15)'
          : 'rgba(239, 83, 80, 0.15)'),
        borderColor: ob.borderColor || null,
        borderWidth: ob.borderWidth || 0,
        label: ob.label || `OB ${ob.structureType || ''}`,
        labelColor: ob.labelColor || (ob.bias === 'BULLISH' ? '#26a69a' : '#ef5350'),
      }));
    });
  }

  // FVG zones
  if (indicatorVisibility.fvg && indicatorData.fvg?.length > 0) {
    const fvgItems = indicatorData.fvg.filter(f => !f.isFilled);
    fvgItems.forEach(fvg => {
      const endTime = fvg.time + 80 * barDuration;
      newPrimitives.push(new RectanglePrimitive({
        startTime: fvg.time,
        endTime: endTime,
        lowPrice: fvg.bottom,
        highPrice: fvg.top,
        fillColor: fvg.color || (fvg.bias === 'BULLISH'
          ? 'rgba(38, 166, 154, 0.12)'
          : 'rgba(239, 83, 80, 0.12)'),
        borderColor: null,
        label: 'FVG',
        labelColor: fvg.bias === 'BULLISH'
          ? 'rgba(38, 166, 154, 0.6)'
          : 'rgba(239, 83, 80, 0.6)',
      }));
    });
  }

  // IFVG zones
  if (indicatorVisibility.ifvg && indicatorData.ifvg?.length > 0) {
    const ifvgItems = indicatorData.ifvg.filter(item => !item.isFilled);
    ifvgItems.forEach(item => {
      const endTime = item.time + 80 * barDuration;
      newPrimitives.push(new RectanglePrimitive({
        startTime: item.startTime || item.sourceTime || item.time,
        endTime,
        lowPrice: item.bottom,
        highPrice: item.top,
        fillColor: item.color || 'rgba(189, 189, 189, 0.12)',
        borderColor: item.borderColor || (item.bias === 'BULLISH'
          ? 'rgba(76, 175, 80, 0.70)'
          : 'rgba(255, 112, 67, 0.75)'),
        borderWidth: 1,
        label: 'IFVG',
        labelColor: item.labelColor || (item.bias === 'BULLISH'
          ? 'rgba(76, 175, 80, 0.95)'
          : 'rgba(255, 112, 67, 0.95)'),
      }));
    });
  }

  // EQH/EQL lines
  if (indicatorVisibility.eqhEql && indicatorData.eqhEql?.length > 0) {
    const eqItems = indicatorData.eqhEql.slice(-200);
    eqItems.forEach(item => {
      newPrimitives.push(createEqhEqlLine(item));
    });
  }

  // Imbalance zones
  if (indicatorVisibility.imbalance && indicatorData.imbalance?.length > 0) {
    const imbItems = indicatorData.imbalance.filter(i => !i.isFilled);
    imbItems.forEach(imb => {
      const endTime = imb.time + 60 * barDuration;
      newPrimitives.push(new RectanglePrimitive({
        startTime: imb.time,
        endTime: endTime,
        lowPrice: imb.low,
        highPrice: imb.high,
        fillColor: imb.color || 'rgba(0, 188, 212, 0.15)',
        borderColor: 'rgba(0, 188, 212, 0.4)',
        borderWidth: 1,
        label: `IMB ${imb.strength?.toFixed(1) || ''}`,
        labelColor: 'rgba(0, 188, 212, 0.8)',
      }));
    });
  }

  // Breaker Blocks
  if (indicatorVisibility.breakerBlocks && indicatorData.breakerBlocks?.length > 0) {
    indicatorData.breakerBlocks.forEach(bb => {
      const endTime = bb.endTime || (bb.startTime + 100 * barDuration);
      newPrimitives.push(new RectanglePrimitive({
        startTime: bb.startTime,
        endTime: endTime,
        lowPrice: bb.low,
        highPrice: bb.high,
        fillColor: bb.color || (bb.bias === 'BULLISH'
          ? 'rgba(38, 166, 154, 0.2)'
          : 'rgba(244, 67, 54, 0.2)'),
        borderColor: bb.borderColor || (bb.bias === 'BULLISH'
          ? 'rgba(38, 166, 154, 0.7)'
          : 'rgba(244, 67, 54, 0.7)'),
        borderWidth: 2,
        label: 'BB',
        labelColor: bb.bias === 'BULLISH' ? '#26a69a' : '#f44336',
      }));
    });
  }

  // Mitigation Blocks
  if (indicatorVisibility.mitigationBlocks && indicatorData.mitigationBlocks?.length > 0) {
    indicatorData.mitigationBlocks.forEach(mb => {
      const endTime = mb.endTime || (mb.startTime + 80 * barDuration);
      newPrimitives.push(new RectanglePrimitive({
        startTime: mb.startTime,
        endTime: endTime,
        lowPrice: mb.low,
        highPrice: mb.high,
        fillColor: mb.color || (mb.bias === 'BULLISH'
          ? 'rgba(38, 166, 154, 0.1)'
          : 'rgba(239, 83, 80, 0.1)'),
        borderColor: mb.borderColor || (mb.bias === 'BULLISH'
          ? 'rgba(38, 166, 154, 0.4)'
          : 'rgba(239, 83, 80, 0.4)'),
        borderWidth: 1,
        label: `MB ${mb.mitigationPercent?.toFixed(0) || ''}%`,
        labelColor: mb.bias === 'BULLISH'
          ? 'rgba(38, 166, 154, 0.7)'
          : 'rgba(239, 83, 80, 0.7)',
      }));
    });
  }

  // Fibonacci Retracements
  if (indicatorVisibility.retracement && indicatorData.retracement?.length > 0) {
    const fibItems = indicatorData.retracement.slice(-15);
    fibItems.forEach(item => {
      newPrimitives.push(createFibRetracement(item));
    });
  }

  // Swings (HH/HL/LH/LL)
  if (indicatorVisibility.swing && indicatorData.swing?.length > 0) {
    const items = indicatorData.swing.slice(-500);
    items.forEach(s => {
      const start = s.time;
      const end = s.time + barDuration;
      const pad = Math.abs((s.price || 0) * 0.002) || 0.0001;

      newPrimitives.push(new RectanglePrimitive({
        startTime: start,
        endTime: end,
        lowPrice: s.price - pad,
        highPrice: s.price + pad,
        fillColor: 'rgba(0,0,0,0)',
        borderColor: null,
        borderWidth: 0,
        label: s.type,
        labelColor: s.isHigh ? '#ff5252' : '#00bfa5',
      }));
    });
  }

  // Gann Swings (zigzag line through swing pivots)
  if (indicatorVisibility.gannSwing && indicatorData.gannSwing?.length > 0) {
    const items = indicatorData.gannSwing
      .slice(-1200)
      .filter(s => Number.isFinite(s?.time) && Number.isFinite(s?.price))
      .sort((a, b) => a.time - b.time);

    for (let i = 1; i < items.length; i += 1) {
      newPrimitives.push(createSwingSegmentLine(items[i - 1], items[i], {
        color: '#ffb300',
        lineWidth: 3,
        zOrder: 'top',
      }));
    }

    // Extend last confirmed swing to current bar extreme (active leg)
    const lastSwing = items[items.length - 1];
    const latestCandle = candles?.[candles.length - 1];
    if (
      lastSwing
      && latestCandle
      && Number.isFinite(latestCandle.time)
      && latestCandle.time > lastSwing.time
    ) {
      let projectedPrice = Number(lastSwing.price);
      for (let i = candles.length - 1; i >= 0; i -= 1) {
        const c = candles[i];
        if (!c || !Number.isFinite(c.time) || c.time < lastSwing.time) break;
        if (lastSwing.isHigh) {
          if (Number.isFinite(c.low) && c.low < projectedPrice) projectedPrice = c.low;
        } else if (Number.isFinite(c.high) && c.high > projectedPrice) {
          projectedPrice = c.high;
        }
      }

      if (Number.isFinite(projectedPrice) && projectedPrice !== lastSwing.price) {
        newPrimitives.push(createSwingSegmentLine(lastSwing, {
          time: latestCandle.time,
          price: projectedPrice,
        }, {
          color: '#ffb300',
          lineWidth: 3,
          zOrder: 'top',
        }));
      }
    }
  }

  return newPrimitives;
}
