import json
import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Mapping, Sequence

from models.schemas import RemediationOpportunity
from server.client import billing_mcp_client
from utils.cost_processor import CostDataProcessor

logger = logging.getLogger(__name__)


def _default_time_window(days: int = 30) -> Mapping[str, str]:
    """Return a UTC date window for the last `days` days (end-exclusive)."""
    today = date.today()
    start = today - timedelta(days=days)
    return {"start": start.isoformat(), "end": today.isoformat()}


def _parse_mcp_payload(response: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort parse for MCP responses that store JSON in result[0].text."""
    if not isinstance(response, dict):
        return {}

    result = response.get("result")
    if isinstance(result, list) and result:
        first = result[0]
        if isinstance(first, dict) and "text" in first:
            text = first.get("text")
            if isinstance(text, str):
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    return {}

    return response


def _compact_cost_optimization_payload(response: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce cost-optimization response to compact fields for LLM context."""
    parsed = _parse_mcp_payload(response)
    data = parsed.get("data", {}) if isinstance(parsed, dict) else {}
    if not isinstance(data, dict):
        data = {}

    preview = data.get("preview", [])
    compact_preview: List[Dict[str, Any]] = []
    if isinstance(preview, list):
        for item in preview[:6]:
            if isinstance(item, dict):
                compact_preview.append(
                    {
                        "key": item.get("key"),
                        "value": item.get("value"),
                    }
                )

    recommendation_samples = _extract_recommendation_samples(data, max_items=12)
    recommendation_candidates = _extract_recommendation_candidates(data, max_items=25)
    return {
        "has_error": bool(response.get("error")) if isinstance(response, dict) else False,
        "error": response.get("error") if isinstance(response, dict) else None,
        "request_info": data.get("request_info"),
        "preview": compact_preview,
        "candidate_count": len(recommendation_candidates),
        "candidates": recommendation_candidates,
        "sample_count": len(recommendation_samples),
        "samples": recommendation_samples,
    }


def _compact_generic_mcp_payload(response: Dict[str, Any]) -> Dict[str, Any]:
    """Compact generic MCP payload into a small, LLM-friendly structure."""
    parsed = _parse_mcp_payload(response)
    data = parsed.get("data", {}) if isinstance(parsed, dict) else {}
    if not isinstance(data, dict):
        data = {}

    preview = data.get("preview", [])
    compact_preview: List[Dict[str, Any]] = []
    if isinstance(preview, list):
        for item in preview[:6]:
            if isinstance(item, dict):
                compact_preview.append(
                    {"key": item.get("key"), "value": item.get("value")}
                )

    recommendation_samples = _extract_recommendation_samples(data, max_items=8)
    return {
        "has_error": bool(response.get("error")) if isinstance(response, dict) else False,
        "error": response.get("error") if isinstance(response, dict) else None,
        "preview": compact_preview,
        "sample_count": len(recommendation_samples),
        "samples": recommendation_samples,
    }


def _map_service_to_resource_types(service: str) -> List[str]:
    lower = (service or "").lower()
    if "ec2" in lower or "elastic compute cloud" in lower:
        return ["Ec2Instance"]
    if "rds" in lower:
        return ["RdsDbInstance", "RdsDbInstanceStorage"]
    if "ebs" in lower:
        return ["EbsVolume"]
    if "lambda" in lower:
        return ["LambdaFunction"]
    if "ecs" in lower:
        return ["EcsService"]
    return []


def _is_recommendation_like_record(record: Dict[str, Any]) -> bool:
    key_set = {k.lower() for k in record.keys()}
    signal_keys = {
        "resourcearn",
        "resourceid",
        "resourcetype",
        "estimatedmonthlysavings",
        "estimatedsavings",
        "actiontype",
        "finding",
        "currentinstance",
        "recommendedinstance",
        "lookbackperiodindays",
        "recommendationoptions",
    }
    return any(key in key_set for key in signal_keys)


def _sanitize_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only high-signal fields to avoid bloating prompt context."""
    keep_keys = {
        "accountId",
        "region",
        "resourceType",
        "resourceId",
        "resourceArn",
        "actionType",
        "implementationEffort",
        "restartNeeded",
        "rollbackPossible",
        "finding",
        "findingReasonCodes",
        "estimatedMonthlySavings",
        "estimatedSavings",
        "savingsOpportunity",
        "savingsOpportunityPercentage",
        "currentConfiguration",
        "recommendedConfiguration",
        "currentInstanceType",
        "recommendedInstanceType",
        "recommendationOptions",
        "utilizationMetrics",
    }
    cleaned: Dict[str, Any] = {}
    for key in keep_keys:
        if key in record:
            cleaned[key] = record[key]
    return cleaned if cleaned else {"raw_keys": list(record.keys())[:12]}


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _extract_recommendation_candidates(
    data: Dict[str, Any],
    max_items: int = 25,
) -> List[Dict[str, Any]]:
    """Extract normalized recommendation entries from cost-optimization payload."""
    if not isinstance(data, dict):
        return []
    recommendations = data.get("recommendations")
    if not isinstance(recommendations, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for rec in recommendations:
        if not isinstance(rec, dict):
            continue

        recommendation_id = (
            rec.get("recommendation_id")
            or rec.get("recommendationId")
            or rec.get("id")
        )
        item = {
            "recommendation_id": recommendation_id,
            "action_type": rec.get("action_type") or rec.get("actionType"),
            "current_resource_type": (
                rec.get("current_resource_type") or rec.get("resourceType")
            ),
            "recommended_resource_type": rec.get("recommended_resource_type"),
            "region": rec.get("region"),
            "estimated_monthly_savings": _num(
                rec.get("estimated_monthly_savings")
                or rec.get("estimatedMonthlySavings")
                or rec.get("estimated_savings")
            ),
            "estimated_savings_percentage": _num(
                rec.get("estimated_savings_percentage")
                or rec.get("savingsOpportunityPercentage")
            ),
            "implementation_effort": (
                rec.get("implementation_effort") or rec.get("implementationEffort")
            ),
            "restart_needed": rec.get("restart_needed") or rec.get("restartNeeded"),
            "rollback_possible": rec.get("rollback_possible")
            or rec.get("rollbackPossible"),
            "resource_arn": rec.get("resource_arn") or rec.get("resourceArn"),
            "resource_id": rec.get("resource_id") or rec.get("resourceId"),
        }
        normalized.append(item)

    normalized.sort(key=lambda r: r.get("estimated_monthly_savings", 0.0), reverse=True)
    return normalized[:max_items]


def _compact_rec_details_payload(response: Dict[str, Any]) -> Dict[str, Any]:
    """Compact rec-details response to high-signal fields for LLM prompting."""
    parsed = _parse_mcp_payload(response)
    data = parsed.get("data", {}) if isinstance(parsed, dict) else {}
    if not isinstance(data, dict):
        data = {}

    base = data.get("base_recommendation", {})
    if not isinstance(base, dict):
        base = {}

    additional = data.get("additional_details", {})
    if not isinstance(additional, dict):
        additional = {}

    compact = {
        "has_error": bool(response.get("error")) if isinstance(response, dict) else False,
        "status": parsed.get("status") if isinstance(parsed, dict) else None,
        "recommendation_id": (
            base.get("recommendation_id")
            or base.get("recommendationId")
            or base.get("id")
        ),
        "action_type": base.get("action_type") or base.get("actionType"),
        "current_resource_type": base.get("current_resource_type"),
        "recommended_resource_type": base.get("recommended_resource_type"),
        "estimated_monthly_savings": _num(base.get("estimated_monthly_savings")),
        "estimated_savings_percentage": _num(base.get("estimated_savings_percentage")),
        "estimated_monthly_cost": _num(base.get("estimated_monthly_cost")),
        "currency_code": base.get("currency_code"),
        "implementation_effort": base.get("implementation_effort"),
        "restart_needed": base.get("restart_needed"),
        "rollback_possible": base.get("rollback_possible"),
        "lookback_period_in_days": base.get("lookback_period_in_days"),
        "recommended_resource_details": base.get("recommended_resource_details"),
        "additional_detail_error": additional.get("error"),
    }

    template = data.get("template")
    if isinstance(template, str) and template:
        compact["template"] = template[:600]
    formatting = data.get("formatting_instructions")
    if isinstance(formatting, str) and formatting:
        compact["formatting_instructions"] = formatting[:600]

    return compact


def _extract_recommendation_samples(
    data: Dict[str, Any],
    max_items: int = 8,
) -> List[Dict[str, Any]]:
    """Recursively scan MCP payload and extract recommendation-like records."""
    if not isinstance(data, dict):
        return []

    samples: List[Dict[str, Any]] = []

    def _walk(value: Any, depth: int) -> None:
        if depth > 5 or len(samples) >= max_items:
            return
        if isinstance(value, dict):
            if _is_recommendation_like_record(value):
                samples.append(_sanitize_record(value))
                return
            for nested in value.values():
                _walk(nested, depth + 1)
            return
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                _walk(item, depth + 1)
                if len(samples) >= max_items:
                    break

    _walk(data, 0)
    return samples


def _compute_optimizer_operation_for_opportunity(
    opportunity: RemediationOpportunity,
) -> str:
    service = (opportunity.service or "").lower()
    category = (opportunity.category or "").lower()

    if "ecs" in service or "ecs" in category:
        return "get_ecs_service_recommendations"
    if "ebs" in service or "ebs" in category:
        return "get_ebs_volume_recommendations"
    if "rds" in service or "rds" in category:
        return "get_rds_recommendations"
    return "get_ec2_instance_recommendations"


async def fetch_cost_breakdown_for_opportunity(
    opportunity: RemediationOpportunity,
    lookback_days: int = 30,
) -> Dict[str, Any]:
    """Fetch a compact cost breakdown summary for an opportunity.

    We intentionally pre-aggregate Cost Explorer data into a small structure to
    keep LLM prompts small and efficient (avoid full ResultsByTime payloads).
    """
    window = _default_time_window(lookback_days)
    logger.info(
        "Fetching cost breakdown for opportunity %s (%s) over %d days",
        opportunity.id,
        opportunity.category,
        lookback_days,
    )

    # Group by USAGE_TYPE to give texture to how spend is distributed.
    raw = await billing_mcp_client.get_cost_and_usage(
        time_period=dict(window),
        granularity="DAILY",
        metric="NetUnblendedCost",
        group_by="USAGE_TYPE",
    )

    results_by_time = CostDataProcessor.extract_results_by_time(raw)
    if not results_by_time:
        return {
            "window": window,
            "total_cost_window": 0.0,
            "gross_spend_window": 0.0,
            "credits_refunds_window": 0.0,
            "top_usage_types": [],
        }

    aggregated = CostDataProcessor.aggregate_grouped_service_cost(
        results_by_time, metric_key="NetUnblendedCost"
    )
    top_usage_types = CostDataProcessor.get_top_services(
        aggregated.get("service_spend", {}),
        top_n=8,
    )

    return {
        "window": window,
        "total_cost_window": float(aggregated.get("total_monthly_spend", 0.0) or 0.0),
        "gross_spend_window": float(aggregated.get("gross_spend", 0.0) or 0.0),
        "credits_refunds_window": float(
            aggregated.get("credits_refunds", 0.0) or 0.0
        ),
        "top_usage_types": top_usage_types,
    }


async def fetch_optimization_hub_data_for_opportunity(
    opportunity: RemediationOpportunity,
) -> Dict[str, Any]:
    """Fetch Cost Optimization Hub recommendation summaries and details for an opportunity."""
    # Cost Optimization Hub filter shape from tool docs.
    resource_types = _map_service_to_resource_types(opportunity.service)
    filters: Dict[str, Any] = {}
    if resource_types:
        filters["resourceTypes"] = resource_types
    if opportunity.region:
        filters["regions"] = [opportunity.region]

    summaries_raw = await billing_mcp_client.list_recommendation_summaries(
        group_by="ResourceType",
        filters=filters or None,
        max_results=20,
    )
    details_raw = await billing_mcp_client.list_recommendations(
        filters=filters or None,
        max_results=20,
    )

    summaries = _compact_cost_optimization_payload(summaries_raw)
    details = _compact_cost_optimization_payload(details_raw)

    top_candidates = details.get("candidates", [])[:3]
    detailed_recommendations: List[Dict[str, Any]] = []
    for candidate in top_candidates:
        if not isinstance(candidate, dict):
            continue
        recommendation_id = candidate.get("recommendation_id")
        if not isinstance(recommendation_id, str) or not recommendation_id:
            continue
        rec_details_raw = await billing_mcp_client.get_recommendation_details(
            recommendation_id
        )
        rec_detail = _compact_rec_details_payload(rec_details_raw)
        rec_detail["from_candidate"] = candidate
        detailed_recommendations.append(rec_detail)

    return {
        "filters": filters,
        "summaries": summaries,
        "recommendations": details,
        "top_recommendation_details": detailed_recommendations,
    }


async def fetch_compute_optimizer_data_for_opportunity(
    opportunity: RemediationOpportunity,
) -> Dict[str, Any]:
    """Fetch compact compute-optimizer recommendations for compute-heavy opportunities."""
    operation = _compute_optimizer_operation_for_opportunity(opportunity)
    filters: Dict[str, Any] = {}
    if opportunity.region:
        filters["regions"] = [opportunity.region]

    raw = await billing_mcp_client.get_compute_optimizer_recommendations(
        operation=operation,
        filters=filters or None,
        max_results=20,
    )
    return {
        "operation": operation,
        "filters": filters,
        "result": _compact_generic_mcp_payload(raw),
    }


async def fetch_recommendations_for_opportunities(
    opportunities: List[RemediationOpportunity],
) -> Dict[str, Any]:
    """Batch fetch compact recommendation context keyed by opportunity id.

    This uses only the Billing & Cost Management MCP today, but is designed so
    that future Compute Optimizer / Trusted Advisor MCP clients can be wired in
    without changing graph/node wiring.
    """
    results: Dict[str, Any] = {}

    for opp in opportunities:
        try:
            breakdown_summary = await fetch_cost_breakdown_for_opportunity(opp)
            optimization_hub = await fetch_optimization_hub_data_for_opportunity(opp)
            compute_optimizer = await fetch_compute_optimizer_data_for_opportunity(opp)
            results[opp.id] = {
                "category": opp.category,
                "service": opp.service,
                "region": opp.region,
                "monthly_cost": opp.monthly_cost,
                "usage_type_summary": breakdown_summary,
                "optimization_hub": optimization_hub,
                "compute_optimizer": compute_optimizer,
            }
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error(
                "Failed to fetch recommendations for opportunity %s: %s",
                opp.id,
                exc,
            )
            results[opp.id] = {"error": str(exc), "category": opp.category}

    logger.info(
        "Fetched compact recommendation context for %d opportunities", len(results)
    )
    return results

