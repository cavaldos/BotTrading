import { TOOL_DEFS } from '../drawings/tools';
import {
  IconCrosshair, IconTrend, IconRay, IconHLine, IconVLine, IconRect, IconFib, IconLong, IconShort,
  IconRuler, IconText, IconBrush, IconMagnet, IconLock, IconUnlock, IconEye, IconEyeOff, IconTrash,
} from './icons';

const GROUPS = [
  [['trend', IconTrend], ['ray', IconRay], ['hline', IconHLine], ['vline', IconVLine]],
  [['rect', IconRect], ['fib', IconFib], ['brush', IconBrush], ['text', IconText]],
  [['long', IconLong], ['short', IconShort], ['measure', IconRuler]],
];

export default function LeftToolbar({ tool, onTool, magnet, onMagnet, locked, onLocked, hidden, onHidden, onRemoveAll, hasDrawings }) {
  return (
    <nav className="tv-panel tv-tools" aria-label="Drawing tools">
      <button
        type="button"
        className="tv-tool"
        aria-pressed={!tool}
        onClick={() => onTool(null)}
        data-tip="Cross"
        data-tip-side="right"
        data-kbd="Esc"
        aria-label="Cross cursor"
      >
        <IconCrosshair size={20} />
      </button>
      {GROUPS.map((group, gi) => (
        <div key={gi} style={{ display: 'contents' }}>
          <span className="tv-tools-sep" />
          {group.map((entry) => {
            const [id, Icon] = entry;
            return (
              <button
                key={id}
                type="button"
                className="tv-tool"
                aria-pressed={tool === id}
                onClick={() => onTool(tool === id ? null : id)}
                data-tip={TOOL_DEFS[id].label}
                data-tip-side="right"
                data-kbd={TOOL_DEFS[id].shortcut}
                aria-label={TOOL_DEFS[id].label}
                disabled={hidden}
              >
                <Icon size={20} />
              </button>
            );
          })}
        </div>
      ))}
      <span className="tv-tools-sep" />
      <button type="button" className="tv-tool" aria-pressed={magnet} onClick={() => onMagnet(!magnet)} data-tip="Magnet: snap to OHLC" data-tip-side="right" aria-label="Magnet mode">
        <IconMagnet size={20} />
      </button>
      <button type="button" className="tv-tool" aria-pressed={locked} onClick={() => onLocked(!locked)} data-tip={locked ? 'Unlock all drawings' : 'Lock all drawings'} data-tip-side="right" aria-label="Lock all drawings">
        {locked ? <IconLock size={20} /> : <IconUnlock size={20} />}
      </button>
      <button type="button" className="tv-tool" aria-pressed={hidden} onClick={() => onHidden(!hidden)} data-tip={hidden ? 'Show all drawings' : 'Hide all drawings'} data-tip-side="right" aria-label="Hide all drawings">
        {hidden ? <IconEyeOff size={20} /> : <IconEye size={20} />}
      </button>
      <button type="button" className="tv-tool" onClick={onRemoveAll} disabled={!hasDrawings} data-tip="Remove all drawings" data-tip-side="right" aria-label="Remove all drawings">
        <IconTrash size={20} />
      </button>
    </nav>
  );
}
