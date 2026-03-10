import json
import logging
import re
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from pydantic import ValidationError

from config import settings
from models.schemas import (
    RemediationItem,
    RemediationOutput,
    RemediationSummary,
    RemediationOpportunity,
)

logger = logging.getLogger(__name__)


REMEDIATION_SYSTEM_PROMPT = """
You are an AWS Cost Optimization and FinOps remediation specialist.

You receive:
- A list of detected optimization opportunities
- Cost Explorer breakdowns and related MCP tool data for each opportunity

Your job:
- Propose SAFE, NON-DESTRUCTIVE remediation actions
- Estimate potential monthly savings based on the provided costs
- Classify risk and effort levels
- Suggest whether remediation can be automated

You MUST return strictly valid JSON matching this schema:
{
  "summary": {
    "total_estimated_savings": number,
    "risk_profile": string,
    "priority_score": number
  },
  "remediations": [
    {
      "service": string,
      "issue_detected": string,
      "recommendation": string,
      "estimated_savings": number,
      "risk_level": "Low" | "Medium" | "High",
      "effort_level": "Low" | "Medium" | "High",
      "automation_level": "Manual" | "Semi" | "Full",
      "rationale": string,
      "implementation_steps": [string],
      "validation_steps": [string],
      "cli_example": string,
      "terraform_example": string | null
    }
  ]
}

Rules:
- Prefer conservative, reversible changes (stop before delete).
- When unsure, suggest investigation steps rather than destructive actions.
- Use realistic but approximate savings estimates – they may be a percentage of the observed monthly cost.
- Always ensure CLI snippets use --dry-run when available or are clearly examples.
- Prioritize Cost Optimization Hub (`cost-optimization`) recommendations first,
  especially entries with highest estimated_monthly_savings.
- If recommendation_context includes `top_recommendation_details` from `rec-details`,
  use those fields directly for recommendation/rationale/steps where applicable.
- Use Compute Optimizer context for rightsizing/performance tuning details only.
- implementation_steps must be concrete and ordered.
- validation_steps must explain how to confirm savings after change.
""".strip()


