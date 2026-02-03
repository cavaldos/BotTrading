import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import json
from datetime import datetime
from src.trade.bybit.tool.get_history import get_closed_pnl_history
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
    Match bằng order_id thay vì (symbol, side, entry_price).
    """
    local_orders = load_local_orders()
    exchange_orders = get_closed_pnl_history(limit=limit, days=days)
    
    ex_map = {}
    for ex in exchange_orders:
        order_id = ex.get('orderId', '')
        if not order_id:
            continue
        
        close_side = ex.get('side', '').upper()
        ex_side = 'SHORT' if close_side == 'BUY' else 'LONG'
        ex_pnl = float(ex.get('closedPnl', 0))
        ex_status = 'WIN' if ex_pnl > 0 else 'LOSS'
        
        created_time = int(ex.get('createdTime', 0))
        ex_time_str = datetime.fromtimestamp(created_time / 1000).strftime('%H:%M:%S') if created_time else ''
        
        ex_map[order_id] = {
            'symbol': ex.get('symbol', ''),
            'side': ex_side,
            'status': ex_status,
            'pnl': ex_pnl,
            'entry': float(ex.get('avgEntryPrice', 0)),
            'exit': float(ex.get('avgExitPrice', 0)),
            'time': ex_time_str,
        }
    
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
        
        if not local_order_id:
            not_found.append({
                'key': key_str,
                'local_status': local_status,
                'order_id': None,
                'reason': 'No order_id in local',
            })
            continue
        
        if local_order_id in ex_map:
            ex_data = ex_map[local_order_id]
            status_match = '✅' if local_status == ex_data['status'] else '❌'
            rr = calculate_rr(ex_data['entry'], ex_data['exit'], local_sl, local_side)
            
            matched.append({
                'symbol': symbol,
                'side': local_side,
                'exchange_entry': ex_data['entry'],
                'exchange_exit': ex_data['exit'],
                'local_sl': local_sl,
                'local_tp': local_tp,
                'rr': rr,
                'local_status': local_status,
                'exchange_status': ex_data['status'],
                'match': status_match,
                'pnl': ex_data['pnl'],
                'ex_time': ex_data['time'],
                'sent_time': sent_time,
                'order_id': local_order_id,
            })
        else:
            not_found.append({
                'key': key_str,
                'local_status': local_status,
                'order_id': local_order_id,
                'reason': 'Not found on exchange',
            })
    
    correct = sum(1 for m in matched if m['match'] == '✅')
    wrong = len(matched) - correct
    
    lines = []
    
    header = f"📊 MATCHED ({len(matched)} orders):"
    sep = "-" * 200
    col_header = f"{'Symbol':<12} {'Side':<6} {'Entry':<12} {'Exit':<12} {'SL':<12} {'TP':<12} {'RR':<8} {'Local':<8} {'Ex':<8} {'Match':<6} {'PnL':<12} {'ExTime':<10} {'SentTime':<10} {'OrderID'}"
    
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
        pnl = round(m['pnl'], 2)
        
        row = f"{m['symbol']:<12} {m['side']:<6} {entry:<12} {exit_p:<12} {sl:<12} {tp:<12} {rr_str:<8} {m['local_status']:<8} {m['exchange_status']:<8} {m['match']:<6} {pnl:<+12.2f} {m['ex_time']:<10} {m['sent_time']:<10} {m['order_id']}"
        
        print(f"{m['symbol']:<12} {m['side']:<6} {entry:<12} {exit_p:<12} {sl:<12} {tp:<12} {rr_str:<8} {m['local_status']:<8} {m['exchange_status']:<8} {m['match']:<6} {pnl_color}{pnl:<+12.2f}{reset} {m['ex_time']:<10} {m['sent_time']:<10} {m['order_id']}")
        lines.append(row)
    
    print(sep)
    print(f"✅ Correct: {correct} | ❌ Wrong: {wrong}")
    
    lines.append(sep)
    lines.append(f"✅ Correct: {correct} | ❌ Wrong: {wrong}")
    
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
