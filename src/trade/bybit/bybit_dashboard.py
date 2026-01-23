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
            # Hidden: BTC and ETH prices removed from balance panel
            # try:
            #     btc = self._get_btc_prices()
            #     if btc and btc.get('symbol'):
            #         balance_table.add_row("", "")
            #         balance_table.add_row("BTC Symbol", f"{btc.get('symbol')}")
            #         if btc.get('last'):
            #             balance_table.add_row("BTC Last", f"${btc.get('last'):,.2f}")
            #         if btc.get('mark'):
            #             balance_table.add_row("BTC Mark", f"${btc.get('mark'):,.2f}")
            #         if btc.get('index'):
            #             balance_table.add_row("BTC Index", f"${btc.get('index'):,.2f}")
            # except Exception:
            #     pass

            # Hidden: BTC and ETH tokens removed from display
            # if other_tokens:
            #     for token, total, price, usd_value in other_tokens[:5]:
            #         balance_table.add_row(
            #             f"{token}",
            #             f"{total:,.8f}  (${usd_value:,.2f})",
            #             style="dim"
            #         )
            
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
            orders_table.add_column("TP", justify="right", style="cyan", width=8)
            orders_table.add_column("SL", justify="right", style="red", width=8)
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
                    "",
                    "",
                    ""
               )
            else:
                # Group orders by parentOrderId to combine TP/SL with main order
                # Also group orphan TP/SL orders by symbol/side/qty
                grouped_orders = {}
                orphan_tpsl = {}  # Store orphan TP/SL orders to group by symbol/side/qty
                
                for order in orders:
                    info = order.get('info', {})
                    parent_id = info.get('parentOrderId')
                    order_id = order.get('id')
                    stop_order_type = info.get('stopOrderType', '')  # 'TakeProfit', 'StopLoss', or empty
                    symbol = order.get('symbol', '')
                    side = order.get('side', '').upper()
                    qty = order.get('amount') or info.get('qty', '')
                    
                    # Determine if this is a TP or SL order based on stopOrderType
                    is_tp = stop_order_type == 'TakeProfit'
                    is_sl = stop_order_type == 'StopLoss'
                    is_main = not (is_tp or is_sl)
                    
                    # If this is a child order (TP/SL), group it with parent
                    if (is_tp or is_sl) and parent_id and parent_id != '0':
                        if parent_id not in grouped_orders:
                            grouped_orders[parent_id] = {'main': None, 'tp': None, 'sl': None}
                        if is_tp:
                            grouped_orders[parent_id]['tp'] = order
                        elif is_sl:
                            grouped_orders[parent_id]['sl'] = order
                    elif is_main:
                        # This is a main order
                        if order_id not in grouped_orders:
                            grouped_orders[order_id] = {'main': None, 'tp': None, 'sl': None}
                        grouped_orders[order_id]['main'] = order
                    elif is_tp or is_sl:
                        # Orphan TP/SL order - group by symbol/side/qty
                        group_key = f"{symbol}:{side}:{qty}"
                        if group_key not in orphan_tpsl:
                            orphan_tpsl[group_key] = {'main': None, 'tp': None, 'sl': None}
                        if is_tp:
                            orphan_tpsl[group_key]['tp'] = order
                        elif is_sl:
                            orphan_tpsl[group_key]['sl'] = order
                
                # Add orphan TP/SL groups to main grouped_orders
                for group_key, group_data in orphan_tpsl.items():
                    # Use group_key as the identifier for orphan groups
                    grouped_orders[f"orphan_{group_key}"] = group_data
                
                # Display grouped orders
                displayed_count = 0
                for group_key, group_data in grouped_orders.items():
                    if displayed_count >= 10:  # Max 10 order groups
                        break
                    
                    main_order = group_data.get('main')
                    tp_order = group_data.get('tp')
                    sl_order = group_data.get('sl')
                    
                    if not main_order:
                        continue
                    
                    symbol = main_order.get('symbol', 'N/A')
                    side = main_order.get('side', 'N/A').upper()
                    order_type = main_order.get('type', 'N/A').upper()
                    amount = main_order.get('amount') or 0
                    price = main_order.get('price') or 0
                    status = main_order.get('status', 'N/A').upper()
                    main_info = main_order.get('info', {})
                    
                    # Extract TP and SL from child orders or order info
                    tp_price = None
                    sl_price = None
                    
                    if tp_order:
                        tp_info = tp_order.get('info', {})
                        # For TP orders, get triggerPrice
                        tp_price = tp_order.get('price') or tp_info.get('triggerPrice')
                    if sl_order:
                        sl_info = sl_order.get('info', {})
                        # For SL orders, get triggerPrice
                        sl_price = sl_order.get('price') or sl_info.get('triggerPrice')
                    
                    # Fallback to info fields if not found in child orders
                    if not tp_price or not sl_price:
                        if not tp_price:
                            tp_price = main_info.get('takeProfit') or main_order.get('stopPrice')
                        if not sl_price:
                            sl_price = main_info.get('stopLoss')
                    
                    # Format amount and price safely
                    try:
                        amount_str = f"{float(amount):,.4f}" if amount else "0"
                    except (ValueError, TypeError):
                        amount_str = "N/A"
                    
                    try:
                        price_str = f"${float(price):,.2f}" if price else "$0"
                    except (ValueError, TypeError):
                        price_str = "N/A"
                    
                    # Format TP
                    try:
                        tp_str = f"${float(tp_price):,.2f}" if tp_price else "-"
                    except (ValueError, TypeError):
                        tp_str = "-"
                    
                    # Format SL
                    try:
                        sl_str = f"${float(sl_price):,.2f}" if sl_price else "-"
                    except (ValueError, TypeError):
                        sl_str = "-"
                    
                    # Format time
                    timestamp = main_order.get('timestamp', 0)
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
                        amount_str,
                        price_str,
                        tp_str,
                        sl_str,
                        f"[{status_color}]{status}[/{status_color}]",
                        order_time
                    )
                    displayed_count += 1
            
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
            orders_table.add_column("Filled", justify="right", width=10)
            orders_table.add_column("Avg Price", justify="right", style="blue", width=12)
            orders_table.add_column("Price", justify="right", style="green", width=12)
            orders_table.add_column("Fee", justify="right", style="yellow", width=10)
            orders_table.add_column("P&L", justify="right", width=12)
            orders_table.add_column("ROI %", justify="right", width=10)
            orders_table.add_column("Time", style="dim", width=10)
            
            if not closed_orders:
                 orders_table.add_row(
                     "[dim]No order history[/dim]",
                     "",
                     "",
                     "",
                     "",
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
                     
                     # Format amount and price safely
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
                     
                     # Calculate P&L and ROI
                     pnl_value = None
                     pnl_display = "N/A"
                     pnl_color = "dim"
                     roi_display = "N/A"
                     roi_color = "dim"
                     
                     try:
                         order_info = order.get('info', {})
                         
                         # Check if this is a position-closing order (reduceOnly, TP/SL)
                         is_reduce_only = order.get('reduceOnly', False)
                         stop_order_type = order_info.get('stopOrderType', '')
                         is_position_close = is_reduce_only or stop_order_type in ('TakeProfit', 'StopLoss')
                         
                         if is_position_close:
                             # Position closing order - calculate P&L from entry/exit prices
                             entry_price = float(order_info.get('price', 0) or 0)
                             exit_price = float(order_info.get('avgPrice', 0) or 0)
                             qty = float(order_info.get('cumExecQty', filled) or 0)
                             
                             if entry_price > 0 and exit_price > 0 and qty > 0:
                                 if side == "BUY":
                                     # Closing SHORT position
                                     pnl_value = (entry_price - exit_price) * qty
                                 else:
                                     # Closing LONG position
                                     pnl_value = (exit_price - entry_price) * qty
                                 
                                 # Subtract fees
                                 fee_cost = float(order_info.get('cumExecFee', 0) or 0)
                                 if fee_cost:
                                     pnl_value -= fee_cost
                         else:
                             # Regular opening order - compare cost vs expected value
                             cost_float = float(cost) if cost else 0
                             avg_price_calc = float(order_info.get('avgPrice', 0) or 0)
                             qty = float(order_info.get('cumExecQty', filled) or 0)
                             
                             # For regular orders, P&L is minimal at fill
                             if cost_float > 0 and avg_price_calc > 0 and qty > 0:
                                 expected_cost = avg_price_calc * qty
                                 pnl_value = cost_float - expected_cost
                         
                         # Only display if we have meaningful data
                         if pnl_value is not None and abs(pnl_value) > 0.01:
                             pnl_color = "green" if pnl_value >= 0 else "red"
                             pnl_symbol = "+" if pnl_value >= 0 else ""
                             pnl_display = f"[{pnl_color}]{pnl_symbol}${abs(pnl_value):,.2f}[/{pnl_color}]"
                             
                             # Calculate ROI % only if we have positive entry cost
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
                    
                    # Get PnL % from Bybit API
                    info = pos.get('info', {})
                    pnl_pct = 0
                    
                    # Get unrealisedPnl (absolute value in USDT)
                    unrealised_pnl = float(info.get('unrealisedPnl', 0) or 0)
                    
                    # Get position value to calculate percentage
                    position_value = float(info.get('positionValue', 0) or 0)
                    position_balance = float(info.get('positionBalance', 0) or 0)
                    
                    # Calculate PnL % = (unrealisedPnl / positionValue) * 100
                    if position_value and position_value != 0:
                        pnl_pct = (unrealised_pnl / position_value) * 100
                    elif position_balance and position_balance != 0:
                        pnl_pct = (unrealised_pnl / position_balance) * 100
                    
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
            Layout(self._create_balance_panel(), name="balance", size=15),
            Layout(self._create_orders_history_panel(), name="history")
        )
        
        # Right column: Open Orders + Positions (stacked vertically)
        layout["right"].split_column(
            Layout(self._create_orders_panel(), name="orders", size=15),
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
