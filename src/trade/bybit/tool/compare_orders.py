import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import json
from src.trade.bybit.tool.get_history import get_closed_pnl_history


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


def compare_orders(days: int = 30, limit: int = 500):
    """
    So sánh status WIN/LOSS giữa local log và sàn Bybit.
    """
    local_orders = load_local_orders()
    exchange_orders = get_closed_pnl_history(limit=limit, days=days)
    
    ex_map = {}
    for ex in exchange_orders:
        ex_symbol = ex.get('symbol', '')
        close_side = ex.get('side', '').upper()
        ex_side = 'SHORT' if close_side == 'BUY' else 'LONG'
        ex_entry = float(ex.get('avgEntryPrice', 0))
        ex_pnl = float(ex.get('closedPnl', 0))
        ex_status = 'WIN' if ex_pnl > 0 else 'LOSS'
        
        key = (ex_symbol, ex_side, round(ex_entry, 4))
        ex_map[key] = {
            'status': ex_status,
            'pnl': ex_pnl,
            'entry': ex_entry,
            'order_id': ex.get('orderId', ''),
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
        local_status = local.get('status', '')
        
        found = False
        for (ex_sym, ex_side, ex_entry), ex_data in ex_map.items():
            if ex_sym == symbol and ex_side == local_side:
                price_diff = abs(ex_entry - local_entry) / local_entry * 100 if local_entry > 0 else 999
                if price_diff < 0.5:
                    found = True
                    status_match = '✅' if local_status == ex_data['status'] else '❌'
                    matched.append({
                        'symbol': symbol,
                        'side': local_side,
                        'local_entry': local_entry,
                        'exchange_entry': ex_data['entry'],
                        'local_status': local_status,
                        'exchange_status': ex_data['status'],
                        'match': status_match,
                        'pnl': ex_data['pnl'],
                        'order_id': ex_data['order_id'],
                    })
                    break
        
        if not found:
            not_found.append({
                'key': key_str,
                'local_status': local_status,
                'order_id': local.get('order_id', ''),
            })
    
    correct = sum(1 for m in matched if m['match'] == '✅')
    wrong = len(matched) - correct
    
    print(f"\n📊 MATCHED ({len(matched)} orders):")
    print("-" * 140)
    print(f"{'Symbol':<12} {'Side':<6} {'Local Entry':<14} {'Ex Entry':<14} {'Local Status':<12} {'Ex Status':<10} {'Match':<6} {'PnL':<12} {'Ex OrderID'}")
    print("-" * 140)
    
    for m in matched:
        pnl_color = '\033[92m' if m['pnl'] >= 0 else '\033[91m'
        reset = '\033[0m'
        print(f"{m['symbol']:<12} {m['side']:<6} {m['local_entry']:<14.4f} {m['exchange_entry']:<14.4f} {m['local_status']:<12} {m['exchange_status']:<10} {m['match']:<6} {pnl_color}{m['pnl']:<+12.4f}{reset} {m['order_id']}")
    
    print("-" * 140)
    print(f"✅ Correct: {correct} | ❌ Wrong: {wrong}")
    
    if not_found:
        print(f"\n⚠️  NOT FOUND ON EXCHANGE ({len(not_found)}):")
        print("-" * 80)
        for n in not_found:
            print(f"  {n['key']} | Local: {n['local_status']} | OrderID: {n['order_id']}")
    
    print("")


if __name__ == '__main__':
    compare_orders(days=30, limit=500)
