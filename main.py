import asyncio
import json
import logging
from typing import Any, Dict, List

from graph.manager_graph import ManagerGraph
from graph.remediation_graph import RemediationGraph

logger = logging.getLogger(__name__)


async def run_manager_example() -> None:
    """Run the Manager graph end-to-end for a sample query."""
    logger.info("Starting AWS Cost Optimization Manager Agent example")

    graph = ManagerGraph()
    user_query = "What are my top 5 AWS services by spend and forecast for next month?"

    state = await graph.invoke(user_query)
    analysis = state.get("analysis_result")

    if isinstance(analysis, dict) and "error" not in analysis:
        output = {
            "total_monthly_spend": analysis["total_monthly_spend"],
            "top_services": analysis["top_services"],
            "forecast_next_month": analysis["forecast_next_month"],
            "summary": analysis["summary"],
        }
    else:
        error_msg = (
            analysis.get("error")
            if isinstance(analysis, dict)
            else "Unknown analysis error"
        )
        output = {
            "error": error_msg,
            "summary": (
                analysis.get("summary") if isinstance(analysis, dict) else ""
            ),
        }

    print(json.dumps(output, indent=2, default=str))


async def run_remediation_example(sample_items: List[Dict[str, Any]]) -> None:
    """Run the Remediation graph end-to-end for sample cost analysis items."""
    logger.info("Starting AWS Cost Remediation Agent example")

    graph = RemediationGraph()
    remediation = await graph.invoke(sample_items)
    print(json.dumps(remediation, indent=2, default=str))


async def main() -> None:
    """Entry point used by CLI / local testing.

    Currently runs only the manager example by default; the remediation example
    can be invoked by importing this module and calling ``run_remediation_example``
    with structured analysis JSON from the Cost Analysis Agent / UI.
    """
    await run_manager_example()


if __name__ == "__main__":
    asyncio.run(main())