class RemediationAgent:
    """LLM-backed agent that turns raw opportunities into a remediation plan."""

    def __init__(self) -> None:
        self.llm = ChatGroq(
            groq_api_key=settings.groq_api_key,
            model=settings.groq_model,
            temperature=0.0,
        )
        logger.info("RemediationAgent initialized with Groq model %s", settings.groq_model)

    async def generate_plan(
        self,
        opportunities: List[RemediationOpportunity],
        raw_recommendations: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Convert detected opportunities + pre-processed recommendation data into a plan."""
        system = SystemMessage(content=REMEDIATION_SYSTEM_PROMPT)

        # Keep the payload compact to avoid hitting model context limits.
        compact_payload = {
            "opportunities": [o.model_dump() for o in opportunities],
            "recommendation_context": raw_recommendations,
        }
        payload_json = json.dumps(compact_payload, indent=2)

        user_content = (
            "You are generating an AWS cost remediation plan.\n\n"
            "Input data (opportunities + compact cost summaries):\n"
            f"{payload_json}\n\n"
            "Important prioritization: use optimization_hub.recommendations.candidates and "
            "optimization_hub.top_recommendation_details first when present, then enrich with "
            "compute_optimizer context.\n"
            "Using ONLY this data, produce a remediation plan JSON that matches the "
            "required schema exactly. Do not include explanations outside JSON."
        )
        human = HumanMessage(content=user_content)

        logger.info(
            "Invoking Groq LLM for remediation strategy generation (%d opportunities)",
            len(opportunities),
        )

        raw_text: str = ""
        try:
            response = await self.llm.ainvoke([system, human])
            raw_text = response.content

            json_str = self._extract_json(raw_text)
            parsed = json.loads(json_str)
            validated = RemediationOutput(**parsed)
            normalized = self._merge_similar_remediations(validated.model_dump())
            return normalized
        except Exception as exc:
            logger.error("Remediation LLM failed or response invalid: %s", exc)
            # Fallback: build a minimal remediation set directly from opportunities
            remediations: List[RemediationItem] = []
            for opp in opportunities:
                est_savings = max(opp.monthly_cost * 0.2, 0.0)
                remediations.append(
                    RemediationItem(
                        service=opp.service,
                        issue_detected=opp.evidence,
                        recommendation=(
                            "Review this opportunity manually in the AWS Console. "
                            "The automated remediation plan could not be generated; "
                            "use standard right-sizing, cleanup, or networking "
                            "optimization practices as appropriate."
                        ),
                        estimated_savings=est_savings,
                        risk_level="Medium",
                        effort_level="Medium",
                        automation_level="Manual",
                        rationale=(
                            "Generated from detected opportunity evidence only because "
                            "structured recommendation payload could not be parsed."
                        ),
                        implementation_steps=[
                            "Validate the resource inventory and current usage over the last 14-30 days.",
                            "Apply a reversible optimization first (stop/schedule/rightsizing in test window).",
                            "Monitor cost and performance for at least 7 days before broader rollout.",
                        ],
                        validation_steps=[
                            "Compare daily NetUnblendedCost before vs after the change for this service.",
                            "Verify utilization metrics remain within expected thresholds.",
                        ],
                        cli_example="# Example: describe related resources with AWS CLI (replace filters appropriately)\n"
                        "aws resourcegroupstaggingapi get-resources --resource-type-filters ec2:instance --output json",
                        terraform_example=None,
                    )
                )

            total = sum(r.estimated_savings for r in remediations)
            summary = RemediationSummary(
                total_estimated_savings=total,
                risk_profile="Medium",
                priority_score=total,
            )
            fallback = RemediationOutput(summary=summary, remediations=remediations)
            data = fallback.model_dump()
            data["error"] = f"LLM error or parse/validation error: {exc}"
            if raw_text:
                data["raw_response"] = raw_text
            return data

    @staticmethod
    def _extract_resource_ids_from_cli(cli_example: str) -> List[str]:
        """Extract common AWS resource identifiers from CLI examples."""
        if not cli_example:
            return []
        patterns = [
            r"--instance-id\s+([^\s]+)",
            r"--db-instance-identifier\s+([^\s]+)",
            r"--volume-id\s+([^\s]+)",
            r"--service\s+([^\s]+)",
            r"--cluster\s+([^\s]+)",
        ]
        ids: List[str] = []
        for pattern in patterns:
            ids.extend(re.findall(pattern, cli_example))

        # Stable dedupe preserving order
        seen = set()
        out: List[str] = []
        for item in ids:
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
        return out

    @staticmethod
    def _dedupe_list(values: List[str]) -> List[str]:
        seen = set()
        out: List[str] = []
        for value in values:
            if not isinstance(value, str):
                continue
            key = value.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(key)
        return out

    def _merge_similar_remediations(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Collapse repetitive remediation items into grouped actions.

        This keeps the same schema but aggregates savings and mentions affected
        resources to avoid near-duplicate cards in UI output.
        """
        remediations = plan.get("remediations")
        if not isinstance(remediations, list) or not remediations:
            return plan

        grouped: Dict[str, Dict[str, Any]] = {}
        for item in remediations:
            if not isinstance(item, dict):
                continue
            key = "||".join(
                [
                    str(item.get("service", "")),
                    str(item.get("recommendation", "")),
                    str(item.get("risk_level", "")),
                    str(item.get("effort_level", "")),
                    str(item.get("automation_level", "")),
                ]
            )
            if key not in grouped:
                grouped[key] = {
                    **item,
                    "_count": 1,
                    "_resource_ids": self._extract_resource_ids_from_cli(
                        str(item.get("cli_example") or "")
                    ),
                    "_estimated_savings_sum": float(item.get("estimated_savings", 0.0) or 0.0),
                }
                continue

            g = grouped[key]
            g["_count"] += 1
            g["_estimated_savings_sum"] += float(item.get("estimated_savings", 0.0) or 0.0)
            g["_resource_ids"].extend(
                self._extract_resource_ids_from_cli(str(item.get("cli_example") or ""))
            )
            g["implementation_steps"] = self._dedupe_list(
                list(g.get("implementation_steps") or [])
                + list(item.get("implementation_steps") or [])
            )
            g["validation_steps"] = self._dedupe_list(
                list(g.get("validation_steps") or [])
                + list(item.get("validation_steps") or [])
            )

        merged: List[Dict[str, Any]] = []
        for _, g in grouped.items():
            count = int(g.pop("_count", 1))
            resource_ids = self._dedupe_list(list(g.pop("_resource_ids", [])))
            savings_sum = float(g.pop("_estimated_savings_sum", 0.0) or 0.0)
            g["estimated_savings"] = round(savings_sum, 3)

            if count > 1:
                g["issue_detected"] = f"{g.get('issue_detected', 'Optimization opportunity')} ({count} resources)"
                resource_note = (
                    ", ".join(resource_ids[:8]) if resource_ids else "multiple resources"
                )
                rationale = str(g.get("rationale") or "")
                g["rationale"] = (
                    f"{rationale} Consolidated from {count} similar recommendations "
                    f"covering: {resource_note}."
                ).strip()

            merged.append(g)

        # Highest savings first
        merged.sort(key=lambda r: float(r.get("estimated_savings", 0.0) or 0.0), reverse=True)
        plan["remediations"] = merged

        summary = plan.get("summary")
        if isinstance(summary, dict):
            total = sum(float(r.get("estimated_savings", 0.0) or 0.0) for r in merged)
            summary["total_estimated_savings"] = round(total, 3)
            # Keep score intuitive and bounded to number of merged actions.
            summary["priority_score"] = min(10.0, float(len(merged)) * 2.5)
            plan["summary"] = summary
        return plan

    @staticmethod
    def _extract_json(text: str) -> str:
        """Best-effort extraction of a JSON object from the LLM response."""
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return match.group(0)
        return text.strip()



