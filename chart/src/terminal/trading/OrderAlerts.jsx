import { IconClose } from '../ui/icons';
import { ORDER_CHANGES, orderDetail, sideLabel, sourceLabel, typeLabel } from './orderText';

/**
 * Cards for order fills (and orders that did not fill), bottom right.
 * They stay until dismissed so a fill is not missed while looking elsewhere.
 */
export default function OrderAlerts({ alerts, onDismiss, onShow }) {
  if (!alerts.length) return null;
  return (
    <div className="tv-alerts" aria-label="Order updates">
      {alerts.map(({ id, order, change }) => {
        const def = ORDER_CHANGES[change] || { title: 'Order update', tone: 'muted' };
        const urgent = change === 'not_filled' || change === 'unknown' || (order.slTpMissing && order.filled > 0);
        return (
          <article key={id} className={`tv-alert tv-alert--${def.tone}`} role={urgent ? 'alert' : 'status'}>
            <header className="tv-alert-head">
              <span className="tv-alert-dot" aria-hidden="true" />
              <b>{def.title}</b>
              <button type="button" className="tv-btn tv-btn--icon tv-alert-close" onClick={() => onDismiss(id)} aria-label="Dismiss">
                <IconClose size={16} />
              </button>
            </header>
            <p className="tv-alert-what">
              <span className={order.side === 'LONG' ? 'tv-up' : 'tv-down'}>{sideLabel(order.side)}</span>
              {' '}{order.symbol} · {typeLabel(order.orderType)} · <span className="tv-muted">{sourceLabel(order)}</span>
            </p>
            <p className="tv-alert-detail num">{orderDetail(order)}</p>
            {order.status === 'FILLED' && order.reason && <p className="tv-alert-detail tv-muted">{order.reason}</p>}
            {order.slTpMissing && order.filled > 0 && (
              <p className="tv-trade-error">Stop loss / target not set on the exchange: {order.slTpMissing}</p>
            )}
            <button type="button" className="tv-btn tv-btn--ghost tv-btn--xs tv-alert-show" onClick={() => onShow(order)}>
              Show on chart
            </button>
          </article>
        );
      })}
    </div>
  );
}
