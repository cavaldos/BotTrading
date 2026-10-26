import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from datetime import datetime, timedelta, timezone
from src.exchanges.bybit.exchange import get_exchange, format_symbol


def get_order_history(
    symbol: str = None,
    limit: int = 50,
    days: int = 7,
    order_status: str = 'Filled',
) -> list:
    """
    Lấy lịch sử orders (Order History API).
    Trả về orderId của lệnh MỞ position (opening order).
    
    Args:
        symbol: Symbol cụ thể (optional, None = tất cả)
        limit: Số lượng records tối đa
        days: Số ngày lấy history (mặc định 7 ngày, max 7 per request)
        order_status: Trạng thái order (Filled, Cancelled, PartiallyFilled, etc.)
    
    Returns:
        list: Danh sách orders với orderId gốc
    """
    exchange = get_exchange()
    
    all_records = []
    now = datetime.now(timezone.utc)
    
    chunks = (days + 6) // 7
    
    for i in range(chunks):
        chunk_end = now - timedelta(days=i * 7)
        chunk_start = now - timedelta(days=min((i + 1) * 7, days))
        
        if i == chunks - 1:
            chunk_start = now - timedelta(days=days)
        
        end_time = int(chunk_end.timestamp() * 1000)
        start_time = int(chunk_start.timestamp() * 1000)
        
        params = {
            'category': 'linear',
            'limit': min(limit, 50),
            'startTime': start_time,
            'endTime': end_time,
            'orderStatus': order_status,
        }
        
        if symbol:
            formatted = format_symbol(symbol)
            api_symbol = formatted.replace('/', '').replace(':USDT', '')
            params['symbol'] = api_symbol
        
        try:
            cursor = None
            while True:
                if cursor:
                    params['cursor'] = cursor
                
                result = exchange.privateGetV5OrderHistory(params)
                
                if str(result.get('retCode')) != '0':
                    print(f"Error: {result.get('retMsg')}")
                    break
                
                records = result.get('result', {}).get('list', [])
                all_records.extend(records)
                
                cursor = result.get('result', {}).get('nextPageCursor')
                if not cursor or len(all_records) >= limit:
                    break
                    
        except Exception as e:
            print(f"Error fetching order history: {e}")
    
    all_records.sort(key=lambda x: int(x.get('createdTime', 0)), reverse=True)
    
    return all_records[:limit]


def get_closed_pnl_history(
    symbol: str = None,
    limit: int = 50,
    days: int = 7,
) -> list:
    """
    Lấy lịch sử giao dịch đã đóng với PnL (Realized P&L).
    
    Args:
        symbol: Symbol cụ thể (optional, None = tất cả)
        limit: Số lượng records tối đa
        days: Số ngày lấy history (mặc định 7 ngày, max 7 per request)
    
    Returns:
        list: Danh sách các giao dịch đã đóng với PnL
    """
    exchange = get_exchange()
    
    all_records = []
    now = datetime.now(timezone.utc)
    
    chunks = (days + 6) // 7
    
    for i in range(chunks):
        chunk_end = now - timedelta(days=i * 7)
        chunk_start = now - timedelta(days=min((i + 1) * 7, days))
        
        if i == chunks - 1:
            chunk_start = now - timedelta(days=days)
        
        end_time = int(chunk_end.timestamp() * 1000)
        start_time = int(chunk_start.timestamp() * 1000)
        
        params = {
            'category': 'linear',
            'limit': limit,
            'startTime': start_time,
            'endTime': end_time,
        }
        
        if symbol:
            formatted = format_symbol(symbol)
            api_symbol = formatted.replace('/', '').replace(':USDT', '')
            params['symbol'] = api_symbol
        
        try:
            result = exchange.privateGetV5PositionClosedPnl(params)
            
            if str(result.get('retCode')) != '0':
                print(f"Error: {result.get('retMsg')}")
                continue
            
            records = result.get('result', {}).get('list', [])
            all_records.extend(records)
            
        except Exception as e:
            print(f"Error fetching closed PnL: {e}")
    
    all_records.sort(key=lambda x: int(x.get('createdTime', 0)), reverse=True)
    
    return all_records[:limit]


def print_pnl_history(symbol: str = None, limit: int = 50, days: int = 7):
    """
    In lịch sử PnL ra console với format đẹp.
    """
    records = get_closed_pnl_history(symbol=symbol, limit=limit, days=days)
    
    if not records:
        print("Không có lịch sử giao dịch nào.")
        return
    
    print("\n" + "=" * 160)
    print(f"{'Order ID':<40} {'Symbol':<15} {'Side':<6} {'Entry Price':<14} {'Exit Price':<14} {'Qty':<12} {'PnL (USDT)':<15} {'Time'}")
    print("=" * 160)
    
    total_pnl = 0.0
    
    for record in records:
        symbol_name = record.get('symbol', '')
        # Bybit API trả về side của LỆNH ĐÓNG position, không phải side gốc
        # BUY để đóng = position gốc là SELL (SHORT)
        # SELL để đóng = position gốc là BUY (LONG)
        close_side = record.get('side', '').upper()
        if close_side == 'BUY':
            side = 'SELL'  # Đóng bằng BUY → Position gốc là SHORT
        elif close_side == 'SELL':
            side = 'BUY'   # Đóng bằng SELL → Position gốc là LONG
        else:
            side = close_side
        
        entry_price = float(record.get('avgEntryPrice', 0))
        exit_price = float(record.get('avgExitPrice', 0))
        qty = float(record.get('qty', 0))
        closed_pnl = float(record.get('closedPnl', 0))
        total_pnl += closed_pnl
        
        order_id = record.get('orderId', '')
        created_time = int(record.get('createdTime', 0))
        time_str = datetime.fromtimestamp(created_time / 1000).strftime('%Y-%m-%d %H:%M:%S')
        
        pnl_color = '\033[92m' if closed_pnl >= 0 else '\033[91m'
        reset_color = '\033[0m'
        
        print(f"{order_id:<40} {symbol_name:<15} {side:<6} {entry_price:<14.4f} {exit_price:<14.4f} {qty:<12.4f} {pnl_color}{closed_pnl:<+15.4f}{reset_color} {time_str}")
    
    print("=" * 160)
    total_color = '\033[92m' if total_pnl >= 0 else '\033[91m'
    print(f"{'TOTAL PnL:':<103} {total_color}{total_pnl:<+15.4f}\033[0m")
    print(f"Số lượng giao dịch: {len(records)}")
    print("=" * 160 + "\n")


if __name__ == '__main__':
    print_pnl_history(limit=50, days=30)
