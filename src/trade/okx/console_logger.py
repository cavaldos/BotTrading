"""
Console Logger for OKX Dashboard

Collects warning/error messages to display in dashboard console panel
instead of printing to terminal.
"""
from collections import deque
from datetime import datetime
from typing import Deque, Tuple

# Global log storage (timestamp, level, message)
_logs: Deque[Tuple[datetime, str, str]] = deque(maxlen=50)


def log_warning(message: str) -> None:
    """Add a warning message to the console log."""
    _logs.append((datetime.now(), "warning", message))


def log_error(message: str) -> None:
    """Add an error message to the console log."""
    _logs.append((datetime.now(), "error", message))


def log_info(message: str) -> None:
    """Add an info message to the console log."""
    _logs.append((datetime.now(), "info", message))


def log_success(message: str) -> None:
    """Add a success message to the console log."""
    _logs.append((datetime.now(), "success", message))


def get_logs(limit: int = 20) -> list:
    """Get the latest log messages.
    
    Returns:
        List of tuples (timestamp, level, message)
    """
    return list(_logs)[-limit:]


def clear_logs() -> None:
    """Clear all log messages."""
    _logs.clear()
