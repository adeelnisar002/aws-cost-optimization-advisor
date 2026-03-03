import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Tool:
    """Representation of an MCP tool."""

    name: str
    description: str
    input_schema: Dict[str, Any]
    category: str = "other"


class ToolRegistry:
    """In-memory registry with simple TTL caching for discovered tools."""

    def __init__(self, cache_ttl_seconds: int = 3600) -> None:
        self._tools: Dict[str, Tool] = {}
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl = timedelta(seconds=cache_ttl_seconds)

    def add_tool(self, tool: Tool) -> None:
        self._tools[tool.name] = tool
        logger.info("Registered MCP tool: %s", tool.name)

    def get_tool(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def list_tools(self) -> List[Tool]:
        return list(self._tools.values())

    def list_tool_names(self) -> List[str]:
        return list(self._tools.keys())

    def get_tools_by_category(self, category: str) -> List[Tool]:
        return [t for t in self._tools.values() if t.category == category]

    def cache_valid(self) -> bool:
        if self._cache_timestamp is None:
            return False
        return datetime.utcnow() - self._cache_timestamp < self._cache_ttl

    def mark_cache_updated(self) -> None:
        self._cache_timestamp = datetime.utcnow()

    def invalidate_cache(self) -> None:
        logger.info("Invalidating MCP tool cache")
        self._cache_timestamp = None
        self._tools.clear()


def categorize_tool_name(name: str) -> str:
    """Simple heuristic categorization based on tool name."""
    lower = name.lower()
    if "cost" in lower or "usage" in lower or "breakdown" in lower:
        return "cost_analysis"
    if "forecast" in lower or "predict" in lower:
        return "forecast"
    if "anomal" in lower:
        return "anomaly_detection"
    return "other"


tool_registry = ToolRegistry()


