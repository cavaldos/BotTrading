"""Lighter trading dashboard (same layout as the Bybit dashboard).

    python -m src.exchanges.lighter.tools.lighter_dashboard

Panels: account balance + session change, open positions, open orders with their TP/SL,
realized P&L history. Balance and positions only need LIGHTER_ACCOUNT_INDEX; open orders and
history also need the API key (LIGHTER_API_PRIVATE_KEY + signer library).

Lighter allows 60 requests per minute per IP (shared with the bot on this machine), so data is
fetched in a background thread on its own schedule (about 11 requests/minute by default) and
the screen redraws from the last values.
"""

from __future__ import annotations

import argparse
import threading
import time
from datetime import datetime
from typing import Callable, Optional

from rich.align import Align
from rich.columns import Columns
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.exchanges.lighter.exchange import TRIGGER_ORDER_TYPES, LighterExchange, get_exchange


def _price(value: Optional[float]) -> str:
    if value is None:
        return "-"
    value = float(value)
    if abs(value) >= 1000:
        return f"${value:,.2f}"
    if abs(value) >= 1:
        return f"${value:,.4f}"
    return f"${value:,.6f}"


def _signed(value: float, decimals: int = 2, prefix: str = "$", suffix: str = "") -> str:
    color = "green" if value >= 0 else "red"
    sign = "+" if value >= 0 else "-"
    return f"[{color}]{sign}{prefix}{abs(value):,.{decimals}f}{suffix}[/{color}]"


def _time(ms: Optional[int], fmt: str = "%H:%M") -> str:
    return datetime.fromtimestamp(ms / 1000).strftime(fmt) if ms else "-"


def _rr(side: str, price: Optional[float], tp: Optional[float], sl: Optional[float]) -> str:
    if not (price and tp and sl):
        return "-"
    long = side in ("buy", "long")
    reward = (tp - price) if long else (price - tp)
    risk = (price - sl) if long else (sl - price)
    return f"{reward / risk:.2f}" if risk > 0 else "-"


