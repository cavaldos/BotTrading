import asyncio
import time
from datetime import datetime
from typing import Optional

import ccxt
from rich.console import Console, Group
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.align import Align

from src.trade.okx.exchange import get_exchange


class OKXDashboard:
    def __init__(self, update_interval: float = 1.0):
        """
        Khởi tạo OKX Dashboard
        
        Args:
            update_interval: Khoảng thời gian cập nhật (giây)
        """
        self.console = Console()
        self.exchange: Optional[ccxt.okx] = None
        self.update_interval = update_interval
        self.is_running = False
        
        # Data cache
        self.balance_data = {}
        self.orders_data = []
        self.positions_data = []
        self.update_time = datetime.now()

    def _get_account_balance(self) -> dict:
        """Lấy thông tin số dư tài khoản OKX
        
        Returns:
            dict: {'total': float or None, 'available': float or None}
        """
        total_balance = None
        available_balance = None
        
        try:
            balance = self.exchange.fetch_balance()
            
            # OKX trả về cấu trúc khác so với Bybit
            # Tìm USDT/USDC balance
            for coin in ('USDT', 'USDC'):
                data = balance.get(coin)
                if isinstance(data, dict):
                    total = float(data.get('total') or data.get('balance') or 0)
                    free = float(data.get('free') or data.get('available') or 0)
                    if total > 0:
                        return {'total': total, 'available': free}
            
            # Fallback: check 'info' structure
            info = balance.get('info', {})
            if isinstance(info, dict):
                for v in info.values():
                    if isinstance(v, dict):
                        coin = v.get('ccy', '').upper()
                        if coin in ('USDT', 'USDC'):
                            eq = float(v.get('totalEq') or v.get('eq') or 0)
                            available = float(v.get('availBal') or 0)
                            if eq > 0:
                                return {'total': eq, 'available': available}
        
        except Exception as e:
            self.console.print(f"[red]Error fetching balance: {e}[/red]")
        
        return {'total': total_balance, 'available': available_balance}

    def _create_balance_panel(self) -> Panel:
        """Create panel displaying account balance"""
        try:
            if not self.exchange:
                return Panel(
                    "[red]❌ Exchange not initialized[/red]",
                    title="[bold cyan]💰 Account Balance[/bold cyan]",
                    border_style="cyan"
                )
            
            # Fetch balance info
            balance = self.exchange.fetch_balance()
            
            # Determine stablecoin keys (USDT and USDC)
            stable_keys = ['USDT', 'USDC']
            total_stable = 0.0
            free_stable = 0.0
            used_stable = 0.0
            for k in stable_keys:
                data = balance.get(k)
                if isinstance(data, dict):
                    total_stable += float(data.get('total', 0) or 0)
                    free_stable += float(data.get('free', 0) or 0)
                    used_stable += float(data.get('used', 0) or 0)
            
            # Calculate unrealized PnL from positions
            unrealized_pnl = 0.0
            try:
                positions = self.exchange.fetch_positions()
                for pos in positions:
                    if isinstance(pos, dict):
                        pnl = float(pos.get('unrealizedPnl') or pos.get('info', {}).get('upl') or 0)
                        unrealized_pnl += pnl
            except Exception:
                pass
            
            # Create balance table
            balance_table = Table(show_header=True, header_style="bold magenta", box=None)
            balance_table.add_column("Type", style="cyan", width=15)
            balance_table.add_column("Value (USDT)", justify="right", style="green", width=20)
            
            balance_table.add_row("Total (stable)", f"${total_stable:,.2f}")
            
            # Unrealized PnL with color
            pnl_color = "green" if unrealized_pnl >= 0 else "red"
            pnl_symbol = "+" if unrealized_pnl >= 0 else ""
            balance_table.add_row("Unrealized P&L", f"[{pnl_color}]{pnl_symbol}${unrealized_pnl:,.2f}[/{pnl_color}]")
            
            balance_table.add_row("Available", f"${free_stable:,.2f}")
            balance_table.add_row("In Use", f"${used_stable:,.2f}")
            
            # Estimate USD value of other tokens
            estimated_total = total_stable
            other_tokens = []
            for k, v in balance.items():
                if k in stable_keys:
                    continue
                if isinstance(v, dict):
                    total = float(v.get('total', 0) or 0)
                    if total and total > 0:
                        # attempt to fetch price
                        price = 0.0
                        for quote in ['USDT', 'USDC']:
                            symbol = f"{k}-{quote}"
                            try:
                                ticker = self.exchange.fetch_ticker(symbol)
                                price = float(ticker.get('last') or ticker.get('close') or 0)
                                if price:
                                    break
                            except Exception:
                                price = 0.0
                        usd_value = total * price
                        estimated_total += usd_value
                        other_tokens.append((k, total, price, usd_value))
            
            # Show estimated total account value
            balance_table.add_row("", "")  # empty spacer
            balance_table.add_row("Estimated Total (USD)", f"${estimated_total:,.2f}", style="bold")
            
            # Add update info
            update_text = Text(
                f"🔄 Last update: {self.update_time.strftime('%H:%M:%S')}",
                style="dim white"
            )
            
            group = Group(balance_table, "", Align.right(update_text))
            
            return Panel(
                group,
                title="[bold cyan]💰 Account Balance[/bold cyan]",
                border_style="cyan",
                padding=(1, 2)
            )
            
        except Exception as e:
            return Panel(
                f"[red]❌ Error: {str(e)}[/red]",
                title="[bold cyan]💰 Account Balance[/bold cyan]",
                border_style="red"
            )

    def _create_orders_panel(self) -> Panel:
        """Create panel displaying open orders"""
        try:
            if not self.exchange:
                return Panel(
                    "[red]❌ Exchange not initialized[/red]",
                    title="[bold yellow]📊 Open Orders[/bold yellow]",
                    border_style="yellow"
                )
            
            # Fetch open orders
            orders = self.exchange.fetch_open_orders()
            
            # Create orders table
            orders_table = Table(
                show_header=True,
                header_style="bold yellow",
                box=None,
                padding=(0, 1)
            )
            orders_table.add_column("Symbol", style="cyan", width=12)
            orders_table.add_column("Side", style="magenta", width=6)
            orders_table.add_column("Type", style="blue", width=8)
            orders_table.add_column("Amount", justify="right", width=12)
            orders_table.add_column("Price", justify="right", style="green", width=12)
            orders_table.add_column("Filled", justify="right", width=10)
            orders_table.add_column("Status", style="white", width=10)
            orders_table.add_column("Time", style="dim", width=10)
            
            if not orders:
                orders_table.add_row(
                    "[dim]No open orders[/dim]",
                    "", "", "", "", "", "", ""
                )
            else:
                displayed_count = 0
                for order in orders[:10]:  # Show top 10
                    symbol = order.get('symbol', 'N/A')
                    side = order.get('side', 'N/A').upper()
                    order_type = order.get('type', 'N/A').upper()
                    amount = order.get('amount') or 0
                    price = order.get('price') or 0
                    filled = order.get('filled') or 0
                    status = order.get('status', 'N/A').upper()
                    
                    # Format values
                    try:
                        amount_str = f"{float(amount):,.4f}" if amount else "0"
                    except (ValueError, TypeError):
                        amount_str = "N/A"
                    
                    try:
                        price_str = f"${float(price):,.2f}" if price else "$0"
                    except (ValueError, TypeError):
                        price_str = "N/A"
                    
                    try:
                        filled_str = f"{float(filled):,.4f}" if filled else "0"
                    except (ValueError, TypeError):
                        filled_str = "N/A"
                    
                    # Status color
                    status_color = "green" if status == "OPEN" else "yellow"
                    side_color = "green" if side == "BUY" else "red"
                    
                    # Format time
                    timestamp = order.get('timestamp', 0)
                    if timestamp:
                        order_time = datetime.fromtimestamp(timestamp/1000).strftime('%H:%M:%S')
                    else:
                        order_time = 'N/A'
                    
                    orders_table.add_row(
                        symbol,
                        f"[{side_color}]{side}[/{side_color}]",
                        order_type,
                        amount_str,
                        price_str,
                        filled_str,
                        f"[{status_color}]{status}[/{status_color}]",
                        order_time
                    )
                    displayed_count += 1
            
            # Add info
            info_text = Text(
                f"📈 Total: {len(orders)} | 🔄 {self.update_time.strftime('%H:%M:%S')}",
                style="dim white"
            )
            
            group = Group(orders_table, "", Align.right(info_text))
            
            return Panel(
                group,
                title="[bold yellow]📊 Open Orders (Realtime)[/bold yellow]",
                border_style="yellow",
                padding=(1, 2)
            )
            
        except Exception as e:
            return Panel(
                f"[red]❌ Error: {str(e)}[/red]",
                title="[bold yellow]📊 Open Orders[/bold yellow]",
                border_style="red"
            )

    def _create_positions_panel(self) -> Panel:
        """Create panel displaying open positions"""
        try:
            if not self.exchange:
                return Panel(
                    "[red]❌ Exchange not initialized[/red]",
                    title="[bold magenta]📈 Open Positions[/bold magenta]",
                    border_style="magenta"
                )
            
            # Fetch positions
            positions = self.exchange.fetch_positions()
            
            # Filter only open positions
            open_positions = [p for p in positions if p.get('contracts', 0) > 0]
            
            # Create positions table
            positions_table = Table(
                show_header=True,
                header_style="bold magenta",
                box=None,
                padding=(0, 1)
            )
            positions_table.add_column("Symbol", style="cyan", width=12)
            positions_table.add_column("Side", style="magenta", width=6)
            positions_table.add_column("Size", justify="right", width=12)
            positions_table.add_column("Entry Price", justify="right", width=12)
            positions_table.add_column("Mark Price", justify="right", width=12)
            positions_table.add_column("Leverage", justify="right", width=8)
            positions_table.add_column("P&L", justify="right", width=8)
            positions_table.add_column("ROI %", justify="right", width=6)
            
            if not open_positions:
                positions_table.add_row(
                    "[dim]No open positions[/dim]",
                    "", "", "", "", "", "", ""
                )
            else:
                for pos in open_positions:
                    symbol = pos.get('symbol', 'N/A')
                    side = pos.get('side', 'N/A').upper()
                    contracts = float(pos.get('contracts', 0) or 0)
                    entry_price = float(pos.get('contractSize', 0) or 0)
                    mark_price = float(pos.get('markPrice', 0) or 0)
                    leverage = float(pos.get('leverage', 1) or 1)
                    unrealized = float(pos.get('unrealizedPnl', 0) or 0)
                    percentage = float(pos.get('percentage', 0) or 0)
                    
                    # Format values
                    try:
                        size_str = f"{contracts:,.4f}"
                    except (ValueError, TypeError):
                        size_str = "N/A"
                    
                    try:
                        entry_str = f"${entry_price:,.2f}"
                    except (ValueError, TypeError):
                        entry_str = "N/A"
                    
                    try:
                        mark_str = f"${mark_price:,.2f}"
                    except (ValueError, TypeError):
                        mark_str = "N/A"
                    
                    # P&L color
                    pnl_color = "green" if unrealized >= 0 else "red"
                    pnl_symbol = "+" if unrealized >= 0 else ""
                    pnl_str = f"[{pnl_color}]{pnl_symbol}${abs(unrealized):,.2f}[/{pnl_color}]"
                    
                    # ROI color
                    roi_color = "green" if percentage >= 0 else "red"
                    roi_symbol = "+" if percentage >= 0 else ""
                    roi_str = f"[{roi_color}]{roi_symbol}{percentage:.2f}%[/{roi_color}]"
                    
                    # Side color
                    side_color = "green" if side == "LONG" else "red"
                    
                    positions_table.add_row(
                        symbol,
                        f"[{side_color}]{side}[/{side_color}]",
                        size_str,
                        entry_str,
                        mark_str,
                        f"{leverage:.1f}x",
                        pnl_str,
                        roi_str
                    )
            
            # Add info
            info_text = Text(
                f"📊 Total: {len(open_positions)} | 🔄 {self.update_time.strftime('%H:%M:%S')}",
                style="dim white"
            )
            
            group = Group(positions_table, "", Align.right(info_text))
            
            return Panel(
                group,
                title="[bold magenta]📈 Open Positions[/bold magenta]",
                border_style="magenta",
                padding=(1, 2)
            )
            
        except Exception as e:
            return Panel(
                f"[red]❌ Error: {str(e)}[/red]",
                title="[bold magenta]📈 Open Positions[/bold magenta]",
                border_style="red"
            )

    def _create_order_history_panel(self) -> Panel:
        """Create panel displaying order history"""
        try:
            if not self.exchange:
                return Panel(
                    "[red]❌ Exchange not initialized[/red]",
                    title="[bold green]📜 Order History[/bold green]",
                    border_style="green"
                )
            
            # Fetch closed orders
            closed_orders = []
            try:
                closed = self.exchange.fetch_closed_orders()
                closed_orders.extend(closed)
            except Exception:
                pass
            
            # Sort by time descending (newest first)
            closed_orders.sort(
                key=lambda x: x.get('timestamp', 0),
                reverse=True
            )
            
            # Create orders table
            orders_table = Table(
                show_header=True,
                header_style="bold green",
                box=None,
                padding=(0, 1)
            )
            orders_table.add_column("Symbol", style="cyan", width=12)
            orders_table.add_column("Side", style="magenta", width=6)
            orders_table.add_column("Type", style="blue", width=8)
            orders_table.add_column("Amount", justify="right", width=8)
            orders_table.add_column("Filled", justify="right", width=8)
            orders_table.add_column("Avg Price", justify="right", style="blue", width=12)
            orders_table.add_column("Price", justify="right", style="green", width=12)
            orders_table.add_column("Fee", justify="right", style="yellow", width=10)
            orders_table.add_column("P&L", justify="right", width=8)
            orders_table.add_column("ROI %", justify="right", width=8)
            orders_table.add_column("Time", style="dim", width=10)
            
            if not closed_orders:
                orders_table.add_row(
                    "[dim]No order history[/dim]",
                    "", "", "", "", "", "", "", "", "", ""
                )
            else:
                # Display latest 10 orders
                for order in closed_orders[:10]:
                    symbol = order.get('symbol', 'N/A')
                    side = order.get('side', 'N/A').upper()
                    order_type = order.get('type', 'N/A').upper()
                    amount = order.get('amount') or 0
                    filled = order.get('filled') or 0
                    price = order.get('price') or 0
                    cost = order.get('cost', 0) or 0
                    fee = order.get('fee', {})
                    
                    # Calculate average fill price
                    avg_price = 0
                    filled_str = "0"
                    avg_price_str = "N/A"
                    
                    try:
                        filled_float = float(filled) if filled else 0
                        cost_float = float(cost) if cost else 0
                        
                        if filled_float > 0 and cost_float > 0:
                            avg_price = cost_float / filled_float
                            filled_str = f"{filled_float:,.4f}"
                            avg_price_str = f"${avg_price:,.2f}"
                        else:
                            filled_str = f"{filled_float:,.4f}"
                    except (ValueError, TypeError):
                        filled_str = "N/A"
                    
                    # Format amount and price
                    try:
                        amount_str = f"{float(amount):,.4f}" if amount else "0"
                    except (ValueError, TypeError):
                        amount_str = "N/A"
                    
                    try:
                        price_str = f"${float(price):,.2f}" if price else "$0"
                    except (ValueError, TypeError):
                        price_str = "N/A"
                    
                    # Extract fee info
                    fee_value = 0
                    fee_str = "$0"
                    if isinstance(fee, dict):
                        fee_value = float(fee.get('cost', 0) or 0)
                        if fee_value:
                            fee_str = f"${fee_value:,.2f}"
                    
                    # Calculate P&L (simplified for closed orders)
                    pnl_display = "N/A"
                    roi_display = "N/A"
                    
                    try:
                        order_info = order.get('info', {})
                        
                        # Try to get P&L from order info
                        pnl_value = float(order_info.get('pnl', 0) or 0)
                        if pnl_value != 0:
                            pnl_color = "green" if pnl_value >= 0 else "red"
                            pnl_symbol = "+" if pnl_value >= 0 else ""
                            pnl_display = f"[{pnl_color}]{pnl_symbol}${abs(pnl_value):,.2f}[/{pnl_color}]"
                            
                            # Calculate ROI
                            entry_cost = float(order_info.get('cumExecValue', cost) or cost or 0)
                            if entry_cost > 0:
                                roi_pct = (pnl_value / entry_cost) * 100
                                roi_color = "green" if roi_pct >= 0 else "red"
                                roi_symbol = "+" if roi_pct >= 0 else ""
                                roi_display = f"[{roi_color}]{roi_symbol}{roi_pct:.2f}%[/{roi_color}]"
                    except (ValueError, TypeError, ZeroDivisionError):
                        pass
                    
                    # Format time
                    timestamp = order.get('timestamp', 0)
                    if timestamp:
                        order_time = datetime.fromtimestamp(timestamp/1000).strftime('%H:%M:%S')
                    else:
                        order_time = 'N/A'
                    
                    # Color side
                    side_color = "green" if side == "BUY" else "red"
                    
                    orders_table.add_row(
                        symbol,
                        f"[{side_color}]{side}[/{side_color}]",
                        order_type,
                        amount_str,
                        filled_str,
                        avg_price_str,
                        price_str,
                        fee_str,
                        pnl_display,
                        roi_display,
                        order_time
                    )
            
            # Add info
            info_text = Text(
                f"📊 Total: {len(closed_orders)} orders | 🔄 {self.update_time.strftime('%H:%M:%S')}",
                style="dim white"
            )
            
            group = Group(orders_table, "", Align.right(info_text))
            
            return Panel(
                group,
                title="[bold green]📜 Order History (Latest 10)[/bold green]",
                border_style="green",
                padding=(1, 2)
            )
            
        except Exception as e:
            return Panel(
                f"[red]❌ Error: {str(e)}[/red]",
                title="[bold green]📜 Order History[/bold green]",
                border_style="red"
            )

    def _create_layout(self) -> Layout:
        """Create dashboard layout"""
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main"),
            Layout(name="footer", size=3)
        )
        
        # Header
        title = Text("🚀 OKX Trading Dashboard", style="bold cyan", justify="center")
        layout["header"].update(Panel(title, border_style="cyan"))
        
        # Main area - 2 rows layout (grid 2x2)
        layout["main"].split_column(
            Layout(name="top"),
            Layout(name="bottom")
        )
        
        # Top row: Balance (left) + Open Orders (right)
        layout["top"].split_row(
            Layout(self._create_balance_panel(), name="balance"),
            Layout(self._create_orders_panel(), name="orders")
        )
        
        # Bottom row: Positions (left) + Order History (right)
        layout["bottom"].split_row(
            Layout(self._create_positions_panel(), name="positions"),
            Layout(self._create_order_history_panel(), name="history")
        )
        
        # Footer
        status = Text(
            f"Status: Connected | Update Interval: {self.update_interval}s | {self.update_time.strftime('%Y-%m-%d %H:%M:%S')}",
            style="dim white",
            justify="center"
        )
        layout["footer"].update(Panel(status, border_style="cyan"))
        
        return layout

    def _update_data(self) -> None:
        """Update dashboard data"""
        try:
            self.update_time = datetime.now()
            
            if not self.exchange:
                self.exchange = get_exchange()
            
            # Update balance
            self.balance_data = self._get_account_balance()
            
            # Update orders
            try:
                self.orders_data = self.exchange.fetch_open_orders()
            except Exception:
                self.orders_data = []
            
            # Update positions
            try:
                self.positions_data = self.exchange.fetch_positions()
            except Exception:
                self.positions_data = []
                
        except Exception as e:
            self.console.print(f"[red]Error updating data: {e}[/red]")

    async def start(self) -> None:
        """Start the dashboard display"""
        self.is_running = True
        
        try:
            self.exchange = get_exchange()
            self.console.print("[green]✓ Connected to OKX[/green]")
        except Exception as e:
            self.console.print(f"[red]✗ Connection failed: {e}[/red]")
            return
        
        try:
            with Live(self._create_layout(), refresh_per_second=1, console=self.console) as live:
                while self.is_running:
                    self._update_data()
                    live.update(self._create_layout())
                    await asyncio.sleep(self.update_interval)
        except KeyboardInterrupt:
            self.is_running = False
            self.console.print("\n[yellow]Dashboard stopped[/yellow]")

    def stop(self) -> None:
        """Stop the dashboard"""
        self.is_running = False


def main():
    """Main entry point"""
    dashboard = OKXDashboard(update_interval=2.0)
    
    try:
        asyncio.run(dashboard.start())
    except KeyboardInterrupt:
        dashboard.stop()


if __name__ == '__main__':
    main()
