from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ServiceSpend(BaseModel):
    """Individual AWS service spend."""

    service: str = Field(..., description="AWS service name")
    spend: float = Field(..., description="Monthly spend in USD")


class CostAnalysisResult(BaseModel):
    """Structured cost analysis output returned by the Manager agent."""

    total_monthly_spend: float = Field(..., description="Total monthly AWS spend in USD")
    top_services: List[ServiceSpend] = Field(
        default_factory=list,
        description="Top services by spend (ideally top 3)",
    )
    forecast_next_month: float = Field(
        ..., description="Forecasted AWS spend for next month in USD"
    )
    summary: str = Field(..., description="Human-readable FinOps summary")

    # Optional but useful metadata
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    raw_cost_data: Optional[Dict[str, Any]] = None
    raw_forecast_data: Optional[Dict[str, Any]] = None


class GraphState(BaseModel):
    """State container used by the LangGraph manager workflow."""

    user_query: str
    discovered_tools: List[Dict[str, Any]] = Field(default_factory=list)
    cost_data: Optional[Dict[str, Any]] = None
    forecast_data: Optional[Dict[str, Any]] = None
    analysis_result: Optional[CostAnalysisResult] = None
    errors: List[str] = Field(default_factory=list)


