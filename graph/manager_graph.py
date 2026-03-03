import logging
from typing import Any, Dict

from langgraph.graph import END, START, StateGraph

from agents.manager_agent import ManagerAgent
from server.client import billing_mcp_client
from models.schemas import GraphState
from utils.cost_processor import CostDataProcessor

logger = logging.getLogger(__name__)


class ManagerGraph:
    """LangGraph-based orchestration for the Manager agent."""

    def __init__(self) -> None:
        self.agent = ManagerAgent()
        self.graph = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(dict)

        workflow.add_node("discover_tools_node", self.discover_tools_node)
        workflow.add_node("fetch_cost_node", self.fetch_cost_node)
        workflow.add_node("fetch_forecast_node", self.fetch_forecast_node)
        workflow.add_node("analyze_node", self.analyze_node)
        workflow.add_node("output_node", self.output_node)

        workflow.add_edge(START, "discover_tools_node")
        workflow.add_edge("discover_tools_node", "fetch_cost_node")
        workflow.add_edge("fetch_cost_node", "fetch_forecast_node")
        workflow.add_edge("fetch_forecast_node", "analyze_node")
        workflow.add_edge("analyze_node", "output_node")
        workflow.add_edge("output_node", END)

        return workflow.compile()

    async def discover_tools_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("discover_tools_node: discovering MCP tools")
        try:
            # Optional: quick credential sanity check before we start using MCP tools.
            cred_status = await billing_mcp_client.check_aws_credentials()
            state["aws_credentials"] = cred_status
            if cred_status.get("valid") is False:
                state.setdefault("errors", []).append(
                    f"aws_credentials: {cred_status.get('error')}"
                )

            await billing_mcp_client.connect()
            tools = await billing_mcp_client.discover_tools()
            state["discovered_tools"] = tools
        except Exception as exc:
            logger.error("Tool discovery failed: %s", exc)
            state.setdefault("errors", []).append(f"discover_tools: {exc}")
        return state

    async def fetch_cost_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("fetch_cost_node: fetching production cost views")
        try:
            from datetime import date, timedelta

            today = date.today()
            first_day_current_month = date(today.year, today.month, 1)
            if today.month == 1:
                first_day_previous_month = date(today.year - 1, 12, 1)
            else:
                first_day_previous_month = date(today.year, today.month - 1, 1)
            target_start = first_day_previous_month
            target_end_exclusive = first_day_current_month

            previous_month_last_day = target_end_exclusive - timedelta(days=1)

            billing_month_label = target_start.strftime("%B %Y")
            billing_period_label = (
                f"{target_start.isoformat()} to {previous_month_last_day.isoformat()}"
            )

            # Comparison windows (current MTD vs same period previous month)
            mtd_start = first_day_current_month
            mtd_end_exclusive = today + timedelta(days=1)
            mtd_days = (mtd_end_exclusive - mtd_start).days

            splm_start = first_day_previous_month
            splm_end_exclusive = splm_start + timedelta(days=mtd_days)
            if splm_end_exclusive > first_day_current_month:
                splm_end_exclusive = first_day_current_month
            splm_end_inclusive = splm_end_exclusive - timedelta(days=1)

            logger.info(
                (
                    "Cost windows: previous_full=[%s,%s) mtd=[%s,%s) "
                    "same_period_last_month=[%s,%s)"
                ),
                target_start.isoformat(),
                target_end_exclusive.isoformat(),
                mtd_start.isoformat(),
                mtd_end_exclusive.isoformat(),
                splm_start.isoformat(),
                splm_end_exclusive.isoformat(),
            )

            # Query A: Accurate monthly total (ungrouped)
            total_response = await billing_mcp_client.get_cost_and_usage(
                time_period={
                    "start": target_start.isoformat(),
                    "end": target_end_exclusive.isoformat(),
                },
                granularity="MONTHLY",
                metric="NetUnblendedCost",
                group_by=None,
            )
            total_results = CostDataProcessor.extract_results_by_time(total_response)
            monthly_total = CostDataProcessor.extract_total_from_results(
                total_results, "NetUnblendedCost"
            )

            # Query B: Service breakdown (grouped) - for top services and gross/credits only
            grouped_response = await billing_mcp_client.get_cost_and_usage(
                time_period={
                    "start": target_start.isoformat(),
                    "end": target_end_exclusive.isoformat(),
                },
                granularity="MONTHLY",
                metric="NetUnblendedCost",
                group_by="SERVICE",
            )
            grouped_results = CostDataProcessor.extract_results_by_time(grouped_response)
            grouped_agg = CostDataProcessor.aggregate_grouped_service_cost(
                grouped_results,
                metric_key="NetUnblendedCost",
            )
            top_services = CostDataProcessor.get_top_services(
                grouped_agg["service_spend"], top_n=3
            )

            # Query C: Month-to-date current month (ungrouped)
            mtd_response = await billing_mcp_client.get_cost_and_usage(
                time_period={
                    "start": mtd_start.isoformat(),
                    "end": mtd_end_exclusive.isoformat(),
                },
                granularity="DAILY",
                metric="NetUnblendedCost",
                group_by=None,
            )
            mtd_results = CostDataProcessor.extract_results_by_time(mtd_response)
            month_to_date_spend = CostDataProcessor.extract_total_from_results(
                mtd_results, "NetUnblendedCost"
            )

            # Query D: Same-period-last-month (ungrouped)
            splm_response = await billing_mcp_client.get_cost_and_usage(
                time_period={
                    "start": splm_start.isoformat(),
                    "end": splm_end_exclusive.isoformat(),
                },
                granularity="DAILY",
                metric="NetUnblendedCost",
                group_by=None,
            )
            splm_results = CostDataProcessor.extract_results_by_time(splm_response)
            same_period_last_month_spend = CostDataProcessor.extract_total_from_results(
                splm_results, "NetUnblendedCost"
            )

            percent_change = 0.0
            if same_period_last_month_spend > 0:
                percent_change = (
                    (month_to_date_spend - same_period_last_month_spend)
                    / same_period_last_month_spend
                ) * 100.0

            state["cost_data"] = {
                "total_monthly_spend": monthly_total,
                # Gross/credits are informational from grouped rows only.
                "gross_spend": grouped_agg.get("gross_spend", 0.0),
                "credits_refunds": grouped_agg.get("credits_refunds", 0.0),
                "top_services": top_services,
                "billing_month": billing_month_label,
                "billing_period": billing_period_label,
                "month_to_date_spend": month_to_date_spend,
                "month_to_date_period": (
                    f"{mtd_start.isoformat()} to {(mtd_end_exclusive - timedelta(days=1)).isoformat()}"
                ),
                "same_period_last_month_spend": same_period_last_month_spend,
                "same_period_last_month_period": (
                    f"{splm_start.isoformat()} to {splm_end_inclusive.isoformat()}"
                ),
                "month_to_date_change_pct": percent_change,
            }

            logger.info(
                (
                    "Cost summary complete: monthly_total=%.4f top_services=%d "
                    "mtd=%.4f splm=%.4f mtd_change_pct=%.2f"
                ),
                monthly_total,
                len(top_services),
                month_to_date_spend,
                same_period_last_month_spend,
                percent_change,
            )
        except Exception as exc:
            logger.error("Cost breakdown failed: %s", exc, exc_info=True)
            state.setdefault("errors", []).append(f"fetch_cost: {exc}")
            state["cost_data"] = {}
        return state

    async def fetch_forecast_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("fetch_forecast_node: fetching cost forecast")
        try:
            from datetime import date, timedelta

            today = date.today()
            # Forecast for the next full month using end-exclusive window
            if today.month == 12:
                start = date(today.year + 1, 1, 1)
                end_exclusive = date(today.year + 1, 2, 1)
            else:
                start = date(today.year, today.month + 1, 1)
                if today.month + 1 == 12:
                    end_exclusive = date(today.year + 1, 1, 1)
                else:
                    end_exclusive = date(today.year, today.month + 2, 1)
            end_inclusive = end_exclusive - timedelta(days=1)
            
            logger.info(
                "Requesting cost forecast for next full month: start=%s, end=%s (exclusive)",
                start.isoformat(),
                end_exclusive.isoformat(),
            )

            # Step 1: Call getCostForecast via MCP
            raw_response = await billing_mcp_client.get_cost_forecast(
                time_period={"start": start.isoformat(), "end": end_exclusive.isoformat()},
                granularity="DAILY",
            )
            
            # Step 2: Extract only the forecasted total amount
            forecast_total = CostDataProcessor.extract_forecast_total(raw_response)
            
            # Step 3: Store only the compact forecast value (not raw JSON)
            state["forecast_data"] = {
                "forecast_next_month": forecast_total,
                "forecast_month": start.strftime("%B %Y"),
                "forecast_period": f"{start.isoformat()} to {end_inclusive.isoformat()}",
            }
            
            # Calculate expected days in forecast period for verification
            days_in_period = (end_exclusive - start).days
            logger.info(
                "Forecast extraction complete: forecast=%.2f for %d days (next full month: %s to %s)",
                forecast_total,
                days_in_period,
                start.isoformat(),
                end_inclusive.isoformat(),
            )
            
        except Exception as exc:
            logger.error("Forecast retrieval failed: %s", exc, exc_info=True)
            state.setdefault("errors", []).append(f"fetch_forecast: {exc}")
            state["forecast_data"] = {
                "forecast_next_month": 0.0,
                "forecast_month": "",
                "forecast_period": "",
            }
        return state

    async def analyze_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("analyze_node: analyzing costs")
        user_query = state.get("user_query", "Analyze my AWS costs.")
        cost_data = state.get("cost_data") or {}
        forecast_data = state.get("forecast_data") or {}

        result = await self.agent.analyze(user_query, cost_data, forecast_data)
        state["analysis_result"] = result
        return state

    async def output_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("output_node: finalizing output")
        return state

    async def invoke(self, user_query: str) -> Dict[str, Any]:
        """Helper to run the full graph for a given user query."""
        initial_state = GraphState(
            user_query=user_query,
            discovered_tools=[],
            cost_data=None,
            forecast_data=None,
            analysis_result=None,
            errors=[],
        ).model_dump()
        final_state = await self.graph.ainvoke(initial_state)
        return final_state


