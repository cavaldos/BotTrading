import asyncio
from datetime import datetime
from typing import List, Dict, Optional

from rich.console import Console
from rich.table import Table
from rich.text import Text

from src.trade.bybit.exchange import get_exchange


class OrderHistoryExporter:
    """Export and display order history from Bybit"""
    
    def __init__(self):
        self.console = Console()
        self.exchange = None
    
    def _initialize_exchange(self):
        """Initialize exchange connection"""
        try:
            self.exchange = get_exchange()
            self.console.print("[green]✓ Exchange connected successfully[/green]")
        except Exception as e:
            self.console.print(f"[red]❌ Failed to connect exchange: {e}[/red]")
            raise
    
    def _fetch_all_orders(self) -> List[Dict]:
        """Fetch all closed and canceled orders"""
        all_orders = []
        
        try:
            self.console.print("[cyan]📥 Fetching closed orders...[/cyan]")
            closed = self.exchange.fetch_closed_orders()
            all_orders.extend(closed)
            self.console.print(f"[green]✓ Found {len(closed)} closed orders[/green]")
        except Exception as e:
            self.console.print(f"[yellow]⚠️  Could not fetch closed orders: {e}[/yellow]")
        
        try:
            self.console.print("[cyan]📥 Fetching canceled orders...[/cyan]")
            canceled = self.exchange.fetch_canceled_orders()
            all_orders.extend(canceled)
            self.console.print(f"[green]✓ Found {len(canceled)} canceled orders[/green]")
        except Exception as e:
            self.console.print(f"[yellow]⚠️  Could not fetch canceled orders: {e}[/yellow]")
        
        # Remove duplicates and sort by time (newest first)
        seen = set()
        unique_orders = []
        for order in all_orders:
            order_id = order.get('id')
            if order_id not in seen:
                seen.add(order_id)
                unique_orders.append(order)
        
        unique_orders.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
        
        return unique_orders
    
    def _calculate_pnl(self, order: Dict) -> tuple:
        """Calculate P&L for an order
        
        Returns:
            (pnl_value: float or None, pnl_display: str)
        """
        try:
            side = order.get('side', '').upper()
            amount = float(order.get('amount') or 0)
            price = float(order.get('price') or 0)
            cost = float(order.get('cost') or 0)
            filled = float(order.get('filled') or 0)
            
            if cost <= 0 or amount <= 0:
                return None, "N/A"
            
            if side == "BUY":
                # For BUY orders, cost is what we spent
                avg_price = cost / amount
                pnl_value = (price - avg_price) * filled
            else:
                # For SELL orders, cost is what we received
                pnl_value = (cost - (price * amount))
            
            pnl_color = "green" if pnl_value >= 0 else "red"
            pnl_symbol = "+" if pnl_value >= 0 else ""
            pnl_display = f"[{pnl_color}]{pnl_symbol}${pnl_value:,.2f}[/{pnl_color}]"
            
            return pnl_value, pnl_display
        
        except (ValueError, TypeError, ZeroDivisionError):
            return None, "N/A"
    
    def _format_value(self, value, default="N/A") -> str:
        """Format numeric value safely"""
        try:
            if value is None:
                return default
            return f"{float(value):,.4f}" if float(value) % 1 != 0 else f"{int(float(value))}"
        except (ValueError, TypeError):
            return default
    
    def _create_table(self) -> Table:
        """Create order history table"""
        table = Table(
            title="[bold cyan]📜 ORDER HISTORY (All - Newest First)[/bold cyan]",
            show_header=True,
            header_style="bold white",
            title_style="bold cyan",
            padding=(0, 1)
        )
        
        table.add_column("Order ID", style="dim", width=16)
        table.add_column("Symbol", style="cyan", width=12)
        table.add_column("Side", width=6)
        table.add_column("Type", style="blue", width=8)
        table.add_column("Amount", justify="right", width=14)
        table.add_column("Price", justify="right", style="green", width=14)
        table.add_column("Filled", justify="right", width=14)
        table.add_column("Cost", justify="right", width=14)
        table.add_column("P&L", justify="right", width=14)
        table.add_column("Status", width=12)
        table.add_column("Time", style="dim", width=12)
        
        return table
    
    def export_to_console(self):
        """Export order history to console"""
        try:
            self._initialize_exchange()
            
            self.console.print("\n[bold cyan]=" * 80 + "[/bold cyan]")
            orders = self._fetch_all_orders()
            
            if not orders:
                self.console.print("[yellow]⚠️  No order history found[/yellow]")
                return
            
            self.console.print(f"\n[green]✓ Total orders: {len(orders)}[/green]\n")
            
            table = self._create_table()
            
            total_pnl = 0
            win_count = 0
            loss_count = 0
            
            for order in orders:
                order_id = order.get('id', 'N/A')[:16]
                symbol = order.get('symbol', 'N/A')
                side = order.get('side', 'N/A').upper()
                order_type = order.get('type', 'N/A').upper()
                amount = order.get('amount') or 0
                price = order.get('price') or 0
                filled = order.get('filled') or 0
                cost = order.get('cost') or 0
                status = order.get('status', 'N/A').upper()
                timestamp = order.get('timestamp', 0)
                
                # Format values
                amount_str = self._format_value(amount)
                price_str = f"${self._format_value(price)}"
                filled_str = self._format_value(filled)
                cost_str = f"${self._format_value(cost)}"
                
                # Calculate P&L
                pnl_value, pnl_display = self._calculate_pnl(order)
                if pnl_value is not None:
                    total_pnl += pnl_value
                    if pnl_value >= 0:
                        win_count += 1
                    else:
                        loss_count += 1
                
                # Format time
                if timestamp:
                    order_time = datetime.fromtimestamp(timestamp / 1000).strftime('%Y-%m-%d %H:%M:%S')
                else:
                    order_time = 'N/A'
                
                # Color side
                side_color = "green" if side == "BUY" else "red"
                side_display = f"[{side_color}]{side}[/{side_color}]"
                
                # Color status
                status_style = "dim" if status == "CANCELED" else "green"
                status_display = f"[{status_style}]{status}[/{status_style}]"
                
                table.add_row(
                    order_id,
                    symbol,
                    side_display,
                    order_type,
                    amount_str,
                    price_str,
                    filled_str,
                    cost_str,
                    pnl_display,
                    status_display,
                    order_time
                )
            
            self.console.print(table)
            
            # Summary
            self.console.print("\n[bold cyan]=" * 80 + "[/bold cyan]")
            self.console.print("[bold cyan]📊 SUMMARY[/bold cyan]")
            self.console.print(f"Total Orders: {len(orders)}")
            self.console.print(f"Winning Trades: [green]{win_count}[/green]")
            self.console.print(f"Losing Trades: [red]{loss_count}[/red]")
            
            total_pnl_color = "green" if total_pnl >= 0 else "red"
            total_pnl_symbol = "+" if total_pnl >= 0 else ""
            self.console.print(
                f"Total P&L: [{total_pnl_color}]{total_pnl_symbol}${total_pnl:,.2f}[/{total_pnl_color}]"
            )
            self.console.print("[bold cyan]=" * 80 + "[/bold cyan]\n")
        
        except Exception as e:
            self.console.print(f"[red]❌ Error: {e}[/red]")
            import traceback
            traceback.print_exc()
    
    def export_to_csv(self, filename: str = "order_history.csv"):
        """Export order history to CSV file"""
        try:
            import csv
            
            self._initialize_exchange()
            orders = self._fetch_all_orders()
            
            if not orders:
                self.console.print("[yellow]⚠️  No order history found[/yellow]")
                return
            
            with open(filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        'order_id', 'symbol', 'side', 'type', 'amount', 'price',
                        'filled', 'cost', 'status', 'pnl', 'timestamp', 'time'
                    ]
                )
                writer.writeheader()
                
                for order in orders:
                    pnl_value, _ = self._calculate_pnl(order)
                    timestamp = order.get('timestamp', 0)
                    order_time = datetime.fromtimestamp(timestamp / 1000).strftime(
                        '%Y-%m-%d %H:%M:%S'
                    ) if timestamp else 'N/A'
                    
                    writer.writerow({
                        'order_id': order.get('id', 'N/A'),
                        'symbol': order.get('symbol', 'N/A'),
                        'side': order.get('side', 'N/A').upper(),
                        'type': order.get('type', 'N/A').upper(),
                        'amount': order.get('amount') or 0,
                        'price': order.get('price') or 0,
                        'filled': order.get('filled') or 0,
                        'cost': order.get('cost') or 0,
                        'status': order.get('status', 'N/A').upper(),
                        'pnl': pnl_value or 0,
                        'timestamp': timestamp,
                        'time': order_time
                    })
            
            self.console.print(f"[green]✓ Order history exported to {filename}[/green]")
        
        except Exception as e:
            self.console.print(f"[red]❌ Error exporting to CSV: {e}[/red]")
            import traceback
            traceback.print_exc()


def main():
    """Main function"""
    exporter = OrderHistoryExporter()
    exporter.export_to_console()
    exporter.export_to_csv("res/order_history_export.csv")

if __name__ == '__main__':
    main()
