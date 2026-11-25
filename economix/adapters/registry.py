"""Registro de mercados (config/markets.yaml) e resolucao do adapter de cada um."""
from __future__ import annotations

import importlib

import yaml

from ..config import settings
from ..evidence import EvidenceStore
from ..models import Strategy
from .base import BaseAdapter, MarketInfo


_KNOWN = {"name", "app_name", "strategy", "android_package", "enabled", "adapter", "notes", "sources", "web_base"}


def load_markets() -> dict[str, MarketInfo]:
    raw = yaml.safe_load(settings.markets_file.read_text(encoding="utf-8")) or {}
    out = {}
    for mid, m in (raw.get("markets") or {}).items():
        out[mid] = MarketInfo(
            id=mid, name=m["name"], app_name=m["app_name"],
            strategy=Strategy(m.get("strategy", "ui")),
            android_package=m.get("android_package"), enabled=bool(m.get("enabled", False)),
            adapter=m.get("adapter", "flow"), notes=m.get("notes", ""), sources=m.get("sources", []),
            web_base=m.get("web_base"),
            extra={k: v for k, v in m.items() if k not in _KNOWN},
        )
    return out


def get_adapter(market: MarketInfo, evidence: EvidenceStore, **kw) -> BaseAdapter:
    if market.adapter == "flow":
        from .flow import FlowAdapter
        return FlowAdapter(market, evidence, **kw)
    mod = importlib.import_module(f"economix.adapters.{market.adapter}")
    return mod.Adapter(market, evidence, **kw)
