import json
import logging
import re
from typing import Any, Dict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from pydantic import ValidationError

from config import settings
from models.schemas import CostAnalysisResult

logger = logging.getLogger(__name__)


MANAGER_SYSTEM_PROMPT = """
You are a FinOps Architect specializing in AWS cost optimization.

You MUST use available MCP tools to retrieve data.
You must never hallucinate AWS data.
All financial numbers must come from tool responses.

Return output in structured JSON:
{
  "total_monthly_spend": number,
  "top_services": [
    { "service": string, "spend": number }
  ],
  "forecast_next_month": number,
  "summary": string
}
""".strip()


class ManagerAgent:
    """Manager (orchestrator) agent responsible for high-level cost analysis."""

    def __init__(self) -> None:
        self.llm = ChatGroq(
            groq_api_key=settings.groq_api_key,
            model=settings.groq_model,
            temperature=0.0,
        )
        logger.info("ManagerAgent initialized with Groq model %s", settings.groq_model)

    async def analyze(
        self,
        user_query: str,
        cost_data: Dict[str, Any],
        forecast_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Use the LLM to transform compact cost summary into a validated analysis.
        
        Args:
            user_query: User's query
            cost_data: Compact cost data with total_monthly_spend and top_services
            forecast_data: Compact forecast data with forecast_next_month
        """
        system = SystemMessage(content=MANAGER_SYSTEM_PROMPT)

        # Construct compact structured summary from pre-processed data
        # cost_data should already be: {total_monthly_spend: float, gross_spend: float, credits_refunds: float, top_services: [...]}
        # forecast_data should already be: {forecast_next_month: float}
        total_monthly_spend = cost_data.get("total_monthly_spend", 0.0)
        gross_spend = cost_data.get("gross_spend", total_monthly_spend)
        credits_refunds = cost_data.get("credits_refunds", 0.0)
        top_services = cost_data.get("top_services", [])
        forecast_next_month = forecast_data.get("forecast_next_month", 0.0)
        
        # Ensure all values are floats
        try:
            total_monthly_spend = float(total_monthly_spend)
        except (ValueError, TypeError):
            total_monthly_spend = 0.0
        
        try:
            gross_spend = float(gross_spend)
        except (ValueError, TypeError):
            gross_spend = total_monthly_spend
        
        try:
            credits_refunds = float(credits_refunds)
        except (ValueError, TypeError):
            credits_refunds = 0.0
        
        try:
            forecast_next_month = float(forecast_next_month)
        except (ValueError, TypeError):
            forecast_next_month = 0.0
        
        # Ensure top_services have float spend values
        normalized_top_services = []
        for svc in top_services:
            if isinstance(svc, dict):
                try:
                    normalized_top_services.append({
                        "service": str(svc.get("service", "Unknown")),
                        "spend": float(svc.get("spend", 0.0)),
                    })
                except (ValueError, TypeError):
                    logger.warning("Invalid service spend value: %s", svc)
        
        # Create compact summary object with separate spending and credits
        compact_summary = {
            "total_monthly_spend": total_monthly_spend,  # Net after credits
            "gross_spend": gross_spend,  # Total spending before credits
            "credits_refunds": credits_refunds,  # Total credits/refunds applied
            "top_services": normalized_top_services,
            "forecast_next_month": forecast_next_month,
        }
        
        # Send ONLY the compact summary to the LLM (no raw JSON bloat)
        summary_json = json.dumps(compact_summary, indent=2)
        
        user_content = (
            "User query:\n"
            f"{user_query}\n\n"
            "Cost analysis summary (pre-processed from AWS Cost Explorer):\n"
            f"{summary_json}\n\n"
            "Based on this summary, provide a natural language explanation. "
            "Note: total_monthly_spend is the net amount after credits/refunds. "
            "gross_spend shows total spending before credits, and credits_refunds shows the credits applied. "
            "Respond with JSON only, matching the required schema exactly:\n"
            "{\n"
            '  "total_monthly_spend": number,\n'
            '  "top_services": [{"service": string, "spend": number}],\n'
            '  "forecast_next_month": number,\n'
            '  "summary": string\n'
            "}"
        )
        human = HumanMessage(content=user_content)

        logger.info("Invoking Groq LLM for cost analysis with compact summary")
        response = await self.llm.ainvoke([system, human])
        raw_text = response.content

        try:
            json_str = self._extract_json(raw_text)
            parsed = json.loads(json_str)

            # Use the compact summary values (not LLM-generated numbers) to ensure accuracy
            validated = CostAnalysisResult(
                total_monthly_spend=total_monthly_spend,  # Use pre-processed value (net after credits)
                top_services=normalized_top_services,  # Use pre-processed values
                forecast_next_month=forecast_next_month,  # Use pre-processed value
                summary=parsed.get("summary", ""),  # Only summary comes from LLM
                # Store compact summaries with gross_spend and credits_refunds
                raw_cost_data={
                    **cost_data,
                    "gross_spend": gross_spend,
                    "credits_refunds": credits_refunds,
                },
                raw_forecast_data=forecast_data,
            )
            return validated.model_dump()
        except (json.JSONDecodeError, ValidationError, KeyError) as exc:
            logger.error("Failed to parse/validate LLM output: %s", exc)
            # Fallback: return the compact summary with a basic summary text
            return {
                "total_monthly_spend": total_monthly_spend,
                "top_services": normalized_top_services,
                "forecast_next_month": forecast_next_month,
                "summary": f"Cost analysis completed. LLM parsing error: {exc}",
                "error": f"LLM parse/validation error: {exc}",
                "raw_response": raw_text,
                "raw_cost_data": {
                    **cost_data,
                    "gross_spend": gross_spend,
                    "credits_refunds": credits_refunds,
                },
            }

    @staticmethod
    def _extract_json(text: str) -> str:
        """Best-effort extraction of a JSON object from the LLM response."""
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return match.group(0)
        return text.strip()


