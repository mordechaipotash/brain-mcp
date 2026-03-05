"""
brain-mcp — Ingester plugin registry.

Use the @register decorator to register an ingester class:

    from brain_mcp.ingest.base import BaseIngester
    from brain_mcp.ingest.registry import register

    @register
    class MyIngester(BaseIngester):
        source_type = "my-source"
        display_name = "My Source"
        ...

Then use get_ingester(), get_all_ingesters(), or discover_all() to access them.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from brain_mcp.ingest.base import BaseIngester

_REGISTRY: dict[str, BaseIngester] = {}


def register(cls):
    """
    Class decorator to register an ingester.

    Instantiates the class and stores it in the global registry
    keyed by source_type.
    """
    instance = cls()
    _REGISTRY[instance.source_type] = instance
    return cls


def get_ingester(source_type: str) -> BaseIngester | None:
    """Get a registered ingester by source_type."""
    return _REGISTRY.get(source_type)


def get_all_ingesters() -> dict[str, BaseIngester]:
    """Get all registered ingesters as {source_type: instance}."""
    return dict(_REGISTRY)


def discover_all() -> dict[str, list[dict]]:
    """
    Run discover() on all registered ingesters.

    Returns:
        Dict of {source_type: [discovered_sources]} for ingesters
        that found something.
    """
    results = {}
    for name, ingester in _REGISTRY.items():
        try:
            found = ingester.discover()
            if found:
                results[name] = found
        except Exception as e:
            print(f"Discovery failed for {name}: {e}", file=sys.stderr)
    return results


def clear_registry():
    """Clear all registered ingesters. Primarily for testing."""
    _REGISTRY.clear()
