import { formatPrice } from '../lib/format';
import { IconClose } from '../ui/icons';
import { approvedVia, countdown, shortReason, sideLabel } from './orderText';
import { useTicker } from './useTrading';
import { useSignalDecision } from './useSignalDecision';

const MAX_CARDS = 3;

function SignalAlert({ signal, serverNow, timeoutMinutes, onDecide, onShow, onDismiss }) {
  const { confirming, busy, error, approve, reject } = useSignalDecision(signal.id, onDecide);
  const pending = signal.status === 'PENDING';
  useTicker(pending);
  const isLong = signal.side === 'LONG';
  const left = signal.expiresAt * 1000 - serverNow();
  const total = (timeoutMinutes || 5) * 60000;

  const failed = signal.status === 'FAILED';
  let title = 'New signal · waiting for you';
  if (signal.status === 'PLACING') title = 'Placing order…';
  if (failed) title = `Order failed${signal.decidedVia && signal.decidedVia !== 'web' ? ` (${approvedVia(signal.decidedVia)})` : ''}`;

  return (
    <article
      className={`tv-alert tv-sig-alert ${signal.status === 'FAILED' ? 'tv-alert--down' : 'tv-alert--accent'}`}
      role="alertdialog"
      aria-label={`${signal.symbol} ${sideLabel(signal.side)} signal`}
    >
      <header className="tv-alert-head">
        <span className="tv-alert-dot" aria-hidden="true" />
        <b>{title}</b>
        {failed && <span className="tv-tag tv-tag--down">Failed</span>}
        {pending && (
          <span className={`tv-sig-timer num ${left < 60000 ? 'tv-down' : ''}`} aria-label="Time left to decide">{countdown(left)}</span>
        )}
        <button type="button" className="tv-btn tv-btn--icon tv-alert-close" onClick={() => onDismiss(signal.id)} aria-label="Hide (stays in the Trade panel)">
          <IconClose size={16} />
        </button>
      </header>
      <p className="tv-alert-what">
        <span className={isLong ? 'tv-up' : 'tv-down'}>{sideLabel(signal.side)}</span>
        {' '}<b className="tv-sig-alert-sym">{signal.symbol}</b>
        {signal.signalTimeframe && <span className="tv-muted"> · {signal.signalTimeframe}</span>}
        {Number.isFinite(signal.rr) && <span className="tv-muted"> · R:R {signal.rr}</span>}
      </p>
      {pending && (
        <div className="tv-sig-progress tv-sig-alert-progress" aria-hidden="true">
          <span style={{ transform: `scaleX(${Math.max(0, Math.min(1, left / total))})` }} />
        </div>
      )}
      <dl className="tv-sig-levels tv-sig-alert-levels num">
        <div><dt>Entry</dt><dd>{formatPrice(signal.entry)}</dd></div>
        <div><dt>Stop</dt><dd className="tv-down">{formatPrice(signal.stopLoss)}</dd></div>
        <div><dt>Target</dt><dd className="tv-up">{formatPrice(signal.takeProfit)}</dd></div>
      </dl>
      {failed && <p className="tv-trade-error">{shortReason(signal.resultText) || 'The exchange did not accept the order.'}</p>}
      {error && !failed && <p className="tv-trade-error" role="alert">{error}</p>}
      <div className="tv-sig-alert-actions">
        <button type="button" className="tv-btn tv-btn--ghost tv-btn--xs" onClick={() => onShow(signal)}>Show on chart</button>
        {pending && (
          <>
            <button type="button" className="tv-btn tv-btn--ghost tv-btn--xs" onClick={reject} disabled={!!busy}>
              {busy === 'reject' ? 'Rejecting…' : 'Reject'}
            </button>
            <button
              type="button"
              className={`tv-btn tv-btn--xs ${isLong ? 'tv-btn--buy' : 'tv-btn--sell'}`}
              onClick={approve}
              disabled={!!busy}
              aria-live="polite"
            >
              {busy === 'approve' ? 'Placing…' : confirming ? `Confirm ${isLong ? 'long' : 'short'}` : 'Approve'}
            </button>
          </>
        )}
      </div>
    </article>
  );
}

/**
 * Bot signals waiting for approval, top right over the chart, so they are seen
 * whatever panel is open. Hidden while the Trade panel (which lists them) is open.
 */
export default function SignalAlerts({ signals, serverNow, timeoutMinutes, onDecide, onShow, onDismiss, onOpenPanel }) {
  if (!signals.length) return null;
  const shown = signals.slice(0, MAX_CARDS);
  const more = signals.length - shown.length;
  return (
    <div className="tv-sig-alerts" aria-label="Signals waiting for approval">
      {shown.map(s => (
        <SignalAlert
          key={s.id}
          signal={s}
          serverNow={serverNow}
          timeoutMinutes={timeoutMinutes}
          onDecide={onDecide}
          onShow={onShow}
          onDismiss={onDismiss}
        />
      ))}
      {more > 0 && (
        <button type="button" className="tv-btn tv-btn--ghost tv-btn--xs tv-sig-alerts-more" onClick={onOpenPanel}>
          {more} more in the Trade panel
        </button>
      )}
    </div>
  );
}
