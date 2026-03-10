import logging
from typing import Any, Dict, List

from models.schemas import RemediationInputItem, RemediationOpportunity

logger = logging.getLogger(__name__)


def normalize_service_name(service_name: str) -> str:
    """Map loosely formatted service names into canonical buckets."""
    name = (service_name or "").strip()
    lower = name.lower()

    if "ec2" in lower or "elastic compute" in lower:
        return "AmazonEC2"
    if "rds" in lower or "relational database" in lower:
        return "AmazonRDS"
    if "ebs" in lower or "elastic block store" in lower:
        return "AmazonEBS"
    if "ecs" in lower or "elastic container service" in lower:
        return "AmazonECS"
    if "s3" in lower:
        return "AmazonS3"
    if "nat" in lower and "gateway" in lower:
        return "AWSNATGateway"
    if "elastic ip" in lower or "eip" in lower:
        return "ElasticIP"

    # Fallback to original string if no mapping was found
    return name


def detect_opportunities(
    items: List[RemediationInputItem],
) -> List[RemediationOpportunity]:
    """Detect high-level optimization opportunities from normalized items.

    This is intentionally heuristic and conservative – it looks for obvious
    high-cost patterns and anomaly hints, leaving nuanced analysis to the LLM.
    """
    if not items:
        return []

    total_cost = sum(max(i.monthly_cost, 0.0) for i in items)
    opportunities: List[RemediationOpportunity] = []

    def pct_of_total(cost: float) -> float:
        return (cost / total_cost * 100.0) if total_cost > 0 else 0.0

    for idx, item in enumerate(items):
        service = normalize_service_name(item.service_name)
        cost = max(item.monthly_cost, 0.0)
        pct = pct_of_total(cost)
        usage_lower = item.usage_type.lower()

        # High EC2 cost
        if service == "AmazonEC2" and (pct >= 20.0 or cost >= 500.0):
            opportunities.append(
                RemediationOpportunity(
                    id=f"ec2-high-cost-{idx}",
                    category="ec2_high_cost",
                    service=service,
                    region=item.region,
                    monthly_cost=cost,
                    evidence=(
                        f"EC2 accounts for {pct:.1f}% of analyzed spend "
                        f"(~${cost:,.2f}/month) with usage type '{item.usage_type}'."
                    ),
                    estimated_waste_band="high" if pct >= 30.0 else "medium",
                )
            )

        # High ECS/Fargate cost
        if service == "AmazonECS" and (pct >= 15.0 or cost >= 200.0):
            opportunities.append(
                RemediationOpportunity(
                    id=f"ecs-high-cost-{idx}",
                    category="ecs_high_cost",
                    service=service,
                    region=item.region,
                    monthly_cost=cost,
                    evidence=(
                        f"ECS contributes {pct:.1f}% of analyzed spend "
                        f"(~${cost:,.2f}/month) with usage type '{item.usage_type}'."
                    ),
                    estimated_waste_band="high" if pct >= 25.0 else "medium",
                )
            )

        # RDS idle / over-provisioned hint
        if service == "AmazonRDS" and (pct >= 10.0 or cost >= 300.0):
            if "idle" in usage_lower or "stopped" in usage_lower:
                band = "high"
            else:
                band = "medium"
            opportunities.append(
                RemediationOpportunity(
                    id=f"rds-idle-{idx}",
                    category="rds_idle_or_overprovisioned",
                    service=service,
                    region=item.region,
                    monthly_cost=cost,
                    evidence=(
                        "RDS shows significant monthly cost "
                        f"(~${cost:,.2f}) with usage '{item.usage_type}', "
                        "which may indicate idle or over-provisioned instances."
                    ),
                    estimated_waste_band=band,
                )
            )

        # EBS potentially unattached or over-sized
        if service == "AmazonEBS" and (pct >= 5.0 or cost >= 100.0):
            if "volume" in usage_lower and "unused" in usage_lower:
                band = "high"
            else:
                band = "medium"
            opportunities.append(
                RemediationOpportunity(
                    id=f"ebs-unattached-{idx}",
                    category="ebs_unattached_or_oversized",
                    service=service,
                    region=item.region,
                    monthly_cost=cost,
                    evidence=(
                        "EBS storage contributes non-trivial cost "
                        f"(~${cost:,.2f}/month). Review volumes for unattached or "
                        "over-sized disks."
                    ),
                    estimated_waste_band=band,
                )
            )

        # Data transfer spikes / NAT gateway overuse
        if "data transfer" in usage_lower or "aws nat gateway" in usage_lower:
            band = "high" if pct >= 5.0 else "medium"
            opportunities.append(
                RemediationOpportunity(
                    id=f"data-transfer-{idx}",
                    category="data_transfer_or_nat_gw_spike",
                    service=service,
                    region=item.region,
                    monthly_cost=cost,
                    evidence=(
                        f"High data transfer or NAT gateway related usage "
                        f"('{item.usage_type}') costing ~${cost:,.2f}/month."
                    ),
                    estimated_waste_band=band,
                )
            )

        # Elastic IP idle usage
        if service == "ElasticIP" or "elastic ip" in usage_lower:
            opportunities.append(
                RemediationOpportunity(
                    id=f"elastic-ip-{idx}",
                    category="unused_elastic_ip",
                    service="ElasticIP",
                    region=item.region,
                    monthly_cost=cost,
                    evidence=(
                        "Detected Elastic IP related charges which often indicate "
                        "unused or idle addresses."
                    ),
                    estimated_waste_band="low",
                )
            )

        # Savings Plan / RI coverage gaps (heuristic: heavy on-demand)
        if any(
            key in usage_lower
            for key in ["boxusage", "ondemand", "on-demand", "running hours"]
        ) and pct >= 15.0:
            opportunities.append(
                RemediationOpportunity(
                    id=f"savings-plan-gap-{idx}",
                    category="savings_plan_or_ri_gap",
                    service=service,
                    region=item.region,
                    monthly_cost=cost,
                    evidence=(
                        "Large share of spend (~"
                        f"${cost:,.2f}/month, {pct:.1f}% of analyzed costs) appears "
                        "to be On-Demand usage, suggesting Savings Plan/RI potential."
                    ),
                    estimated_waste_band="medium",
                )
            )

        # Anomaly flag passthrough
        if item.anomaly_flag:
            opportunities.append(
                RemediationOpportunity(
                    id=f"anomaly-{idx}",
                    category="spend_anomaly",
                    service=service,
                    region=item.region,
                    monthly_cost=cost,
                    evidence=(
                        "Upstream analysis flagged this line item as anomalous; "
                        f"monthly cost is ~${cost:,.2f} for usage '{item.usage_type}'."
                    ),
                    estimated_waste_band="unknown",
                )
            )

    logger.info(
        "Detected %d remediation opportunities from %d input items",
        len(opportunities),
        len(items),
    )
    return opportunities



