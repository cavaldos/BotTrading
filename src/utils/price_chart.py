"""
Price Chart - Biểu đồ realtime 3 đường giá từ Binance, Bybit, OKX
"""
from __future__ import annotations

import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

import asciichartpy
import ccxt
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.live import Live

from rounding import round_price, get_price_precision

console = Console()

def get_max_points() -> int:
    """Tính số điểm tối đa dựa trên chiều rộng terminal."""
    try:
        import shutil
        width = shutil.get_terminal_size().columns
        # Trừ đi 15 ký tự cho trục Y (số giá), lấy 2/3 chiều rộng còn lại
        return int((width - 15) * 2 / 3)
    except Exception:
        return 100

MAX_POINTS = get_max_points()
COLORS = {
    'binance': asciichartpy.yellow,
    'bybit': asciichartpy.cyan,
    'okx': asciichartpy.magenta,
}


class PriceChart:
    def __init__(self, symbol: str = 'BTC', interval: float = 1.0):
        self.symbol = symbol.upper()
        self.interval = interval
        self.exchanges = self._create_exchanges()
        # Cập nhật MAX_POINTS khi khởi tạo
        self.max_points = get_max_points()
        # Lưu giá theo thời điểm, mỗi entry là dict {binance, bybit, okx}
        self.price_history: deque = deque(maxlen=self.max_points)
        self.last_prices = {}
        self.errors = {}
    
    def _create_exchanges(self) -> dict:
        return {
            'binance': ccxt.binance({
                'enableRateLimit': True,
                'options': {'defaultType': 'future'}
            }),
            'bybit': ccxt.bybit({
                'enableRateLimit': True,
                'options': {'defaultType': 'swap'}
            }),
            'okx': ccxt.okx({
                'enableRateLimit': True,
                'options': {'defaultType': 'swap'}
            }),
        }
    
    def _format_symbol(self, exchange_name: str) -> str:
        return f"{self.symbol}/USDT:USDT"
    
    def _fetch_price(self, exchange_name: str) -> float | None:
        try:
            exchange = self.exchanges[exchange_name]
            symbol = self._format_symbol(exchange_name)
            ticker = exchange.fetch_ticker(symbol)
            price = ticker.get('last')
            self.errors[exchange_name] = None
            return price
        except Exception as e:
            self.errors[exchange_name] = str(e)[:30]
            return None
    
    def fetch_all_prices(self):
        """Lấy giá từ 3 sàn đồng thời."""
        prices_at_time = {}
        
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                name: executor.submit(self._fetch_price, name)
                for name in self.exchanges.keys()
            }
            
            for name, future in futures.items():
                price = future.result()
                if price:
                    self.last_prices[name] = price
                    prices_at_time[name] = price
                elif self.last_prices.get(name):
                    # Giữ giá cũ nếu lỗi
                    prices_at_time[name] = self.last_prices[name]
        
        # Chỉ thêm vào history nếu có đủ 3 giá
        if len(prices_at_time) == 3:
            self.price_history.append(prices_at_time)
    
    def _build_chart(self) -> str:
        """Tạo chart với 3 đường."""
        if len(self.price_history) < 2:
            return "Đang chờ dữ liệu..."
        
        # Tách thành 3 series từ history
        binance_series = [p['binance'] for p in self.price_history]
        bybit_series = [p['bybit'] for p in self.price_history]
        okx_series = [p['okx'] for p in self.price_history]
        
        try:
            chart = asciichartpy.plot(
                [binance_series, bybit_series, okx_series],
                {
                    'height': 15,
                    'format': '{:14.5f}',
                    'colors': [COLORS['binance'], COLORS['bybit'], COLORS['okx']],
                }
            )
            return chart
        except Exception as e:
            return f"Lỗi vẽ chart: {e}"
    
    def _build_legend(self) -> str:
        """Tạo legend hiển thị giá và chênh lệch (plain text)."""
        lines = []
        
        binance_price = self.last_prices.get('binance')
        
        color_symbols = {'binance': '■', 'bybit': '■', 'okx': '■'}
        
        for name in ['binance', 'bybit', 'okx']:
            price = self.last_prices.get(name)
            error = self.errors.get(name)
            
            if error:
                lines.append(f"  {name.upper():8} Error: {error}")
                continue
            
            if not price:
                lines.append(f"  {name.upper():8} Waiting...")
                continue
            
            # Tính chênh lệch
            if binance_price and name != 'binance':
                diff = price - binance_price
                diff_pct = (diff / binance_price) * 100
                diff_str = f"{diff:+.5f} ({diff_pct:+.2f}%)"
            else:
                diff_str = "(base)"
            
            rounded_price = round_price(price)
            precision = get_price_precision(price)
            lines.append(f"  {color_symbols[name]} {name.upper():8} ${format(rounded_price, f'.{precision}f')}  {diff_str}")
        
        return "\n".join(lines)
    
    def _build_display(self) -> str:
        """Tạo nội dung hiển thị (không dùng Panel)."""
        chart = self._build_chart()
        legend = self._build_legend()
        
        points = len(self.price_history)
        
        lines = [
            f"{'─' * 20} 📈 {self.symbol}/USDT Price Chart {'─' * 20}",
            "",
            chart,
            "",
            legend,
            "",
            f"Points: {points}/{self.max_points} | Interval: {self.interval}s | {datetime.now().strftime('%H:%M:%S')}",
            f"Binance (yellow) | Bybit (cyan) | OKX (magenta)",
        ]
        
        return "\n".join(lines)
    
    def run(self):
        """Chạy chart realtime."""
        console.print(f"[yellow]Đang kết nối tới 3 sàn...[/yellow]")
        
        try:
            while True:
                self.fetch_all_prices()
                
                # Clear screen và in chart
                console.clear()
                print(self._build_display())
                
                time.sleep(self.interval)
        except KeyboardInterrupt:
            console.print("\n[yellow]Đã dừng.[/yellow]")


def main():
    import sys
    
    symbol = sys.argv[1] if len(sys.argv) > 1 else 'BTC'
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
    
    chart = PriceChart(symbol=symbol, interval=interval)
    chart.run()


if __name__ == '__main__':
    main()