class LighterDashboard:
    def __init__(
        self,
        update_interval: float = 1.0,
        account_every: float = 10.0,
        orders_every: float = 15.0,
        history_every: float = 60.0,
    ):
        self.console = Console()
        self.exchange: Optional[LighterExchange] = None
        self.update_interval = update_interval
        self.schedule = {"account": account_every, "orders": orders_every, "history": history_every}
        self.is_running = False

        self.summary: Optional[dict] = None
        self.positions: list[dict] = []
        self.orders: Optional[list[dict]] = None
        self.pnl_records: Optional[list[dict]] = None
        self.errors: dict[str, str] = {}
        self.fetched_at: dict[str, float] = {}
        self.update_time = datetime.now()

        # Session tracking (like the Bybit dashboard)
        self._initial_balance: Optional[float] = None
        self._initial_balance_time: Optional[datetime] = None
        self._stop = threading.Event()

    # -- data (background thread) ----------------------------------------------

    def _load_account(self) -> None:
        account = self.exchange.fetch_account()
        self.summary = self.exchange.account_summary(account)
        self.positions = self.exchange.parse_positions(account)
        if self._initial_balance is None:
            self._initial_balance = self.summary["equity"]
            self._initial_balance_time = datetime.now()

    def _load_orders(self) -> None:
        self.orders = self.exchange.fetch_open_orders()

    def _load_history(self) -> None:
        self.pnl_records = self.exchange.fetch_closed_pnl(limit=25)

    def _jobs(self) -> list[tuple[str, Callable[[], None]]]:
        jobs = [("account", self._load_account)]
        if self.exchange.has_api_key:
            jobs += [("orders", self._load_orders), ("history", self._load_history)]
        return jobs

    def _refresh_loop(self) -> None:
        while not self._stop.is_set():
            for name, job in self._jobs():
                if self._stop.is_set():
                    break
                if time.time() - self.fetched_at.get(name, 0.0) < self.schedule[name]:
                    continue
                try:
                    job()
                    self.errors.pop(name, None)
                except Exception as error:  # keep the dashboard alive; show the error in the panel
                    self.errors[name] = str(error)
                self.fetched_at[name] = time.time()
            self._stop.wait(0.5)

    # -- panels ------------------------------------------------------------------

    def _age(self, name: str) -> str:
        fetched = self.fetched_at.get(name)
        return f"{int(time.time() - fetched)}s ago" if fetched else "loading..."

    def _footer(self, name: str, text: str = "") -> Text:
        footer = Text(f"{text}{' | ' if text else ''}🔄 {self._age(name)}", style="dim white")
        if name in self.errors:
            footer = Text(f"⚠️ {self.errors[name][:120]}", style="yellow")
        return footer

    def _session_table(self, current_total: float) -> Table:
        table = Table(show_header=True, header_style="bold yellow", box=None)
        table.add_column("Session Stats", style="cyan", width=18)
        table.add_column("Value", justify="right", style="green", width=18)

        initial = self._initial_balance or current_total
        change = current_total - initial
        change_pct = (change / initial * 100) if initial > 0 else 0.0
        duration = datetime.now() - (self._initial_balance_time or datetime.now())
        hours, remainder = divmod(int(duration.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)

        table.add_row("Initial Balance", f"${initial:,.2f}")
        table.add_row("Current Balance", f"${current_total:,.2f}")
        table.add_row("", "")
        table.add_row("Session Change", _signed(change))
        table.add_row("Change %", _signed(change_pct, prefix="", suffix="%"))
        table.add_row("", "")
        table.add_row("Session Duration", f"{hours:02d}:{minutes:02d}:{seconds:02d}")
        table.add_row("Started At", self._initial_balance_time.strftime("%H:%M:%S") if self._initial_balance_time else "N/A")
        return table

    def _create_balance_panel(self) -> Panel:
        title = "[bold cyan]💰 Account Balance (USDC)[/bold cyan]"
        if self.summary is None:
            message = self.errors.get("account", "Loading...")
            return Panel(f"[yellow]{message}[/yellow]", title=title, border_style="cyan" if "account" not in self.errors else "red")

        s = self.summary
        notional = sum(p["notional"] for p in self.positions)
        table = Table(show_header=True, header_style="bold magenta", box=None)
        table.add_column("Type", style="cyan", width=18)
        table.add_column("Value (USDC)", justify="right", style="green", width=16)
        table.add_row("Equity", f"${s['equity']:,.2f}", style="bold")
        table.add_row("Collateral", f"${s['collateral']:,.2f}")
        table.add_row("Available", f"${s['available']:,.2f}")
        table.add_row("In Use (margin)", f"${s['initial_margin']:,.2f}")
        table.add_row("Maintenance", f"${s['maintenance_margin']:,.2f}")
        table.add_row("", "")
        table.add_row("Unrealized PnL", _signed(s["unrealized_pnl"]))
        table.add_row("Open Notional", f"${notional:,.2f}")
        table.add_row("Account Leverage", f"{notional / s['equity']:.2f}x" if s["equity"] > 0 else "-")

        combined = Columns([table, self._session_table(s["equity"])], padding=(0, 2), expand=True)
        footer = self._footer("account", f"Account #{s['account_index']}")
        return Panel(Group(combined, "", Align.right(footer)), title=title, border_style="cyan", padding=(1, 2))

    def _create_positions_panel(self) -> Panel:
        title = "[bold magenta]📍 Open Positions[/bold magenta]"
        table = Table(show_header=True, header_style="bold magenta", box=None)
        table.add_column("Symbol", style="cyan", width=9)
        table.add_column("Side", style="magenta", width=6)
        table.add_column("Size", justify="right", width=11)
        table.add_column("Entry", justify="right", style="blue", width=12)
        table.add_column("Mark", justify="right", width=12)
        table.add_column("Liq.", justify="right", style="dim", width=12)
        table.add_column("PnL %", justify="right", width=8)
        table.add_column("PnL (USD)", justify="right", width=12)
        table.add_column("Lev", justify="right", style="dim", width=4)

        if not self.positions:
            table.add_row("[dim]No open positions[/dim]", *[""] * 8)
        for position in self.positions[:10]:
            side = position["side"].upper()
            side_color = "green" if side == "LONG" else "red"
            pnl = position["unrealizedPnl"]
            pnl_pct = (pnl / position["notional"] * 100) if position["notional"] else 0.0
            table.add_row(
                position["symbol"],
                f"[{side_color}]{side}[/{side_color}]",
                f"{position['contracts']:,.4f}",
                _price(position["entryPrice"]),
                _price(position["markPrice"]),
                _price(position["liquidationPrice"]),
                _signed(pnl_pct, prefix="", suffix="%"),
                _signed(pnl),
                f"{position['leverage']:.0f}x" if position["leverage"] else "-",
            )
        footer = self._footer("account", f"📊 Positions: {len(self.positions)}")
        return Panel(Group(table, "", Align.right(footer)), title=title, border_style="magenta", padding=(1, 2))

    def _group_orders(self, orders: list[dict]) -> list[dict]:
        """Join each entry order with its TP/SL children; SL/TP without an entry
        (tied to a position) are grouped per symbol and side."""
        mains = [o for o in orders if o["type"] not in TRIGGER_ORDER_TYPES]
        groups = {o["id"]: {"main": o, "tp": None, "sl": None} for o in mains}
        by_client = {o["clientOrderId"]: o["id"] for o in mains if o["clientOrderId"]}
        orphans: dict[str, dict] = {}

        for order in orders:
            if order["type"] not in TRIGGER_ORDER_TYPES:
                continue
            kind = "tp" if order["type"].startswith("take-profit") else "sl"
            parent_id = order["parentId"] if order["parentId"] in groups else None
            if parent_id is None and order["clientOrderId"]:
                # this tool numbers TP = entry + 1, SL = entry + 2
                offset = 1 if kind == "tp" else 2
                parent_id = by_client.get(str(int(order["clientOrderId"]) - offset))
            if parent_id is not None:
                groups[parent_id][kind] = order
            else:
                key = f"{order['symbol']}:{order['side']}"
                orphans.setdefault(key, {"main": None, "tp": None, "sl": None, "symbol": order["symbol"], "side": order["side"]})[kind] = order
        return list(groups.values()) + list(orphans.values())

    def _create_orders_panel(self) -> Panel:
        title = "[bold yellow]📊 Open Orders[/bold yellow]"
        if not self.exchange.has_api_key:
            return Panel(
                "[dim]Cần LIGHTER_API_PRIVATE_KEY + LIGHTER_API_KEY_INDEX để xem lệnh đang mở.\n"
                "Kiểm tra cài đặt: python -m src.exchanges.lighter.tools.test_connect[/dim]",
                title=title, border_style="yellow", padding=(1, 2),
            )
        table = Table(show_header=True, header_style="bold yellow", box=None)
        table.add_column("Coin", style="cyan", width=8)
        table.add_column("Side", style="magenta", width=5)
        table.add_column("Type", style="blue", width=6)
        table.add_column("Qty", justify="right", width=10)
        table.add_column("Price", justify="right", style="green", width=11)
        table.add_column("TP", justify="right", style="cyan", width=11)
        table.add_column("SL", justify="right", style="red", width=11)
        table.add_column("RR", justify="right", style="magenta", width=5)
        table.add_column("Status", style="yellow", width=8)
        table.add_column("Time", style="dim", width=6)

        orders = self.orders or []
        if self.orders is None:
            table.add_row("[dim]Loading...[/dim]", *[""] * 9)
        elif not orders:
            table.add_row("[dim]No open orders[/dim]", *[""] * 9)
        positions = {p["symbol"]: p for p in self.positions}
        for group in self._group_orders(orders):
            main, tp, sl = group["main"], group["tp"], group["sl"]
            tp_price = tp["triggerPrice"] if tp else None
            sl_price = sl["triggerPrice"] if sl else None
            if main is not None:
                symbol, side, order_type = main["symbol"], main["side"], main["type"].upper()[:6]
                qty, price, status, timestamp = main["remaining"], main["price"], main["status"], main["timestamp"]
            else:
                # SL/TP of a position: the closing side is the opposite of the position
                child = tp or sl
                position = positions.get(group["symbol"])
                symbol, order_type, status, timestamp = group["symbol"], "TP/SL", child["status"], child["timestamp"]
                side = "buy" if group["side"] == "sell" else "sell"
                qty = child["remaining"] or (position["contracts"] if position else 0)
                price = position["entryPrice"] if position else None
            side_text = "BUY" if side == "buy" else "SELL"
            side_color = "green" if side == "buy" else "red"
            table.add_row(
                symbol,
                f"[{side_color}]{side_text}[/{side_color}]",
                order_type,
                f"{qty:,.4f}" if qty else "pos",
                _price(price),
                _price(tp_price),
                _price(sl_price),
                _rr(side, price, tp_price, sl_price),
                status.upper()[:8],
                _time(timestamp),
            )
        footer = self._footer("orders", f"📈 Orders: {len(orders)}")
        return Panel(Group(table, "", Align.right(footer)), title=title, border_style="yellow", padding=(1, 2))

    def _create_history_panel(self) -> Panel:
        title = "[bold green]📜 P&L History (Realized)[/bold green]"
        if not self.exchange.has_api_key:
            return Panel("[dim]Cần API key để xem lịch sử khớp lệnh.[/dim]", title=title, border_style="green", padding=(1, 2))

        table = Table(show_header=True, header_style="bold green", box=None, padding=(0, 1))
        table.add_column("Symbol", style="cyan", width=9)
        table.add_column("Side", style="magenta", width=5)
        table.add_column("Entry", justify="right", style="blue", width=12)
        table.add_column("Exit", justify="right", style="yellow", width=12)
        table.add_column("Qty", justify="right", width=11)
        table.add_column("Realized P&L", justify="right", width=13)
        table.add_column("Time", style="dim", width=12)

        records = self.pnl_records or []
        if self.pnl_records is None:
            table.add_row("[dim]Loading...[/dim]", *[""] * 6)
        elif not records:
            table.add_row("[dim]No P&L history[/dim]", *[""] * 6)
        total = profit = loss = 0.0
        for record in records:
            pnl = record["closed_pnl"]
            total += pnl
            profit += max(pnl, 0.0)
            loss += min(pnl, 0.0)
            side_color = "green" if record["side"] == "BUY" else "red"
            symbol = record["symbol"] + (" [red]LIQ[/red]" if record["type"] == "liquidation" else "")
            table.add_row(
                symbol,
                f"[{side_color}]{record['side']}[/{side_color}]",
                _price(record["avg_entry_price"]),
                _price(record["avg_exit_price"]),
                f"{record['qty']:,.4f}",
                _signed(pnl, decimals=4),
                _time(record["time"], "%m-%d %H:%M"),
            )

        summary = Text("💰 Total: ", style="white")
        summary.append(f"{'+' if total >= 0 else '-'}${abs(total):,.2f}", style="green" if total >= 0 else "red")
        summary.append(" | ", style="dim white")
        summary.append(f"Win: +${profit:,.2f}", style="green")
        summary.append(" | ", style="dim white")
        summary.append(f"Loss: -${abs(loss):,.2f}", style="red")
        summary.append(f" | Trades: {len(records)}", style="dim white")
        footer = self._footer("history")
        return Panel(Group(summary, "", table, "", Align.right(footer)), title=title, border_style="green", padding=(1, 2))

    def _make_layout(self) -> Layout:
        layout = Layout()
        network = "TESTNET" if self.exchange.testnet else "MAINNET"
        mode = "" if self.exchange.has_api_key else " | READ-ONLY"
        header = Panel(
            Align.center(Text(
                f"🤖 LIGHTER TRADING DASHBOARD | {datetime.now().strftime('%H:%M | %d/%m/%Y')} | {network}{mode}",
                style="bold cyan",
            )),
            border_style="cyan",
            height=3,
        )
        layout.split_column(Layout(header, name="header", size=3), Layout(name="body"))
        layout["body"].split_row(Layout(name="left"), Layout(name="right"))
        layout["left"].split_column(
            Layout(self._create_balance_panel(), name="balance", size=17),
            Layout(self._create_positions_panel(), name="positions"),
        )
        layout["right"].split_column(
            Layout(self._create_orders_panel(), name="orders", size=15),
            Layout(self._create_history_panel(), name="history"),
        )
        return layout

    # -- run ---------------------------------------------------------------------

    def start(self) -> None:
        try:
            self.console.print("[cyan]🚀 Starting Lighter Dashboard...[/cyan]")
            self.exchange = get_exchange()
            _ = self.exchange.account_index  # fail early when the account is not configured
            self.console.print(f"[green]✓ Connected ({len(self.exchange.markets)} markets)[/green]")
            self.is_running = True
            threading.Thread(target=self._refresh_loop, name="lighter-dashboard-data", daemon=True).start()

            with Live(self._make_layout(), console=self.console, refresh_per_second=max(1, int(1 / self.update_interval)), screen=True) as live:
                while self.is_running:
                    self.update_time = datetime.now()
                    live.update(self._make_layout())
                    time.sleep(self.update_interval)
        except KeyboardInterrupt:
            self.console.print("\n[yellow]⚠️  Dashboard stopped by user[/yellow]")
        except Exception as error:
            self.console.print(f"[red]❌ Error: {error}[/red]")
        finally:
            self.is_running = False
            self._stop.set()

    def stop(self) -> None:
        self.is_running = False
        self._stop.set()
        self.console.print("[yellow]Dashboard stopped[/yellow]")


def run_dashboard(update_interval: float = 1.0, **schedule) -> None:
    LighterDashboard(update_interval=update_interval, **schedule).start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Lighter trading dashboard")
    parser.add_argument("--interval", type=float, default=1.0, help="screen refresh (seconds)")
    parser.add_argument("--account-every", type=float, default=10.0, help="balance + positions refresh (seconds)")
    parser.add_argument("--orders-every", type=float, default=15.0, help="open orders refresh (seconds)")
    parser.add_argument("--history-every", type=float, default=60.0, help="P&L history refresh (seconds)")
    args = parser.parse_args()
    run_dashboard(
        update_interval=args.interval,
        account_every=args.account_every,
        orders_every=args.orders_every,
        history_every=args.history_every,
    )


if __name__ == "__main__":
    main()
