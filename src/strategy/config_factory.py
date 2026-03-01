from __future__ import annotations

from dataclasses import fields
from typing import Any, Mapping

from src.strategy.smc_strategy import Config


def build_strategy_config(
    *,
    data_folder: str,
    output_folder: str,
    strategy_config: Mapping[str, Any] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> Config:
    """Build strategy Config from shared strategy settings plus runtime overrides."""
    if not data_folder:
        raise ValueError("data_folder is required")
    if not output_folder:
        raise ValueError("output_folder is required")

    merged: dict[str, Any] = dict(strategy_config or {})
    if overrides:
        for key, value in overrides.items():
            if value is not None:
                merged[key] = value

    merged["data_folder"] = data_folder
    merged["output_folder"] = output_folder

    config_fields = {item.name for item in fields(Config)}
    filtered_values = {key: value for key, value in merged.items() if key in config_fields}
    return Config(**filtered_values)
