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

from src.trade.bybit.exchange import get_exchange


class BybitDashboard:
    def __init__(self, update_interval: float = 1.0):
        """
        Khởi tạo Bybit Dashboard
        
        Args:
            update_interval: Khoảng thời gian cập nhật (giây)
        """
        self.console = Console()
        self.exchange: Optional[ccxt.bybit] = None
        self.update_interval = update_interval
        self.is_running = False
        
        # Data cache
        self.balance_data = {}
        self.orders_data = []
        self.positions_data = []
        self.update_time = datetime.now()
        # Simple cache for tickers or special balances to reduce rate limits
        self._ticker_cache = {}

    def _get_unified_margin_balance(self) -> dict:
        """Try multiple v5/private endpoints and fallbacks to obtain unified margin balance

        Returns:
            dict: {'total': float or None, 'available': float or None}
        """
        margin_total = None
        margin_available = None

        # 1) Try common private V5 endpoints exposed by CCXT (best effort)
        candidates = [
            'privateGetV5AccountQueryBalance',
            'privateGetV5AccountWalletBalance',
            'privateGetAccountWalletBalance',
            'privateGetWalletBalance',
            'privateGetV2PrivateWalletBalance',
        ]

        for method_name in candidates:
            try:
                method = getattr(self.exchange, method_name, None)
                if method is None:
                    continue
                resp = method({})
                if not isinstance(resp, dict):
                    continue

                # Try to find list of coins in common keys
                for key in ('result', 'data', 'balances', 'list', 'rows'):
                    items = resp.get(key)
                    if isinstance(items, list):
                        for itm in items:
                            if not isinstance(itm, dict):
                                continue
                            coin = (itm.get('coin') or itm.get('currency') or itm.get('asset') or '').upper()
                            # Bybit v5 often uses 'equity' or 'availableBalance'
                            equity = itm.get('equity') or itm.get('totalEquity') or itm.get('balance') or itm.get('walletBalance') or itm.get('amount') or itm.get('availableBalance')
                            available = itm.get('availableBalance') or itm.get('free')
                            if coin in ('USDT', 'USDC') and equity is not None:
                                margin_total = float(equity)
                                if available is not None:
                                    margin_available = float(available)
                                return {'total': margin_total, 'available': margin_available}

                # If resp itself is a dict keyed by coin
                for k, v in resp.items():
                    if isinstance(v, dict):
                        coin = k.upper()
                        equity = v.get('equity') or v.get('total') or v.get('balance')
                        available = v.get('free') or v.get('available')
                        if coin in ('USDT', 'USDC') and equity is not None:
                            return {'total': float(equity), 'available': float(available) if available is not None else None}

            except Exception:
                continue

        # 2) Try fetch_balance with possible types (margin / future / funding)
        try_types = [{'type': 'margin'}, {'type': 'future'}, {'type': 'swap'}, {}]
        for params in try_types:
            try:
                fb = self.exchange.fetch_balance(params)
                # Check typical structure
                for coin in ('USDT', 'USDC'):
                    data = fb.get(coin)
                    if isinstance(data, dict):
                        total = data.get('total') or data.get('equity') or data.get('balance')
                        free = data.get('free') or data.get('available')
                        if total is not None:
                            return {'total': float(total), 'available': float(free) if free is not None else None}
                # Also check fb['info'] for unified wallet
                info = fb.get('info') or {}
                if isinstance(info, dict):
                    # scan for totals
                    for v in info.values():
                        if isinstance(v, dict) and ('equity' in v or 'availableBalance' in v):
                            if v.get('coin', '').upper() in ('USDT', 'USDC'):
                                return {'total': float(v.get('equity') or v.get('balance') or 0), 'available': float(v.get('availableBalance') or 0)}
            except Exception:
                continue

        return {'total': margin_total, 'available': margin_available}

    def _get_btc_prices(self) -> dict:
        """Fetch BTC mark/index/last prices using available symbols."""
        symbols_to_try = ['BTC/USDC', 'BTC/USDT', 'BTC/USDT:USDT', 'BTC/USDC:USDC']
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
            
            # Create balance table
            balance_table = Table(show_header=True, header_style="bold magenta", box=None)
            balance_table.add_column("Type", style="cyan", width=15)
            balance_table.add_column("Value (USDT)", justify="right", style="green", width=20)
            
            balance_table.add_row("Total (stable)", f"${total_stable:,.2f}")
            balance_table.add_row("Available", f"${free_stable:,.2f}")
            balance_table.add_row("In Use", f"${used_stable:,.2f}")
            
            # Estimate USD value of other tokens (convert to stable using ticker)
            estimated_total = total_stable
            other_tokens = []
            for k, v in balance.items():
                if k in stable_keys:
                    continue
                if isinstance(v, dict):
                    total = float(v.get('total', 0) or 0)
                    if total and total > 0:
                        # attempt to fetch price in USDT then USDC
                        price = 0.0
                        for quote in ['USDT', 'USDC']:
                            symbol = f"{k}/{quote}"
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
            
            # Show estimated total account value (stable + crypto converted)
            balance_table.add_row("", "")  # empty spacer
            balance_table.add_row("Estimated Total (USD)", f"${estimated_total:,.2f}", style="bold")

            # Attempt to fetch unified margin balance (matches UI 'Unified Trading Account' values)
            try:
                margin = self._get_unified_margin_balance()
                if margin and margin.get('total') is not None:
                    balance_table.add_row("", "")
                    balance_table.add_row("Margin Balance (Unified)", f"${margin.get('total'):,.2f}")
                    if margin.get('available') is not None:
                        balance_table.add_row("Margin Available", f"${margin.get('available'):,.2f}")
            except Exception:
                pass

            # Attempt to fetch BTC mark/index/last prices for quick comparison
            try:
                btc = self._get_btc_prices()
                if btc and btc.get('symbol'):
                    balance_table.add_row("", "")
                    balance_table.add_row("BTC Symbol", f"{btc.get('symbol')}")
                    if btc.get('last'):
                        balance_table.add_row("BTC Last", f"${btc.get('last'):,.2f}")
                    if btc.get('mark'):
                        balance_table.add_row("BTC Mark", f"${btc.get('mark'):,.2f}")
                    if btc.get('index'):
                        balance_table.add_row("BTC Index", f"${btc.get('index'):,.2f}")
            except Exception:
                pass

            if other_tokens:
                for token, total, price, usd_value in other_tokens[:5]:
                    balance_table.add_row(
                        f"{token}",
                        f"{total:,.8f}  (${usd_value:,.2f})",
                        style="dim"
                    )
            
            # Add update info
            update_text = Text(
                f"🔄 Updated: {self.update_time.strftime('%H:%M:%S')}",
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
        """Create panel displaying open orders in real-time"""
        try:
            if not self.exchange:
                return Panel(
                    "[red]❌ Exchange not initialized[/red]",
                    title="[bold yellow]📊 Open Orders (Realtime)[/bold yellow]",
                    border_style="yellow"
                )
            
            # Fetch open orders
            orders = self.exchange.fetch_open_orders()
            
            orders_table = Table(
                show_header=True,
                header_style="bold yellow",
                box=None
            )
            orders_table.add_column("Symbol", style="cyan", width=12)
            orders_table.add_column("Side", style="magenta", width=6)
            orders_table.add_column("Type", style="blue", width=8)
            orders_table.add_column("Quantity", justify="right", width=12)
            orders_table.add_column("Price", justify="right", style="green", width=14)
            orders_table.add_column("Status", style="yellow", width=10)
            orders_table.add_column("Time", style="dim", width=10)
            
            if not orders:
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
                for order in orders[:10]:  # Max 10 orders
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
                    status_color = "green" if status == "OPEN" else "yellow"
                    
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
                f"📈 Total Orders: {len(orders)} | 🔄 {self.update_time.strftime('%H:%M:%S')}",
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
    
    def _create_orders_history_panel(self) -> Panel:
        """Create panel displaying order history with scrolling"""
        try:
            if not self.exchange:
                return Panel(
                    "[red]❌ Exchange not initialized[/red]",
                    title="[bold green]📜 Order History[/bold green]",
                    border_style="green"
                )
            
            # Fetch closed and canceled orders (Bybit 5/02 update)
            closed_orders = []
            try:
                closed = self.exchange.fetch_closed_orders()
                closed_orders.extend(closed)
            except Exception:
                pass
            
            try:
                canceled = self.exchange.fetch_canceled_orders()
                closed_orders.extend(canceled)
            except Exception:
                pass
            
            # Sort by time descending (newest first)
            closed_orders.sort(
                key=lambda x: x.get('timestamp', 0), 
                reverse=True
            )
            
            orders_table = Table(
                show_header=True,
                header_style="bold green",
                box=None,
                padding=(0, 1)
            )
            orders_table.add_column("Symbol", style="cyan", width=12)
            orders_table.add_column("Side", style="magenta", width=6)
            orders_table.add_column("Type", style="blue", width=8)
            orders_table.add_column("Amount", justify="right", width=12)
            orders_table.add_column("Price", justify="right", style="green", width=12)
            orders_table.add_column("Status", style="yellow", width=10)
            orders_table.add_column("Time", style="dim", width=10)
            
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
                title="[bold green]📜 Order History (All - Newest First)[/bold green]",
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
                    entry_price = float(pos.get('info', {}).get('avgPrice', 0) or 0)
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
                    "🤖 BYBIT TRADING DASHBOARD",
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
            self.console.print("[cyan]🚀 Starting Bybit Dashboard...[/cyan]")
            
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
    dashboard = BybitDashboard(update_interval=update_interval)
    dashboard.start()


if __name__ == '__main__':
    # Run dashboard with update every 2 seconds
    run_dashboard(update_interval=2.0)
