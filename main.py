import asyncio
import json
import logging

from config import settings
from graph.manager_graph import ManagerGraph


logger = logging.getLogger(__name__)


async def main() -> None:
    """Run the Manager graph end-to-end for a sample query."""
    logger.info("Starting AWS Cost Optimization Manager Agent")

    graph = ManagerGraph()
    user_query = "What are my top 3 AWS services by spend and forecast for next month?"

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


if __name__ == "__main__":
    asyncio.run(main())


