import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import json
from datetime import datetime
from src.trade.bybit.tool.get_history import get_closed_pnl_history, get_order_history
from src.trade.bybit.exchange import get_exchange, format_symbol
from src.utils.rounding import round_price


def load_local_orders(json_path: str = None) -> list:
    """Load orders từ file JSON local."""
    if json_path is None:
        json_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))),
            'res', 'logs', 'sent_orders.json'
        )
    
    with open(json_path, 'r') as f:
        data = json.load(f)
    
    return data.get('sent_orders', [])


def calculate_rr(entry: float, exit: float, sl: float, side: str) -> float:
    """Tính tỉ lệ Risk/Reward thực tế."""
    if side.upper() == 'LONG':
        risk = abs(entry - sl)
        reward = exit - entry
    else:
        risk = abs(sl - entry)
        reward = entry - exit
    
    if risk == 0:
        return 0.0
    return round(reward / risk, 2)


def compare_orders(days: int = 30, limit: int = 500, save_md: bool = True):
    """
    So sánh status WIN/LOSS giữa local log và sàn Bybit.
    Match bằng order_id từ Order History API (opening order_id).
    """
    local_orders = load_local_orders()
    
    # Lấy order history (opening orders) để match order_id
    order_history = get_order_history(limit=limit, days=days, order_status='Filled')
    
    # Lấy closed PnL để biết WIN/LOSS và exit price
    pnl_history = get_closed_pnl_history(limit=limit, days=days)
    
    # Build map order_id -> order info từ Order History
    order_map = {}
    # Build map parent_order_id -> [child orders] to find TP/SL child orders
    children_map = {}
    # Instantiate exchange for best-effort lookups later
    exchange = get_exchange()

    def _parse_price(value):
        if value in (None, '', 0, '0', '0.0'):
            return None
        try:
            parsed = float(value)
        except Exception:
            return None
        return parsed if parsed > 0 else None

    def _extract_sl_tp_from_info(info: dict) -> tuple:
        """Try many possible keys to extract SL/TP from an order/info dict."""
        sl = None
        tp = None
        if not isinstance(info, dict):
            return sl, tp

        keys = {
            'sl': ['stopLoss', 'stop_loss', 'stopLossPrice', 'slLimitPrice', 'slTriggerPrice', 'stop_price', 'stopPrice'],
            'tp': ['takeProfit', 'take_profit', 'takeProfitPrice', 'tpLimitPrice', 'tpTriggerPrice', 'take_profit']
        }

        for k in keys['sl']:
            v = _parse_price(info.get(k))
            if v is not None:
                sl = v
                break

        for k in keys['tp']:
            v = _parse_price(info.get(k))
            if v is not None:
                tp = v
                break

        return sl, tp

    for order in order_history:
        order_id = order.get('orderId', '')
        if not order_id:
            continue
        
        side = order.get('side', '').upper()
        ex_side = 'LONG' if side == 'BUY' else 'SHORT'
        
        created_time = int(order.get('createdTime', 0))
        time_str = datetime.fromtimestamp(created_time / 1000).strftime('%H:%M:%S') if created_time else ''
        
        # Attempt to read SL/TP from the order history record if present
        info_sl, info_tp = _extract_sl_tp_from_info(order)

        # Record as child if this order references a parentOrderId
        parent_id = order.get('parentOrderId') or (order.get('info', {}) or {}).get('parentOrderId')
        if parent_id and str(parent_id) != '0':
            children_map.setdefault(str(parent_id), []).append(order)

        order_map[order_id] = {
            'symbol': order.get('symbol', ''),
            'side': ex_side,
            'entry': float(order.get('avgPrice', 0)) or float(order.get('price', 0)),
            'qty': float(order.get('cumExecQty', 0)),
            'time': time_str,
            'stop_loss': info_sl,
            'take_profit': info_tp,
        }
    
    # Build map (symbol, side, entry) -> PnL info để lấy exit price và WIN/LOSS
    pnl_map = []
    for pnl in pnl_history:
        close_side = pnl.get('side', '').upper()
        ex_side = 'SHORT' if close_side == 'BUY' else 'LONG'
        ex_pnl = float(pnl.get('closedPnl', 0))
        
        pnl_map.append({
            'symbol': pnl.get('symbol', ''),
            'side': ex_side,
            'entry': float(pnl.get('avgEntryPrice', 0)),
            'exit': float(pnl.get('avgExitPrice', 0)),
            'pnl': ex_pnl,
            'status': 'WIN' if ex_pnl > 0 else 'LOSS',
            'matched': False,
        })
    
    matched = []
    not_found = []
    
    for local in local_orders:
        key_str = local.get('key', '')
        parts = key_str.split('|')
        if len(parts) < 2:
            continue
        
        symbol = parts[0].split('_')[0]
        local_side = parts[1]
        local_entry = float(local.get('entry_price', 0))
        local_sl = float(local.get('stop_loss', 0))
        local_tp = float(local.get('take_profit', 0))
        local_status = local.get('status', '')
        local_order_id = local.get('order_id', '')
        
        sent_at = local.get('sent_at', '')
        sent_time = ''
        if sent_at:
            try:
                sent_time = datetime.strptime(sent_at, '%Y-%m-%d %H:%M:%S').strftime('%H:%M:%S')
            except:
                sent_time = sent_at
        
        # Match bằng order_id từ Order History API
        if local_order_id and local_order_id in order_map:
            order_info = order_map[local_order_id]
            
            # Tìm PnL info từ pnl_map bằng (symbol, side, entry)
            pnl_info = None
            for pnl in pnl_map:
                if pnl['matched']:
                    continue
                if pnl['symbol'] != order_info['symbol']:
                    continue
                if pnl['side'] != order_info['side']:
                    continue
                # So sánh entry price với tolerance 0.5%
                price_diff = abs(pnl['entry'] - order_info['entry']) / order_info['entry'] * 100 if order_info['entry'] > 0 else 100
                if price_diff <= 0.5:
                    pnl_info = pnl
                    pnl['matched'] = True
                    break
            
            if pnl_info:
                status_match = '✅' if local_status == pnl_info['status'] else '❌'
                rr = calculate_rr(order_info['entry'], pnl_info['exit'], local_sl, local_side)

                # Best-effort: get SL/TP as recorded on the exchange for this order
                ex_sl = order_info.get('stop_loss')
                ex_tp = order_info.get('take_profit')

                # First, try to find child orders (parentOrderId) and read TP/SL from them
                try:
                    children = children_map.get(str(local_order_id), [])
                except Exception:
                    children = []

                if children:
                    for child in children:
                        # Determine child order type (TakeProfit / StopLoss) from known keys
                        stype = (child.get('stopOrderType') or child.get('info', {}).get('stopOrderType') or '').lower()

                        # Try to extract a price from child order (price/triggerPrice or info fields)
                        child_price = None
                        for k in ('price', 'triggerPrice', 'trigger_price', 'stopPrice', 'stop_price'):
                            v = child.get(k) or (child.get('info', {}) or {}).get(k)
                            v = _parse_price(v)
                            if v is not None:
                                child_price = v
                                break

                        if 'take' in stype or 'tp' in stype:
                            if child_price is not None and ex_tp is None:
                                ex_tp = child_price
                        if 'stop' in stype or 'sl' in stype:
                            if child_price is not None and ex_sl is None:
                                ex_sl = child_price

                # If not available from order history children, fall back to fetch_order details
                if (ex_sl is None or ex_tp is None) and local_order_id:
                    try:
                        formatted_symbol = format_symbol(order_info.get('symbol', ''))
                        fetched = exchange.fetch_order(local_order_id, formatted_symbol)
                        # Inspect top-level keys
                        sl_from_order, tp_from_order = _extract_sl_tp_from_info(fetched)  # top-level
                        if sl_from_order is None:
                            sl_from_order, tp_from_order = _extract_sl_tp_from_info(fetched.get('info', {}))

                        if ex_sl is None:
                            ex_sl = sl_from_order
                        if ex_tp is None:
                            ex_tp = tp_from_order
                    except Exception:
                        # Ignore fetch errors and keep None
                        ex_sl = ex_sl if ex_sl is not None else None
                        ex_tp = ex_tp if ex_tp is not None else None

                if ex_sl is not None and ex_sl <= 0:
                    ex_sl = None
                if ex_tp is not None and ex_tp <= 0:
                    ex_tp = None

                if ex_sl is None and ex_tp is None:
                    ex_sl = pnl_info['exit']
                    ex_tp = pnl_info['exit']

                matched.append({
                    'symbol': symbol,
                    'side': local_side,
                    'exchange_entry': order_info['entry'],
                    'exchange_exit': pnl_info['exit'],
                    'local_sl': local_sl,
                    'local_tp': local_tp,
                    'exchange_exit': ex_sl,
                    'rr': rr,
                    'local_status': local_status,
                    'exchange_status': pnl_info['status'],
                    'match': status_match,
                    'pnl': pnl_info['pnl'],
                    'ex_time': order_info['time'],
                    'sent_time': sent_time,
                    'order_id': local_order_id,
                })
            else:
                # Có order nhưng chưa có PnL (position chưa đóng)
                not_found.append({
                    'key': key_str,
                    'local_status': local_status,
                    'order_id': local_order_id,
                    'reason': 'Position not closed yet',
                })
        else:
            not_found.append({
                'key': key_str,
                'local_status': local_status,
                'order_id': local_order_id,
                'reason': 'Not matched on exchange (symbol/side/entry)',
            })
    
    correct = sum(1 for m in matched if m['match'] == '✅')
    wrong = len(matched) - correct
    # Tổng lệnh thắng / thua theo thông tin PnL của exchange
    total_wins = sum(1 for m in matched if m.get('exchange_status') == 'WIN')
    total_losses = sum(1 for m in matched if m.get('exchange_status') == 'LOSS')
    # Tổng lợi nhuận (sum PnL) cho thắng / thua và tổng chung
    total_win_pnl = sum(m.get('pnl', 0) for m in matched if m.get('exchange_status') == 'WIN')
    total_loss_pnl = sum(m.get('pnl', 0) for m in matched if m.get('exchange_status') == 'LOSS')
    total_pnl = total_win_pnl + total_loss_pnl

    lines = []

    header = f"📊 MATCHED ({len(matched)} orders):"
    sep = "-" * 200
    col_header = f"{'Symbol':<12} {'Side':<6} {'Entry':<12} {'Exit':<12} {'Diff%':<9} {'SL':<12} {'TP':<12} {'ExExit':<12} {'RR':<8} {'Local':<8} {'Ex':<8} {'Match':<6} {'PnL':<12} {'ExTime':<10} {'SentTime':<10} {'OrderID'}"

    print(f"\n{header}")
    print(sep)
    print(col_header)
    print(sep)

    lines.append(f"# Compare Orders Report")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append(header)
    lines.append(sep)
    lines.append(col_header)
    lines.append(sep)

    for m in matched:
        pnl_color = '\033[92m' if m['pnl'] >= 0 else '\033[91m'
        reset = '\033[0m'
        rr_str = f"{m['rr']:+.2f}"

        entry = round_price(m['exchange_entry'])
        exit_p = round_price(m['exchange_exit'])
        sl = round_price(m['local_sl'])
        tp = round_price(m['local_tp'])
        ex_exit = round_price(m['exchange_exit']) if m.get('exchange_exit') is not None else '-'
        pnl = round(m['pnl'], 2)
        diff_pct = ((m['exchange_exit'] - m['exchange_entry']) / m['exchange_entry'] * 100)
        diff_pct_str = f"{diff_pct:+.2f}%" if m['exchange_entry'] else "-"

        row = f"{m['symbol']:<12} {m['side']:<6} {entry:<12} {exit_p:<12} {diff_pct_str:<9} {sl:<12} {tp:<12} {ex_exit:<12} {rr_str:<8} {m['local_status']:<8} {m['exchange_status']:<8} {m['match']:<6} {pnl:<+12.2f} {m['ex_time']:<10} {m['sent_time']:<10} {m['order_id']}"

        print(f"{m['symbol']:<12} {m['side']:<6} {entry:<12} {exit_p:<12} {diff_pct_str:<9} {sl:<12} {tp:<12} {ex_exit:<12} {rr_str:<8} {m['local_status']:<8} {m['exchange_status']:<8} {m['match']:<6} {pnl_color}{pnl:<+12.2f}{reset} {m['ex_time']:<10} {m['sent_time']:<10} {m['order_id']}")
        lines.append(row)

    print(sep)
    # Format PnL strings for display
    win_pnl_str = f"{total_win_pnl:+.2f}"
    loss_pnl_str = f"{total_loss_pnl:+.2f}"
    total_pnl_str = f"{total_pnl:+.2f}"

    print(f"✅ Correct: {correct} | ❌ Wrong: {wrong}")
    print(f"🏆 Wins: {total_wins} | 🥀 Losses: {total_losses} | 💰 Win PnL: {win_pnl_str} | 💸 Loss PnL: {loss_pnl_str} | Total: {total_pnl_str}")

    lines.append(sep)
    lines.append(f"✅ Correct: {correct} | ❌ Wrong: {wrong}")
    lines.append(f"🏆 Wins: {total_wins} | 🥀 Losses: {total_losses} | 💰 Win PnL: {win_pnl_str} | 💸 Loss PnL: {loss_pnl_str} | Total: {total_pnl_str}")
    
    if not_found:
        print(f"\n⚠️  NOT FOUND ON EXCHANGE ({len(not_found)}):")
        print("-" * 100)
        
        lines.append("")
        lines.append(f"⚠️  NOT FOUND ON EXCHANGE ({len(not_found)}):")
        lines.append("-" * 100)
        
        for n in not_found:
            oid = n['order_id'] or 'N/A'
            reason = n.get('reason', '')
            row = f"  {n['key']} | Local: {n['local_status']} | OrderID: {oid} | {reason}"
            print(row)
            lines.append(row)
    
    print("")
    
    if save_md:
        md_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))),
            'res', 'compare.md'
        )
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        print(f"📄 Saved to: {md_path}")


if __name__ == '__main__':
    compare_orders(days=30, limit=500)
