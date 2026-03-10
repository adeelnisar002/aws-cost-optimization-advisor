from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field, field_validator


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
    analysis_type: str = "full_account"
    discovered_tools: List[Dict[str, Any]] = Field(default_factory=list)
    cost_data: Optional[Dict[str, Any]] = None
    forecast_data: Optional[Dict[str, Any]] = None
    analysis_result: Optional[CostAnalysisResult] = None
    errors: List[str] = Field(default_factory=list)


class RemediationInputItem(BaseModel):
    """Single cost analysis item passed into the remediation workflow."""

    service_name: str = Field(..., description="AWS service name (e.g., AmazonEC2)")
    monthly_cost: float = Field(..., description="Monthly cost attributed to this item")
    usage_type: str = Field(..., description="AWS usage type or family string")
    region: str = Field(..., description="AWS region code (e.g., eu-central-1)")
    anomaly_flag: Optional[bool] = Field(
        default=None,
        description="Optional anomaly indicator from upstream analysis",
    )


class RemediationOpportunity(BaseModel):
    """Detected optimization opportunity derived from cost analysis items."""

    id: str = Field(..., description="Stable identifier for the opportunity")
    category: str = Field(..., description="High-level remediation category")
    service: str = Field(..., description="Canonical AWS service name")
    region: Optional[str] = Field(
        default=None, description="Region most associated with the opportunity"
    )
    monthly_cost: float = Field(
        ..., description="Cost associated with this opportunity (best-effort)"
    )
    evidence: str = Field(
        ..., description="Human-readable evidence/justification for detection"
    )
    estimated_waste_band: Optional[str] = Field(
        default=None,
        description="Optional coarse estimate of waste, e.g. low/medium/high",
    )


class RemediationItem(BaseModel):
    """Single remediation recommendation suitable for dashboard rendering."""

    service: str
    issue_detected: str
    recommendation: str
    estimated_savings: float
    risk_level: Literal["Low", "Medium", "High"]
    effort_level: Literal["Low", "Medium", "High"]
    automation_level: Literal["Manual", "Semi", "Full"]
    rationale: Optional[str] = None
    implementation_steps: List[str] = Field(default_factory=list)
    validation_steps: List[str] = Field(default_factory=list)
    cli_example: Optional[str] = None
    terraform_example: Optional[str] = None

    @staticmethod
    def _normalize_three_level(value: Any, *, default: str = "Medium") -> str:
        text = str(value or "").strip().lower().replace("_", "").replace("-", "")
        mapping = {
            "verylow": "Low",
            "low": "Low",
            "medium": "Medium",
            "med": "Medium",
            "high": "High",
            "veryhigh": "High",
        }
        return mapping.get(text, default)

    @staticmethod
    def _normalize_automation_level(value: Any) -> str:
        text = str(value or "").strip().lower().replace("_", "").replace("-", "")
        mapping = {
            "manual": "Manual",
            "semi": "Semi",
            "semiautomated": "Semi",
            "semiauto": "Semi",
            "partial": "Semi",
            "full": "Full",
            "automatic": "Full",
            "automated": "Full",
            "auto": "Full",
        }
        return mapping.get(text, "Manual")

    @field_validator("risk_level", mode="before")
    @classmethod
    def normalize_risk_level(cls, value: Any) -> str:
        return cls._normalize_three_level(value, default="Medium")

    @field_validator("effort_level", mode="before")
    @classmethod
    def normalize_effort_level(cls, value: Any) -> str:
        return cls._normalize_three_level(value, default="Medium")

    @field_validator("automation_level", mode="before")
    @classmethod
    def normalize_automation_level(cls, value: Any) -> str:
        return cls._normalize_automation_level(value)


class RemediationSummary(BaseModel):
    """High-level summary for remediation plan."""

    total_estimated_savings: float
    risk_profile: str
    priority_score: float


class RemediationOutput(BaseModel):
    """Full remediation output payload returned to the UI/dashboard."""

    summary: RemediationSummary
    remediations: List[RemediationItem]


class RemediationGraphState(BaseModel):
    """State container for the LangGraph remediation workflow."""

    analysis_items: List[RemediationInputItem]
    discovered_tools: List[Dict[str, Any]] = Field(default_factory=list)
    detected_opportunities: List[RemediationOpportunity] = Field(default_factory=list)
    raw_recommendations: Dict[str, Any] = Field(default_factory=dict)
    remediation_plan: Optional[RemediationOutput] = None
    errors: List[str] = Field(default_factory=list)

