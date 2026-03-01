from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml  # pyright: ignore[reportMissingModuleSource]


ConfigPath = Union[str, Path]

_CONFIG_DIR = Path(__file__).resolve().parent
_ALIASES = {
    "strategy": _CONFIG_DIR / "strategy.yaml",
    "live": _CONFIG_DIR / "live.yaml",
    "backtest": _CONFIG_DIR / "backtest.yaml",
}


def _resolve_config_path(config_path: Optional[ConfigPath], default_name: str) -> Path:
    if config_path is None:
        return _CONFIG_DIR / default_name

    key = str(config_path).strip().lower()
    if key in _ALIASES:
        return _ALIASES[key]

    resolved = Path(config_path)
    if resolved.is_absolute():
        return resolved

    cwd_candidate = Path.cwd() / resolved
    if cwd_candidate.exists():
        return cwd_candidate

    project_root = _CONFIG_DIR.parent.parent
    project_candidate = project_root / resolved
    if project_candidate.exists():
        return project_candidate

    if resolved.parts and resolved.parts[0] == "config":
        return _CONFIG_DIR / Path(*resolved.parts[1:])

    if resolved.parts and resolved.parts[0] == "src" and len(resolved.parts) > 1 and resolved.parts[1] == "config":
        return _CONFIG_DIR / Path(*resolved.parts[2:])

    resolved = cwd_candidate
    return resolved


def _read_yaml(config_path: Path) -> Dict[str, Any]:
    if not config_path.exists():
        print(f"Config file not found at {config_path}")
        return {}

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
            return config if isinstance(config, dict) else {}
    except Exception as e:
        print(f"Error reading config file {config_path}: {e}")
        return {}


def _resolve_example_config_path(config_path: Path) -> Optional[Path]:
    if config_path.suffix == ".yaml":
        example_path = config_path.with_name(f"{config_path.stem}.ex.yaml")
        if example_path.exists():
            return example_path
    return None


def load_yaml_config(config_path: Optional[ConfigPath] = None, default_name: str = "backtest.yaml") -> Dict[str, Any]:
    resolved_path = _resolve_config_path(config_path, default_name)
    if resolved_path.exists():
        return _read_yaml(resolved_path)

    example_path = _resolve_example_config_path(resolved_path)
    if example_path is not None:
        print(f"Config file not found at {resolved_path}, fallback to {example_path}")
        return _read_yaml(example_path)

    return _read_yaml(resolved_path)


def merge_strategy_config(env_config: Optional[Dict[str, Any]] = None, strategy_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    merged = dict(env_config or {})
    merged["strategy"] = dict(strategy_config or load_strategy_config())
    return merged


def _normalize_timeframe_list(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]

    if not isinstance(value, list):
        return []

    normalized: List[str] = []
    for item in value:
        tf = str(item).strip()
        if tf:
            normalized.append(tf)
    return normalized


def load_strategy_config(config_path: Optional[ConfigPath] = None) -> Dict[str, Any]:
    config = load_yaml_config(config_path=config_path, default_name="strategy.yaml")

    if not isinstance(config, dict):
        return {}

    if "signal_timeframe" not in config and "indicator_timeframe" in config:
        config["signal_timeframe"] = config.get("indicator_timeframe")

    signal_timeframes = _normalize_timeframe_list(config.get("signal_timeframe", "5m"))
    config["signal_timeframe"] = signal_timeframes or ["5m"]

    entry_timeframe = str(config.get("entry_timeframe", "1m")).strip()
    config["entry_timeframe"] = entry_timeframe or "1m"

    return config


def load_backtest_config(config_path: Optional[ConfigPath] = None, include_strategy: bool = True) -> Dict[str, Any]:
    config = load_yaml_config(config_path=config_path, default_name="backtest.yaml")
    if include_strategy:
        return merge_strategy_config(config)
    return config


def load_live_config(config_path: Optional[ConfigPath] = None, include_strategy: bool = True) -> Dict[str, Any]:
    config = load_yaml_config(config_path=config_path, default_name="live.yaml")
    if include_strategy:
        return merge_strategy_config(config)
    return config


def load_config(config_path: Optional[ConfigPath] = None) -> Dict[str, Any]:
    """Default project config loader.

    Behavior:
    - `None` -> backtest config + shared strategy config
    - alias `backtest` / `live` / `strategy`
    - explicit path -> loads that file directly
    """
    if config_path is None:
        return load_backtest_config(include_strategy=True)

    key = str(config_path).strip().lower()
    if key == "backtest":
        return load_backtest_config(include_strategy=True)
    if key == "live":
        return load_live_config(include_strategy=True)
    if key == "strategy":
        return load_strategy_config()

    return load_yaml_config(config_path=config_path, default_name="backtest.yaml")
