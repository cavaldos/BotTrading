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

from src.exchanges.binance.exchange import get_exchange


class BinanceDashboard:
    def __init__(self, update_interval: float = 1.0):
        """
        Khởi tạo Binance Dashboard
        
        Args:
            update_interval: Khoảng thời gian cập nhật (giây)
        """
        self.console = Console()
        self.exchange: Optional[ccxt.binance] = None
        self.update_interval = update_interval
        self.is_running = False
        
        # Data cache
        self.balance_data = {}
        self.orders_data = []
        self.positions_data = []
        self.update_time = datetime.now()
        # Simple cache for tickers or special balances to reduce rate limits
        self._ticker_cache = {}

    def _get_wallet_balance(self) -> dict:
        """Try to obtain wallet balance with fallbacks
        
        Returns:
            dict: {'total': float or None, 'available': float or None}
        """
        balance_total = None
        balance_available = None

        try:
            # Fetch full balance
            fb = self.exchange.fetch_balance()
            
            # Check USDT and USDC
            for coin in ('USDT', 'USDC'):
                data = fb.get(coin)
                if isinstance(data, dict):
                    total = data.get('total')
                    free = data.get('free')
                    if total is not None:
                        return {'total': float(total), 'available': float(free) if free is not None else None}
            
        except Exception:
            pass

        return {'total': balance_total, 'available': balance_available}

    def _get_btc_prices(self) -> dict:
        """Fetch BTC mark/index/last prices using available symbols."""
        symbols_to_try = ['BTC/USDT', 'BTC/USDT:USDT']
        for s in symbols_to_try:
            try:
                ticker = self.exchange.fetch_ticker(s)
                info = ticker.get('info') or {}
                last = float(ticker.get('last') or ticker.get('close') or 0)
                mark = info.get('markPrice') or info.get('mark_price') or info.get('mark')
                index = info.get('indexPrice') or info.get('index_price') or info.get('index')
                # convert to float when possible
                try:
                    mark = float(mark) if mark is not None else None
                except Exception:
                    mark = None
                try:
                    index = float(index) if index is not None else None
                except Exception:
                    index = None
                return {'symbol': s, 'last': last, 'mark': mark, 'index': index}
            except Exception:
                continue

        # fallback: try generic BTC/USDT ticker
        try:
            ticker = self.exchange.fetch_ticker('BTC/USDT')
            return {'symbol': 'BTC/USDT', 'last': float(ticker.get('last') or 0), 'mark': None, 'index': None}
        except Exception:
            return {'symbol': None, 'last': None, 'mark': None, 'index': None}

    def _create_balance_panel(self) -> Panel:
        """Create panel displaying account balance (Spot + Futures)"""
        try:
            if not self.exchange:
                return Panel(
                    "[red]❌ Exchange not initialized[/red]",
                    title="[bold cyan]💰 Account Balance[/bold cyan]",
                    border_style="cyan"
                )
            
            # Create balance table
            balance_table = Table(show_header=True, header_style="bold magenta", box=None)
            balance_table.add_column("Type", style="cyan", width=20)
            balance_table.add_column("Value (USDT)", justify="right", style="green", width=20)
            
            stable_keys = ['USDT', 'USDC']
            total_all = 0.0
            
            # ===== SPOT WALLET =====
            try:
                spot_balance = self.exchange.fetch_balance({'type': 'spot'})
                spot_total = 0.0
                spot_free = 0.0
                spot_used = 0.0
                spot_assets = {}
                
                # Collect all assets with balance > 0
                for k, v in spot_balance.items():
                    if isinstance(v, dict) and k not in ['free', 'used', 'total', 'info']:
                        total = float(v.get('total', 0) or 0)
                        free = float(v.get('free', 0) or 0)
                        used = float(v.get('used', 0) or 0)
                        if total > 0:
                            spot_assets[k] = {'total': total, 'free': free, 'used': used}
                
                # Calculate stablecoin totals
                for k in stable_keys:
                    if k in spot_assets:
                        spot_total += spot_assets[k]['total']
                        spot_free += spot_assets[k]['free']
                        spot_used += spot_assets[k]['used']
                
                balance_table.add_row("[bold cyan]SPOT WALLET[/bold cyan]", "")
                balance_table.add_row("  Total (USDT/USDC)", f"${spot_total:,.2f}")
                balance_table.add_row("  Available", f"${spot_free:,.2f}")
                balance_table.add_row("  In Use", f"${spot_used:,.2f}")
                
                # Show other assets in Spot
                for asset, data in sorted(spot_assets.items()):
                    if asset not in stable_keys:
                        try:
                            ticker = self.exchange.fetch_ticker(f"{asset}/USDT")
                            price = float(ticker.get('last', 0) or 0)
                            usd_value = data['total'] * price
                            balance_table.add_row(f"  {asset}", f"${usd_value:,.2f} ({data['total']:.6f})")
                            spot_total += usd_value
                        except Exception:
                            balance_table.add_row(f"  {asset}", f"{data['total']:.6f}")
                
                total_all += spot_total
            except Exception as e:
                balance_table.add_row("[bold cyan]SPOT WALLET[/bold cyan]", "[red]Error[/red]")
            
            # ===== FUTURES WALLET =====
            try:
                futures_balance = self.exchange.fetch_balance({'type': 'future'})
                futures_total = 0.0
                futures_free = 0.0
                futures_used = 0.0
                
                for k in stable_keys:
                    data = futures_balance.get(k)
                    if isinstance(data, dict):
                        futures_total += float(data.get('total', 0) or 0)
                        futures_free += float(data.get('free', 0) or 0)
                        futures_used += float(data.get('used', 0) or 0)
                
                balance_table.add_row("", "")  # spacer
                balance_table.add_row("[bold magenta]FUTURES WALLET[/bold magenta]", "")
                balance_table.add_row("  Total", f"${futures_total:,.2f}")
                balance_table.add_row("  Available", f"${futures_free:,.2f}")
                balance_table.add_row("  In Use", f"${futures_used:,.2f}")
                total_all += futures_total
            except Exception as e:
                balance_table.add_row("", "")
                balance_table.add_row("[bold magenta]FUTURES WALLET[/bold magenta]", "[red]Error[/red]")
            
            # ===== TOTAL =====
            balance_table.add_row("", "")  # spacer
            balance_table.add_row("[bold green]TOTAL BALANCE[/bold green]", f"[bold green]${total_all:,.2f}[/bold green]")

            # Attempt to fetch BTC mark/index/last prices for quick comparison
            try:
                btc = self._get_btc_prices()
                if btc and btc.get('symbol'):
                    balance_table.add_row("", "")
                    balance_table.add_row("[dim]BTC Prices[/dim]", "")
                    if btc.get('last'):
                        balance_table.add_row("  Last", f"${btc.get('last'):,.2f}")
            except Exception:
                pass
            
            # Add update info
            update_text = Text(
                f"🔄 Updated: {self.update_time.strftime('%H:%M:%S')}",
                style="dim white"
            )
            
            group = Group(balance_table, "", Align.right(update_text))
            
            return Panel(
                group,
                title="[bold cyan]💰 Account Balance (Spot + Futures)[/bold cyan]",
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
                    title="[bold yellow]📋 Open Orders[/bold yellow]",
                    border_style="yellow"
                )
            
            # Fetch open orders
            open_orders = self.exchange.fetch_open_orders()
            
            orders_table = Table(
                show_header=True,
                header_style="bold yellow",
                box=None
            )
            orders_table.add_column("Symbol", style="cyan", width=12)
            orders_table.add_column("Side", style="yellow", width=6)
            orders_table.add_column("Type", width=8)
            orders_table.add_column("Amount", justify="right", width=14)
            orders_table.add_column("Price", justify="right", width=14)
            orders_table.add_column("Status", width=10)
            orders_table.add_column("Time", width=10)
            
            if not open_orders:
                orders_table.add_row(
                    "[dim]No open orders[/dim]",
                    "",
                    "",
                    "",
                    "",
                    "",
                    ""
                )
            else:
                # Display all open orders
                for order in open_orders:
                    symbol = order.get('symbol', 'N/A')
                    side = order.get('side', 'N/A').upper()
                    order_type = order.get('type', 'N/A').upper()
                    amount = order.get('amount', 0)
                    price = order.get('price', 0)
                    status = order.get('status', 'N/A').upper()
                    
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
                        f"{amount:,.4f}",
                        f"${price:,.2f}",
                        status,
                        order_time
                    )
            
            # Add info
            info_text = Text(
                f"📊 Total: {len(open_orders)} orders | 🔄 {self.update_time.strftime('%H:%M:%S')}",
                style="dim white"
            )
            
            group = Group(orders_table, "", Align.right(info_text))
            
            return Panel(
                group,
                title="[bold yellow]📋 Open Orders[/bold yellow]",
                border_style="yellow",
                padding=(1, 2)
            )
            
        except Exception as e:
            return Panel(
                f"[red]❌ Error: {str(e)}[/red]",
                title="[bold yellow]📋 Open Orders[/bold yellow]",
                border_style="red"
            )

    def _create_orders_history_panel(self) -> Panel:
        """Create panel displaying order history"""
        try:
            if not self.exchange:
                return Panel(
                    "[red]❌ Exchange not initialized[/red]",
                    title="[bold green]📜 Order History[/bold green]",
                    border_style="green"
                )
            
            # Binance requires a symbol argument for fetch_closed_orders
            # Fetch closed orders for major symbols
            closed_orders = []
            symbols_to_try = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT']
            
            for symbol in symbols_to_try:
                try:
                    orders = self.exchange.fetch_closed_orders(symbol, limit=10)
                    closed_orders.extend(orders)
                except Exception:
                    continue
            
            # Sort by timestamp (newest first)
            closed_orders.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
            closed_orders = closed_orders[:20]  # Keep only 20 most recent
            
            orders_table = Table(
                show_header=True,
                header_style="bold green",
                box=None
            )
            orders_table.add_column("Symbol", style="cyan", width=12)
            orders_table.add_column("Side", style="green", width=6)
            orders_table.add_column("Type", width=8)
            orders_table.add_column("Amount", justify="right", width=14)
            orders_table.add_column("Price", justify="right", width=14)
            orders_table.add_column("Status", width=10)
            orders_table.add_column("Time", width=10)
            
            if not closed_orders:
                orders_table.add_row(
                    "[dim]No order history[/dim]",
                    "",
                    "",
                    "",
                    "",
                    "",
                    ""
                )
            else:
                # Display all orders, newest first
                for order in closed_orders:
                    symbol = order.get('symbol', 'N/A')
                    side = order.get('side', 'N/A').upper()
                    order_type = order.get('type', 'N/A').upper()
                    amount = order.get('amount', 0)
                    price = order.get('price', 0)
                    status = order.get('status', 'N/A').upper()
                    
                    # Format time
                    timestamp = order.get('timestamp', 0)
                    if timestamp:
                        order_time = datetime.fromtimestamp(timestamp/1000).strftime('%H:%M:%S')
                    else:
                        order_time = 'N/A'
                    
                    # Color side
                    side_color = "green" if side == "BUY" else "red"
                    status_color = "dim" if status == "CANCELED" else "green"
                    
                    orders_table.add_row(
                        symbol,
                        f"[{side_color}]{side}[/{side_color}]",
                        order_type,
                        f"{amount:,.4f}",
                        f"${price:,.2f}",
                        f"[{status_color}]{status}[/{status_color}]",
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
                title="[bold green]📜 Order History (Recent)[/bold green]",
                border_style="green",
                padding=(1, 2),
                height=None  # Allow flexible height for scrolling
            )
            
        except Exception as e:
            return Panel(
                f"[red]❌ Error: {str(e)}[/red]",
                title="[bold green]📜 Order History[/bold green]",
                border_style="red"
            )
    
    def _create_positions_panel(self) -> Panel:
        """Create panel displaying open positions"""
        try:
            if not self.exchange:
                return Panel(
                    "[red]❌ Exchange not initialized[/red]",
                    title="[bold magenta]📍 Open Positions[/bold magenta]",
                    border_style="magenta"
                )
            
            # Fetch positions
            positions = self.exchange.fetch_positions()
            
            positions_table = Table(
                show_header=True,
                header_style="bold magenta",
                box=None
            )
            positions_table.add_column("Symbol", style="cyan", width=12)
            positions_table.add_column("Side", style="magenta", width=6)
            positions_table.add_column("Size", justify="right", width=12)
            positions_table.add_column("Entry", justify="right", style="blue", width=14)
            positions_table.add_column("Mark Price", justify="right", width=14)
            positions_table.add_column("PnL %", justify="right", width=10)
            
            # Filter positions with size > 0
            active_positions = [p for p in positions if abs(float(p.get('contracts', 0) or 0)) > 0]
            
            if not active_positions:
                positions_table.add_row(
                    "[dim]No open positions[/dim]",
                    "",
                    "",
                    "",
                    "",
                    ""
                )
            else:
                for pos in active_positions[:8]:  # Max 8 positions
                    symbol = pos.get('symbol', 'N/A')
                    side = pos.get('side', 'N/A').upper()
                    contracts = abs(float(pos.get('contracts', 0) or 0))
                    entry_price = float(pos.get('info', {}).get('entryPrice', 0) or 0)
                    mark_price = float(pos.get('markPrice', 0) or 0)
                    pnl_pct = float(pos.get('percentage', 0) or 0) * 100
                    
                    # Color side
                    side_color = "green" if side == "LONG" else "red"
                    pnl_color = "green" if pnl_pct >= 0 else "red"
                    pnl_symbol = "+" if pnl_pct >= 0 else ""
                    
                    positions_table.add_row(
                        symbol,
                        f"[{side_color}]{side}[/{side_color}]",
                        f"{contracts:,.4f}",
                        f"${entry_price:,.2f}",
                        f"${mark_price:,.2f}",
                        f"[{pnl_color}]{pnl_symbol}{pnl_pct:.2f}%[/{pnl_color}]"
                    )
            
            info_text = Text(
                f"📊 Positions: {len(active_positions)} | 🔄 {self.update_time.strftime('%H:%M:%S')}",
                style="dim white"
            )
            
            group = Group(positions_table, "", Align.right(info_text))
            
            return Panel(
                group,
                title="[bold magenta]📍 Open Positions[/bold magenta]",
                border_style="magenta",
                padding=(1, 2)
            )
            
        except Exception as e:
            return Panel(
                f"[red]❌ Error: {str(e)}[/red]",
                title="[bold magenta]📍 Open Positions[/bold magenta]",
                border_style="red"
            )
    
    def _make_layout(self) -> Layout:
        """Create dashboard layout"""
        layout = Layout()
        
        # Header
        header = Panel(
            Align.center(
                Text(
                    "🤖 BINANCE TRADING DASHBOARD",
                    style="bold cyan"
                )
            ),
            border_style="cyan",
            height=3
        )
        
        # Split vertically: header + body
        layout.split_column(
            Layout(header, name="header", size=3),
            Layout(name="body")
        )
        
        # Split body horizontally: left (balance + history) + right (orders + positions)
        layout["body"].split_row(
            Layout(name="left"),
            Layout(name="right")
        )
        
        # Left column: Balance + Order History (stacked vertically)
        layout["left"].split_column(
            Layout(self._create_balance_panel(), name="balance"),
            Layout(self._create_orders_history_panel(), name="history")
        )
        
        # Right column: Open Orders + Positions (stacked vertically)
        layout["right"].split_column(
            Layout(self._create_orders_panel(), name="orders"),
            Layout(self._create_positions_panel(), name="positions")
        )
        
        return layout
    
    def start(self):
        """Start the dashboard"""
        try:
            self.console.print("[cyan]🚀 Starting Binance Dashboard...[/cyan]")
            
            # Initialize exchange
            self.exchange = get_exchange()
            self.console.print("[green]✓ Exchange connected successfully[/green]")
            
            self.is_running = True
            
            # Update loop
            with Live(
                self._make_layout(),
                console=self.console,
                refresh_per_second=1/self.update_interval,
                screen=True
            ) as live:
                while self.is_running:
                    try:
                        # Update time
                        self.update_time = datetime.now()
                        
                        # Update layout
                        live.update(self._make_layout())
                        
                        # Wait before next update
                        time.sleep(self.update_interval)
                        
                    except KeyboardInterrupt:
                        break
                    except Exception as e:
                        self.console.print(f"[red]❌ Update error: {e}[/red]")
                        time.sleep(self.update_interval)
        
        except KeyboardInterrupt:
            self.console.print("\n[yellow]⚠️  Dashboard stopped by user[/yellow]")
        except Exception as e:
            self.console.print(f"[red]❌ Error: {e}[/red]")
            import traceback
            traceback.print_exc()
        finally:
            self.is_running = False
    
    def stop(self):
        """Stop the dashboard"""
        self.is_running = False
        self.console.print("[yellow]Dashboard stopped[/yellow]")


def run_dashboard(update_interval: float = 2.0):
    """
    Run the dashboard
    
    Args:
        update_interval: Update interval in seconds
    """
    dashboard = BinanceDashboard(update_interval=update_interval)
    dashboard.start()


if __name__ == '__main__':
    # Run dashboard with update every 2 seconds
    run_dashboard(update_interval=2.0)
