import { useState } from 'react';
import { Dialog } from './Dialog';
import { INDICATOR_BY_TYPE } from '../indicators/registry';

/** Inputs + style editor for one indicator instance. Changes apply on OK. */
export default function IndicatorSettings({ inst, onApply, onClose }) {
  const def = INDICATOR_BY_TYPE[inst.type];
  const [tab, setTab] = useState(def.inputs.length ? 'inputs' : 'style');
  const [inputs, setInputs] = useState(inst.inputs);
  const [plots, setPlots] = useState(inst.plots);

  const setInput = (key, value) => setInputs(prev => ({ ...prev, [key]: value }));
  const setPlot = (key, patch) => setPlots(prev => ({ ...prev, [key]: { ...prev[key], ...patch } }));

  const normalized = () => {
    const out = {};
    def.inputs.forEach(i => {
      let v = inputs[i.key];
      if (i.type === 'int' || i.type === 'float') {
        v = Number(v);
        if (!Number.isFinite(v)) v = i.default;
        if (i.type === 'int') v = Math.round(v);
        v = Math.min(i.max ?? Infinity, Math.max(i.min ?? -Infinity, v));
      }
      out[i.key] = v;
    });
    return out;
  };

  const apply = () => {
    onApply({ ...inst, inputs: normalized(), plots });
    onClose();
  };

  const reset = () => {
    setInputs(Object.fromEntries(def.inputs.map(i => [i.key, i.default])));
    setPlots(Object.fromEntries(def.plots.map(p => [p.key, { color: p.color, width: p.width, visible: true }])));
  };

  return (
    <Dialog
      title={def.name}
      size="sm"
      onClose={onClose}
      footer={(
        <>
          <button type="button" className="tv-btn tv-btn--ghost" onClick={reset} style={{ marginRight: 'auto' }}>Defaults</button>
          <button type="button" className="tv-btn tv-btn--ghost" onClick={onClose}>Cancel</button>
          <button type="button" className="tv-btn tv-btn--primary" onClick={apply}>Ok</button>
        </>
      )}
    >
      <div className="tv-bottom-head" role="tablist" style={{ paddingLeft: 12 }}>
        {def.inputs.length > 0 && (
          <button type="button" role="tab" className="tv-tab" aria-selected={tab === 'inputs'} onClick={() => setTab('inputs')}>Inputs</button>
        )}
        <button type="button" role="tab" className="tv-tab" aria-selected={tab === 'style'} onClick={() => setTab('style')}>Style</button>
      </div>
      <form className="tv-dialog-body" onSubmit={(e) => { e.preventDefault(); apply(); }}>
        {tab === 'inputs' && def.inputs.map(i => (
          <div className="tv-field" key={i.key}>
            <label htmlFor={`in-${i.key}`}>{i.label}</label>
            {i.type === 'select' ? (
              <select id={`in-${i.key}`} className="tv-select" value={inputs[i.key]} onChange={(e) => setInput(i.key, e.target.value)}>
                {i.options.map(o => <option key={o} value={o}>{o}</option>)}
              </select>
            ) : (
              <input
                id={`in-${i.key}`}
                className="tv-input num"
                type="number"
                inputMode="decimal"
                min={i.min}
                max={i.max}
                step={i.step || 1}
                value={inputs[i.key]}
                onChange={(e) => setInput(i.key, e.target.value)}
              />
            )}
          </div>
        ))}
        {tab === 'style' && def.plots.map(p => {
          const s = plots[p.key] || p;
          return (
            <div className="tv-field" key={p.key}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <input type="checkbox" checked={s.visible !== false} onChange={(e) => setPlot(p.key, { visible: e.target.checked })} />
                {p.label}
              </label>
              <div className="tv-style-row">
                <input type="color" className="tv-color" value={s.color} onChange={(e) => setPlot(p.key, { color: e.target.value })} aria-label={`${p.label} color`} />
                {p.type !== 'histogram' && (
                  <select className="tv-select" style={{ width: 80 }} value={s.width} onChange={(e) => setPlot(p.key, { width: Number(e.target.value) })} aria-label={`${p.label} line width`}>
                    {[1, 2, 3, 4].map(w => <option key={w} value={w}>{w}px</option>)}
                  </select>
                )}
              </div>
            </div>
          );
        })}
        <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
      </form>
    </Dialog>
  );
}
