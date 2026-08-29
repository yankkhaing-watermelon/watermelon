"""Compatibility facade for the recovered, self-contained repository."""
from __future__ import annotations

from pathlib import Path


def ensure() -> Path:
    return Path(__file__).resolve().parent


def engine():
    import config
    import data_fetcher
    return {"config": config, "data_fetcher": data_fetcher}
