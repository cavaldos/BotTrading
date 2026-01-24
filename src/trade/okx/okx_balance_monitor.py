import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List

import asciichartpy
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from src.trade.okx.exchange import get_exchange

# Paths
LOG_FILE = Path(__file__).parent.parent.parent.parent / "res" / "account" / "okx.log"
MAX_CHART_POINTS = 50  # Số điểm tối đa hiển thị trên chart


class OKXBalanceMonitor:
    def __init__(self, interval: float = 5.0):
        self.interval = interval
        self.console = Console()
        self.exchange = None
        self.balance_history: List[float] = []

    def _get_total_balance(self) -> Optional[float]:
        try:
            balance = self.exchange.fetch_balance()
            for coin in ('USDT', 'USDC'):
                data = balance.get(coin)
                if isinstance(data, dict):
                    total = float(data.get('total') or data.get('balance') or 0)
                    if total > 0:
                        return total
            # Fallback
            info = balance.get('info', {})
            if isinstance(info, dict):
                for v in info.values():
                    if isinstance(v, dict):
                        ccy = v.get('ccy', '').upper()
                        if ccy in ('USDT', 'USDC'):
                            eq = float(v.get('totalEq') or v.get('eq') or 0)
                            if eq > 0:
                                return eq
        except Exception as e:
            self.console.print(f"[red]Error fetching balance: {e}[/red]")
        return None

    def _append_log(self, balance: float) -> None:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{timestamp},{balance:.4f}\n")

    def _load_history_from_log(self) -> List[float]:
        history = []
        if LOG_FILE.exists():
            try:
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    for line in f:
                        parts = line.strip().split(",")
                        if len(parts) >= 2:
                            history.append(float(parts[1]))
            except Exception:
                pass
        return history[-MAX_CHART_POINTS:]

    def _build_display(self) -> Panel:
        lines = []
        
        if len(self.balance_history) < 2:
            lines.append("[yellow]Waiting for more data points...[/yellow]")
        else:
            chart = asciichartpy.plot(
                self.balance_history,
                {'height': 12, 'format': '{:12.2f}'}
            )
            lines.append(chart)
            
            current = self.balance_history[-1]
            min_val = min(self.balance_history)
            max_val = max(self.balance_history)
            change = self.balance_history[-1] - self.balance_history[0]
            change_pct = (change / self.balance_history[0] * 100) if self.balance_history[0] else 0
            
            lines.append("")
            lines.append(f"[green]Current:[/green] ${current:,.2f}")
            lines.append(f"[cyan]Min:[/cyan] ${min_val:,.2f} | [cyan]Max:[/cyan] ${max_val:,.2f}")
            
            change_color = "green" if change >= 0 else "red"
            lines.append(f"[{change_color}]Change: {'+' if change >= 0 else ''}{change:,.2f} ({change_pct:+.2f}%)[/{change_color}]")
        
        lines.append(f"\n[dim]Points: {len(self.balance_history)} | Interval: {self.interval}s | {datetime.now().strftime('%H:%M:%S')}[/dim]")
        
        return Panel(
            Text.from_markup("\n".join(lines)),
            title="[bold cyan]📊 OKX Balance Monitor[/bold cyan]",
            subtitle=f"[dim]{LOG_FILE}[/dim]",
            border_style="cyan"
        )

    def run(self) -> None:
        self.console.print("[yellow]Connecting to OKX...[/yellow]")
        try:
            self.exchange = get_exchange()
            self.console.print("[green]✓ Connected[/green]")
        except Exception as e:
            self.console.print(f"[red]✗ Connection failed: {e}[/red]")
            return

        # Load existing history
        self.balance_history = self._load_history_from_log()
        
        try:
            with Live(self._build_display(), refresh_per_second=1, console=self.console) as live:
                while True:
                    balance = self._get_total_balance()
                    if balance is not None:
                        self._append_log(balance)
                        self.balance_history.append(balance)
                        self.balance_history = self.balance_history[-MAX_CHART_POINTS:]
                    
                    live.update(self._build_display())
                    time.sleep(self.interval)
                
        except KeyboardInterrupt:
            self.console.print("\n[yellow]Monitor stopped[/yellow]")


def main():
    monitor = OKXBalanceMonitor(interval=120.0)
    monitor.run()


if __name__ == '__main__':
    main()
